"""
Clipboard helpers.

capture_selected_text() is called from a daemon thread spawned when the user
taps Right-Alt to START recording.  Toggle mode means Right-Alt is already
physically released by then, so no modifier-key conflict.

Windows capture strategy (tried in order):
  1. SendInput Ctrl+C  — works when our app and the target run at the same
                         privilege level (the common case).
  2. UIAutomation      — Microsoft's accessibility API crosses UIPI boundaries,
                         so it works even when the target runs as Administrator.
  3. Empty string      — graceful fallback; pipeline continues without context.
"""

import sys
import time
import threading


# ── Windows: check privilege levels ──────────────────────────────────────────

def _is_process_elevated(pid: int) -> bool:
    """Return True if the given process is running elevated (as Administrator)."""
    import ctypes, ctypes.wintypes as wt
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenElevation = 20

    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not hproc:
        return False
    try:
        htoken = wt.HANDLE()
        if not advapi32.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(htoken)):
            return False
        try:
            elevation = wt.DWORD()
            size = wt.DWORD()
            advapi32.GetTokenInformation(
                htoken, TokenElevation,
                ctypes.byref(elevation), ctypes.sizeof(elevation),
                ctypes.byref(size),
            )
            return bool(elevation.value)
        finally:
            kernel32.CloseHandle(htoken)
    finally:
        kernel32.CloseHandle(hproc)


def _our_process_elevated() -> bool:
    import os
    return _is_process_elevated(os.getpid())


# ── Windows: SendInput Ctrl+C ─────────────────────────────────────────────────

