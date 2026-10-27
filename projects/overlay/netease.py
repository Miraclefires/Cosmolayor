# -*- coding: utf-8 -*-
r"""网易云的真实播放进度（自己解码本地日志，不联网、不依赖第三方库）

背景：网易云虽然开了 SMTC，但 timeline 四个字段（start/end/position/max_seek）恒为 0，
`last_updated_time` 还是 1601-01-01 —— 接口有能力，它没实现。

但客户端把播放事件写进 `%LOCALAPPDATA%\NetEase\CloudMusic\cloudmusic.elog`
（约 10MB，一直在追加）。这个文件不是文本，是**逐字节混淆**过的：

    每个输出字节 f(b) = d*16 + (b>>6)*4 + (~(b>>4) & 3)
    其中 d = ((b>>4) ^ (((b&15)+8) % 16)) % 16
    也就是说，拿 0x91 当换行（解码后是 0x0A），其余按上表还原成 UTF-8。

解码后每行是 `[pid:tid:MMDD/HHMMSS:开机毫秒:级别:文件(行)] [本地时间] 内容`。
我们只关心几类事件：

    【playing】,"checkPlayPrivilege",{...}             播放权限预检（带完整曲目 JSON）
    【playing】,"playOneTrackInPlayingList",{...}      播放列表切歌（track 里是曲目）
    【playing】,"native播放资源load完成，开始播放",{...} 真正出声（songId）
    【playing】,"setPlayingPosition",123.4              拖进度条
    【playing】,"native播放state",1,"<songId>_xxx"      1=播放 2=暂停（playId 证明真出声）
    【app】,{"actionId":"exitApp"}                      客户端退出

它们之间是"预检 → 出声"两段式：权限预检在"加入下一首播放"时也会写，所以不能直接当切歌，
要等 `load完成` 或 `native播放state` 的 playId 确认。

状态机跟原库一致（预检挂起、出声确认、seek/暂停锚定进度），只是去掉了 webdb 查询：
歌名/歌手/时长都能从这几个事件的 JSON 里拿到，拿不到就让上层退回 SMTC。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

_BOOT_MS: float | None = None
_BOOT_AT = 0.0


def _boot_ms() -> float | None:
    """开机时刻（epoch 毫秒）。日志头里的毫秒是"开机以来的毫秒"，
    加上它才是绝对时间；缓存 60 秒，避免每行都调系统调用。"""
    global _BOOT_MS, _BOOT_AT
    now = time.time()
    if _BOOT_MS is None or now - _BOOT_AT > 60:
        try:
            fn = ctypes.windll.kernel32.GetTickCount64
            fn.restype = ctypes.c_uint64
            _BOOT_MS = now * 1000.0 - float(fn())
            _BOOT_AT = now
        except Exception:                             # noqa: BLE001
            return None
    return _BOOT_MS

DEFAULT_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) \
    / "NetEase" / "CloudMusic" / "cloudmusic.elog"

# ---------------------------------------------------------------- 解码表
def _build_table() -> bytes:
    table = bytearray(256)
    for b in range(256):
        high = b >> 4
        low = b & 0x0F
        digit = (high ^ ((low + 8) % 16)) % 16
        table[b] = (digit * 16 + (b >> 6) * 4 + (~high & 3)) & 0xFF
    return bytes(table)


TABLE = _build_table()
NEWLINE = TABLE.index(0x0A)          # 编码流里的换行字节（0x91）

_HEADER = re.compile(
    r"^\[\d+:\d+:(\d{4})/(\d{6}):(\d+):[A-Z]+:[^\]]*\]\s*"
    r"\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\]"
)
_JSON = re.compile(r"\{.*\}")
_RE_POS = re.compile(r'"setPlayingPosition",(-?\d+(?:\.\d+)?)')
_RE_STATE = re.compile(r'"native播放state",(\d+)(?:,"([^"]*)")?')
_RE_LOAD = '"native播放资源load完成，开始播放"'
_MAX_PENDING = 16


def _to_int(v, default: int = -1) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def titles_match(a: str, b: str) -> bool:
    """歌名互相包含就算同一首（网易云有时带 (Extended Ver.) 之类的后缀差异）"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


