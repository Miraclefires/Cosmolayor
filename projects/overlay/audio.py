# -*- coding: utf-8 -*-
"""系统音频回环 → 原始 PCM（WASAPI，只用标准库）

浏览器抓不到系统音频，但 Windows 上可以用 WASAPI loopback 录"正在播放的声音"。
这里**不依赖 pyaudiowpatch / numpy**：直接 ctypes 调 COM 接口，把 PCM 交给页面，
FFT 由页面里的 `js/fft.js`（自写 radix-2）负责。

链路：
    IMMDeviceEnumerator → 默认渲染设备 → IAudioClient(SHARED + LOOPBACK)
    → IAudioCaptureClient::GetBuffer → 单声道 float → 每 33ms 取最近 2048 点
    → int16 PCM → `/api/pcm-stream`(SSE, base64) → 页面 FFT

实测：48kHz 立体声 float32 回环，CPU < 1%，内存几 MB。
"""
from __future__ import annotations

import array
import ctypes
import math
import threading
import time
import uuid
from ctypes import POINTER, byref, c_int32, c_int64, c_uint32, c_void_p, sizeof, Structure, WINFUNCTYPE
import ctypes.wintypes as wt

# ---------------------------------------------------------------- COM 基础设施
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

HRESULT = c_int32
RPC_E_CHANGED_MODE = -2147417850                     # 0x80010106


class GUID(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str | None = None):
        super().__init__()
        if text:
            u = uuid.UUID(text)
            b = u.bytes
            # uuid.bytes 是按字符串顺序的大端；GUID 结构里 Data1/2/3 都是小端存储
            self.Data1 = int.from_bytes(b[0:4], "big")
            self.Data2 = int.from_bytes(b[4:6], "big")
            self.Data3 = int.from_bytes(b[6:8], "big")
            for i in range(8):
                self.Data4[i] = b[8 + i]


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_uint32)]


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioClient = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
IID_IPropertyStore = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
PKEY_FRIENDLY_NAME = (GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)

CLSCTX_INPROC_SERVER = 1
STGM_READ = 0
E_RENDER, E_CONSOLE = 0, 0                            # eRender, eConsole
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
SUBTYPE_PCM = "00000001-0000-0010-8000-00AA00389B71"
SUBTYPE_FLOAT = "00000003-0000-0010-8000-00AA00389B71"


class _Com:
    """极简 COM 对象：按 vtable 下标调用 + 引用计数"""

    def __init__(self, ptr: int):
        self.ptr = ptr
        self._vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
        self._cache: dict[tuple, object] = {}

    def _fn(self, index: int, restype, *argtypes):
        key = (index, restype, argtypes)
        fn = self._cache.get(key)
        if fn is None:
            fn = WINFUNCTYPE(restype, c_void_p, *argtypes)(self._vtbl[index])
            self._cache[key] = fn
        return fn

    def release(self) -> None:
        if self.ptr:
            try:
                self._fn(2, c_uint32)(self.ptr)
            finally:
                self.ptr = 0

    def __del__(self):
        try:
            self.release()
        except Exception:                            # noqa: BLE001
            pass


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} 失败：0x{hr & 0xFFFFFFFF:08X}")


def _co_init() -> None:
    hr = ole32.CoInitializeEx(None, 0)               # COINIT_MULTITHREADED
    if hr < 0 and hr != RPC_E_CHANGED_MODE:
        raise OSError(f"CoInitializeEx 失败：0x{hr & 0xFFFFFFFF:08X}")


def _device_name(device: "_Device") -> str:
    """IMMDevice::OpenPropertyStore → PKEY_Device_FriendlyName（失败就退回设备 id）"""
    store_ptr = c_void_p()
    try:
        hr = device._fn(4, HRESULT, c_uint32, POINTER(c_void_p))(
            device.ptr, STGM_READ, byref(store_ptr))
        if hr >= 0 and store_ptr.value:
            store = _Com(store_ptr.value)
            key = PROPERTYKEY()
            key.fmtid = PKEY_FRIENDLY_NAME[0]
            key.pid = PKEY_FRIENDLY_NAME[1]
            # PROPVARIANT：vt 在开头，字符串指针在偏移 8（32/64 位都是）
            pv = (ctypes.c_byte * 24)()
            hr = store._fn(5, HRESULT, POINTER(PROPERTYKEY), c_void_p)(
                store.ptr, byref(key), byref(pv))
            if hr >= 0:
                vt = int.from_bytes(bytes(pv[0:2]), "little")
                if vt == 31:                         # VT_LPWSTR
                    raw = bytes(pv)
                    plen = sizeof(c_void_p)
                    sp = int.from_bytes(raw[8:8 + plen], "little")
                    if sp:
                        name = ctypes.wstring_at(sp)
                        ole32.PropVariantClear(byref(pv))
                        if name:
                            store.release()
                            return name
                ole32.PropVariantClear(byref(pv))
            store.release()
    except Exception:                                # noqa: BLE001
        pass
    return ""