def _sendinput_ctrl_c() -> int:
    """Send Ctrl+C via SendInput.  Returns number of events injected (5 = success)."""
    import ctypes, ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    INPUT_KEYBOARD  = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL, VK_C, VK_RMENU = 0x11, 0x43, 0xA5

    class _KI(ctypes.Structure):
        _fields_ = [("wVk",        wt.WORD),
                    ("wScan",      wt.WORD),
                    ("dwFlags",    wt.DWORD),
                    ("time",       wt.DWORD),
                    # ULONG_PTR — pointer-sized: 4 bytes on 32-bit, 8 on 64-bit.
                    # Using c_size_t ensures correct size & alignment on both.
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _IU(ctypes.Union):
        # _pad must cover the largest union member (MOUSEINPUT = 32 bytes on
        # 64-bit).  Together with type(4) + alignment-pad(4) this gives the
        # correct sizeof(INPUT) = 40 bytes that SendInput checks as cbSize.
        _fields_ = [("ki", _KI), ("_pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_    = [("type", wt.DWORD), ("u", _IU)]

    def _mk(vk, flags=0):
        i = INPUT(); i.type = INPUT_KEYBOARD
        i.ki.wVk = vk; i.ki.dwFlags = flags
        return i

    # Prepend a synthetic Right-Alt key-up to clear the Alt modifier state.
    # The WH_KEYBOARD_LL hook fires on keydown; even when we suppress the event
    # (return 1), the virtual key state (GetKeyState/GetAsyncKeyState) still
    # tracks VK_MENU as "held" until the physical key is released. Target apps
    # check GetKeyState(VK_MENU) while processing WM_KEYDOWN for VK_C, so they
    # see Alt+Ctrl+C instead of Ctrl+C, and ignore it. Injecting the key-up
    # first updates the virtual state so the subsequent Ctrl+C arrives clean.
    events = (_mk(VK_RMENU, KEYEVENTF_KEYUP),
              _mk(VK_CONTROL), _mk(VK_C),
              _mk(VK_C, KEYEVENTF_KEYUP), _mk(VK_CONTROL, KEYEVENTF_KEYUP))
    arr = (INPUT * 5)(*events)
    cb = ctypes.sizeof(INPUT)
    print(f"[clipboard] INPUT cbSize={cb}  (expect 40 on 64-bit Python)")
    return user32.SendInput(5, arr, cb)


# ── Windows: UIAutomation selected-text (works across UIPI) ──────────────────

def _uia_get_selected_text() -> str:
    """
    Use IUIAutomation to read the selected text from the focused element.
    Works even when the target process is elevated (UIAutomation crosses UIPI).
    Returns "" on any failure.
    """
    try:
        # comtypes is a small pure-Python COM bridge (pip install comtypes)
        import comtypes.client  # type: ignore
        import comtypes         # type: ignore

        uia = comtypes.client.CreateObject(
            "{FF48DBA4-60EF-4201-AA87-54103EEF594E}",   # CLSID_CUIAutomation
            interface=comtypes.client.GetModule(         # load UIAutomationClient
                "UIAutomationCore.dll"
            ),
        )
    except Exception:
        pass

    # Simpler fallback: use the UIAutomationCore IUIAutomation COM object
    # via ctypes without comtypes type-library generation.
    try:
        import ctypes, ctypes.wintypes as wt

        ole32 = ctypes.windll.ole32

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.ULONG), ("Data2", wt.USHORT),
                        ("Data3", wt.USHORT), ("Data4", ctypes.c_ubyte * 8)]

        def _guid(s):
            g = GUID()
            p = s.strip("{}")
            parts = p.split("-")
            g.Data1 = int(parts[0], 16)
            g.Data2 = int(parts[1], 16)
            g.Data3 = int(parts[2], 16)
            tail = bytes.fromhex(parts[3] + parts[4])
            for i, b in enumerate(tail):
                g.Data4[i] = b
            return g

        CLSID_UIA = _guid("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
        IID_UIA   = _guid("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")
        CLSCTX_INPROC = 1

        # CoInitialize for this thread
        ole32.CoInitialize(None)

        ppv = ctypes.c_void_p()
        hr  = ole32.CoCreateInstance(
            ctypes.byref(CLSID_UIA), None, CLSCTX_INPROC,
            ctypes.byref(IID_UIA),   ctypes.byref(ppv),
        )
        if hr != 0 or not ppv:
            print(f"[clipboard] UIA CoCreateInstance hr={hr:#010x}")
            return ""

        # vtable layout of IUIAutomation (inherits IUnknown: QI/AddRef/Release)
        # Index 3 = GetRootElement, Index 5 = GetFocusedElement
        # We need GetFocusedElement (index 8 in IUIAutomation)
        # Ref: https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomation
        # IUnknown: 0=QI, 1=AddRef, 2=Release
        # IUIAutomation starts at 3; GetFocusedElement is at offset 8
        vt = ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))
        vtbl = ctypes.cast(vt[0], ctypes.POINTER(ctypes.c_void_p))

        # GetFocusedElement(IUIAutomation* self, IUIAutomationElement** el) → HRESULT
        GET_FOCUSED = 8
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p))
        get_focused = proto(vtbl[GET_FOCUSED])
        el = ctypes.c_void_p()
        hr = get_focused(ppv, ctypes.byref(el))
        if hr != 0 or not el:
            print(f"[clipboard] UIA GetFocusedElement hr={hr:#010x}")
            return ""

        # GetCurrentPattern(element, UIA_TextPatternId=10014, ppPattern)
        UIA_TextPatternId = 10014
        el_vt   = ctypes.cast(el, ctypes.POINTER(ctypes.c_void_p))
        el_vtbl = ctypes.cast(el_vt[0], ctypes.POINTER(ctypes.c_void_p))
        # IUIAutomationElement vtable (after 3 IUnknown slots):
        # 3=SetFocus 4=GetRuntimeId 5=FindFirst 6=FindAll 7=FindFirstBuildCache
        # 8=FindAllBuildCache 9=BuildUpdatedCache 10=GetCurrentPropertyValue
        # 11=GetCurrentPropertyValueEx 12=GetCachedPropertyValue
        # 13=GetCachedPropertyValueEx 14=GetCurrentPatternAs
        # 15=GetCachedPatternAs 16=GetCurrentPattern  ← correct index
        GET_PATTERN = 16
        proto2 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                     ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        get_pattern = proto2(el_vtbl[GET_PATTERN])
        pat = ctypes.c_void_p()
        hr = get_pattern(el, UIA_TextPatternId, ctypes.byref(pat))
        if hr != 0 or not pat:
            print(f"[clipboard] UIA GetCurrentPattern hr={hr:#010x} (no TextPattern)")
            return ""

        # ITextPattern::GetSelection → IUIAutomationTextRangeArray*
        pat_vt   = ctypes.cast(pat, ctypes.POINTER(ctypes.c_void_p))
        pat_vtbl = ctypes.cast(pat_vt[0], ctypes.POINTER(ctypes.c_void_p))
        # ITextPattern: 0=QI,1=AddRef,2=Release, 3=GetSelection
        GET_SEL = 3
        proto3 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_void_p))
        get_sel = proto3(pat_vtbl[GET_SEL])
        ranges = ctypes.c_void_p()
        hr = get_sel(pat, ctypes.byref(ranges))
        if hr != 0 or not ranges:
            print(f"[clipboard] UIA GetSelection hr={hr:#010x}")
            return ""

        # IUIAutomationTextRangeArray::get_Length → int
        ra_vt   = ctypes.cast(ranges, ctypes.POINTER(ctypes.c_void_p))
        ra_vtbl = ctypes.cast(ra_vt[0], ctypes.POINTER(ctypes.c_void_p))
        # 0=QI,1=AddRef,2=Release, 3=get_Length, 4=GetElement
        GET_LEN = 3
        proto4 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_int))
        get_len = proto4(ra_vtbl[GET_LEN])
        length  = ctypes.c_int(0)
        get_len(ranges, ctypes.byref(length))
        if length.value == 0:
            return ""

        # GetElement(0) → IUIAutomationTextRange*
        GET_EL = 4
        proto5 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_void_p))
        get_el = proto5(ra_vtbl[GET_EL])
        rng = ctypes.c_void_p()
        get_el(ranges, 0, ctypes.byref(rng))
        if not rng:
            return ""

        # IUIAutomationTextRange::GetText(-1) → BSTR
        rng_vt   = ctypes.cast(rng, ctypes.POINTER(ctypes.c_void_p))
        rng_vtbl = ctypes.cast(rng_vt[0], ctypes.POINTER(ctypes.c_void_p))
        # IUIAutomationTextRange vtable (after 3 IUnknown slots):
        # 3=Clone 4=Compare 5=CompareEndpoints 6=ExpandToEnclosingUnit
        # 7=FindAttribute 8=FindText 9=GetAttributeValue
        # 10=GetBoundingRectangles 11=GetEnclosingElement 12=GetText  ← correct
        GET_TEXT = 12
        proto6 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                     ctypes.c_int, ctypes.POINTER(ctypes.c_wchar_p))
        get_text = proto6(rng_vtbl[GET_TEXT])
        bstr = ctypes.c_wchar_p()
        hr = get_text(rng, -1, ctypes.byref(bstr))
        if hr == 0 and bstr.value:
            result = bstr.value
            print(f"[clipboard] UIA selected text: {repr(result[:60])}")
            return result

        return ""

    except Exception as exc:
        print(f"[clipboard] UIA failed: {exc}")
        return ""