class ElogWatcher:
    """常驻：启动时回放整份日志，之后只读追加的部分"""

    def __init__(self, path: str | os.PathLike | None = None, poll: float = 0.3):
        self.path = Path(path) if path else DEFAULT_PATH
        self.poll = poll
        self.ok = False
        self.error = ""

        self.lock = threading.Lock()
        self._track = {"id": -1, "name": "", "artists": [], "album": "", "duration": 0.0}
        self._pos = 0.0                  # 锚点：位置（秒）
        self._at = 0.0                   # 锚点：墙上时间（epoch 毫秒）
        self._playing = False
        self._anchored = False           # 本曲是否已被 seek / 播放状态显式锚定
        self._pending: dict[int, dict] = {}
        self._size = 0                   # 已消费的字节数
        self._pending_bytes = b""        # 不完整的最后一行（编码态）
        self._seek_at = 0.0              # 最近一次 seek 事件的时间（诊断用）
        self.events = 0

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        try:
            data = self.path.read_bytes()
        except OSError as e:
            self.error = f"读不到 elog：{e}"
            self.ok = False
            return
        try:
            for line in self._consume(data):
                self._apply(line)
            self._size = len(data)
            self.ok = True
            self.error = ""
        except Exception as e:                    # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.ok = False
            return
        self._thread = threading.Thread(target=self._poll, daemon=True, name="elog-tail")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---------- 尾部轮询 ----------
    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                size = self.path.stat().st_size
                if size < self._size:            # 日志被清空/轮转：整份重来
                    self._reset()
                    self._size = 0
                    self._pending_bytes = b""
                    self.start_again()
                elif size > self._size:
                    with self.path.open("rb") as f:
                        f.seek(self._size)
                        chunk = f.read()
                    self._size = size
                    for line in self._consume(chunk):
                        self._apply(line)
                    self.ok = True
                    self.error = ""
            except FileNotFoundError:
                self.ok = False
            except OSError as e:
                self.error = f"{type(e).__name__}: {e}"
            self._stop.wait(self.poll)

    def start_again(self) -> None:
        """日志被清空后重读（不重启线程）"""
        try:
            data = self.path.read_bytes()
        except OSError:
            return
        for line in self._consume(data):
            self._apply(line)
        self._size = len(data)

    # ---------- 解码 ----------
    def _consume(self, chunk: bytes) -> list[str]:
        """把编码字节切成完整行并解码；最后不完整的一行留到下次"""
        data = self._pending_bytes + chunk
        k = data.rfind(bytes((NEWLINE,)))
        if k < 0:
            self._pending_bytes = data
            return []
        head, self._pending_bytes = data[:k], data[k + 1:]
        text = head.translate(TABLE).decode("utf-8", "ignore")
        return text.split("\n")

    # ---------- 状态机 ----------
    def _reset(self) -> None:
        with self.lock:
            self._track = {"id": -1, "name": "", "artists": [], "album": "", "duration": 0.0}
            self._pos = 0.0
            self._at = 0.0
            self._playing = False
            self._anchored = False
            self._pending.clear()

    @staticmethod
    def _track_from(data: dict) -> dict:
        t = data.get("track") if isinstance(data.get("track"), dict) else data
        album = t.get("album")
        if isinstance(album, dict):
            album_name = album.get("name") or ""
        elif isinstance(album, str):
            album_name = album
        else:
            album_name = ""
        return {
            "id": _to_int(t.get("id", t.get("songId", -1))),
            "name": t.get("name") or "",
            "artists": [a.get("name", "") for a in (t.get("artists") or []) if isinstance(a, dict)],
            "album": album_name,
            "duration": max(0.0, float(t.get("duration") or 0) / 1000.0),
        }

    def _mark_pending(self, track: dict) -> None:
        if track["id"] == -1 or not track["name"]:
            return
        if track["id"] == self._track["id"]:
            return
        self._pending.pop(track["id"], None)
        self._pending[track["id"]] = track
        while len(self._pending) > _MAX_PENDING:
            self._pending.pop(next(iter(self._pending)))

    def _position_at(self, at_ms: float) -> float:
        if not self._playing:
            return max(0.0, self._pos)
        return max(0.0, self._pos + (at_ms - self._at) / 1000.0)

    def _apply(self, line: str) -> None:
        if "【" not in line:
            return
        m = _HEADER.match(line)
        if not m:
            return
        y, mo, d, hh, mm, ss = (int(m.group(i)) for i in range(4, 10))
        rel_ms = int(m.group(3))
        try:
            wall_ms = datetime(y, mo, d, hh, mm, ss).timestamp() * 1000.0
        except ValueError:
            return
        boot = _boot_ms()
        ts = (boot + rel_ms) if boot is not None else (wall_ms + rel_ms % 1000)
        if abs(ts - wall_ms) > 5000:          # 系统时钟被大改过：退回本地时间
            ts = wall_ms + rel_ms % 1000
        self.events += 1

        if '"actionId":"exitApp"' in line:
            self._reset()
            return

        if _RE_LOAD in line:
            data = self._json(line)
            if isinstance(data, dict):
                with self.lock:
                    self._on_load(data, ts)
            return

        if '"setPlayingPosition"' in line:
            pm = _RE_POS.search(line)
            if pm:
                with self.lock:
                    self._pos = max(0.0, float(pm.group(1)))
                    self._at = ts
                    self._anchored = True
                    self._seek_at = ts
            return

        if '"native播放state"' in line:
            sm = _RE_STATE.search(line)
            if sm:
                with self.lock:
                    self._on_state(sm.group(1) == "1", sm.group(2), ts)
            return

        if '"checkPlayPrivilege"' in line or '"playOneTrackInPlayingList"' in line:
            data = self._json(line)
            if isinstance(data, dict):
                with self.lock:
                    self._mark_pending(self._track_from(data))

    @staticmethod
    def _json(line: str):
        m = _JSON.search(line)
        if not m:
            return None
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None

    # 以下都在 lock 里调用
    def _on_load(self, data: dict, ts: float) -> None:
        sid = _to_int(data.get("songId", -1))
        if sid == -1:
            return
        if sid == self._track["id"]:
            # 同一首重新出声（重播 / seek 后重载）：已经被 seek 或播放状态锚定过就不动，
            # 否则当作从头开始（重播）
            if not self._anchored:
                self._pos, self._at = 0.0, ts
            self._playing = True
            return
        track = self._pending.pop(sid, None)
        if track is None:
            track = {"id": sid, "name": "", "artists": [], "album": "", "duration": 0.0}
        self._track = track
        self._pos, self._at = 0.0, ts
        self._playing = True
        self._anchored = False

    def _on_state(self, playing: bool, play_id: str | None, ts: float) -> None:
        sid = -1
        if play_id:
            sid = _to_int(play_id.split("_")[0])
        if sid != -1 and sid != self._track["id"]:
            track = self._pending.pop(sid, None)
            if track is not None:
                self._track = track
                self._pos, self._at = 0.0, ts
                self._playing = playing
                self._anchored = False
                return
            # 不知道是哪首（没抓到预检）：保持旧曲目，等下一次元数据
            return
        self._pos = self._position_at(ts)
        self._at = ts
        self._playing = playing
        self._anchored = True

    # ---------- 对外 ----------
    def snapshot(self) -> dict | None:
        if not self.ok:
            return None
        now_ms = time.time() * 1000.0
        with self.lock:
            if self._track["id"] == -1:
                return None
            dur = self._track["duration"]
            pos = self._position_at(now_ms)
            if dur > 0:
                pos = min(pos, dur)
            return {
                "name": self._track["name"],
                "artists": list(self._track["artists"]),
                "album": self._track["album"],
                "duration": dur,
                "position": round(pos, 2),
                "playing": self._playing,
                "id": self._track["id"],
            }

    def diagnostics(self) -> dict:
        with self.lock:
            return {
                "ok": self.ok,
                "error": self.error,
                "events": self.events,
                "pending": len(self._pending),
                "trackId": self._track["id"],
                "name": self._track["name"],
                "anchored": self._anchored,
                "playing": self._playing,
                "position": round(self._position_at(time.time() * 1000.0), 2),
                "duration": self._track["duration"],
                "lastSeekAgeSec": (round((time.time() * 1000.0 - self._seek_at) / 1000, 1)
                                   if self._seek_at else None),
            }


if __name__ == "__main__":
    w = ElogWatcher()
    t0 = time.time()
    w.start()
    print("可用:", w.ok, "事件:", w.events, "启动 %.2fs" % (time.time() - t0), w.error)
    for _ in range(3):
        time.sleep(0.7)
        s = w.snapshot()
        if s:
            print("  %.1fs / %.1fs  %s - %s  playing=%s" % (
                s["position"], s["duration"], s["name"], "/".join(s["artists"]), s["playing"]))
        else:
            print("  没有数据")
    w.stop()
