# -*- coding: utf-8 -*-
"""叠加层自测（产品测试用，零依赖）

自己拉起 server.py，逐项检查接口、数据、串联和降级路径，最后打印 PASS/FAIL 表。

    python tools/selftest.py              # 全跑（约 20~30 秒）
    python tools/selftest.py --port 8810  # 换端口
    python tools/selftest.py --keep       # 跑完不关服务（方便手动看）

不依赖真实播放：没歌时相关项标 SKIP，不算失败。
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PY = sys.executable
CREATE_NO_WINDOW = 0x08000000
if str(BASE) not in sys.path:            # 让自测能直接 import audio / server
    sys.path.insert(0, str(BASE))


class Skip(Exception):
    pass


# ---------------------------------------------------------------- HTTP
def get(url: str, timeout: float = 6.0, method: str = "GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def get_json(url: str, timeout: float = 6.0):
    _, _, raw = get(url, timeout)
    return json.loads(raw)


def free_port(start: int) -> int:
    for p in range(start, start + 30):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到空闲端口")


# ---------------------------------------------------------------- 服务进程
class Server:
    def __init__(self, port: int, *args: str):
        self.port = port
        self.args = args
        self.proc: subprocess.Popen | None = None
        self.log: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, wait: float = 25.0) -> None:
        self.proc = subprocess.Popen(
            [PY, "-u", "server.py", "--port", str(self.port), *self.args],
            cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        t0 = time.time()
        while time.time() - t0 < wait:
            if self.proc.poll() is not None:
                break
            try:
                _, _, raw = get(self.url + "/api/health", timeout=1.5)
                if json.loads(raw).get("ok"):
                    return
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.15)
        tail = ""
        if self.proc.stdout is not None:
            try:
                tail = (self.proc.stdout.read() or "")[-800:]
            except (OSError, ValueError):
                pass
        raise RuntimeError(f"服务没起来（端口 {self.port}）\n{tail}")

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
            except OSError:
                pass
        finally:
            self.proc = None


# ---------------------------------------------------------------- 测试
class Runner:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def run(self, name: str, fn):
        try:
            detail = fn()
            self.rows.append(("PASS", name, detail or ""))
        except Skip as e:
            self.rows.append(("SKIP", name, str(e)))
        except AssertionError as e:
            self.rows.append(("FAIL", name, str(e) or "断言失败"))
        except Exception as e:                            # noqa: BLE001
            self.rows.append(("FAIL", name, f"{type(e).__name__}: {e}"))

    def report(self) -> int:
        width = max(len(n) for _, n, _ in self.rows) + 2
        print("\n" + "=" * 78)
        for state, name, detail in self.rows:
            mark = {"PASS": " ok ", "SKIP": "skip", "FAIL": "FAIL"}[state]
            print(f"[{mark}] {name.ljust(width)} {detail}")
        print("=" * 78)
        bad = sum(1 for s, _, _ in self.rows if s == "FAIL")
        skip = sum(1 for s, _, _ in self.rows if s == "SKIP")
        print(f"共 {len(self.rows)} 项：通过 {len(self.rows) - bad - skip}，跳过 {skip}，失败 {bad}")
        return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--keep", action="store_true", help="跑完不关服务")
    ap.add_argument("--tone", action="store_true",
                    help="额外放 2 秒 -26dB 测试音，验证回环真的录到了声音（会出声）")
    args = ap.parse_args()

    port = args.port or free_port(8810)
    srv = Server(port)
    r = Runner()

    state_path = BASE / "runtime" / "state.json"
    state_before = state_path.read_bytes() if state_path.is_file() else None

    try:
        srv.start()
        # 冷启动：等数据桥吐出第一行（PS 子进程起来要 ~1 秒），再开始检查
        t_boot = time.time()
        snap = get_json(srv.url + "/api/snapshot")
        while time.time() - t_boot < 12:
            if (snap.get("bridge") or {}).get("ok"):
                break
            time.sleep(0.25)
            snap = get_json(srv.url + "/api/snapshot")
        boot_ms = int((time.time() - t_boot) * 1000)

        def _boot():
            assert snap["bridge"].get("ok"), "12 秒内数据桥没就绪"
            return f"数据桥 {boot_ms} ms 内就绪"

        r.run("冷启动就绪", _boot)

        def _health():
            st, _, raw = get(srv.url + "/api/health")
            assert st == 200
            j = json.loads(raw)
            assert j.get("ok"), j
            return f"v={j.get('server')}"

        r.run("health", _health)

        def _index():
            st, _, raw = get(srv.url + "/index.html")
            text = raw.decode("utf-8", "ignore")
            assert st == 200
            for need in ("js/fft.js", "js/overlay.js", "id=\"spec\"", "id=\"bannerFill\""):
                assert need in text, f"index 缺 {need}"
            return "含 fft.js / overlay.js / canvas"

        r.run("index 页面", _index)

        def _control():
            st, _, raw = get(srv.url + "/control")
            text = raw.decode("utf-8", "ignore")
            assert st == 200 and "control.js" in text and "preview" in text
            return "控制面板可访问"

        r.run("control 页面", _control)

        def _snapshot():
            for k in ("state", "now", "bridge", "server", "t"):
                assert k in snap, f"snapshot 缺 {k}"
            return f"{snap['server']}"

        r.run("snapshot 结构", _snapshot)

        def _now():
            now = snap["now"]
            if now.get("idle"):
                raise Skip("当前没有播放器/没在放歌")
            assert now.get("title"), "title 为空"
            assert isinstance(now.get("durationKnown"), bool), "durationKnown 不是布尔"
            assert now.get("position", -1) >= 0, "position 为负"
            if now.get("durationKnown") and now["duration"] > 0:
                assert now["position"] <= now["duration"] + 1.5, \
                    f"position {now['position']} > duration {now['duration']}"
            return f"{now['title'][:24]} · {now.get('position')}/{now.get('duration')} · {now.get('positionSource')}"

        r.run("now 数据", _now)

        def _read_rms(url: str, tries: int = 5) -> float:
            """连取几帧取最大：单帧可能正好落在过零点上，max 比单帧稳"""
            import array
            best = 0.0
            for _ in range(tries):
                d = get_json(url + "/api/pcm")
                assert d.get("ok"), "没有 PCM"
                a = array.array("h")
                a.frombytes(base64.b64decode(d["b"]))
                if a:
                    best = max(best, (sum(x * x for x in a) / len(a)) ** 0.5)
                if best > 200:
                    break
                time.sleep(0.25)
            return best

        def _live():
            n0 = get_json(srv.url + "/api/snapshot")["now"]
            if not n0.get("playing"):
                raise Skip("当前没在播放（按一下播放键再跑，这项会验进度和声音）")
            p0 = float(n0.get("position") or 0)
            time.sleep(3.2)
            n1 = get_json(srv.url + "/api/snapshot")["now"]
            assert n1.get("playing"), "3 秒后变成暂停了"
            p1 = float(n1.get("position") or 0)
            assert p1 > p0 + 2.0, f"进度没走：{p0} → {p1}"
            rms = _read_rms(srv.url)
            if rms <= 200:
                # 曲尾淡出 / 换曲间隙是真的没声音，别误报成"链路坏了"。
                # 2026-10-27 踩过：采样落在 147.0/150.0s，连跑两次一次红一次绿。
                dur = float(n1.get("duration") or 0)
                if n1.get("durationKnown") and dur and p1 > dur - 4.0:
                    raise Skip(f"采样落在曲尾（{p1:.1f}/{dur:.1f}s），没声音是正常的")
                raise AssertionError(f"PCM 没能量（rms={rms:.0f}）")
            return f"进度 {p0:.1f} → {p1:.1f} · PCM rms {rms:.0f}"

        r.run("播放中链路", _live)

        diag = get_json(srv.url + "/api/diag")

        def _diag():
            for k in ("audio", "diag"):
                assert k in diag, f"diag 缺 {k}"
            assert "cover" in diag["diag"], "diag 缺 cover"
            assert "elog" in diag["diag"], "diag 缺 elog（数据桥没起？）"
            return "audio / cover / elog 都在"

        r.run("诊断结构", _diag)

        def _audio():
            au = diag.get("audio") or {}
            if not au.get("ok"):
                raise Skip(au.get("error") or "音频采集未启用")
            assert au.get("rate", 0) > 0, "采样率为 0"
            assert au.get("format"), "格式为空"
            mode = au.get("mode") or "?"
            return f"[{mode}] {str(au.get('device'))[:26]} · {au.get('format')}"

        r.run("音频采集", _audio)

        def _audio_lock():
            """面板切「音频来源」→ 服务端要真的换捕获后端（不用重启）"""
            cur = get_json(srv.url + "/api/snapshot")["state"]
            orig = cur.get("audioLock", "")
            try:
                get(srv.url + "/api/state", method="POST", body={"audioLock": "cloudmusic.exe"})
                time.sleep(1.2)
                au = (get_json(srv.url + "/api/diag") or {}).get("audio") or {}
                assert au.get("ok"), au.get("error") or "音频没起来"
                assert au.get("mode") == "process", f"没进进程回环：{au.get('mode')}"
                assert "cloudmusic.exe" in str(au.get("device", "")), f"目标不对：{au.get('device')}"
                locked = str(au.get("device"))

                get(srv.url + "/api/state", method="POST", body={"audioLock": ""})
                time.sleep(1.2)
                au2 = (get_json(srv.url + "/api/diag") or {}).get("audio") or {}
                assert au2.get("mode") == "system", f"没切回整机混音：{au2.get('mode')}"
                return f"锁定 {locked[:24]} → 切回 {str(au2.get('device'))[:20]}"
            finally:
                get(srv.url + "/api/state", method="POST", body={"audioLock": orig})
                time.sleep(0.8)

        r.run("音频锁定切换", _audio_lock)

        def _proc_peak(pid: int, secs: float = 1.2) -> int:
            """直接拿 AudioCapture 锁某个 pid 读一会儿，返回峰值"""
            import audio as _A
            cap = _A.AudioCapture(lock_pid=pid)
            cap.start()
            time.sleep(secs)
            snap2 = cap.snapshot()
            cap.stop()
            time.sleep(0.2)
            assert snap2.get("ok"), snap2.get("error") or "进程回环没起来"
            assert snap2.get("mode") == "process", f"模式不对：{snap2.get('mode')}"
            assert snap2.get("seq", 0) > 0, "没有出帧"
            return int(snap2.get("peak") or 0)

        def _proc_loopback():
            """进程回环本体：锁一个自己起的进程，能激活、能出帧"""
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(1.5)
                peak = _proc_peak(p.pid)
                return f"锁 pid {p.pid} · peak {peak} · 出帧正常"
            finally:
                p.kill()

        r.run("进程回环可激活", _proc_loopback)

        def _proc_isolation():
            """只锁定一个程序：放音的抓得到、无关的抓不到（需要 --tone 出声）"""
            if not args.tone:
                raise Skip("未加 --tone（要出声才能验隔离）")
            import math
            import struct
            import wave

            path = BASE / "runtime" / "selftest-proc.wav"
            rate, secs = 48000, 7
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                buf = bytearray()
                for i in range(rate * secs):
                    buf += struct.pack("<h", int(0.25 * 32767 * math.sin(2 * math.pi * 440 * i / rate)))
                w.writeframes(bytes(buf))

            code = (f"import winsound,time; "
                    f"winsound.PlaySound(r'{path}', winsound.SND_FILENAME | winsound.SND_ASYNC); "
                    f"time.sleep(9)")
            player = subprocess.Popen([sys.executable, "-c", code],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            quiet = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(9)"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(1.8)
                hot = _proc_peak(player.pid, 1.4)
                cold = _proc_peak(quiet.pid, 1.4)
                assert hot > 2000, f"放音进程没抓到（峰值 {hot}）"
                assert cold < 200, f"无关进程串音了（峰值 {cold}）"
                return f"放音 {hot} / 无关 {cold} —— 完全隔离"
            finally:
                for p in (player, quiet):
                    p.kill()
                try:
                    path.unlink()
                except OSError:
                    pass

        r.run("进程回环不串音", _proc_isolation)

        def _tone():
            if not args.tone:
                raise Skip("未加 --tone")
            import array
            import math
            import struct
            import wave
            import winsound

            path = BASE / "runtime" / "selftest-tone.wav"
            rate, secs = 48000, 2
            with wave.open(str(path), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(rate)
                buf = bytearray()
                for i in range(rate * secs):
                    v = int(0.05 * 32767 * math.sin(2 * math.pi * 440 * i / rate))
                    buf += struct.pack("<hh", v, v)
                w.writeframes(bytes(buf))

            # 服务器默认开着「只抓网易云」，而这个测试音是本进程放的
            # → 先切成整机混音，测完再恢复
            cur = get_json(srv.url + "/api/snapshot")["state"]
            orig = cur.get("audioLock", "")
            try:
                get(srv.url + "/api/state", method="POST", body={"audioLock": ""})
                time.sleep(0.9)
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
                peaks = []
                for _ in range(10):
                    time.sleep(0.25)
                    d = get_json(srv.url + "/api/pcm")
                    if not d.get("ok"):
                        continue
                    a = array.array("h")
                    a.frombytes(base64.b64decode(d["b"]))
                    peaks.append(max(abs(x) for x in a))
            finally:
                winsound.PlaySound(None, winsound.SND_PURGE)
                get(srv.url + "/api/state", method="POST", body={"audioLock": orig})
                try:
                    path.unlink()
                except OSError:
                    pass
            best = max(peaks) if peaks else 0
            assert best > 300, f"回环没收到测试音（峰值 {best}）"
            return f"回环峰值 {best}（-26dB 测试音）"

        r.run("回环收到真实声音", _tone)

        def _pcm():
            d = get_json(srv.url + "/api/pcm")
            if not d.get("ok"):
                raise Skip(d.get("error") or "没有 PCM")
            pcm = base64.b64decode(d["b"])
            assert len(pcm) == 4096, f"PCM 长度 {len(pcm)}（应为 2048×int16=4096）"
            assert d.get("r") in (44100, 48000, 96000), f"采样率 {d.get('r')}"
            return f"{len(pcm)} 字节 · {d['r']} Hz · seq={d['q']}"

        r.run("PCM 单帧", _pcm)

        def _sse():
            req = urllib.request.Request(srv.url + "/api/pcm-stream")
            seqs = []
            got = 0
            with urllib.request.urlopen(req, timeout=8) as resp:
                ctype = resp.headers.get("Content-Type", "")
                assert "text/event-stream" in ctype, ctype
                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    d = json.loads(line[5:].strip())
                    if not d.get("ok"):
                        raise Skip("采集暂时不可用")
                    pcm = base64.b64decode(d["b"])
                    assert len(pcm) == 4096
                    seqs.append(d["q"])
                    got += 1
                    if got >= 4:
                        break
            assert len(seqs) >= 3, f"只收到 {len(seqs)} 帧"
            assert all(b > a for a, b in zip(seqs, seqs[1:])), f"seq 不递增 {seqs}"
            return f"{len(seqs)} 帧 · seq {seqs[0]}→{seqs[-1]}"

        r.run("PCM SSE 推流", _sse)

        def _cover():
            # 封面是换歌后台线程抓的（重试 0/1.5/4 秒），最多等 8 秒
            cb = 0
            t0 = time.time()
            while time.time() - t0 < 8:
                cb = (get_json(srv.url + "/api/diag").get("diag") or {}).get("coverBytes") or 0
                if cb:
                    break
                time.sleep(0.5)
            if not cb:
                raise Skip("这首歌没有封面 / 抓取还没完成")
            st, headers, body = get(srv.url + "/cover.jpg")
            assert st == 200 and len(body) > 500, f"封面 {len(body)} 字节"
            assert body[:2] == b"\xff\xd8" or body[:4] == b"\x89PNG", "不是 JPEG/PNG"
            assert "image/" in headers.get("Content-Type", ""), headers.get("Content-Type")
            return f"{len(body)} 字节 · {headers.get('Content-Type')}"

        r.run("封面文件", _cover)

        def _devices():
            d = get_json(srv.url + "/api/audio/devices")
            devs = d.get("devices") or []
            assert devs, "设备列表为空"
            assert devs[0].get("rate", 0) > 0
            return f"{devs[0].get('name')[:30]}"

        r.run("音频设备接口", _devices)

        def _spectrum_api():
            d = get_json(srv.url + "/api/spectrum")
            assert "ok" in d and "device" in d
            return "ok" if d["ok"] else (d.get("error") or "未就绪")

        r.run("spectrum 接口", _spectrum_api)

        original = snap["state"]

        def _state_roundtrip():
            st, _, raw = get(srv.url + "/api/state", method="POST", body={"palette": "cyan"})
            assert st == 200, raw
            now = get_json(srv.url + "/api/snapshot")["state"]
            assert now["palette"] == "cyan", f"palette={now['palette']}"
            disk = json.loads(state_path.read_text(encoding="utf-8"))
            assert disk["palette"] == "cyan", "没落盘"
            return "cyan 生效并落盘"

        r.run("开关读写 + 落盘", _state_roundtrip)

        def _state_guard():
            get(srv.url + "/api/state", method="POST",
                body={"palette": "not-a-palette", "bannerPos": "sideways", "visible": {"badge": "x"}})
            now = get_json(srv.url + "/api/snapshot")["state"]
            assert now["palette"] == "cyan", "非法 palette 被写进去了"
            assert now["bannerPos"] in ("top", "bottom"), "bannerPos 被写坏"
            assert isinstance(now["visible"]["badge"], bool), "visible 被写坏"
            # 卡片缩放：超范围的夹住，非法值忽略（保留上一个合法值）
            get(srv.url + "/api/state", method="POST", body={"cardScale": 3.4})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("cardScale") == 1.5, f"上限没夹住：{st.get('cardScale')}"
            get(srv.url + "/api/state", method="POST", body={"cardScale": 0.2})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("cardScale") == 0.75, f"下限没夹住：{st.get('cardScale')}"
            get(srv.url + "/api/state", method="POST", body={"cardScale": "abc"})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("cardScale") == 0.75, f"非法值把状态写坏了：{st.get('cardScale')}"
            # 大频谱 / 角标 / 条幅：位置与缩放都要夹住（第十三轮：缩放上限 = 1.0× = 默认大小）
            get(srv.url + "/api/state", method="POST",
                body={"specScale": 9, "specX": -9999, "specY": 9999,
                      "badgeScale": 9, "bannerScale": 0.1,
                      "badgeX": 9999, "bannerY": -5})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("specScale") == 1.0, f"频谱缩放上限没夹住：{st.get('specScale')}"
            assert st.get("badgeScale") == 1.0, f"角标缩放上限没夹住：{st.get('badgeScale')}"
            assert st.get("bannerScale") == 0.4, f"条幅缩放下限没夹住：{st.get('bannerScale')}"
            assert st.get("specX") == -200 and st.get("specY") == 1080, \
                f"频谱位置没夹住：{st.get('specX')},{st.get('specY')}"
            assert st.get("badgeX") == 1200, f"角标 x 没夹住：{st.get('badgeX')}"
            assert st.get("bannerY") == 0, f"条幅 y 没夹住：{st.get('bannerY')}"
            # 透明度 0 ~ 1：超范围夹住，非法值忽略（保留上一个合法值）
            get(srv.url + "/api/state", method="POST",
                body={"badgeOpacity": 2, "bannerOpacity": -1, "cardOpacity": "abc"})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("badgeOpacity") == 1.0 and st.get("bannerOpacity") == 0.0, "透明度没夹住"
            assert st.get("cardOpacity") == 1.0, f"非法透明度把状态写坏了：{st.get('cardOpacity')}"
            # 第十六轮：圆角夹取 + 封面旋转开关
            get(srv.url + "/api/state", method="POST", body={"radius": 99, "coverSpin": True})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("radius") == 44, f"圆角上限没夹住：{st.get('radius')}"
            assert st.get("coverSpin") is True, "coverSpin 没写进去"
            get(srv.url + "/api/state", method="POST", body={"radius": -5})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("radius") == 0, f"圆角下限没夹住：{st.get('radius')}"
            get(srv.url + "/api/state", method="POST", body={"radius": 30, "coverSpin": False})
            # 第十六轮：跟随封面取色的开关
            get(srv.url + "/api/state", method="POST", body={"autoPalette": True})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("autoPalette") is True, "autoPalette 没写进去"
            get(srv.url + "/api/state", method="POST", body={"autoGrad": True})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("autoGrad") is True, "autoGrad 没写进去"
            get(srv.url + "/api/state", method="POST", body={"autoPalette": False, "autoGrad": False})
            get(srv.url + "/api/state", method="POST",
                body={"cardScale": 1, "specScale": 1, "specX": 0, "specY": 0, "cardSpectrum": True,
                      "badgeScale": 1, "bannerScale": 1, "badgeX": 0, "badgeY": 0,
                      "bannerX": 60, "bannerY": 932, "cardX": 60, "cardY": 240,
                      "badgeOpacity": 1, "bannerOpacity": 1, "cardOpacity": 1})
            st = get_json(srv.url + "/api/snapshot")["state"]
            assert st.get("cardSpectrum") is True, "cardSpectrum 没写进去"
            return "卡片 0.75~1.5 / 角标条幅频谱 0.4~1.0、x -200~1200、y 0~1080、透明度 0~1"

        r.run("开关校验", _state_guard)

        def _new_fields():
            """第十三轮字段齐不齐 —— 同时也抓「服务端还是旧进程」这个老坑"""
            st = get_json(srv.url + "/api/snapshot")["state"]
            need = ["badgeOpacity", "bannerOpacity", "cardOpacity",
                    "badgeScale", "bannerScale",
                    "badgeX", "badgeY", "bannerX", "bannerY", "cardX", "cardY",
                    "radius", "coverSpin", "autoPalette", "autoGrad"]
            miss = [k for k in need if k not in st]
            assert not miss, f"快照缺字段（服务端是旧进程？）：{miss}"
            return f"{len(need)} 个透明度/缩放/位置字段都在"

        r.run("布局字段齐全", _new_fields)

        def _palettes():
            """八套配色（四套原色 + 四套霓虹撞色）都要能存进状态"""
            need = ["magenta", "cinnabar", "cyan", "acid",
                    "neon", "electric", "toxic", "sunset"]
            rejected = []
            for p in need:
                get(srv.url + "/api/state", method="POST", body={"palette": p})
                st = get_json(srv.url + "/api/snapshot")["state"]
                if st.get("palette") != p:
                    rejected.append(p)
            get(srv.url + "/api/state", method="POST", body={"palette": "cinnabar"})
            assert not rejected, f"这些配色被服务端拒了：{rejected}"
            return f"{len(need)} 套配色都能存（含 neon/electric/toxic/sunset）"

        r.run("配色八套齐全", _palettes)

        def _clamp_agree():
            """夹取范围：前端 overlay.js 和服务端 server.py 必须一模一样。

            第十三轮起「拖拽/缩放/透明度」都是两边各存一份常量，
            改一处忘另一处就会出现"面板能拖但存不住"或者"服务端收窄了但页面不认"。
            """
            import re as _re

            def nums_js(text):
                out = {}
                for m in _re.finditer(r"(\w+)_MIN\s*=\s*(-?[\d.]+),\s*\w+_MAX\s*=\s*(-?[\d.]+)", text):
                    out[m.group(1)] = (float(m.group(2)), float(m.group(3)))
                return out

            def nums_py(text):
                out = {}
                for m in _re.finditer(r"(\w+)_MIN,\s*\w+_MAX\s*=\s*(-?[\d.]+),\s*(-?[\d.]+)", text):
                    out[m.group(1)] = (float(m.group(2)), float(m.group(3)))
                return out

            js = nums_js((BASE / "js" / "overlay.js").read_text(encoding="utf-8"))
            py = nums_py((BASE / "server.py").read_text(encoding="utf-8"))

            # 左边 = server.py 的常量名，右边 = overlay.js 的常量名
            pairs = [
                ("POS_X", "POS_X"), ("POS_Y", "POS_Y"),
                ("COMP_SCALE", "COMP_SCALE"), ("OPACITY", "OPACITY"),
                ("CARD_SCALE", "SCALE"),          # 卡片：py 叫 CARD_SCALE_*，js 叫 SCALE_*
            ]
            for name_py, name_js in pairs:
                assert name_py in py, f"server.py 里找不到 {name_py}_MIN/_MAX"
                assert name_js in js, f"overlay.js 里找不到 {name_js}_MIN/_MAX"
                assert py[name_py] == js[name_js], (
                    f"{name_py} 夹取范围不一致：server {py[name_py]} vs js {js[name_js]}")
            return " / ".join(f"{n} {py[n][0]}~{py[n][1]}" for n, _ in pairs)

        r.run("夹取范围前后端一致", _clamp_agree)

        def _fps_switch():
            get(srv.url + "/api/state", method="POST", body={"spectrumFps": 60})
            time.sleep(0.6)
            d60 = get_json(srv.url + "/api/diag")["audio"]
            assert d60.get("fps") == 60, f"切 60 没生效：{d60.get('fps')}"
            get(srv.url + "/api/state", method="POST", body={"spectrumFps": 30})
            time.sleep(0.6)
            d30 = get_json(srv.url + "/api/diag")["audio"]
            assert d30.get("fps") == 30, f"切回 30 没生效：{d30.get('fps')}"
            return "30 → 60 → 30 即时生效"

        r.run("频谱帧率切换", _fps_switch)

        def _badge_pos():
            # 两个角都能切、都写进快照；最后留回 bl
            for corner in ("tl", "bl"):
                get(srv.url + "/api/state", method="POST", body={"badgePos": corner})
                st = get_json(srv.url + "/api/snapshot")["state"]
                assert st.get("badgePos") == corner, f"{corner} 没生效：{st.get('badgePos')}"
            # 非法值（包括已删掉的右半边）要被挡住，不能把状态写坏
            for bad in ("top-left", "br", "tr"):
                get(srv.url + "/api/state", method="POST", body={"badgePos": bad})
                st = get_json(srv.url + "/api/snapshot")["state"]
                assert st.get("badgePos") == "bl", f"非法角标位置被写入：{bad} → {st.get('badgePos')}"
            _, _, js = get(srv.url + "/js/badge.js")
            assert b'b-ball' in js and b'GEOM' in js, "badge.js 旧版本（没有小球/多角几何）"
            assert b"'br'" not in js and b"'tr'" not in js, "badge.js 里还留着右半边角标"
            return "bl / tl 切换正常，非法值挡回，badge.js 含小球且无右半边"

        r.run("角标位置 + 小球", _badge_pos)

        def _blocked():
            for path, want in (("/runtime/state.json", 404), ("/tools/smtc-bridge.ps1", 404)):
                try:
                    get(srv.url + path)
                except urllib.error.HTTPError as e:
                    assert e.code == want, f"{path} -> {e.code}"
                else:
                    raise AssertionError(f"{path} 没被拦")
            return "runtime/ tools/ 不可下载"

        r.run("运行时文件拦截", _blocked)

        def _traversal():
            req = urllib.request.Request(srv.url + "/%2e%2e/HANDOFF.md")
            try:
                st, _, body = get(srv.url + "/%2e%2e/HANDOFF.md")
            except urllib.error.HTTPError as e:
                assert e.code in (403, 404), e.code
                return f"拦住了（{e.code}）"
            raise AssertionError(f"没拦住（{st}）")

        r.run("路径穿越", _traversal)

        def _bad_json():
            req = urllib.request.Request(
                srv.url + "/api/state", data=b"{not json",
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                assert e.code == 400, e.code
                return "坏 JSON → 400"
            raise AssertionError("坏 JSON 没被拒")

        r.run("坏请求体", _bad_json)

        def _head():
            req = urllib.request.Request(srv.url + "/index.html", method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
                assert resp.headers.get("Content-Length")
                assert resp.read() == b"", "HEAD 不该有 body"
            return "HEAD 正常（OBS 预检会用到）"

        r.run("HEAD 请求", _head)

        def _unknown():
            try:
                get(srv.url + "/api/nope")
            except urllib.error.HTTPError as e:
                assert e.code == 404, e.code
                return "未知接口 → 404"
            raise AssertionError("未知接口没 404")

        r.run("未知接口", _unknown)

        def _multi_sse():
            import threading
            got: list[int] = []
            errs: list[str] = []

            def worker():
                try:
                    req = urllib.request.Request(srv.url + "/api/pcm-stream")
                    n = 0
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        for raw in resp:
                            if raw.startswith(b"data:"):
                                d = json.loads(raw[5:].strip())
                                if d.get("ok"):
                                    base64.b64decode(d["b"])
                                    n += 1
                                    if n >= 3:
                                        break
                    got.append(n)
                except Exception as e:                     # noqa: BLE001
                    errs.append(f"{type(e).__name__}: {e}")

            ts = [threading.Thread(target=worker) for _ in range(3)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=15)
            assert not errs, errs[0]
            assert len(got) == 3 and all(n >= 3 for n in got), got
            return "3 个客户端同时收流都正常"

        r.run("多客户端 SSE", _multi_sse)

        def _churn():
            base = get_json(srv.url + "/api/diag").get("threads", 0)

            def one():
                try:
                    req = urllib.request.Request(srv.url + "/api/pcm-stream")
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        resp.readline()
                except (urllib.error.URLError, OSError):
                    pass

            import threading
            for _ in range(20):
                t = threading.Thread(target=one)
                t.start()
                t.join(timeout=6)
            time.sleep(1.0)
            after = get_json(srv.url + "/api/diag").get("threads", 0)
            assert after <= base + 4, f"线程从 {base} 涨到 {after}（SSE 连接泄漏？）"
            return f"20 次连断：线程 {base} → {after}"

        r.run("SSE 连断不泄漏", _churn)

        def _bridge_restart():
            st, _, raw = get(srv.url + "/api/bridge/restart", method="POST", body={})
            assert st == 200 and json.loads(raw).get("ok"), raw
            time.sleep(2.0)
            d = get_json(srv.url + "/api/diag")["diag"]
            assert d.get("ok"), d.get("error")
            return "重启后数据桥 ok"

        r.run("数据桥重启", _bridge_restart)

        # ---- 降级模式 ----
        demo = Server(free_port(port + 1), "--demo", "--no-bridge", "--no-audio")
        try:
            demo.start()

            def _demo():
                now = get_json(demo.url + "/api/snapshot")["now"]
                assert now.get("demo") is True, "不是演示数据"
                assert now.get("title") == "Neon Tokyo", now.get("title")
                assert "lyrics" not in now, "歌词面板已删，now 里不该再有 lyrics"
                return f"{now['title']} · {now['artist']}"

            r.run("降级：演示模式", _demo)

            def _no_bridge_restart():
                try:
                    get(demo.url + "/api/bridge/restart", method="POST", body={})
                except urllib.error.HTTPError as e:
                    assert e.code == 409, e.code
                    return "无数据桥 → 409（不是 500）"
                raise AssertionError("没有数据桥还能重启？")

            r.run("降级：无桥重启", _no_bridge_restart)
        finally:
            demo.stop()

        na = Server(free_port(port + 20), "--no-audio", "--no-bridge")
        try:
            na.start()

            def _no_audio():
                d = get_json(na.url + "/api/diag")
                assert (d.get("audio") or {}).get("ok") is False
                try:
                    get(na.url + "/api/pcm-stream", timeout=3)
                except urllib.error.HTTPError as e:
                    assert e.code == 503, e.code
                    return "音频关闭 → 503 + 诊断写明"
                raise AssertionError("关了音频还能拉流")

            r.run("降级：--no-audio", _no_audio)
        finally:
            na.stop()

    finally:
        if not args.keep:
            srv.stop()
        if state_before is not None:
            state_path.write_bytes(state_before)   # 恢复用户的开关
            print("已恢复原来的 state.json")

    code = r.report()
    if args.keep:
        print(f"服务保留在 {srv.url}/control")
    return code


if __name__ == "__main__":
    sys.exit(main())