# -*- coding: utf-8 -*-
"""本地服务（OBS 浏览器源 + 控制面板共用）

一个进程干三件事：
   1. 发静态文件（index.html / control.html / css / js），禁用缓存 —— 改完刷新就生效
   2. /api/snapshot 给叠加层喂数据（状态 + 正在播放）
   3. /api/state 收控制面板的开关

在它内部还跑着 SMTC 数据桥（bridge.py），所以只需要启动这一个进程：
    python server.py            # 默认 http://127.0.0.1:8765
    python server.py --no-bridge --port 9000

浏览器（OBS 之外的普通浏览器）：
    http://127.0.0.1:8765/control     ← 控制面板
    http://127.0.0.1:8765/index.html  ← 叠加层本体（OBS 用这个）
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / "runtime"
STATE_PATH = RUNTIME / "state.json"
NOW_PATH = RUNTIME / "now-playing.json"
COVER_PATH = RUNTIME / "cover.jpg"

SERVER_ID = "cosmolayor/1.0"
VERSION = "1.0.0"          # 对外版本号（面板角落 / 启动横幅 / ZIP 名都用它）

# 音频回环（真实频谱）是可选件：抓不到就退回模拟动效
try:
    from audio import AudioCapture
    AUDIO_IMPORT_ERROR = ""
except Exception as _e:                                  # noqa: BLE001
    AudioCapture = None
    AUDIO_IMPORT_ERROR = str(_e)

DEFAULT_STATE = {
    "visible": {"badge": True, "banner": True, "card": True, "spectrum": True},
    "palette": "magenta",
    "bannerPos": "top",          # top | bottom
    "coverShape": "circle",      # circle | square
    "spectrumMode": "full",      # full | narrow
    "spectrumFps": 30,           # 频谱数据帧率：30 | 60
    "badgePos": "bl",            # 角标位置：bl 左下 | tl 左上
    "cardScale": 1.0,            # 播放卡片等比缩放（0.75 ~ 1.5，锚点左下角）
    "cardSpectrum": True,        # 卡片底部的小频谱
    "specX": 0,                  # 大频谱：左边距（1920 坐标系）
    "specY": 0,                  # 大频谱：距底边
    "specScale": 1.0,            # 大频谱：等比缩放 0.4 ~ 1.0（锚点左下）
    # 第十三轮：透明度 / 等比缩放 / 自由位置
    "badgeOpacity": 1.0,         # 角标透明度 0 ~ 1
    "bannerOpacity": 1.0,        # 条幅透明度
    "cardOpacity": 1.0,          # 卡片透明度
    "badgeScale": 1.0,           # 角标等比缩放 0.4 ~ 1.0（锚点左下）
    "bannerScale": 1.0,          # 条幅等比缩放 0.4 ~ 1.0（锚点左下）
    "badgeX": 0,                 # 角标左边距
    "badgeY": 0,                 # 角标距底边
    "bannerX": 60,               # 条幅左边距（顶部时的默认值）
    "bannerY": 932,              # 条幅距底边（顶部 60 + 高 88 → 1080-148=932）
    "cardX": 60,                 # 卡片左边距
    "cardY": 240,                # 卡片距底边（左下角标在时往上让位：1080-632-208）
    "badgeSpectrum": True,       # 角标头上的频谱是否可视化
    "hideWhenIdle": True,
    "demo": "auto",              # auto | on | off
    # 第十四轮：音频只抓一个程序（进程回环）；空字符串 = 整机混音（老行为）
    "audioLock": "cloudmusic.exe",
    # 第十六轮：整体圆角 / 封面旋转
    "radius": 30,                # 0 ~ 44：所有墨块 + 角标三角的圆角
    "coverSpin": False,          # 封面旋转（播放时转、暂停时停）
    "autoPalette": False,        # 跟随封面取色（每首重算，失败退回手动配色）
    "autoGrad": False,           # 自动配色也用渐变（c1 → c2）
}

PALETTES = {"magenta", "cinnabar", "cyan", "acid",
            "neon", "electric", "toxic", "sunset"}   # 后四个是第十六轮加的霓虹撞色

CARD_SCALE_MIN, CARD_SCALE_MAX = 0.75, 1.5

# 第十三轮：角标 / 条幅 / 频谱 等比缩放，上限就是"当前默认大小"（1.0×）——只能缩，不能放大
COMP_SCALE_MIN, COMP_SCALE_MAX = 0.4, 1.0
# 自由摆放的坐标范围：x = 左边距，y = **距底边**；四个组件共用同一套
POS_X_MIN, POS_X_MAX = -200, 1200
POS_Y_MIN, POS_Y_MAX = 0, 1080
OPACITY_MIN, OPACITY_MAX = 0.0, 1.0
RADIUS_MIN, RADIUS_MAX = 0, 44      # 第十六轮：圆角滑块范围（默认 30，和设计规范一致）

# 大频谱沿用老名字，值就是上面那套，别单独改
SPEC_SCALE_MIN, SPEC_SCALE_MAX = COMP_SCALE_MIN, COMP_SCALE_MAX
SPEC_X_MIN, SPEC_X_MAX = POS_X_MIN, POS_X_MAX
SPEC_Y_MIN, SPEC_Y_MAX = POS_Y_MIN, POS_Y_MAX

# 第十三轮新增的数值字段：(键, 下界, 上界, 小数位)
# load_state 与 sanitize 共用这一张表 —— 以后加同类字段只改这里
NEW_NUM_FIELDS = (
    ("badgeOpacity", OPACITY_MIN, OPACITY_MAX, 2),
    ("bannerOpacity", OPACITY_MIN, OPACITY_MAX, 2),
    ("cardOpacity", OPACITY_MIN, OPACITY_MAX, 2),
    ("badgeScale", COMP_SCALE_MIN, COMP_SCALE_MAX, 2),
    ("bannerScale", COMP_SCALE_MIN, COMP_SCALE_MAX, 2),
    ("badgeX", POS_X_MIN, POS_X_MAX, 0),
    ("badgeY", POS_Y_MIN, POS_Y_MAX, 0),
    ("bannerX", POS_X_MIN, POS_X_MAX, 0),
    ("bannerY", POS_Y_MIN, POS_Y_MAX, 0),
    ("cardX", POS_X_MIN, POS_X_MAX, 0),
    ("cardY", POS_Y_MIN, POS_Y_MAX, 0),
    ("radius", RADIUS_MIN, RADIUS_MAX, 0),
)


def clamp_num(value, lo: float, hi: float):
    """返回 [lo, hi] 内的有限数；非法值返回 None（调用方据此忽略）"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return min(hi, max(lo, v))


