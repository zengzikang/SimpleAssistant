"""
Windows-native global keyboard hook via ctypes (WH_KEYBOARD_LL).

Interaction model: TOGGLE
  First  keydown → on_press()   (start recording)
  Second keydown → on_release() (stop  recording)
Key-up events are ignored.

64-bit notes
------------
* wt.LPARAM / wt.WPARAM are defined as c_long / c_ulong (32-bit) in
  ctypes.wintypes — WRONG on 64-bit Windows where LPARAM/WPARAM are
  pointer-sized.  We use c_ssize_t / c_size_t throughout.
* SetWindowsHookExW for WH_KEYBOARD_LL requires hMod = NULL.
  Passing a module handle produces ERROR_MOD_NOT_FOUND (126).
* The hook thread must have a message queue before SetWindowsHookExW.
  PeekMessage(PM_NOREMOVE) creates it without consuming any messages.
"""

import sys
import ctypes
import ctypes.wintypes as wt
import threading
from typing import Callable, Optional

if sys.platform != "win32":
    raise ImportError("win32_hotkey is Windows-only")

# use_last_error=True so ctypes.get_last_error() is reliable
_user32   = ctypes.WinDLL("user32",   use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN     = 0x0100
WM_KEYUP       = 0x0101
WM_SYSKEYDOWN  = 0x0104
WM_SYSKEYUP    = 0x0105
PM_NOREMOVE    = 0x0000
VK_RMENU       = 0xA5   # 165 — Right Alt, layout-independent

# Pointer-sized integer types (correct on both 32-bit and 64-bit)
_LRESULT = ctypes.c_ssize_t   # signed pointer-sized  (LRESULT / LPARAM)
_WPARAM  = ctypes.c_size_t    # unsigned pointer-sized (WPARAM)
_LPARAM  = ctypes.c_ssize_t   # signed pointer-sized  (LPARAM)

# Correct function prototype for the low-level keyboard hook callback
_HOOKPROC = ctypes.WINFUNCTYPE(
    _LRESULT,       # return type (LRESULT)
    ctypes.c_int,   # nCode
    _WPARAM,        # wParam
    _LPARAM,        # lParam  ← pointer to KBDLLHOOKSTRUCT; must be ptr-sized
)

# Fix CallNextHookEx argtypes so 64-bit lParam doesn't overflow
_user32.CallNextHookEx.restype  = _LRESULT
_user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,  # hhk
    ctypes.c_int,     # nCode
    _WPARAM,          # wParam
    _LPARAM,          # lParam
]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      wt.DWORD),
        ("scanCode",    wt.DWORD),
        ("flags",       wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR — ptr-sized
    ]


class Win32KeyHook:
    """
    Installs WH_KEYBOARD_LL in a private thread with its own GetMessage loop.
    Fires on_press() / on_release() from that thread (not the Qt main thread).
    Make sure the callbacks are thread-safe (e.g. emit a Qt signal).
    """

    def __init__(self, vk_code: int, on_press: Callable, on_release: Callable):
        self._vk         = vk_code
        self._on_press   = on_press
        self._on_release = on_release
        self._hook: Optional[int] = None
        self._proc       = None          # keep-alive — prevents GC crash
        self._thread: Optional[threading.Thread] = None
        self._ready      = threading.Event()

    def start(self) -> bool:
        self._thread = threading.Thread(
            target=self._run, name="Win32HookThread", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return bool(self._hook)

    def stop(self):
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        if self._thread and self._thread.ident:
            _user32.PostThreadMessageW(self._thread.ident, 0x0012, 0, 0)  # WM_QUIT

    def _run(self):
        # Create the thread message queue before installing the hook.
        msg = wt.MSG()
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)

        recording = False   # toggle state

        LLKHF_INJECTED = 0x10   # set in KBDLLHOOKSTRUCT.flags for SendInput events

        def hook_proc(nCode: int, wParam: int, lParam: int) -> int:
            nonlocal recording
            if nCode >= 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == self._vk:
                    if kb.flags & LLKHF_INJECTED:
                        # Synthetic event injected by SendInput (our own Ctrl+C
                        # sequence starts with a VK_RMENU keyup to flush the Alt
                        # modifier state).  Pass it through so target apps see
                        # GetKeyState(VK_MENU) == "released" before the Ctrl+C
                        # arrives — otherwise they interpret it as Alt+Ctrl+C.
                        pass
                    else:
                        # Physical key — handle toggle and suppress.
                        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                            if not recording:
                                recording = True
                                try:
                                    self._on_press()
                                except Exception as exc:
                                    print(f"[Win32KeyHook] on_press error: {exc}")
                            else:
                                recording = False
                                try:
                                    self._on_release()
                                except Exception as exc:
                                    print(f"[Win32KeyHook] on_release error: {exc}")
                        # Suppress physical VK_RMENU keydown AND keyup:
                        # keydown would activate menus; keyup (Alt release)
                        # also triggers menu activation in many apps.
                        return 1
            return _user32.CallNextHookEx(self._hook or 0, nCode, wParam, lParam)

        self._proc = _HOOKPROC(hook_proc)

        # hMod MUST be NULL for WH_KEYBOARD_LL (MSDN requirement).
        self._hook = _user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, None, 0
        )

        if not self._hook:
            err = ctypes.get_last_error()
            print(f"[Win32KeyHook] SetWindowsHookExW failed (error={err})")
            self._ready.set()
            return

        print(f"[Win32KeyHook] 钩子已安装  vk={self._vk}  hook_id={self._hook}")
        self._ready.set()

        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        print("[Win32KeyHook] 钩子线程已退出")