class _Device(_Com):
    def activate(self, iid: GUID) -> int:
        p = c_void_p()
        hr = self._fn(3, HRESULT, POINTER(GUID), c_uint32, c_void_p, POINTER(c_void_p))(
            self.ptr, byref(iid), CLSCTX_INPROC_SERVER, None, byref(p))
        _check(hr, "IMMDevice::Activate")
        return p.value

    def get_id(self) -> str:
        p = ctypes.c_wchar_p()
        hr = self._fn(5, HRESULT, POINTER(ctypes.c_wchar_p))(self.ptr, byref(p))
        if hr < 0 or not p.value:
            return ""
        text = p.value
        ole32.CoTaskMemFree(p)
        return text


class _AudioClient(_Com):
    def initialize(self, mix_ptr: int, flags: int = AUDCLNT_STREAMFLAGS_LOOPBACK,
                   buffer_hns: int = 0) -> None:
        hr = self._fn(3, HRESULT, c_int32, c_uint32, c_int64, c_int64, c_void_p, c_void_p)(
            self.ptr, AUDCLNT_SHAREMODE_SHARED, flags, buffer_hns, 0, mix_ptr, None)
        _check(hr, "IAudioClient::Initialize")

    def get_mix_format(self) -> int:
        p = c_void_p()
        hr = self._fn(8, HRESULT, POINTER(c_void_p))(self.ptr, byref(p))
        _check(hr, "IAudioClient::GetMixFormat")
        return p.value

    def get_service(self, iid: GUID) -> int:
        p = c_void_p()
        hr = self._fn(14, HRESULT, POINTER(GUID), POINTER(c_void_p))(self.ptr, byref(iid), byref(p))
        _check(hr, "IAudioClient::GetService")
        return p.value

    def start(self) -> None:
        _check(self._fn(10, HRESULT)(self.ptr), "IAudioClient::Start")

    def stop(self) -> None:
        try:
            self._fn(11, HRESULT)(self.ptr)
        except Exception:                            # noqa: BLE001
            pass


class _CaptureClient(_Com):
    def next_packet(self) -> int:
        n = c_uint32()
        _check(self._fn(5, HRESULT, POINTER(c_uint32))(self.ptr, byref(n)),
               "IAudioCaptureClient::GetNextPacketSize")
        return n.value

    def buffer(self):
        data = c_void_p()
        frames = c_uint32()
        flags = c_uint32()
        hr = self._fn(3, HRESULT, POINTER(c_void_p), POINTER(c_uint32), POINTER(c_uint32),
                      c_void_p, c_void_p)(self.ptr, byref(data), byref(frames),
                                          byref(flags), None, None)
        _check(hr, "IAudioCaptureClient::GetBuffer")
        return data.value, frames.value, flags.value

    def release_buffer(self, frames: int) -> None:
        _check(self._fn(4, HRESULT, c_uint32)(self.ptr, frames),
               "IAudioCaptureClient::ReleaseBuffer")


def default_device():
    """打开默认渲染设备 → (device, 名称)"""
    p = c_void_p()
    _check(ole32.CoCreateInstance(
        byref(CLSID_MMDeviceEnumerator), None, CLSCTX_INPROC_SERVER,
        byref(IID_IMMDeviceEnumerator), byref(p)), "CoCreateInstance(MMDeviceEnumerator)")
    enum = _Com(p.value)
    dev_ptr = c_void_p()
    try:
        hr = enum._fn(4, HRESULT, c_int32, c_int32, POINTER(c_void_p))(
            enum.ptr, E_RENDER, E_CONSOLE, byref(dev_ptr))
        _check(hr, "IMMDeviceEnumerator::GetDefaultAudioEndpoint")
        dev = _Device(dev_ptr.value)
        return dev, _device_name(dev)
    finally:
        enum.release()