def clamp_scale(value) -> float:
    """卡片缩放只认 0.75~1.5 的有限数；非法值返回 0（调用方据此忽略）"""
    v = clamp_num(value, CARD_SCALE_MIN, CARD_SCALE_MAX)
    return 0.0 if v is None else round(v, 2)


def clean_exe_name(value):
    """音频锁定的进程名白名单：只认 "xxx.exe"（自动去目录），"" = 不锁定

    非法值返回 None（调用方保留旧值）。
    """
    if not isinstance(value, str):
        return None
    name = value.strip().replace("\\", "/").split("/")[-1]
    if not name:
        return ""
    if len(name) > 64 or not name.lower().endswith(".exe"):
        return None
    if any(ch in name for ch in ' \t"|<>*?'):
        return None
    return name

DEMO_TRACK = {
    "title": "Neon Tokyo",
    "artist": "AMANE",
    "album": "雨音",
    "source": "demo",
    "duration": 248.0,
}

STALE_SEC = 8.0          # 桥超过这么久没更新就当断了


# ---------------------------------------------------------------- 状态存取
_state_lock = threading.Lock()


def load_state() -> dict:
    st = json.loads(json.dumps(DEFAULT_STATE))
    try:
        if STATE_PATH.is_file():
            saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                vis = saved.pop("visible", None)
                st.update({k: v for k, v in saved.items() if k in DEFAULT_STATE})
                if isinstance(vis, dict):
                    st["visible"].update({k: bool(v) for k, v in vis.items() if k in st["visible"]})
    except (OSError, json.JSONDecodeError):
        pass
    # 旧版本/手改过的 state.json 可能存着已经删掉的右半边角标 → 退回默认
    if st.get("badgePos") not in ("bl", "tl"):
        st["badgePos"] = DEFAULT_STATE["badgePos"]
    scale = clamp_scale(st.get("cardScale"))
    st["cardScale"] = scale or DEFAULT_STATE["cardScale"]
    st["specX"] = int(round(clamp_num(st.get("specX"), SPEC_X_MIN, SPEC_X_MAX) or 0))
    st["specY"] = int(round(clamp_num(st.get("specY"), SPEC_Y_MIN, SPEC_Y_MAX) or 0))
    st["specScale"] = round(clamp_num(st.get("specScale"), SPEC_SCALE_MIN, SPEC_SCALE_MAX) or 1.0, 2)
    # 第十三轮字段：旧 state.json 里没有 → 用默认值；有但超范围 → 夹回边界
    for key, lo, hi, digits in NEW_NUM_FIELDS:
        v = clamp_num(st.get(key), lo, hi)
        st[key] = DEFAULT_STATE[key] if v is None else (int(round(v)) if digits == 0 else round(v, digits))
    if not isinstance(st.get("cardSpectrum"), bool):
        st["cardSpectrum"] = DEFAULT_STATE["cardSpectrum"]
    if not isinstance(st.get("coverSpin"), bool):
        st["coverSpin"] = DEFAULT_STATE["coverSpin"]
    if not isinstance(st.get("autoPalette"), bool):
        st["autoPalette"] = DEFAULT_STATE["autoPalette"]
    if not isinstance(st.get("autoGrad"), bool):
        st["autoGrad"] = DEFAULT_STATE["autoGrad"]
    lock = clean_exe_name(st.get("audioLock"))
    st["audioLock"] = lock if lock is not None else DEFAULT_STATE["audioLock"]
    return st