# ── Scintilla direct read ─────────────────────────────────────────────────────

def _sci_get_selected_text(hwnd_sci) -> str:
    """
    Read selected text directly from a Scintilla control via SCI_GETSELTEXT.
    Uses VirtualAllocEx/ReadProcessMemory for safe cross-process access.
    Bypasses clipboard entirely — works regardless of privilege or focus state.
    """
    import ctypes, ctypes.wintypes as wt
    user32   = ctypes.WinDLL("user32",   use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    SCI_GETSELTEXT = 2161

    # Length query: lParam=0 → returns byte count including null terminator.
    # This is a simple integer return — safe to call without a buffer.
    length = user32.SendMessageW(hwnd_sci, SCI_GETSELTEXT, 0, 0)
    print(f"[clipboard] Scintilla SCI_GETSELTEXT length={length}")
    if length <= 1:
        return ""   # 1 = just the null terminator = no selection

    # SCI_GETSELTEXT with a pointer writes into the TARGET process's address
    # space — passing our own pointer crashes Notepad++.  Allocate the buffer
    # inside Notepad++'s process, let Scintilla write there, then read it back.
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd_sci, ctypes.byref(pid))

    PROCESS_VM_OPERATION = 0x0008
    PROCESS_VM_READ      = 0x0010
    hproc = kernel32.OpenProcess(
        PROCESS_VM_OPERATION | PROCESS_VM_READ, False, pid.value)
    if not hproc:
        print(f"[clipboard] OpenProcess failed err={ctypes.get_last_error()}")
        return ""
    try:
        # Must declare restype/argtypes so 64-bit pointers are not truncated
        # to 32-bit (ctypes default is c_int).  Truncated addresses cause
        # Scintilla to write to wrong memory → Notepad++ crash.
        kernel32.VirtualAllocEx.restype  = ctypes.c_void_p
        kernel32.VirtualAllocEx.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, wt.DWORD, wt.DWORD]
        kernel32.VirtualFreeEx.restype   = wt.BOOL
        kernel32.VirtualFreeEx.argtypes  = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
        kernel32.ReadProcessMemory.restype  = wt.BOOL
        kernel32.ReadProcessMemory.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

        MEM_COMMIT_RESERVE = 0x3000
        PAGE_READWRITE     = 0x04
        remote = kernel32.VirtualAllocEx(
            hproc, None, length + 1, MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not remote:
            print(f"[clipboard] VirtualAllocEx failed err={ctypes.get_last_error()}")
            return ""
        try:
            # lParam must be pointer-sized; declare SendMessageA argtypes here
            # to prevent 64-bit address truncation on the way to Scintilla.
            user32.SendMessageA.restype  = ctypes.c_ssize_t
            user32.SendMessageA.argtypes = [
                wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_void_p]
            user32.SendMessageA(hwnd_sci, SCI_GETSELTEXT, 0,
                                ctypes.c_void_p(remote))
            local = ctypes.create_string_buffer(length + 1)
            read  = ctypes.c_size_t(0)
            kernel32.ReadProcessMemory(
                hproc, ctypes.c_void_p(remote),
                local, length + 1, ctypes.byref(read))
            raw = local.raw[:length - 1]   # strip null terminator
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("latin-1")
        finally:
            kernel32.VirtualFreeEx(hproc, ctypes.c_void_p(remote), 0, 0x8000)
    finally:
        kernel32.CloseHandle(hproc)


# ── WM_COPY / focused-control helper ─────────────────────────────────────────

def _wm_copy(hwnd_foreground) -> str:
    """
    Read the selection from the focused child window inside hwnd_foreground.
    For Scintilla: uses SCI_GETSELTEXT (direct, no clipboard).
    For other controls: sends WM_COPY and reads clipboard.
    No keyboard injection — immune to Alt-key state.
    """
    import ctypes, ctypes.wintypes as wt

    user32   = ctypes.WinDLL("user32",   use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize",      wt.DWORD),
                    ("flags",       wt.DWORD),
                    ("hwndActive",  wt.HWND),
                    ("hwndFocus",   wt.HWND),
                    ("hwndCapture", wt.HWND),
                    ("hwndMenuOwner", wt.HWND),
                    ("hwndMoveSize",  wt.HWND),
                    ("hwndCaret",   wt.HWND),
                    ("rcCaret",     wt.RECT)]

    tid = user32.GetWindowThreadProcessId(hwnd_foreground, None)
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    hwnd_target = hwnd_foreground
    if user32.GetGUIThreadInfo(tid, ctypes.byref(gti)) and gti.hwndFocus:
        hwnd_target = gti.hwndFocus

    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd_target, cls, 64)
    print(f"[clipboard] focused control: hwnd={hwnd_target}  class={repr(cls.value)}")

    # Scintilla: read directly via SCI_GETSELTEXT (most reliable)
    if "scintilla" in cls.value.lower():
        result = _sci_get_selected_text(hwnd_target)
        if result:
            print(f"[clipboard] Scintilla direct: {repr(result[:60])}")
        return result

    # Other controls: WM_COPY → clipboard
    user32.OpenClipboard(None)
    user32.EmptyClipboard()
    user32.CloseClipboard()

    WM_COPY = 0x0301
    user32.SendMessageW(hwnd_target, WM_COPY, 0, 0)
    time.sleep(0.05)

    CF_UNICODETEXT = 13
    result = ""
    if user32.OpenClipboard(None):
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if handle:
                ptr = kernel32.GlobalLock(handle)
                if ptr:
                    try:
                        result = ctypes.wstring_at(ptr)
                    finally:
                        kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    if result:
        print(f"[clipboard] WM_COPY result: {repr(result[:60])}")
    else:
        print("[clipboard] WM_COPY: clipboard empty after send")
    return result


