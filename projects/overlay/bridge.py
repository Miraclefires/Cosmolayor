# -*- coding: utf-8 -*-
"""SMTC 数据桥

后台常驻：拉起 tools/smtc-bridge.ps1（Windows PowerShell 5.1 + WinRT），
解析它吐出的 JSON 行，补上进度估算和歌词，写 runtime/now-playing.json。

为什么要估算进度：网易云走 SMTC 只给元数据，Position/EndTime 恒为 0（实测）。
真实进度（Spotify / 浏览器）直接透传，不估。

单独跑（调试用）：
    python bridge.py            # 每秒打印当前状态
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / "runtime"
TOOLS = BASE / "tools"

# 封面：自己写的 C# 小工具读 SMTC 缩略图（PowerShell 5.1 投影不出那个流对象）
try:
    from cover import fetch_thumbnail, status as cover_status
except Exception:                                    # noqa: BLE001
    def fetch_thumbnail(timeout: float = 8.0):       # type: ignore[misc]
        return None
    def cover_status():                              # type: ignore[misc]
        return {"ok": False, "error": "cover.py 不可用"}

# 真实播放进度：网易云的 timeline 是空的，只能读它的本地日志
try:
    from netease import ElogWatcher, titles_match
except Exception:                                    # noqa: BLE001
    ElogWatcher = None                               # type: ignore[assignment]
    def titles_match(a, b):                          # type: ignore[misc]
        return False

PS_EXE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------- 子进程回收
# PowerShell 数据桥是常驻子进程：服务被强杀 / 崩溃时，如果只靠 proc.kill()，
# 这个子进程会活下来变成孤儿（2026-10-27 一晚上攒了 30 多个）。
# 用 Windows 的 Job Object（KILL_ON_JOB_CLOSE）把它绑到服务进程上 ——
# 服务一退出（哪怕是 taskkill），job 句柄关闭，系统就把子进程一起收掉。
_JOB = None


def _make_job():
    """建一个"最后一个句柄关闭就杀掉所有成员"的 job（失败返回 None，不影响主流程）"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000      # KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        return kernel32, job
    except Exception:                                    # noqa: BLE001
        return None


def _bind_to_job(proc) -> None:
    """把刚起的子进程塞进 job；失败就算了（老系统 / 嵌套 job 限制）"""
    global _JOB
    if _JOB is None:
        _JOB = _make_job()
    if not _JOB:
        return
    kernel32, job = _JOB
    try:
        kernel32.AssignProcessToJobObject(job, int(proc._handle))   # noqa: SLF001
    except Exception:                                    # noqa: BLE001
        pass



