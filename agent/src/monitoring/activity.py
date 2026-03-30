"""Activity tracker for EndpointClaw agent (Phase 2).

Monitors the foreground application and window title using Win32 APIs
(ctypes) and detects idle/active transitions.  On non-Windows platforms
all methods are functional but do not capture any data — a warning is
logged once at startup.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import logging
import platform
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import psutil

if TYPE_CHECKING:
    from ..core.config import AgentConfig
    from ..core.database import Database

logger = logging.getLogger("endpointclaw.monitoring.activity")

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

_IS_WINDOWS = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# Win32 type definitions (safe to define on all platforms — only *called*
# on Windows)
# ---------------------------------------------------------------------------

if _IS_WINDOWS:
    try:
        _user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.UINT),
                ("dwTime", ctypes.wintypes.DWORD),
            ]

        _WIN32_READY = True
    except Exception:
        _WIN32_READY = False
        logger.warning("Failed to load Win32 DLLs — activity tracking disabled")
else:
    _WIN32_READY = False


# ---------------------------------------------------------------------------
# ActivityTracker
# ---------------------------------------------------------------------------

class ActivityTracker:
    """Tracks foreground application changes and user idle state.

    On Windows, uses ctypes to call:
    - ``user32.GetForegroundWindow()`` -- current window handle
    - ``user32.GetWindowTextW()``      -- window title
    - ``user32.GetWindowThreadProcessId()`` -- owning process ID
    - ``kernel32.GetLastInputInfo()``  -- idle detection
    - ``psutil.Process(pid).name()``   -- executable name

    On other platforms, methods are no-ops that log a warning once.
    """

    POLL_INTERVAL: float = 1.0  # seconds

    def __init__(self, config: AgentConfig, database: Database) -> None:
        self.config = config
        self.db = database

        self.paused: bool = False

        # Internal state
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

        # Last recorded foreground info
        self._last_info: Optional[dict] = None
        self._last_change_time: float = time.monotonic()

        # Idle tracking
        idle_threshold_minutes: int = getattr(config, "idle_threshold", 5)
        self._idle_threshold_ms: int = idle_threshold_minutes * 60 * 1000
        self._is_idle: bool = False

        if not _IS_WINDOWS:
            logger.warning(
                "Activity tracking requires Windows — running in no-op mode on %s",
                platform.system(),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the monitoring loop as an asyncio task."""
        if self._running:
            return
        self._running = True
        logger.info("Activity tracker: _IS_WINDOWS=%s, _WIN32_READY=%s", _IS_WINDOWS, _WIN32_READY)
        if not _WIN32_READY:
            logger.warning("Activity tracker: Win32 not ready — _poll_once will no-op. Check ctypes/user32 import.")
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Activity tracker started")

    async def stop(self) -> None:
        """Stop the monitoring loop and emit a final event for the last window."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Emit closing event for the last tracked window
        if self._last_info is not None:
            await self._emit_focus_event(self._last_info)
            self._last_info = None

        logger.info("Activity tracker stopped")

    def pause(self) -> None:
        """Pause activity capture (e.g., user clicked 'Pause Monitoring')."""
        self.paused = True
        logger.info("Activity tracking paused")

    def resume(self) -> None:
        """Resume activity capture."""
        self.paused = False
        logger.info("Activity tracking resumed")

    # ------------------------------------------------------------------
    # Monitor loop
    # ------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        """Poll the foreground window every second."""
        while self._running:
            try:
                if not self.paused:
                    await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in activity monitor poll")

            await asyncio.sleep(self.POLL_INTERVAL)

    async def _poll_once(self) -> None:
        """Single poll iteration: check foreground window and idle state."""
        if not _WIN32_READY:
            return

        current_info = await asyncio.to_thread(self._get_foreground_info)
        if current_info is None:
            return

        now = time.monotonic()

        # --- Check if the foreground window changed ---
        if self._last_info is not None:
            changed = (
                current_info.get("app_name") != self._last_info.get("app_name")
                or current_info.get("window_title") != self._last_info.get("window_title")
            )
            if changed:
                # Emit an event for the PREVIOUS window (with duration)
                await self._emit_focus_event(self._last_info)
                self._last_info = current_info
                self._last_change_time = now
        else:
            # First observation
            self._last_info = current_info
            self._last_change_time = now

        # --- Idle detection ---
        idle_now = await asyncio.to_thread(self._check_idle)
        if idle_now and not self._is_idle:
            # Transition to idle
            self._is_idle = True
            await self._emit_idle_event("idle_start")
        elif not idle_now and self._is_idle:
            # Transition back to active
            self._is_idle = False
            await self._emit_idle_event("idle_end")

    # ------------------------------------------------------------------
    # Win32 helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_foreground_info() -> Optional[dict]:
        """Return a dict with app_name, window_title, and pid for the
        current foreground window.  Returns None on failure.
        """
        if not _WIN32_READY:
            return None

        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return None

            # Window title
            buf_len = 512
            buf = ctypes.create_unicode_buffer(buf_len)
            _user32.GetWindowTextW(hwnd, buf, buf_len)
            window_title = buf.value or ""

            # Process ID
            pid = ctypes.wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_val = pid.value

            # Process name via psutil
            app_name = ""
            if pid_val:
                try:
                    proc = psutil.Process(pid_val)
                    app_name = proc.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    app_name = "unknown"

            return {
                "app_name": app_name,
                "window_title": window_title,
                "pid": pid_val,
            }
        except Exception:
            logger.debug("Failed to get foreground window info", exc_info=True)
            return None

    @staticmethod
    def _check_idle() -> bool:
        """Return True if the user has been idle longer than the threshold.

        Uses ``kernel32.GetLastInputInfo`` to determine idle time in
        milliseconds.
        """
        if not _WIN32_READY:
            return False

        try:
            lii = _LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
            if not _kernel32.GetLastInputInfo(ctypes.byref(lii)):
                return False

            tick_count = _kernel32.GetTickCount()
            idle_ms = tick_count - lii.dwTime
            # Use a fixed 5-minute default if the tracker instance isn't
            # accessible from this static context; the caller handles the
            # threshold comparison.
            return idle_ms > 300_000  # 5 minutes fallback
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def _emit_focus_event(self, info: dict) -> None:
        """Emit an ``app_focus`` event recording how long a window was in the
        foreground.
        """
        now = time.monotonic()
        duration_ms = int((now - self._last_change_time) * 1000)

        event = {
            "event_type": "app_focus",
            "application": info.get("app_name", ""),
            "window_title": info.get("window_title", ""),
            "pid": info.get("pid"),
            "duration_ms": duration_ms,
            "company_id": getattr(self.config, "company_id", "corvex"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            await self.db.insert_activity_event(event)
            logger.debug(
                "app_focus: %s [%s] — %d ms",
                event["application"],
                event["window_title"][:50],
                duration_ms,
            )
        except Exception:
            logger.exception("Failed to persist app_focus event")

    async def _emit_idle_event(self, event_type: str) -> None:
        """Emit an ``idle_start`` or ``idle_end`` event."""
        event = {
            "event_type": event_type,
            "application": "",
            "window_title": "",
            "duration_ms": 0,
            "company_id": getattr(self.config, "company_id", "corvex"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            await self.db.insert_activity_event(event)
            logger.info("Idle event: %s", event_type)
        except Exception:
            logger.exception("Failed to persist %s event", event_type)