def _parse_mix_format(ptr: int) -> dict:
    """从 WAVEFORMATEX* 读出格式；SubFormat 在偏移 24（WAVE_FORMAT_EXTENSIBLE）"""
    raw = ctypes.string_at(ptr, 40)
    tag = int.from_bytes(raw[0:2], "little")
    ch = int.from_bytes(raw[2:4], "little")
    rate = int.from_bytes(raw[4:8], "little")
    block = int.from_bytes(raw[12:14], "little")
    bits = int.from_bytes(raw[14:16], "little")
    cb = int.from_bytes(raw[16:18], "little")
    sub = ""
    if tag == WAVE_FORMAT_EXTENSIBLE and cb >= 22:
        g = GUID()
        g.Data1 = int.from_bytes(raw[24:28], "little")
        g.Data2 = int.from_bytes(raw[28:30], "little")
        g.Data3 = int.from_bytes(raw[30:32], "little")
        for i in range(8):
            g.Data4[i] = raw[32 + i]
        sub = (f"{g.Data1:08X}-{g.Data2:04X}-{g.Data3:04X}-"
               f"{g.Data4[0]:02X}{g.Data4[1]:02X}-" +
               "".join(f"{b:02X}" for b in g.Data4[2:8]))
    if sub == SUBTYPE_FLOAT:
        kind = "float"
    elif sub == SUBTYPE_PCM or tag == 1:
        kind = "pcm"
    elif tag == 3:
        kind = "float"
    else:
        kind = f"tag{tag:#x}"
    return {"tag": tag, "channels": ch, "rate": rate, "block": block,
            "bits": bits, "kind": kind}


# ---------------------------------------------------------------- 进程级回环（只抓一个程序）
# 默认设备回环抓的是"整个输出设备的混音"——浏览器、通知、别的播放器都会进频谱。
# Win10 20348+ / Win11 支持 Process Loopback：ActivateAudioInterfaceAsync +
# VAD\Process_Loopback + 目标进程（含子进程树），拿到的 IAudioClient 里只有那个程序的声音。
#
# 实测（Win11 26200）：抓放音进程 peak 0.25、抓安静进程 peak 0.0000 —— 完全隔离。
#
# 两个和普通回环不同的地方：
#   1. 进程回环的 IAudioClient **不支持 GetMixFormat**（E_NOTIMPL），必须自己给格式
#      → 借用默认设备的混音格式（_default_mix_format）
#   2. 必须自己实现 IActivateAudioInterfaceCompletionHandler 回调（见 _ProcessLoopbackHandler）
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
VT_BLOB = 65
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0

IID_IUnknown = GUID("{00000000-0000-0000-C000-000000000046}")
IID_IAgileObject = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
IID_IActivateAudioInterfaceCompletionHandler = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
IID_IActivateAudioInterfaceAsyncOperation = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
IID_IAudioSessionManager2 = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
IID_IAudioSessionControl2 = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")

_mmdevapi = ctypes.WinDLL("Mmdevapi.dll", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x102
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _ProcessLoopbackParams(Structure):
    _fields_ = [("TargetProcessId", c_uint32), ("ProcessLoopbackMode", c_int32)]


class _ActivationParams(Structure):
    _fields_ = [("ActivationType", c_int32), ("ProcessLoopbackParams", _ProcessLoopbackParams)]


class _PropVariant(Structure):
    """24 字节：vt + 3×WORD 保留(8) + union(16)。VT_BLOB 时 cbSize@8、pBlobData@16"""
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("blob_size", c_uint32), ("_pad", c_uint32), ("blob_data", c_void_p)]


class _ProcessEntry32W(Structure):
    _fields_ = [("dwSize", c_uint32), ("cntUsage", c_uint32), ("th32ProcessID", c_uint32),
                ("th32DefaultHeapID", c_void_p), ("th32ModuleID", c_uint32),
                ("cntThreads", c_uint32), ("th32ParentProcessID", c_uint32),
                ("pcPriClassBase", c_int32), ("dwFlags", c_uint32),
                ("szExeFile", ctypes.c_wchar * 260)]