# ── Windows main capture ──────────────────────────────────────────────────────

def _capture_win32() -> str:
    import ctypes, ctypes.wintypes as wt, os

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # Log foreground window
    hwnd = user32.GetForegroundWindow()
    buf  = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    print(f"[clipboard] foreground: hwnd={hwnd}  title={repr(buf.value[:60])}")

    # Detect UIPI: get the PID of the foreground window
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    target_elevated = False
    try:
        target_elevated = _is_process_elevated(pid.value)
    except Exception:
        pass
    we_elevated = _our_process_elevated()
    print(f"[clipboard] privilege: ours={'admin' if we_elevated else 'user'}  "
          f"target={'admin' if target_elevated else 'user'}")

    # ── Strategy 1: WM_COPY (no keyboard injection, works for most controls) ──
    result = _wm_copy(hwnd)
    if result:
        return result

    # ── Strategy 2: SendInput Ctrl+C (same or higher privilege required) ──────
    if not target_elevated or we_elevated:
        # Verify the target window still has foreground focus before injecting.
        # The recording overlay (shown via record_started signal on Qt main
        # thread) may have appeared between clipboard-capture-start and here,
        # potentially deactivating the target and clearing its selection.
        hwnd_now = user32.GetForegroundWindow()
        if hwnd_now != hwnd:
            buf2 = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd_now, buf2, 256)
            print(f"[clipboard] foreground changed → {repr(buf2.value[:50])}; "
                  f"skipping SendInput")
            return ""

        # Clear clipboard
        user32.OpenClipboard(None)
        user32.EmptyClipboard()
        user32.CloseClipboard()
        time.sleep(0.05)

        sent = _sendinput_ctrl_c()
        print(f"[clipboard] SendInput sent={sent}/5")

        # Poll the clipboard instead of a single fixed sleep.
        # Chromium-based apps (Chrome, VSCode, Electron) write to the clipboard
        # asynchronously after the Renderer process handles the key event via
        # IPC — a single 300 ms sleep can race with that write.
        CF_UNICODETEXT = 13
        result = ""
        for _ in range(10):           # up to ~600 ms total (10 × 60 ms)
            time.sleep(0.06)
            if user32.OpenClipboard(None):
                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if handle:
                        ptr = ctypes.windll.kernel32.GlobalLock(handle)
                        if ptr:
                            try:
                                result = ctypes.wstring_at(ptr)
                            finally:
                                ctypes.windll.kernel32.GlobalUnlock(handle)
                finally:
                    user32.CloseClipboard()
            if result:
                break

        if result:
            print(f"[clipboard] SendInput result: {repr(result[:60])}")
            return result
        print("[clipboard] SendInput: clipboard empty, trying UIAutomation…")

    # ── Strategy 3: UIAutomation (crosses UIPI — works with elevated targets) ─
    print("[clipboard] trying UIAutomation (UIPI bypass)…")
    result = _uia_get_selected_text()
    if result:
        return result

    # ── Fallback ──────────────────────────────────────────────────────────────
    if target_elevated and not we_elevated:
        print("[clipboard] UIPI: target is elevated, we are not.\n"
              "  → 请以管理员身份运行简单助手，或关闭目标程序的管理员权限。")
    else:
        print("[clipboard] could not capture selected text")
    return ""


