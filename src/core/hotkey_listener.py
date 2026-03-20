"""
Global Right-Alt hotkey listener — TOGGLE mode.

First tap  → record_started  (start recording, capture clipboard)
Second tap → record_stopped  (stop recording, trigger pipeline)

Windows : uses Win32KeyHook (ctypes, WH_KEYBOARD_LL).
Other OS: falls back to pynput (toggle on key-press, ignores key-release).

Clipboard capture runs on a separate daemon thread so the hook callback
returns immediately (required by Windows for low-level hooks).
By the time the capture thread simulates Ctrl+C the Right-Alt key has
already been physically released (user only tapped it), so there is no
modifier-key conflict.
"""

import sys
import threading
from PyQt5.QtCore import QObject, pyqtSignal

_RIGHT_ALT_VK = 165   # Windows VK_RMENU — layout-independent


class HotkeyListener(QObject):
    record_started = pyqtSignal()
    record_stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hook         = None   # Win32KeyHook instance
        self._listener     = None   # pynput Listener instance (non-Windows)
        self._recording    = False  # toggle state
        self.captured_text = ""
        self._capture_done = threading.Event()
        self._capture_done.set()

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self) -> bool:
        if sys.platform == "win32":
            return self._start_win32()
        return self._start_pynput()

    def stop(self):
        if self._hook:
            self._hook.stop()
            self._hook = None
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    # ── Windows: ctypes WH_KEYBOARD_LL ───────────────────────────────────────

    def _start_win32(self) -> bool:
        try:
            from src.core.win32_hotkey import Win32KeyHook

            def on_press():
                # Called from hook thread — must return fast
                print(f"[HotkeyListener] START recording")
                self.captured_text = ""
                self._capture_done.clear()
                threading.Thread(
                    target=self._capture_clipboard, daemon=True
                ).start()
                self.record_started.emit()     # Qt queued delivery → safe

            def on_release():
                print(f"[HotkeyListener] STOP recording")
                self.record_stopped.emit()

            self._hook = Win32KeyHook(_RIGHT_ALT_VK, on_press, on_release)
            ok = self._hook.start()
            if ok:
                print("[HotkeyListener] Win32 钩子启动成功（单击切换模式）")
            else:
                print("[HotkeyListener] Win32 钩子启动失败，尝试 pynput…")
                self._hook = None
                return self._start_pynput()
            return ok

        except Exception as exc:
            print(f"[HotkeyListener] Win32 路径出错: {exc}，尝试 pynput…")
            return self._start_pynput()

    # ── Fallback: pynput ──────────────────────────────────────────────────────

    def _start_pynput(self) -> bool:
        try:
            from pynput import keyboard as kb
            from pynput.keyboard import Key

            right_alt_keys: set = set()
            for name in ("alt_r", "alt_gr"):
                try:
                    right_alt_keys.add(getattr(Key, name))
                except AttributeError:
                    pass

            def _is_right_alt(key) -> bool:
                if key in right_alt_keys:
                    return True
                try:
                    if key.vk == _RIGHT_ALT_VK:
                        return True
                except AttributeError:
                    pass
                return str(_RIGHT_ALT_VK) in repr(key)

            def on_press(key):
                try:
                    if not _is_right_alt(key):
                        return
                    if not self._recording:
                        self._recording = True
                        print(f"[HotkeyListener/pynput] START {key!r}")
                        self.captured_text = ""
                        self._capture_done.clear()
                        threading.Thread(
                            target=self._capture_clipboard, daemon=True
                        ).start()
                        self.record_started.emit()
                    else:
                        self._recording = False
                        print(f"[HotkeyListener/pynput] STOP {key!r}")
                        self.record_stopped.emit()
                except Exception as exc:
                    print(f"[HotkeyListener/pynput] on_press: {exc}")

            # key-release is ignored in toggle mode
            self._listener = kb.Listener(on_press=on_press)
            self._listener.daemon = True
            self._listener.start()
            print(f"[HotkeyListener] pynput 启动（单击切换模式），匹配键: {right_alt_keys}")
            return True

        except Exception as exc:
            print(f"[HotkeyListener] pynput 也失败: {exc}")
            return False

    # ── Clipboard capture (separate daemon thread) ────────────────────────────

    def wait_for_capture(self, timeout: float = 0.0) -> bool:
        return self._capture_done.wait(timeout)

    def _capture_clipboard(self):
        try:
            from src.core.clipboard_util import capture_selected_text
            # The standalone test works because copy is triggered after the
            # target app has fully settled. In the main flow we start capture
            # immediately after the global hotkey toggles, which is too early
            # for some Qt/Chromium apps. Give focus/selection a brief moment
            # to stabilize before simulating copy.
            import time
            time.sleep(0.18)
            self.captured_text = capture_selected_text()
            print(f"[HotkeyListener] 剪贴板: {repr(self.captured_text[:60])}")
        except Exception as exc:
            print(f"[HotkeyListener] 剪贴板捕获失败: {exc}")
            self.captured_text = ""
        finally:
            self._capture_done.set()