def save_state(st: dict) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def sanitize(incoming: dict, current: dict) -> dict:
    """只认识这几个键，值也收窄一下，别让控制面板写进奇怪的东西"""
    st = json.loads(json.dumps(current))
    if not isinstance(incoming, dict):
        return st

    vis = incoming.get("visible")
    if isinstance(vis, dict):
        for k in st["visible"]:
            if k in vis:
                st["visible"][k] = bool(vis[k])

    if incoming.get("palette") in PALETTES:
        st["palette"] = incoming["palette"]
    if incoming.get("bannerPos") in ("top", "bottom"):
        st["bannerPos"] = incoming["bannerPos"]
    if incoming.get("coverShape") in ("circle", "square"):
        st["coverShape"] = incoming["coverShape"]
    if incoming.get("spectrumMode") in ("full", "narrow"):
        st["spectrumMode"] = incoming["spectrumMode"]
    if incoming.get("badgePos") in ("bl", "tl"):
        st["badgePos"] = incoming["badgePos"]
    try:
        scale = clamp_scale(incoming.get("cardScale"))
    except (TypeError, ValueError):
        scale = 0.0
    if scale:                                  # 非法/NaN 直接忽略，保留原值
        st["cardScale"] = scale
    try:
        fps = int(incoming.get("spectrumFps"))
    except (TypeError, ValueError):
        fps = 0
    if fps in (30, 60):
        st["spectrumFps"] = fps
    if isinstance(incoming.get("badgeSpectrum"), bool):
        st["badgeSpectrum"] = incoming["badgeSpectrum"]
    if isinstance(incoming.get("cardSpectrum"), bool):
        st["cardSpectrum"] = incoming["cardSpectrum"]
    if isinstance(incoming.get("coverSpin"), bool):
        st["coverSpin"] = incoming["coverSpin"]
    if isinstance(incoming.get("autoPalette"), bool):
        st["autoPalette"] = incoming["autoPalette"]
    if isinstance(incoming.get("autoGrad"), bool):
        st["autoGrad"] = incoming["autoGrad"]
    # 大频谱的位置 / 缩放
    v = clamp_num(incoming.get("specX"), SPEC_X_MIN, SPEC_X_MAX)
    if v is not None:
        st["specX"] = int(round(v))
    v = clamp_num(incoming.get("specY"), SPEC_Y_MIN, SPEC_Y_MAX)
    if v is not None:
        st["specY"] = int(round(v))
    v = clamp_num(incoming.get("specScale"), SPEC_SCALE_MIN, SPEC_SCALE_MAX)
    if v is not None:
        st["specScale"] = round(v, 2)
    # 第十三轮：透明度 / 角标条幅缩放 / 四个组件的自由位置
    for key, lo, hi, digits in NEW_NUM_FIELDS:
        v = clamp_num(incoming.get(key), lo, hi)
        if v is not None:
            st[key] = int(round(v)) if digits == 0 else round(v, digits)
    if isinstance(incoming.get("hideWhenIdle"), bool):
        st["hideWhenIdle"] = incoming["hideWhenIdle"]
    if "audioLock" in incoming:
        lock = clean_exe_name(incoming.get("audioLock"))
        if lock is not None:
            st["audioLock"] = lock
    if incoming.get("demo") in ("auto", "on", "off"):
        st["demo"] = incoming["demo"]
    return st