def _guid_eq(a, b: GUID) -> bool:
    return (a.Data1 == b.Data1 and a.Data2 == b.Data2 and a.Data3 == b.Data3
            and bytes(a.Data4) == bytes(b.Data4))


class _ProcessLoopbackHandler:
    """自己搓的 COM 对象：IUnknown + IAgileObject + IActivateAudioInterfaceCompletionHandler

    - 引用计数是假的（恒 1、永不释放）：全程只需要一个，进程结束就没了
    - 必须声明 IAgileObject，否则 ActivateAudioInterfaceAsync 跨线程回调会拒绝
    - 回调函数对象要一直被引用（self._live），被 GC 掉就是野指针
    """

    def __init__(self) -> None:
        self.done = threading.Event()
        self._live = []

        QI = WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
        AR = WINFUNCTYPE(c_uint32, c_void_p)
        AC = WINFUNCTYPE(HRESULT, c_void_p, c_void_p)

        def query_interface(this, riid, ppv):
            g = riid.contents
            if (_guid_eq(g, IID_IUnknown) or _guid_eq(g, IID_IAgileObject)
                    or _guid_eq(g, IID_IActivateAudioInterfaceCompletionHandler)):
                ppv[0] = this
                return 0
            ppv[0] = None
            return -2147467262                          # E_NOINTERFACE

        def add_ref(this):
            return 1

        def release(this):
            return 1

        def activate_completed(this, operation):
            self.done.set()
            return 0

        fns = [QI(query_interface), AR(add_ref), AR(release), AC(activate_completed)]
        self._live = fns
        self._vtbl = (c_void_p * 4)(*[ctypes.cast(f, c_void_p).value for f in fns])
        self._obj = (c_void_p * 2)(ctypes.cast(self._vtbl, c_void_p).value, 0)
        self.ptr = ctypes.addressof(self._obj)


def _activation_error(hr: int) -> str:
    code = hr & 0xFFFFFFFF
    if code == 0x80070057:
        return "系统不支持进程回环（需要 Win10 20348+ 或 Win11）"
    if code == 0x80070002 or code == 0x80070490:
        return "目标进程不存在"
    return f"激活进程回环失败：0x{code:08X}"


def activate_process_client(pid: int) -> int:
    """对 pid（含子进程树）激活 IAudioClient，返回裸指针（失败抛 OSError）"""
    _co_init()

    params = _ActivationParams(
        AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
        _ProcessLoopbackParams(int(pid), PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE))
    pv = _PropVariant()
    pv.vt = VT_BLOB
    pv.blob_size = sizeof(params)
    pv.blob_data = ctypes.cast(byref(params), c_void_p).value

    handler = _ProcessLoopbackHandler()
    op = c_void_p()
    fn = _mmdevapi.ActivateAudioInterfaceAsync
    fn.argtypes = [wt.LPCWSTR, POINTER(GUID), POINTER(_PropVariant), c_void_p, POINTER(c_void_p)]
    fn.restype = HRESULT
    hr = fn(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(IID_IAudioClient),
            byref(pv), c_void_p(handler.ptr), byref(op))
    if hr < 0:
        raise OSError(_activation_error(hr))
    if not op.value:
        raise OSError("ActivateAudioInterfaceAsync 没有返回 operation")
    if not handler.done.wait(5.0):
        raise OSError("等进程回环激活回调超时")

    opobj = _Com(op.value)
    activate_hr = HRESULT()
    unk = c_void_p()
    try:
        hr2 = opobj._fn(3, HRESULT, POINTER(HRESULT), POINTER(c_void_p))(
            opobj.ptr, byref(activate_hr), byref(unk))
        if hr2 < 0:
            raise OSError(f"GetActivateResult 失败：0x{hr2 & 0xFFFFFFFF:08X}")
        if activate_hr.value < 0:
            raise OSError(_activation_error(activate_hr.value))
        if not unk.value:
            raise OSError("激活成功但没拿到 IAudioClient")
        return unk.value
    finally:
        opobj.release()


