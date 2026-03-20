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
import subprocess
import base64


# ── Windows: modifier-state helpers ──────────────────────────────────────────

def _wait_for_right_alt_release(timeout_ms: int = 200) -> None:
    """Wait briefly for the physical Right-Alt key to be released."""
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    VK_RMENU = 0xA5
    deadline = time.perf_counter() + (timeout_ms / 1000.0)

    while time.perf_counter() < deadline:
        if not (user32.GetAsyncKeyState(VK_RMENU) & 0x8000):
            print("[clipboard] Right-Alt released before SendInput")
            break
        time.sleep(0.01)
    else:
        print("[clipboard] Right-Alt still pressed at SendInput timeout")


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


def _get_process_image_name(pid: int) -> str:
    import ctypes, ctypes.wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not hproc:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
            path = buf.value
            return path.rsplit("\\", 1)[-1]

        # Fallback for older/odd cases.
        if psapi.GetModuleBaseNameW(hproc, None, buf, len(buf)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(hproc)


# ── Windows: SendInput Ctrl+C ─────────────────────────────────────────────────

def _sendinput_ctrl_c() -> int:
    """Send Ctrl+C via SendInput.  Returns number of events injected (5 = success)."""
    import ctypes, ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    INPUT_KEYBOARD  = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL, VK_C = 0x11, 0x43
    VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
    VK_MENU, VK_LMENU, VK_RMENU = 0x12, 0xA4, 0xA5
    VK_SHIFT, VK_LSHIFT, VK_RSHIFT = 0x10, 0xA0, 0xA1

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

    _wait_for_right_alt_release()

    # Right Alt can behave as AltGr (Ctrl+Alt) on some layouts. Before sending
    # Ctrl+C, force-release all common modifier virtual keys so Chromium-based
    # apps do not interpret the sequence as AltGr+C / Alt+Ctrl+C.
    release_modifiers = (
        VK_RMENU, VK_MENU, VK_LMENU,
        VK_RCONTROL, VK_LCONTROL, VK_CONTROL,
        VK_RSHIFT, VK_LSHIFT, VK_SHIFT,
    )
    events = tuple(_mk(vk, KEYEVENTF_KEYUP) for vk in release_modifiers) + (
        _mk(VK_CONTROL), _mk(VK_C),
        _mk(VK_C, KEYEVENTF_KEYUP), _mk(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    arr = (INPUT * len(events))(*events)
    cb = ctypes.sizeof(INPUT)
    print(f"[clipboard] INPUT cbSize={cb}  (expect 40 on 64-bit Python)")
    return user32.SendInput(len(events), arr, cb)


def _pynput_ctrl_c() -> bool:
    try:
        from pynput.keyboard import Controller, Key

        ctrl = Controller()
        ctrl.press(Key.ctrl)
        ctrl.press("c")
        time.sleep(0.05)
        ctrl.release("c")
        ctrl.release(Key.ctrl)
        return True
    except Exception as exc:
        print(f"[clipboard] pynput Ctrl+C failed: {exc}")
        return False


def _pynput_ctrl_insert() -> bool:
    try:
        from pynput.keyboard import Controller, Key

        ctrl = Controller()
        ctrl.press(Key.ctrl)
        ctrl.press(Key.insert)
        time.sleep(0.05)
        ctrl.release(Key.insert)
        ctrl.release(Key.ctrl)
        return True
    except Exception as exc:
        print(f"[clipboard] pynput Ctrl+Insert failed: {exc}")
        return False


def _sendinput_apps_copy() -> int:
    """Open the context menu via Apps key and trigger Copy via accelerator."""
    import ctypes, ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    INPUT_KEYBOARD  = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_APPS, VK_C = 0x5D, 0x43

    class _KI(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD),
                    ("wScan", wt.WORD),
                    ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _IU(ctypes.Union):
        _fields_ = [("ki", _KI), ("_pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_    = [("type", wt.DWORD), ("u", _IU)]

    def _mk(vk, flags=0):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.ki.wVk = vk
        i.ki.dwFlags = flags
        return i

    events = (
        _mk(VK_APPS), _mk(VK_APPS, KEYEVENTF_KEYUP),
        _mk(VK_C), _mk(VK_C, KEYEVENTF_KEYUP),
    )
    arr = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def _clipboard_text_once():
    try:
        import pyperclip

        return pyperclip.paste() or ""
    except Exception as exc:
        print(f"[clipboard] pyperclip paste failed: {exc}")

    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    return result


def _poll_clipboard_text(attempts: int = 20, sleep_s: float = 0.06) -> str:
    result = ""
    for _ in range(attempts):
        time.sleep(sleep_s)
        result = _clipboard_text_once()
        if result:
            return result
    return ""


def _get_focused_target(hwnd_foreground):
    import ctypes, ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)

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
    return hwnd_target, cls.value


def _send_appcommand_copy(hwnd_target) -> bool:
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WM_APPCOMMAND = 0x0319
    APPCOMMAND_COPY = 36
    lparam = APPCOMMAND_COPY << 16
    return bool(user32.SendMessageW(hwnd_target, WM_APPCOMMAND, hwnd_target, lparam))


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
            return _uia_get_selected_text_powershell()

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
            return _uia_get_selected_text_powershell()

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
            return _uia_get_selected_text_powershell()

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
            return _uia_get_selected_text_powershell()

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
            return _uia_get_selected_text_powershell()

        # GetElement(0) → IUIAutomationTextRange*
        GET_EL = 4
        proto5 = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_void_p))
        get_el = proto5(ra_vtbl[GET_EL])
        rng = ctypes.c_void_p()
        get_el(ranges, 0, ctypes.byref(rng))
        if not rng:
            return _uia_get_selected_text_powershell()

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

        return _uia_get_selected_text_powershell()

    except Exception as exc:
        print(f"[clipboard] UIA failed: {exc}")
        return _uia_get_selected_text_powershell()


def _uia_get_selected_text_powershell() -> str:
    """
    Fallback UIAutomation probe via PowerShell/.NET.
    Chromium often exposes the useful TextPattern on a descendant rather than
    directly on the focused element, so this path breadth-first walks a small
    UIA subtree and returns the first non-empty selection it finds.
    """
    script = r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$trueCond = [System.Windows.Automation.Condition]::TrueCondition
$textPatternId = [System.Windows.Automation.TextPattern]::Pattern
$treeScopeChildren = [System.Windows.Automation.TreeScope]::Children

function Get-SelectionText([System.Windows.Automation.AutomationElement]$root) {
    if ($null -eq $root) { return $null }

    $queue = New-Object 'System.Collections.Generic.Queue[System.Windows.Automation.AutomationElement]'
    $queue.Enqueue($root)
    $visited = 0

    while ($queue.Count -gt 0 -and $visited -lt 200) {
        $visited += 1
        $el = $queue.Dequeue()
        if ($null -eq $el) { continue }

        $pattern = $null
        if ($el.TryGetCurrentPattern($textPatternId, [ref]$pattern) -and $null -ne $pattern) {
            try {
                $ranges = $pattern.GetSelection()
                if ($null -ne $ranges -and $ranges.Length -gt 0) {
                    $text = $ranges[0].GetText(-1)
                    if (-not [string]::IsNullOrWhiteSpace($text)) {
                        return $text
                    }
                }
            } catch {}
        }

        try {
            $children = $el.FindAll($treeScopeChildren, $trueCond)
            for ($i = 0; $i -lt $children.Count; $i++) {
                $child = $children.Item($i)
                if ($null -ne $child) {
                    $queue.Enqueue($child)
                }
            }
        } catch {}
    }

    return $null
}

$focused = [System.Windows.Automation.AutomationElement]::FocusedElement
$result = Get-SelectionText $focused

if ([string]::IsNullOrWhiteSpace($result)) {
    try {
        $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
        $parent = $walker.GetParent($focused)
        $result = Get-SelectionText $parent
    } catch {}
}

if (-not [string]::IsNullOrWhiteSpace($result)) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::Write($result)
}
"""
    try:
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.5,
            creationflags=creationflags,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if stderr:
                print(f"[clipboard] PowerShell UIA failed: {stderr[:200]}")
            return ""
        result = (proc.stdout or "").strip()
        if result:
            print(f"[clipboard] PowerShell UIA selected text: {repr(result[:60])}")
        return result
    except Exception as exc:
        print(f"[clipboard] PowerShell UIA exception: {exc}")
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


def _std_edit_get_selected_text(hwnd_target) -> str:
    """
    Read selected text from common Win32 text controls without using clipboard.
    Covers Edit / RichEdit family controls used by many editors and chat apps.
    """
    import ctypes, ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    WM_GETTEXT       = 0x000D
    WM_GETTEXTLENGTH = 0x000E
    EM_GETSEL        = 0x00B0

    length = user32.SendMessageW(hwnd_target, WM_GETTEXTLENGTH, 0, 0)
    if length <= 0:
        return ""

    start = wt.DWORD(0)
    end = wt.DWORD(0)
    user32.SendMessageW(hwnd_target, EM_GETSEL, ctypes.byref(start), ctypes.byref(end))
    if end.value <= start.value:
        return ""

    buf = ctypes.create_unicode_buffer(length + 1)
    copied = user32.SendMessageW(hwnd_target, WM_GETTEXT, length + 1, buf)
    if copied <= 0:
        return ""

    text = buf.value
    if start.value >= len(text):
        return ""
    return text[start.value:end.value]


def _sendinput_ctrl_insert() -> int:
    """Fallback copy accelerator used by some editors and chat controls."""
    import ctypes, ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    INPUT_KEYBOARD  = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL, VK_INSERT = 0x11, 0x2D

    class _KI(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD),
                    ("wScan", wt.WORD),
                    ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _IU(ctypes.Union):
        _fields_ = [("ki", _KI), ("_pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_    = [("type", wt.DWORD), ("u", _IU)]

    def _mk(vk, flags=0):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.ki.wVk = vk
        i.ki.dwFlags = flags
        return i

    events = (
        _mk(VK_CONTROL), _mk(VK_INSERT),
        _mk(VK_INSERT, KEYEVENTF_KEYUP), _mk(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    arr = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def _is_standard_text_control(class_name: str) -> bool:
    cls = (class_name or "").lower()
    return (
        cls == "edit"
        or cls.startswith("richedit")
        or cls == "richeditd2dpt"
        or cls == "richedit20w"
        or cls == "richedit20a"
        or cls == "richedit50w"
    )


# ── WM_COPY / focused-control helper ─────────────────────────────────────────

def _wm_copy(hwnd_foreground) -> str:
    """
    Read the selection from the focused child window inside hwnd_foreground.
    For Scintilla: uses SCI_GETSELTEXT (direct, no clipboard).
    For other controls: sends WM_COPY and reads clipboard.
    No keyboard injection — immune to Alt-key state.
    """
    import ctypes

    user32   = ctypes.WinDLL("user32",   use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hwnd_target, class_name = _get_focused_target(hwnd_foreground)
    print(f"[clipboard] focused control: hwnd={hwnd_target}  class={repr(class_name)}")

    result = _direct_control_capture(hwnd_target, class_name)
    if result:
        return result

    # Other controls: WM_COPY → clipboard
    user32.OpenClipboard(None)
    user32.EmptyClipboard()
    user32.CloseClipboard()

    WM_COPY = 0x0301
    user32.SendMessageW(hwnd_target, WM_COPY, 0, 0)
    time.sleep(0.05)

    result = _clipboard_text_once()

    if result:
        print(f"[clipboard] WM_COPY result: {repr(result[:60])}")
    else:
        print("[clipboard] WM_COPY: clipboard empty after send")
    return result


def _direct_control_capture(hwnd_target, class_name: str) -> str:
    # Scintilla: read directly via SCI_GETSELTEXT (most reliable)
    if "scintilla" in class_name.lower():
        result = _sci_get_selected_text(hwnd_target)
        if result:
            print(f"[clipboard] Scintilla direct: {repr(result[:60])}")
        return result

    # Standard Edit / RichEdit family: read text and selection directly.
    if _is_standard_text_control(class_name):
        result = _std_edit_get_selected_text(hwnd_target)
        if result:
            print(f"[clipboard] Text-control direct: {repr(result[:60])}")
        return result

    return ""


def _is_chromium_window_class(class_name: str) -> bool:
    cls = (class_name or "").lower()
    return cls.startswith("chrome_widgetwin") or "renderwidgethost" in cls


def _is_chat_or_office_process(process_name: str) -> bool:
    name = (process_name or "").lower()
    return name in {
        "weixin.exe",
        "wechat.exe",
        "qq.exe",
        "qqnt.exe",
        "tim.exe",
        "wxwork.exe",
        "dingtalk.exe",
        "winword.exe",
        "wps.exe",
        "wpp.exe",
        "et.exe",
        "wpspdf.exe",
    }


# ── Windows main capture ──────────────────────────────────────────────────────

def _capture_win32() -> str:
    import ctypes, ctypes.wintypes as wt, os

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # Log foreground window
    hwnd = user32.GetForegroundWindow()
    buf  = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    print(f"[clipboard] foreground: hwnd={hwnd}  title={repr(buf.value[:60])}")
    cls_buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls_buf, 64)
    foreground_class = cls_buf.value

    # Detect UIPI: get the PID of the foreground window
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_name = _get_process_image_name(pid.value)
    target_elevated = False
    try:
        target_elevated = _is_process_elevated(pid.value)
    except Exception:
        pass
    we_elevated = _our_process_elevated()
    if process_name:
        print(f"[clipboard] process: {process_name}")
    print(f"[clipboard] privilege: ours={'admin' if we_elevated else 'user'}  "
          f"target={'admin' if target_elevated else 'user'}")

    hwnd_target, focused_class = _get_focused_target(hwnd)
    print(f"[clipboard] focused control: hwnd={hwnd_target}  class={repr(focused_class)}")

    # ── Strategy 1: direct control reads for known controls ──────────────────
    result = _direct_control_capture(hwnd_target, focused_class)
    if result:
        return result

    # ── Strategy 2: simulated Ctrl+C (same or higher privilege required) ──────
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

        try:
            import pyperclip
            pyperclip.copy("")
        except Exception:
            user32.OpenClipboard(None)
            user32.EmptyClipboard()
            user32.CloseClipboard()
        time.sleep(0.05)

        sent = _pynput_ctrl_c()
        print(f"[clipboard] pynput Ctrl+C sent={sent}")
        result = _poll_clipboard_text()
        if result:
            print(f"[clipboard] pynput Ctrl+C result: {repr(result[:60])}")
            return result
        print("[clipboard] pynput Ctrl+C: clipboard empty")

        # For custom-rendered windows (Qt/Chromium/Electron), match the
        # standalone test as closely as possible before trying WM_COPY-style
        # fallbacks. Sending WM_COPY to the top-level host window can be a no-op
        # or disturb the internal selection routing.
        if not _is_standard_text_control(focused_class) and "scintilla" not in focused_class.lower():
            print("[clipboard] custom-rendered control; skipping early WM_COPY path")
        else:
            result = _wm_copy(hwnd)
            if result:
                return result

        sent = _sendinput_ctrl_c()
        print(f"[clipboard] SendInput sent={sent} events")
        result = _poll_clipboard_text()
        if result:
            print(f"[clipboard] SendInput result: {repr(result[:60])}")
            return result
        print("[clipboard] SendInput: clipboard empty")

        if _is_chat_or_office_process(process_name):
            print("[clipboard] Chat/Office process detected; trying Ctrl+Insert…")
            sent = _pynput_ctrl_insert()
            print(f"[clipboard] pynput Ctrl+Insert sent={sent}")
            result = _poll_clipboard_text(attempts=20, sleep_s=0.06)
            if result:
                print(f"[clipboard] pynput Ctrl+Insert result: {repr(result[:60])}")
                return result
            print("[clipboard] pynput Ctrl+Insert: clipboard empty")

            sent = _sendinput_ctrl_insert()
            print(f"[clipboard] Ctrl+Insert copy sent={sent} events")
            result = _poll_clipboard_text(attempts=20, sleep_s=0.06)
            if result:
                print(f"[clipboard] Ctrl+Insert result: {repr(result[:60])}")
                return result
            print("[clipboard] Ctrl+Insert: clipboard empty")

            print("[clipboard] trying WM_APPCOMMAND copy…")
            ok = _send_appcommand_copy(hwnd_target)
            print(f"[clipboard] WM_APPCOMMAND copy returned={ok}")
            result = _poll_clipboard_text(attempts=20, sleep_s=0.06)
            if result:
                print(f"[clipboard] WM_APPCOMMAND result: {repr(result[:60])}")
                return result
            print("[clipboard] WM_APPCOMMAND: clipboard empty")

        if _is_chromium_window_class(foreground_class):
            print("[clipboard] Chromium window detected; trying context-menu copy…")
            sent = _sendinput_apps_copy()
            print(f"[clipboard] Apps-key copy sent={sent} events")
            result = _poll_clipboard_text(attempts=25, sleep_s=0.08)
            if result:
                print(f"[clipboard] Apps-key copy result: {repr(result[:60])}")
                return result
            print("[clipboard] Apps-key copy: clipboard empty, trying UIAutomation…")
        else:
            print("[clipboard] trying UIAutomation…")

    # ── Strategy 3: UIAutomation (crosses UIPI — works with elevated targets) ─
    print("[clipboard] trying UIAutomation (UIPI bypass)…")
    result = _uia_get_selected_text()
    if result:
        return result

    # ── Fallback ──────────────────────────────────────────────────────────────
    if target_elevated and not we_elevated:
        print("[clipboard] UIPI: target is elevated, we are not.\n"
              "  → 请以管理员身份运行简单助手，或关闭目标程序的管理员权限。")
    elif _is_chromium_window_class(foreground_class):
        print("[clipboard] Chromium page text is not accessible through this path.")
        print("[clipboard] Chrome/Edge often keep web-content accessibility off")
        print("[clipboard] unless a screen reader is detected or renderer")
        print("[clipboard] accessibility is forced on.")
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