# ---------------------------------------------------------------- 数据
def demo_now() -> dict:
    dur = DEMO_TRACK["duration"]
    pos = time.time() % dur
    return dict(DEMO_TRACK, **{
        "idle": False,
        "playing": True,
        "position": round(pos, 2),
        "duration": dur,
        "durationKnown": True,
        "real": False,
        "cover": "",
        "demo": True,
        "ts": time.time(),
    })


def idle_now() -> dict:
    return {
        "idle": True, "playing": False, "title": "—", "artist": "", "album": "",
        "source": "", "position": 0.0, "duration": 0.0, "durationKnown": False,
        "real": False, "cover": "", "demo": False, "ts": time.time(),
    }


def read_bridge_json() -> tuple[dict | None, dict]:
    """--no-bridge 时从磁盘读（bridge.py 单独跑）"""
    try:
        data = json.loads(NOW_PATH.read_text(encoding="utf-8"))
        age = time.time() - float(data.get("ts", 0))
        return data, {"ok": age < STALE_SEC, "ageMs": round(age * 1000, 1),
                      "error": "", "source": data.get("source", "")}
    except (OSError, json.JSONDecodeError, ValueError):
        return None, {"ok": False, "ageMs": None, "error": "没有 now-playing.json", "source": ""}


def build_snapshot(state: dict, bridge) -> dict:
    if bridge is not None:
        data, bst = bridge.snapshot()
    else:
        data, bst = read_bridge_json()

    mode = state.get("demo", "auto")
    fresh = bool(data) and (bst.get("ageMs") is None or bst["ageMs"] < STALE_SEC * 1000)

    if mode == "on":
        # 只有明确打开"演示模式"才用假数据：直播时没歌就该是空画面
        now = demo_now()
    elif fresh and not data.get("idle"):
        now = dict(data)
        now["demo"] = False
        now.pop("epoch", None)
    else:
        now = idle_now()

    return {
        "state": state,
        "now": now,
        "bridge": bst,
        "server": SERVER_ID,
        "t": round(time.time() * 1000),
    }