def list_processes() -> list[tuple[int, int, str]]:
    """[(pid, ppid, exe)]；只用 kernel32，不启子进程（沙箱里也能跑）"""
    out: list[tuple[int, int, str]] = []
    _kernel32.CreateToolhelp32Snapshot.restype = c_void_p
    _kernel32.CreateToolhelp32Snapshot.argtypes = [c_uint32, c_uint32]
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return out
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = sizeof(entry)
        handle = c_void_p(snap)
        if not _kernel32.Process32FirstW(handle, byref(entry)):
            return out
        while True:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            if not _kernel32.Process32NextW(handle, byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(c_void_p(snap))
    return out


def active_session_pids() -> set[int]:
    """当前处于 Active 状态的音频会话属于哪些进程（失败就返回空集合，不影响主流程）

    用途：同一个 exe 开了好几份时，挑"正在出声"的那一份去锁。
    """
    out: set[int] = set()
    dev = mgr = enumerator = None
    try:
        _co_init()                                   # 可能在任何线程被调用，先保证 COM 可用
    except OSError:
        return out
    try:
        dev, _name = default_device()
        mgr = _Com(dev.activate(IID_IAudioSessionManager2))
        # IAudioSessionManager2: 5 = GetSessionEnumerator
        p = c_void_p()
        if mgr._fn(5, HRESULT, POINTER(c_void_p))(mgr.ptr, byref(p)) < 0 or not p.value:
            return out
        enumerator = _Com(p.value)
        count = c_int32()
        enumerator._fn(3, HRESULT, POINTER(c_int32))(enumerator.ptr, byref(count))
        for i in range(max(0, count.value)):
            ctrl = c_void_p()
            # IAudioSessionEnumerator: 4 = GetSession(int, IAudioSessionControl**)
            if enumerator._fn(4, HRESULT, c_int32, POINTER(c_void_p))(
                    enumerator.ptr, i, byref(ctrl)) < 0 or not ctrl.value:
                continue
            obj = _Com(ctrl.value)
            try:
                c2 = c_void_p()
                hr = obj._fn(0, HRESULT, POINTER(GUID), POINTER(c_void_p))(
                    obj.ptr, byref(IID_IAudioSessionControl2), byref(c2))
                if hr < 0 or not c2.value:
                    continue
                c2o = _Com(c2.value)
                try:
                    state, pid = c_int32(), c_uint32()
                    # IAudioSessionControl: 3 = GetState；IAudioSessionControl2: 14 = GetProcessId
                    hr1 = c2o._fn(3, HRESULT, POINTER(c_int32))(c2o.ptr, byref(state))
                    hr2 = c2o._fn(14, HRESULT, POINTER(c_uint32))(c2o.ptr, byref(pid))
                    if hr1 >= 0 and hr2 >= 0 and state.value == 1 and pid.value:  # 1 = Active
                        out.add(int(pid.value))
                finally:
                    c2o.release()
            finally:
                obj.release()
    except Exception:                                # noqa: BLE001
        pass
    finally:
        for o in (enumerator, mgr, dev):
            try:
                if o is not None:
                    o.release()
            except Exception:                        # noqa: BLE001
                pass
    return out


def _tree_pids(root: int, procs: list[tuple[int, int, str]], limit: int = 512) -> set[int]:
    """root 及其所有子孙进程的 pid"""
    kids: dict[int, list[int]] = {}
    for pid, ppid, _name in procs:
        kids.setdefault(ppid, []).append(pid)
    out: set[int] = set()
    stack = [root]
    while stack and len(out) < limit:
        p = stack.pop()
        if p in out:
            continue
        out.add(p)
        stack.extend(kids.get(p, ()))
    return out


def find_root_pid(exe: str) -> int:
    """按进程名找"根进程"（父进程不是同一个程序的）；找不到返回 0

    网易云有很多子进程（gpu-process / renderer），音频可能由子进程渲染，
    所以锁根进程 + INCLUDE_TREE 才抓得全。

    同一个 exe 有多条独立进程树时（本机就见过好几个 python.exe），
    优先选「树里有进程正在出声」的那条 —— 否则会锁到一条安静的树，
    表现为**频谱永远是平的且不报错**，很难查。
    """
    want = (exe or "").strip().lower()
    if not want:
        return 0
    procs = list_processes()
    names = {pid: name.lower() for pid, _ppid, name in procs}
    matches = [(pid, ppid) for pid, ppid, name in procs if name.lower() == want]
    if not matches:
        return 0
    roots = [pid for pid, ppid in matches if names.get(ppid, "") != want]
    if not roots:
        roots = [matches[0][0]]
    if len(roots) == 1:
        return roots[0]

    live = active_session_pids()
    if live:
        for root in roots:
            if _tree_pids(root, procs) & live:
                return root
    return roots[0]


def process_alive(pid: int) -> bool:
    """进程还在不在（用 SYNCHRONIZE 句柄 + 0 超时等，不启动子进程）"""
    if not pid:
        return False
    _kernel32.OpenProcess.restype = c_void_p
    h = _kernel32.OpenProcess(PROCESS_SYNCHRONIZE, False, int(pid))
    if not h:
        return False
    try:
        return _kernel32.WaitForSingleObject(c_void_p(h), 0) == WAIT_TIMEOUT
    finally:
        _kernel32.CloseHandle(c_void_p(h))


def default_mix_format() -> int:
    """默认设备的混音格式（WAVEFORMATEX*，CoTaskMem，用完要 free）

    进程回环的 IAudioClient 调用 GetMixFormat 会返回 E_NOTIMPL，只能借这份格式。
    """
    dev, _name = default_device()
    client = None
    try:
        client = _AudioClient(dev.activate(IID_IAudioClient))
        return client.get_mix_format()
    finally:
        if client is not None:
            client.release()
        dev.release()


# ---------------------------------------------------------------- 采集线程
FRAME_SAMPLES = 2048          # 一帧 FFT 窗口（48k 下 43ms，跟拍更快）
PUBLISH_FPS = 30              # 默认每秒推多少帧 PCM（控制面板可切 30/60）


class AudioCapture(threading.Thread):
    """常驻线程：录回环 → 单声道 int16 PCM，页面自己算 FFT

    lock_exe 非空时走 **进程回环**：只录这个程序（含子进程树）的声音，
    网易云以外的一切（浏览器、通知、别的播放器）都不会进频谱。
    """

    def __init__(self, fps: int = PUBLISH_FPS, frame: int = FRAME_SAMPLES,
                 lock_exe: str = "", lock_pid: int = 0):
        super().__init__(daemon=True, name="wasapi-loopback")
        self.fps = fps
        self.frame = frame
        self.lock_exe = (lock_exe or "").strip()
        self.lock_pid = int(lock_pid or 0)      # 精确锁某个 pid（测试/调试用，优先于 exe）

        self.lock = threading.Lock()
        self._cond = threading.Condition(self.lock)
        self.ok = False
        self.error = ""
        self.device = ""
        self.device_id = ""
        self.mode = "process" if (self.lock_exe or self.lock_pid) else "system"   # process | system
        self.target = self.lock_exe or (f"pid {self.lock_pid}" if self.lock_pid else "")
        self.rate = 0
        self.channels = 0
        self.format = ""
        self.peak = 0
        self.rms = 0.0
        self.seq = 0
        self.last_update = 0.0
        self._pcm = b"\x00\x00" * frame
        self._buf = array.array("f")           # 最近的采样（单声道）
        self._audio_at = 0.0                   # 最近一次真的收到音频包的时刻
        self._stop = threading.Event()
        self._reopen = threading.Event()       # 换锁定目标 / 换设备时踢一下采集循环

    # ---------- 对外 ----------
    def stop(self) -> None:
        self._stop.set()

    def set_lock(self, exe: str) -> None:
        """切换锁定目标（"" = 全部系统声音）；空转时立刻重连，不用重启服务"""
        exe = (exe or "").strip()
        if exe == self.lock_exe:
            return
        self.lock_exe = exe
        self.mode = "process" if exe else "system"
        self.target = exe
        self._reopen.set()

    def set_fps(self, fps: int) -> None:
        """运行时改推流帧率（控制面板的「频谱帧率」用；30/60 两种）"""
        try:
            self.fps = max(10, min(60, int(fps)))
        except (TypeError, ValueError):
            pass

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "ok": self.ok,
                "error": self.error,
                "device": self.device,
                "mode": self.mode,              # process = 只抓一个程序，system = 整机混音
                "target": self.target,
                "rate": self.rate,
                "channels": self.channels,
                "format": self.format,
                "peak": self.peak,
                "fps": self.fps,
                "frameMs": round(self.frame * 1000 / self.rate) if self.rate else 0,
                "seq": self.seq,
                "ageMs": round((time.time() - self.last_update) * 1000) if self.last_update else None,
            }

    def pcm(self) -> tuple[bytes, int, int] | None:
        """(int16 PCM 字节, 采样率, 帧号)；没数据返回 None"""
        with self.lock:
            if not self.ok or not self.rate:
                return None
            return self._pcm, self.rate, self.seq

    def wait_frame(self, last_seq: int, timeout: float = 1.0) -> tuple[bytes, int, int] | None:
        """等下一帧（SSE 用，别空转轮询）"""
        with self._cond:
            if self.ok and self.rate and self.seq == last_seq:
                self._cond.wait(timeout)
            if not self.ok or not self.rate:
                return None
            return self._pcm, self.rate, self.seq

    # ---------- 主循环 ----------
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                _co_init()
                self._capture()
            except Exception as e:                   # noqa: BLE001
                with self.lock:
                    self.ok = False
                    self.error = f"{type(e).__name__}: {e}"
                    self._buf = array.array("f")
                if not self._stop.is_set():
                    self._stop.wait(3.0)

    def _capture(self) -> None:
        self._reopen.clear()
        lock = (self.lock_exe or "").strip()
        fixed_pid = self.lock_pid

        # 锁定的程序没开：先推静音（直播里宁可一条平线，也别退回假动效）
        if lock and not fixed_pid and not find_root_pid(lock):
            self._wait_for_target(lock)
            return

        client = None
        capture = None
        dev = None
        started = False
        pid = 0
        try:
            if lock or fixed_pid:
                pid = fixed_pid or find_root_pid(lock)
                if not pid:
                    return
                client = _AudioClient(activate_process_client(pid))
                name = f"{lock or 'pid'} (pid {pid})"
                device_id = ""
                mix = default_mix_format()          # 进程回环不支持 GetMixFormat
                bufs = (200_000, 10_000_000)        # 20ms / 1s
            else:
                dev, name = default_device()
                client = _AudioClient(dev.activate(IID_IAudioClient))
                device_id = dev.get_id()
                mix = client.get_mix_format()
                bufs = (0, 10_000_000)

            try:
                info = _parse_mix_format(mix)
                last_err = None
                for buf in bufs:
                    try:
                        client.initialize(mix, AUDCLNT_STREAMFLAGS_LOOPBACK, buffer_hns=buf)
                        last_err = None
                        break
                    except OSError as e:            # noqa: PERF203
                        last_err = e
                if last_err is not None:
                    raise last_err
            finally:
                ole32.CoTaskMemFree(c_void_p(mix))

            capture = _CaptureClient(client.get_service(IID_IAudioCaptureClient))
            client.start()
            started = True

            ch, rate, kind, bits = info["channels"], info["rate"], info["kind"], info["bits"]
            block = info["block"] or max(1, ch * max(1, bits // 8))
            with self.lock:
                self.ok = True
                self.error = ""
                self.device = name
                self.device_id = device_id
                self.mode = "process" if (lock or fixed_pid) else "system"
                self.target = lock or (f"pid {pid}" if fixed_pid else "全部系统声音")
                self.rate = rate
                self.channels = ch
                self.format = f"{kind}{bits} ×{ch}ch @{rate}"
                self.last_update = time.time()

            next_pub = time.time()
            next_check = time.time() + 1.0

            while not self._stop.is_set():
                packets = capture.next_packet()
                if packets:
                    for _ in range(8):               # 一次最多消化 8 包，别饿着
                        if not packets:
                            break
                        ptr, frames, flags = capture.buffer()
                        try:
                            if flags & 0x2 or not ptr:   # AUDCLNT_BUFFERFLAGS_SILENT
                                self._push([0.0] * frames)
                            else:
                                self._push(self._decode(ptr, frames, ch, block, kind, bits))
                        finally:
                            capture.release_buffer(frames)
                        packets = capture.next_packet()
                else:
                    time.sleep(0.008)

                if self._reopen.is_set():            # 面板换了锁定目标
                    return

                if pid:
                    now_chk = time.time()
                    if now_chk >= next_check:        # 每秒看一次目标还在不在
                        next_check = now_chk + 1.0
                        if not process_alive(pid):
                            raise RuntimeError(f"{lock} 已退出，等它回来")

                now = time.time()
                interval = 1.0 / max(1, self.fps)   # 每拍读一次，面板改帧率即时生效
                if now >= next_pub:
                    self._publish(now)
                    next_pub += interval
                    if next_pub <= now:               # 落后太多就重新对齐，不补帧
                        next_pub = now + interval
        finally:
            if started and client is not None:
                client.stop()
            if capture is not None:
                capture.release()
            if client is not None:
                client.release()
            if dev is not None:
                dev.release()

    def _wait_for_target(self, exe: str) -> None:
        """锁定的程序还没启动：推静音帧，每 0.5 秒看一次它有没有起来"""
        with self.lock:
            self.ok = True
            self.error = ""
            self.device = f"等待 {exe}"
            self.device_id = ""
            self.mode = "process"
            self.target = f"{exe}（未运行）"
            self.rate = 48000
            self.channels = 1
            self.format = "silence"
            self.last_update = time.time()

        next_pub = time.time()
        next_check = time.time() + 0.5
        while not self._stop.is_set() and not self._reopen.is_set():
            now = time.time()
            if now >= next_pub:
                self._publish(now)
                next_pub += 1.0 / max(1, self.fps)
                continue
            if now >= next_check:
                next_check = now + 0.5
                if find_root_pid(exe):
                    return
            time.sleep(0.01)

    # ---------- 采样转换 ----------
    def _push(self, samples) -> None:
        if not samples:
            return
        self._audio_at = time.time()
        self._buf.extend(samples)
        extra = len(self._buf) - self.frame * 2
        if extra > 0:
            del self._buf[:extra]

    @staticmethod
    def _decode(ptr: int, frames: int, ch: int, block: int, kind: str, bits: int) -> array.array:
        """取第 0 声道（切片是 C 速度，不用逐样本循环）"""
        if kind == "float" and bits == 32:
            typ, scale = "f", 1.0
        elif kind == "pcm" and bits == 16:
            typ, scale = "h", 1.0 / 32768.0
        elif kind == "pcm" and bits == 32:
            typ, scale = "i", 1.0 / 2147483648.0
        else:
            raise RuntimeError(f"不支持的混音格式（{kind} {bits}bit）")

        raw = ctypes.string_at(ptr, frames * block)
        a = array.array(typ)
        a.frombytes(raw[:frames * block])
        mono = a[0::ch] if ch > 1 else a          # 按元素跳采（C 速度）
        if scale != 1.0:
            return array.array("f", (v * scale for v in mono))
        return mono if typ == "f" else array.array("f", mono)

    # ---------- 发布 ----------
    def _publish(self, now: float) -> None:
        with self.lock:
            buf = self._buf
            fresh = (now - self._audio_at) < 0.25      # 没新包就别重放旧声音
            if not fresh:
                tail = array.array("f", [0.0]) * self.frame
                self._buf = array.array("f")          # 恢复播放时别带上一段残留
            elif len(buf) < self.frame:
                tail = array.array("f", [0.0]) * (self.frame - len(buf))
                tail.extend(buf)
            else:
                tail = buf[-self.frame:]
        ints = array.array("h", (int(max(-1.0, min(1.0, v)) * 32767.0) for v in tail))
        data = ints.tobytes()
        peak = max((abs(v) for v in tail), default=0.0)
        rms = math.sqrt(sum(v * v for v in tail) / len(tail)) if tail else 0.0
        with self.lock:
            self._pcm = data
            self.peak = int(min(1.0, peak) * 32767)
            self.rms = round(rms, 4)
            self.seq += 1
            self.last_update = now
            self._cond.notify_all()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="WASAPI 回环 → PCM")
    ap.add_argument("--lock", default="cloudmusic.exe",
                    help="只抓这个进程（含子进程树），默认 cloudmusic.exe；空字符串 = 不锁定")
    ap.add_argument("--all", action="store_true", help="不锁定，抓整机混音")
    _a = ap.parse_args()

    cap = AudioCapture(lock_exe="" if _a.all else _a.lock)
    cap.start()
    print(f"采集中…（{'整机混音' if _a.all else '锁定 ' + _a.lock}）Ctrl+C 结束")
    try:
        while True:
            time.sleep(1.0)
            s = cap.snapshot()
            print("[%s] ok=%s target=%s fmt=%s peak=%5d seq=%d err=%s" % (
                s["mode"], s["ok"], s["target"][:24], s["format"],
                s["peak"], s["seq"], s["error"]))
    except KeyboardInterrupt:
        cap.stop()
        print("\nbye")