# ---------------------------------------------------------------- 桥
class Bridge(threading.Thread):
    def __init__(self, interval_ms: int = 800, runtime: Path = RUNTIME):
        super().__init__(daemon=True, name="smtc-bridge")
        self.interval_ms = interval_ms
        self.runtime = runtime
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.json_path = self.runtime / "now-playing.json"
        self.cover_path = self.runtime / "cover.jpg"

        self.lock = threading.Lock()
        self.latest: dict | None = None
        self.ok = False
        self.error = ""
        self.cover_version = 0

        self._stop = threading.Event()
        self._restart = False
        self._proc = None
        self.restarts = 0
        self.last_raw = None
        self._key = ""
        self._pos = 0.0
        self._at = 0.0
        self._dur = 0.0
        self._playing = False
        self._real = False
        self._t0 = 0.0
        self._cover_jobs: set[str] = set()
        self._source = "estimate"                     # estimate | smtc | elog
        self.elog = ElogWatcher() if ElogWatcher else None

    # ---------- 生命周期 ----------
    def run(self) -> None:
        if self.elog is not None:
            self.elog.start()                          # 网易云日志监听（失败也不影响元数据）
        first = True
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception as e:                      # noqa: BLE001
                self.ok = False
                self.error = f"{type(e).__name__}: {e}"
                self._stop.wait(2.0)
            if not first and not self._stop.is_set():
                self.restarts += 1
            first = False
            self._stop.wait(0.4)                        # 子进程退出后别空转

    def stop(self) -> None:
        self._stop.set()
        if self.elog is not None:
            self.elog.stop()

    def restart(self) -> None:
        """踢掉 PowerShell 子进程重开（换播放器源 / 卡住时用）"""
        self._restart = True
        with self.lock:
            # 重开后当作新会话，重新识别当前曲目
            self._key = ""
            self._t0 = 0.0
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _run_once(self) -> None:
        script = TOOLS / "smtc-bridge.ps1"
        if not PS_EXE.is_file():
            self.ok = False
            self.error = f"找不到 powershell.exe：{PS_EXE}"
            self._stop.wait(5)
            return

        cmd = [
            str(PS_EXE), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-IntervalMs", str(self.interval_ms),
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        _bind_to_job(proc)                              # 服务死了别再留孤儿 PowerShell
        self._proc = proc
        try:
            for line in proc.stdout:                    # 阻塞读，一行一拍
                if self._stop.is_set() or self._restart:
                    break
                line = line.strip()
                if line:
                    self._ingest(line)
        finally:
            self._restart = False
            try:
                proc.kill()
            except OSError:
                pass
            self._proc = None

    # ---------- 数据处理 ----------
    def _ingest(self, line: str) -> None:
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            return

        if m.get("fatal"):
            self.ok = False
            self.error = m["fatal"]
            return
        if not m.get("ok"):
            self.ok = False
            self.error = m.get("error", "SMTC 读取失败")
            return

        self.ok = True
        self.error = ""
        self.last_raw = m
        now = time.time()

        if m.get("none"):
            with self.lock:
                if self._key:
                    self._key = ""
                    self._pos = 0.0
                self.latest = {"idle": True, "playing": False, "ts": now}
            self._write()
            return

        title = (m.get("title") or "").strip()
        artist = (m.get("artist") or "").strip()
        album = (m.get("album") or "").strip()
        key = f"{title}|{artist}"
        pos = float(m.get("position") or 0.0)
        dur = float(m.get("duration") or 0.0)
        playing = bool(m.get("playing"))

        # 网易云：SMTC 的 timeline 恒为 0，用本地日志里的真实进度覆盖
        elog = self.elog.snapshot() if self.elog is not None else None
        elog_ok = bool(elog and elog.get("duration", 0) > 0
                       and titles_match(elog.get("name", ""), title))
        if elog_ok:
            pos = max(0.0, float(elog["position"]))
            dur = float(elog["duration"])
            playing = bool(elog["playing"])

        with self.lock:
            if key != self._key:
                self._key = key
                self._pos = max(0.0, pos)
                self._at = now
                self._dur = dur
                self._playing = playing
                self._real = elog_ok or (pos > 0 and dur > 0)
                self._source = "elog" if elog_ok else ("smtc" if self._real else "estimate")
                self._t0 = now
                self._kick_cover(key)
            else:
                if elog_ok:
                    self._pos, self._at, self._real = pos, now, True
                    self._source = "elog"
                elif pos > 0 and dur > 0:
                    # 来源给了真实进度：直接用，顺便用它校准
                    self._pos, self._at, self._real = pos, now, True
                    self._source = "smtc"
                elif self._playing and not self._real:
                    self._pos += now - self._at
                    self._at = now
                    self._source = "estimate"
                else:
                    self._at = now
                if dur > 0:
                    self._dur = dur
                self._playing = playing

            self.latest = {
                "idle": False,
                "title": title or "—",
                "artist": artist,
                "album": album,
                "source": m.get("source", ""),
                "playing": self._playing,
                "position": round(self._pos, 2),
                "duration": round(self._dur, 2),
                "durationKnown": self._dur > 0,
                "real": self._real,
                "positionSource": self._source,
                "demo": False,
                "ts": now,
            }
        self._write()

    # ---------- 封面 ----------
    def _kick_cover(self, key: str) -> None:
        """换歌时异步抓一次封面（不阻塞轮询）；抓不到就留空的占位"""
        if key in self._cover_jobs:
            return
        self._cover_jobs.add(key)

        def worker():
            try:
                try:
                    self.cover_path.unlink()       # 先清旧图，别让上一首的封面挂着
                except OSError:
                    pass
                for delay in (0.0, 1.5, 4.0):      # 有些播放器封面到得比元数据晚
                    if delay:
                        time.sleep(delay)
                    data = fetch_thumbnail()
                    if data and len(data) > 512:
                        tmp = self.cover_path.with_suffix(".jpg.tmp")
                        tmp.write_bytes(data)
                        os.replace(tmp, self.cover_path)
                        return
            except Exception:                      # noqa: BLE001
                pass
            finally:
                self._cover_jobs.discard(key)

        threading.Thread(target=worker, daemon=True, name="cover").start()

    # ---------- 输出 ----------
    def _cover_url(self) -> str:
        try:
            st = self.cover_path.stat()
        except OSError:
            return ""
        ver = int(st.st_mtime_ns // 1_000_000)
        if ver != self.cover_version:
            self.cover_version = ver
        return f"/cover.jpg?v={ver}"

    def _write(self) -> None:
        with self.lock:
            data = dict(self.latest or {"idle": True, "playing": False})
            data["cover"] = self._cover_url()
            data["epoch"] = time.time()
        try:
            tmp = self.json_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.json_path)
        except OSError as e:
            self.error = f"写 now-playing.json 失败：{e}"

    def snapshot(self) -> tuple[dict | None, dict]:
        """给 server 用：返回 (数据, 桥状态)"""
        with self.lock:
            data = dict(self.latest) if self.latest else None
            if data is not None:
                data["cover"] = self._cover_url()
        age = (time.time() - data["ts"]) * 1000 if data else None
        return data, {"ok": self.ok, "ageMs": age, "error": self.error,
                      "source": (data or {}).get("source", "")}

    def diagnostics(self) -> dict:
        """给控制面板的「诊断」用：把桥的内部状态摊开"""
        with self.lock:
            info = {
                "ok": self.ok,
                "error": self.error,
                "restarts": self.restarts,
                "intervalMs": self.interval_ms,
                "raw": dict(self.last_raw) if self.last_raw else None,
                "trackKey": self._key,
                "position": round(self._pos, 2),
                "duration": round(self._dur, 2),
                "realProgress": self._real,
            }
        p = self._proc
        info["psAlive"] = bool(p is not None and p.poll() is None)
        info["positionSource"] = self._source
        if self.elog is not None:
            e = self.elog.snapshot()
            info["elog"] = {
                "ok": self.elog.ok,
                "error": self.elog.error,
                "track": (e or {}).get("name", ""),
                "position": (e or {}).get("position"),
                "duration": (e or {}).get("duration"),
            }
            detail = getattr(self.elog, "diagnostics", None)
            if callable(detail):
                info["elogDetail"] = detail()
        try:
            info["coverBytes"] = self.cover_path.stat().st_size
        except OSError:
            info["coverBytes"] = 0
        info["cover"] = cover_status()
        info["coverUrl"] = self._cover_url()
        return info


# ---------------------------------------------------------------- 调试入口
def _main() -> None:
    b = Bridge()
    b.start()
    print("bridge 启动，Ctrl+C 结束（先放首歌试试）")
    try:
        while True:
            time.sleep(1)
            data, st = b.snapshot()
            if not data:
                print("等待数据…  ok=%s %s" % (st["ok"], st["error"]))
            elif data.get("idle"):
                print("无播放器  ok=%s %s" % (st["ok"], st["error"]))
            else:
                print("%-22s %-14s %s  %6.1fs/%s" % (
                    data.get("title", "")[:22], data.get("artist", "")[:14],
                    data.get("source", "")[:22],
                    data.get("position", 0),
                    "%.0f" % data["duration"] if data.get("durationKnown") else "?",
                ))
    except KeyboardInterrupt:
        b.stop()
        print("\nbye")


if __name__ == "__main__":
    _main()
