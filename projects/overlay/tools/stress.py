# -*- coding: utf-8 -*-
"""叠加层稳定性/压力自测（零依赖）

    python tools/stress.py [--seconds 30] [--clients 2]

做三件事：
  1. 拉起 server.py，开 N 个 SSE 客户端连续收 PCM，统计帧率和 seq 断档
  2. 中途丢一个临时 LRC 进去，验证歌词能自动匹配（结束后删掉）
  3. 每 5 秒采一次服务进程的 CPU / 内存

不改用户的开关，不改 runtime 里的其他文件。
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PY = sys.executable
CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def free_port(start: int) -> int:
    for p in range(start, start + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("没有空闲端口")


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=6) as r:
        return json.loads(r.read())


class ProcStats:
    def __init__(self, pid: int):
        self.h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)

    def cpu_seconds(self) -> float:
        ft = [wt.FILETIME() for _ in range(4)]
        ok = ctypes.windll.kernel32.GetProcessTimes(
            self.h, *[ctypes.byref(x) for x in ft])
        if not ok:
            return 0.0
        k = (ft[2].dwHighDateTime << 32) | ft[2].dwLowDateTime
        u = (ft[3].dwHighDateTime << 32) | ft[3].dwLowDateTime
        return (k + u) / 1e7

    def mem_mb(self) -> float:
        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(self.h, ctypes.byref(pmc), pmc.cb):
            return 0.0
        return pmc.WorkingSetSize / 1024 / 1024


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--clients", type=int, default=2)
    ap.add_argument("--fps", type=int, default=30, choices=(30, 60), help="服务端 --fps（默认 30）")
    args = ap.parse_args()

    port = free_port(8830)
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [PY, "-u", "server.py", "--port", str(port), "--fps", str(args.fps)], cwd=str(BASE),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
    )
    lrc_path = None
    try:
        t0 = time.time()
        snap = None
        while time.time() - t0 < 20:
            try:
                snap = get_json(url + "/api/snapshot")
                if (snap.get("bridge") or {}).get("ok"):
                    break
            except Exception:                                  # noqa: BLE001
                pass
            time.sleep(0.3)
        print(f"服务就绪 {int((time.time() - t0) * 1000)} ms")
        now = (snap or {}).get("now") or {}
        title = now.get("title") or ""
        print(f"当前曲目：{title or '（无）'}  来源={now.get('positionSource')}")

        stop = threading.Event()
        stats = {"frames": 0, "gaps": 0, "last_seq": None, "errors": []}
        lock = threading.Lock()

        def reader():
            try:
                req = urllib.request.Request(url + "/api/pcm-stream")
                prev = None
                with urllib.request.urlopen(req, timeout=10) as resp:
                    while not stop.is_set():
                        raw = resp.readline()
                        if not raw:
                            break
                        if not raw.startswith(b"data:"):
                            continue
                        d = json.loads(raw[5:].strip())
                        if not d.get("ok"):
                            continue
                        base64.b64decode(d["b"])
                        with lock:
                            s = d.get("q")
                            if prev is not None and s != prev + 1:
                                stats["gaps"] += 1
                            prev = s
                            stats["frames"] += 1
            except Exception as e:                             # noqa: BLE001
                with lock:
                    stats["errors"].append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=reader, daemon=True) for _ in range(args.clients)]
        for t in threads:
            t.start()

        if title:
            lrc_path = BASE / "lyrics" / f"{title}.lrc"
            lrc_path.write_text(
                "[00:00.00]テスト行一\n[00:00.00]测试第一行\n"
                "[00:10.00]テスト行二\n[00:10.00]测试第二行\n", encoding="utf-8")
            print(f"已放入临时歌词：{lrc_path.name}")

        ps = ProcStats(proc.pid)
        c0 = ps.cpu_seconds()
        t_start = time.time()
        lyric_ok = None
        next_sample = t_start + 5
        last_frames = 0
        while time.time() - t_start < args.seconds:
            time.sleep(0.5)
            if lyric_ok is None and lrc_path is not None:
                try:
                    n = get_json(url + "/api/snapshot")["now"].get("lyrics") or []
                    if n:
                        lyric_ok = len(n)
                        print(f"歌词已匹配：{lyric_ok} 行")
                except Exception:                              # noqa: BLE001
                    pass
            if time.time() >= next_sample:
                next_sample += 5
                with lock:
                    frames = stats["frames"]
                print(f"  t+{int(time.time() - t_start):2d}s  frames={frames} "
                      f"({(frames - last_frames) / 5:.1f} fps)  seq_gaps={stats['gaps']}")
                last_frames = frames

        elapsed = time.time() - t_start
        c1 = ps.cpu_seconds()
        stop.set()
        with lock:
            frames = stats["frames"]
            gaps = stats["gaps"]
            errors = list(stats["errors"])
        cpu_pct = (c1 - c0) / elapsed * 100
        print("\n--- 结果 ---")
        print(f"时长            {elapsed:.1f}s × {args.clients} 客户端")
        print(f"总帧数          {frames}（{frames / elapsed:.1f} fps）")
        print(f"seq 断档        {gaps}")
        print(f"CPU             单核 {cpu_pct:.1f}%（12 核平均 {cpu_pct / 12:.2f}%）")
        print(f"内存            {ps.mem_mb():.1f} MB")
        print(f"歌词自动匹配    {'OK ' + str(lyric_ok) + ' 行' if lyric_ok else '未匹配（可能没歌/没到 10 秒重试）'}")
        print(f"客户端错误      {errors or '无'}")
        ok = gaps == 0 and not errors and frames > 0
        print("结论            " + ("通过" if ok else "有异常，看上面"))
        return 0 if ok else 1
    finally:
        if lrc_path and lrc_path.is_file():
            lrc_path.unlink()
            print("已删除临时歌词")
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:                                      # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
