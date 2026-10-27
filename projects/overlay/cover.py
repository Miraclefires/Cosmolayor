# -*- coding: utf-8 -*-
"""从 SMTC 读封面（自己写的 C# 小工具，不依赖任何第三方包）

为什么不用 PowerShell：SMTC 的 Thumbnail 是 WinRT 流对象，Windows PowerShell 5.1
投影不出这个接口（拿到的永远是裸 __ComObject，读不了 Size/ReadBytes）。实测：
PowerShell 能拿到封面"引用"，但一碰流就失败。

为什么不用 Python 的 winrt 包：那是第三方依赖，本轮目标就是清零。

现在的做法：`tools/cover-fetch.cs`（本仓库自己的代码）用系统自带的 .NET Framework
编译器 csc.exe 现场编译成 `runtime/bin/cover-fetch.exe`，再执行它读缩略图落盘。
编译只需要 `C:\\Windows\\System32\\WinMetadata\\*.winmd`（系统自带），不需要 Windows SDK。

关键点：csc 直接引用单个 winmd 时，`System.Runtime.WindowsRuntime` 会要求 Windows SDK
的合并元数据 `Windows.winmd`（本机没有）。所以 C# 里**不用 AsTask/await**，而是轮询
`IAsyncOperation.Status` 再取 `GetResults()` —— 这样就不用引 WindowsRuntime 桥接库。

封面就是系统媒体浮窗里那张，一般 108×108，够角标(32)/条幅(56)/卡片(88) 用。
没有缩略图就返回 None，页面上退回渐变色占位。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "tools" / "cover-fetch.cs"
BIN = BASE / "runtime" / "bin" / "cover-fetch.exe"
# 程序目录不可写时（装在 Program Files / 受限环境里），编译产物放到临时目录：
#   发布包已经预置了 cover-fetch.exe，正常情况下根本用不到这条；
#   但万一 exe 丢了、或者被杀软删掉，这里能兜住，否则封面永远是空的。
FALLBACK_BIN = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "cosmolayor" / "cover-fetch.exe"

# 和数据桥保持同一份白名单：只认音乐播放器，别把 Chrome 的 B 站封面读进来
ALLOWED = ("cloudmusic", "spotify", "qqmusic", "foobar", "musicbee", "aimp", "potplayer", "zune")

CREATE_NO_WINDOW = 0x08000000
WIN_DIR = Path(os.environ.get("SystemRoot", r"C:\Windows"))
WINMETA = WIN_DIR / "System32" / "WinMetadata"

_build_lock = threading.Lock()
_build_error = ""            # 编译失败原因（诊断面板用）
_build_fail_at = 0.0         # 上次编译失败的时间（失败后 30 秒内不重试，免得诊断轮询反复编译）
_last_error = ""             # 上一次抓取失败原因（诊断面板用）


def _csc() -> Path | None:
    """找 .NET Framework 自带的 C# 编译器（Win10/11 默认就在，不用装 SDK）"""
    for arch in ("Framework64", "Framework"):
        p = WIN_DIR / "Microsoft.NET" / arch / "v4.0.30319" / "csc.exe"
        if p.is_file():
            return p
    return None


def _framework_dir(csc: Path) -> Path:
    return csc.parent


def ensure_binary() -> Path | None:
    """按需编译 C# 小工具；源码没变就直接用现成的 exe"""
    global _build_error, _build_fail_at
    if not SRC.is_file():
        _build_error = "缺少 tools/cover-fetch.cs"
        return None
    if BIN.is_file() and BIN.stat().st_mtime >= SRC.stat().st_mtime:
        return BIN
    if _build_fail_at and time.time() - _build_fail_at < 30:
        return None                                     # 刚失败过，别反复编译

    with _build_lock:
        if BIN.is_file() and BIN.stat().st_mtime >= SRC.stat().st_mtime:
            return BIN                                     # 另一个线程刚编译好
        csc = _csc()
        if csc is None:
            _build_error = "找不到 csc.exe（.NET Framework 4.x）"
            _build_fail_at = time.time()
            return None
        fw = _framework_dir(csc)
        refs = [
            WINMETA / "Windows.Media.winmd",
            WINMETA / "Windows.Foundation.winmd",
            WINMETA / "Windows.Storage.winmd",
            fw / "mscorlib.dll",
            fw / "System.dll",
            fw / "System.Core.dll",
            fw / "System.Runtime.dll",
        ]
        missing = [str(r) for r in refs if not r.is_file()]
        if missing:
            _build_error = "缺少引用：" + ", ".join(missing)
            _build_fail_at = time.time()
            return None

        # 先试程序目录，写不进去（Program Files / 受限环境）就退到临时目录
        target = BIN
        try:
            BIN.parent.mkdir(parents=True, exist_ok=True)
            probe = BIN.parent / ".write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            target = FALLBACK_BIN
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                _build_error = f"没有可写目录存放封面工具：{e}"
                _build_fail_at = time.time()
                return None

        tmp = target.with_name(target.stem + ".build.exe")
        cmd = [str(csc), "/nologo", "/langversion:5", "/target:exe", "/platform:anycpu",
               "/codepage:65001", "/out:" + str(tmp)]
        cmd += ["/r:" + str(r) for r in refs]
        cmd.append(str(SRC))
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace",
                creationflags=CREATE_NO_WINDOW, timeout=90,
            )
            if res.returncode != 0 or not tmp.is_file():
                _build_error = ((res.stdout or "") + (res.stderr or "")).strip()[:400] or "csc 编译失败"
                _build_fail_at = time.time()
                return None
            os.replace(tmp, target)
            _build_error = ""
            _build_fail_at = 0.0
            return target
        except (OSError, subprocess.SubprocessError) as e:
            _build_error = f"{type(e).__name__}: {e}"
            _build_fail_at = time.time()
            return None
        finally:
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass


def fetch_thumbnail(timeout: float = 8.0) -> bytes | None:
    """同步取一次封面字节；后台线程里调用没问题（每次新起一个短进程）"""
    global _last_error
    exe = ensure_binary()
    if exe is None:
        _last_error = _build_error or "封面工具不可用"
        return None

    out = BASE / "runtime" / "cover.fetch.tmp"
    try:
        res = subprocess.run(
            [str(exe), ",".join(ALLOWED), str(out)],
            capture_output=True, text=True, errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=timeout,
        )
        if res.returncode != 0:
            msg = (res.stdout or "").strip().splitlines()
            _last_error = msg[-1] if msg else f"exit {res.returncode}"
            return None
        data = out.read_bytes()
        _last_error = ""
        return data or None
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        _last_error = f"{type(e).__name__}: {e}"
        return None
    finally:
        try:
            if out.is_file():
                out.unlink()
        except OSError:
            pass


def status() -> dict:
    """给诊断面板：工具是否可用、最近一次错误"""
    if BIN.is_file() and SRC.is_file() and BIN.stat().st_mtime >= SRC.stat().st_mtime:
        ok, err = True, ""
    else:
        exe = ensure_binary()
        ok, err = bool(exe), _build_error
    return {"ok": ok, "error": err or _last_error, "exe": str(BIN) if ok else ""}


if __name__ == "__main__":
    t0 = time.time()
    st = status()
    print("工具:", st)
    data = fetch_thumbnail()
    print("字节:", len(data) if data else 0, " 耗时: %.2fs" % (time.time() - t0),
          " 头:", data[:4].hex(" ") if data else "-")