# ── Non-Windows fallback ──────────────────────────────────────────────────────

def _capture_pynput() -> str:
    try:
        import pyperclip
        from pynput.keyboard import Controller, Key

        pyperclip.copy("")
        time.sleep(0.06)
        ctrl = Controller()
        ctrl.press(Key.ctrl); ctrl.press("c")
        ctrl.release("c");    ctrl.release(Key.ctrl)
        time.sleep(0.25)
        result = pyperclip.paste() or ""
        print(f"[clipboard] pynput result: {repr(result[:60])}")
        return result
    except Exception as e:
        print(f"[clipboard] _capture_pynput failed: {e}")
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

def capture_selected_text() -> str:
    if sys.platform == "win32":
        return _capture_win32()
    return _capture_pynput()


def set_clipboard(text: str):
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception as e:
        print(f"[clipboard_util] set_clipboard failed: {e}")


def simulate_paste(delay_ms: int = 120):
    def _do():
        time.sleep(delay_ms / 1000)
        try:
            from pynput.keyboard import Controller, Key
            ctrl = Controller()
            ctrl.press(Key.ctrl); ctrl.press("v")
            ctrl.release("v");    ctrl.release(Key.ctrl)
        except Exception as e:
            print(f"[clipboard_util] simulate_paste failed: {e}")
    threading.Thread(target=_do, daemon=True).start()