# ---------------------------------------------------------------- HTTP
class QuietHTTPServer(ThreadingHTTPServer):
    """浏览器/OBS 断开连接是常态，别刷一屏 traceback。

    注意：`handle_error` 是 **server** 的方法（socketserver 在 process_request_thread
    里调），写在 Handler 里是死代码 —— 之前就是这么写的，所以控制台照样刷
    ConnectionResetError（2026-10-27 从托盘换成黑窗口后一眼就看到了）。
    """
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "cosmolayor"
    protocol_version = "HTTP/1.1"

    # 安静点：只把错误写出来
    def log_message(self, fmt, *args):
        pass

    def log_error(self, fmt, *args):
        sys.stderr.write("[http] " + (fmt % args) + "\n")

    # ---------- 工具 ----------
    def _send(self, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _text(self, code: int, msg: str) -> None:
        self._send(code, msg.encode("utf-8"), "text/plain; charset=utf-8")

    def _body(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        return self.rfile.read(n) if n > 0 else b""

    # ---------- 路由 ----------
    def do_GET(self):
        self._route(get=True)

    def do_HEAD(self):
        self._route(get=True)

    def do_POST(self):
        self._route(get=False)

    def _route(self, get: bool):
        path = urlparse(self.path).path
        try:
            if path in ("/api/snapshot", "/api/state"):
                self._api(path, get)
            elif path == "/api/diag":
                self._diag()
            elif path == "/api/bridge/restart":
                if get:
                    self._text(405, "use POST")
                else:
                    self._bridge_restart()
            elif path == "/api/spectrum":
                self._json(AUDIO.snapshot() if AUDIO else
                           {"ok": False, "error": AUDIO_IMPORT_ERROR or "音频采集未启用"})
            elif path == "/api/pcm-stream":
                self._pcm_stream()
            elif path == "/api/pcm":
                self._pcm_once()
            elif path == "/api/audio/devices":
                snap = AUDIO.snapshot() if AUDIO else {}
                self._json({"devices": [{
                    "index": 0, "name": snap.get("device", ""),
                    "channels": snap.get("channels", 0), "rate": snap.get("rate", 0),
                }]})
            elif path == "/api/health":
                self._json({"ok": True, "server": SERVER_ID, "t": round(time.time() * 1000)})
            elif path == "/api/diagnose":
                self._diagnose()
            elif path == "/cover.jpg":
                self._cover()
            elif path in ("/", "/index.html"):
                self._file("index.html")
            elif path == "/control":
                self._file("control.html")
            else:
                rel = path.lstrip("/")
                if rel.startswith("runtime/") or rel.startswith("tools/"):
                    self._text(404, "not found")
                    return
                self._file(rel)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                              # noqa: BLE001
            self.log_error("处理 %s 出错：%s", self.path, e)
            try:
                self._text(500, "server error: %s" % e)
            except OSError:
                pass

    # ---------- 具体 ----------
    def _api(self, path: str, get: bool):
        global STATE
        if path == "/api/state" and not get:
            try:
                incoming = json.loads(self._body().decode("utf-8") or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._text(400, "bad json")
                return
            with _state_lock:
                STATE = sanitize(incoming, STATE)
                save_state(STATE)
            if AUDIO is not None:
                AUDIO.set_fps(STATE["spectrumFps"])   # 面板改帧率，立刻生效
                AUDIO.set_lock(STATE["audioLock"])    # 换锁定目标，立刻重连，不用重启
            self._json({"ok": True, "state": STATE})
            return

        with _state_lock:
            st = json.loads(json.dumps(STATE))
        self._json(build_snapshot(st, BRIDGE))

    def _diag(self):
        """控制面板的「诊断」：把桥看到的原始数据也吐出来"""
        with _state_lock:
            st = json.loads(json.dumps(STATE))
        snap = build_snapshot(st, BRIDGE)
        snap["audio"] = AUDIO.snapshot() if AUDIO else {
            "ok": False, "error": AUDIO_IMPORT_ERROR or "音频采集未启用"
        }
        snap["threads"] = threading.active_count()
        if BRIDGE is not None:
            snap["diag"] = BRIDGE.diagnostics()
        else:
            data, bst = read_bridge_json()
            snap["diag"] = {
                "ok": bst["ok"], "error": bst["error"], "restarts": 0, "intervalMs": None,
                "raw": None,
                "coverBytes": COVER_PATH.stat().st_size if COVER_PATH.is_file() else 0,
                "psAlive": False, "note": "服务以 --no-bridge 启动，读的是文件",
            }
        self._json(snap)

    def _diagnose(self):
        """一键导出诊断：给测试者用的纯文本包（系统 / Python / 状态 / 桥 / 音频 / 进程）"""
        import datetime
        import platform

        with _state_lock:
            st = json.loads(json.dumps(STATE))
        snap = build_snapshot(st, BRIDGE)
        snap["audio"] = AUDIO.snapshot() if AUDIO else {
            "ok": False, "error": AUDIO_IMPORT_ERROR or "音频采集未启用"
        }
        snap["threads"] = threading.active_count()
        if BRIDGE is not None:
            snap["diag"] = BRIDGE.diagnostics()
        else:
            snap["diag"] = {"ok": False, "note": "服务以 --no-bridge 启动"}

        here = str(BASE)
        exe = sys.executable
        bundled = os.path.normcase(os.path.dirname(exe)).startswith(os.path.normcase(here))

        lines = [
            "Cosmolayor 诊断包",
            "=" * 48,
            f"时间       {datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}",
            f"版本       {SERVER_ID}  (v{VERSION})",
            f"系统       {platform.platform()}  ({platform.machine()})",
            f"Python     {platform.python_version()}  {exe}",
            f"解释器     {'发布包自带（免安装）' if bundled else '系统安装的 Python'}",
            f"程序目录   {here}",
            f"当前目录   {os.getcwd()}",
            f"线程数     {snap['threads']}",
            "",
            "--- 当前状态 state ---",
            json.dumps(st, ensure_ascii=False, indent=2),
            "",
            "--- 运行诊断 diag ---",
            json.dumps(snap, ensure_ascii=False, indent=2, default=str),
        ]

        # 外部进程：网易云 / PowerShell 数据桥
        try:
            import subprocess
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudmusic.exe", "/NH"],
                                 capture_output=True, text=True, timeout=4,
                                 encoding="mbcs", errors="replace").stdout or ""
            cloud = sum(1 for ln in out.splitlines() if "cloudmusic" in ln.lower())
            lines += ["", "--- 外部进程 ---",
                      f"cloudmusic.exe  {cloud} 个进程" if cloud else "cloudmusic.exe  没在运行"]
        except Exception as e:                                  # noqa: BLE001
            lines += ["", f"（查进程失败：{e}）"]

        # 音频目标探测：锁的程序到底能不能被找到（频谱一条平线时最有用）
        try:
            import audio as _audio
            lock = str(st.get("audioLock") or "")
            procs = _audio.list_processes()
            hits = [(pid, ppid) for pid, ppid, name in procs
                    if name.lower() == lock.lower()][:8] if lock else []
            lines += ["", "--- 音频目标探测 ---",
                      f"锁定目标     {lock or '（空 = 整机混音）'}",
                      f"进程快照     {len(procs)} 个进程（前 12 个：" + ", ".join(sorted({n for _, _, n in procs})[:12]) + "）",
                      f"名字匹配     {len(hits)} 个 {hits}",
                      f"find_root_pid {_audio.find_root_pid(lock) if lock else '-'}",
                      f"正在出声的 pid {sorted(_audio.active_session_pids())}"]
        except Exception as e:                                  # noqa: BLE001
            lines += ["", f"（音频探测失败：{e}）"]

        body = ("\r\n".join(str(x) for x in lines) + "\r\n").encode("utf-8-sig")
        name = "cosmolayor-diag-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".txt"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _bridge_restart(self):
        if BRIDGE is None:
            self._json({"ok": False, "error": "服务未启用数据桥（--no-bridge）"}, 409)
            return
        BRIDGE.restart()
        self._json({"ok": True})

    def _pcm_once(self):
        """单帧 PCM（JSON）：给截图/调试用，不用开 SSE"""
        if AUDIO is None:
            self._json({"ok": False, "error": AUDIO_IMPORT_ERROR or "音频采集未启用"})
            return
        pcm = AUDIO.pcm()
        if not pcm:
            self._json({"ok": False, "error": AUDIO.snapshot().get("error") or "还没有 PCM"})
            return
        self._json({"ok": True, "r": pcm[1], "q": pcm[2],
                    "b": base64.b64encode(pcm[0]).decode("ascii")})

    def _pcm_stream(self):
        """SSE：20fps 推回环 PCM（int16 单声道，base64），FFT 由页面自己算"""
        if AUDIO is None:
            self._text(503, "audio disabled")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_seq = -1
        last_beat = 0.0
        try:
            while True:
                pcm = AUDIO.pcm()
                if pcm is None:
                    now = time.time()
                    if now - last_beat > 1.0:            # 采集失败：1Hz 告诉页面退回模拟
                        last_beat = now
                        self.wfile.write(b'data: {"ok":false}\n\n')
                        self.wfile.flush()
                    time.sleep(0.1)
                    continue
                if pcm[2] != last_seq:
                    last_seq = pcm[2]
                    payload = json.dumps(
                        {"ok": True, "r": pcm[1], "q": last_seq,
                         "b": base64.b64encode(pcm[0]).decode("ascii")},
                        separators=(",", ":"),
                    )
                    self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                else:
                    AUDIO.wait_frame(last_seq, timeout=1.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _cover(self):
        if not COVER_PATH.is_file():
            self._text(404, "no cover")
            return
        body = COVER_PATH.read_bytes()
        ctype = "image/png" if body[:4] == b"\x89PNG" else "image/jpeg"
        self._send(200, body, ctype)

    def _file(self, rel: str):
        try:
            target = (BASE / rel).resolve()
            target.relative_to(BASE)
        except (ValueError, OSError):
            self._text(403, "forbidden")
            return
        if not target.is_file():
            self._text(404, "not found")
            return

        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".html", ".css", ".js", ".json"):
            ctype += "; charset=utf-8"
        # 效果图这类大文件允许缓存，页面/脚本一律不缓存
        cache = target.suffix in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")
        self._send(200, target.read_bytes(), ctype, cache=cache)


# ---------------------------------------------------------------- 启动
STATE: dict = {}
BRIDGE = None
AUDIO = None


def lan_ip() -> str:
    """取本机局域网 IPv4（只在 --host 0.0.0.0 时用来打印地址）。

    只用本机主机名解析，**不连接任何外部地址** —— 早期版本用 UDP connect 到
    8.8.8.8 取路由出口（不发包，但会建一个指向公网的 socket），为了"零联网"
    这个承诺更干净，改成现在这样。
    """
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


def main() -> None:
    global STATE, BRIDGE, AUDIO
    ap = argparse.ArgumentParser(description="直播叠加层本地服务")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-bridge", action="store_true", help="不启 SMTC 数据桥（只用演示数据）")
    ap.add_argument("--no-audio", action="store_true", help="不做真实频谱（降 CPU / 不想录系统声音时）")
    ap.add_argument("--fps", type=int, default=None, choices=(30, 60),
                    help="频谱数据帧率（默认读 state.json，首次 30；也可在面板里改）")
    ap.add_argument("--open", action="store_true", help="启动后自动打开控制面板（Cosmolayor.cmd 用的就是这个）")
    ap.add_argument("--demo", action="store_true", help="强制演示数据（等同控制面板打开演示模式）")
    ap.add_argument("--audio-all", action="store_true",
                    help="音频不锁定进程，抓整机混音（默认只抓 state.json 里的 audioLock，通常是网易云）")
    args = ap.parse_args()

    RUNTIME.mkdir(parents=True, exist_ok=True)
    STATE = load_state()
    if args.fps is not None:
        STATE["spectrumFps"] = args.fps          # 命令行覆盖（面板之后还能改）
    if args.demo:
        STATE["demo"] = "on"
    if args.audio_all:
        STATE["audioLock"] = ""

    if not args.no_audio:
        try:
            if AudioCapture is None:
                raise RuntimeError(AUDIO_IMPORT_ERROR or "音频模块不可用")
            AUDIO = AudioCapture(fps=int(STATE.get("spectrumFps", 30)),
                                 lock_exe=str(STATE.get("audioLock") or ""))
            AUDIO.start()
        except Exception as e:                              # noqa: BLE001
            AUDIO = None
            print("[audio] 真实频谱不可用，改用模拟动效：", e)

    if not args.no_bridge:
        try:
            from bridge import Bridge
            BRIDGE = Bridge()
            BRIDGE.start()
        except Exception as e:                              # noqa: BLE001
            BRIDGE = None
            print("[bridge] 启动失败，改用演示数据：", e)

    httpd = QuietHTTPServer((args.host, args.port), Handler)

    url = f"http://{args.host}:{args.port}"
    print(f"Cosmolayor v{VERSION}  ·  直播叠加层（零第三方依赖）")
    print()
    print(f"叠加层  {url}/index.html")
    print(f"控制台  {url}/control")
    if args.host == "0.0.0.0":
        print(f"局域网  http://{lan_ip()}:{args.port}/index.html")
    print(f"数据桥  {'关闭（演示）' if args.no_bridge else '运行中 · SMTC'}")
    if AUDIO is not None:
        lock = str(STATE.get("audioLock") or "")
        src = f"进程回环 · {lock}" if lock else "整机混音"
        print(f"频谱    真实 · {src} · {AUDIO.fps}fps（FFT 在页面里算，绘制 60fps）")
    else:
        print("频谱    模拟 · 真实频谱不可用（见 --no-audio）")
    print("Ctrl+C 结束")
    if args.open:
        # 给采集/桥一点点时间，再弹浏览器（Cosmolayor.cmd 走这条）
        def _open_panel() -> None:
            try:
                import webbrowser
                webbrowser.open(f"{url}/control")
            except Exception:                               # noqa: BLE001
                pass
        threading.Timer(1.5, _open_panel).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n收工")
    finally:
        if BRIDGE:
            BRIDGE.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()




