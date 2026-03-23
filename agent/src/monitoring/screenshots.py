"""Screenshot capture engine for EndpointClaw agent.

Captures screenshots at configurable intervals and on application-switch
triggers.  Screenshots are saved as compressed JPEG files in a date-based
directory structure under ``config.data_dir/screenshots/``.

Uses Pillow's ``ImageGrab`` which works on both Windows and macOS.  Errors
are handled gracefully so the agent keeps running even if capture fails.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..core.config import AgentConfig
    from ..core.database import Database

logger = logging.getLogger("endpointclaw.monitoring.screenshots")

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

_IS_WINDOWS = platform.system() == "Windows"


def _get_foreground_info() -> dict:
    """Return the current foreground application and window title.

    Uses ctypes on Windows; returns empty strings on other platforms.
    """
    if not _IS_WINDOWS:
        return {"app_name": "", "window_title": ""}

    try:
        import ctypes
        import ctypes.wintypes
        import psutil as _psutil

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"app_name": "", "window_title": ""}

        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value or ""

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app_name = ""
        if pid.value:
            try:
                app_name = _psutil.Process(pid.value).name()
            except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                app_name = "unknown"

        return {"app_name": app_name, "window_title": title}
    except Exception:
        return {"app_name": "", "window_title": ""}


# ---------------------------------------------------------------------------
# ScreenshotEngine
# ---------------------------------------------------------------------------

class ScreenshotEngine:
    """Captures and stores screenshots at intervals or on demand."""

    MAX_WIDTH: int = 1920  # Resize threshold

    def __init__(self, config: AgentConfig, database: Database) -> None:
        self.config = config
        self.db = database

        self.paused: bool = False

        # Configuration
        self._interval: int = getattr(config, "screenshot_interval", 300)  # seconds
        self._quality: int = getattr(config, "screenshot_quality", 70)  # JPEG quality 1-100

        # Storage directory
        data_dir: Path = getattr(config, "data_dir", Path.home() / ".endpointclaw")
        self._screenshots_dir: Path = data_dir / "screenshots"

        # State
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the periodic capture loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())
        logger.info(
            "Screenshot engine started — interval=%ds, quality=%d, dir=%s",
            self._interval,
            self._quality,
            self._screenshots_dir,
        )

    async def stop(self) -> None:
        """Stop the capture loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Screenshot engine stopped")

    def pause(self) -> None:
        """Pause screenshot capture."""
        self.paused = True
        logger.info("Screenshot capture paused")

    def resume(self) -> None:
        """Resume screenshot capture."""
        self.paused = False
        logger.info("Screenshot capture resumed")

    async def capture_now(self, trigger_type: str = "manual") -> Optional[dict]:
        """Take a screenshot immediately and return its metadata dict.

        Steps:
        1. Capture screen with ImageGrab.grab()
        2. Resize if wider than MAX_WIDTH (maintain aspect ratio)
        3. Save as JPEG with configured quality
        4. Get current foreground app/window info
        5. Save metadata to database
        6. Return screenshot info dict

        Returns None if capture fails.
        """
        try:
            result = await asyncio.to_thread(self._capture_sync, trigger_type)
            if result is None:
                return None

            # Persist metadata to database
            try:
                await self.db.insert_screenshot(result)
            except Exception:
                logger.exception("Failed to save screenshot metadata to database")

            return result

        except Exception:
            logger.exception("Screenshot capture failed")
            return None

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    async def _capture_loop(self) -> None:
        """Capture screenshots at the configured interval."""
        while self._running:
            try:
                if not self.paused:
                    await self.capture_now(trigger_type="interval")

                    # Periodic cleanup of old screenshots
                    await asyncio.to_thread(self._cleanup_old, max_age_days=7)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in screenshot capture loop")

            await asyncio.sleep(self._interval)

    # ------------------------------------------------------------------
    # Synchronous capture (runs in thread)
    # ------------------------------------------------------------------

    def _capture_sync(self, trigger_type: str) -> Optional[dict]:
        """Capture, resize, save, and return metadata.  Runs in a worker
        thread via ``asyncio.to_thread``.
        """
        try:
            from PIL import Image, ImageGrab
        except ImportError:
            logger.warning(
                "Pillow is not installed — screenshot capture disabled. "
                "Install with: pip install Pillow"
            )
            return None

        # 1. Capture
        try:
            img: Image.Image = ImageGrab.grab()
        except Exception as exc:
            logger.warning("ImageGrab.grab() failed: %s", exc)
            return None

        # 2. Resize if necessary
        width, height = img.size
        if width > self.MAX_WIDTH:
            ratio = self.MAX_WIDTH / width
            new_height = int(height * ratio)
            img = img.resize((self.MAX_WIDTH, new_height), Image.LANCZOS)
            logger.debug("Resized screenshot from %dx%d to %dx%d", width, height, self.MAX_WIDTH, new_height)

        # 3. Build save path: screenshots/YYYY-MM-DD/screenshot_HHmmss.jpg
        now = datetime.now(timezone.utc)
        date_dir = self._screenshots_dir / now.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        filename = f"screenshot_{now.strftime('%H%M%S')}.jpg"
        save_path = date_dir / filename

        try:
            img.save(str(save_path), "JPEG", quality=self._quality, optimize=True)
        except Exception as exc:
            logger.warning("Failed to save screenshot to %s: %s", save_path, exc)
            return None

        file_size = save_path.stat().st_size

        # 4. Foreground info
        fg = _get_foreground_info()

        # 5. Build metadata record
        record = {
            "file_path": str(save_path),
            "filename": filename,
            "storage_path": str(save_path.relative_to(self._screenshots_dir)),
            "captured_at": now.isoformat(),
            "trigger_type": trigger_type,
            "active_application": fg.get("app_name", ""),
            "active_window_title": fg.get("window_title", ""),
            "width": img.size[0],
            "height": img.size[1],
            "file_size_bytes": file_size,
            "quality": self._quality,
            "company_id": getattr(self.config, "company_id", "corvex"),
        }

        logger.debug(
            "Screenshot captured: %s (%dx%d, %d bytes, trigger=%s)",
            save_path,
            img.size[0],
            img.size[1],
            file_size,
            trigger_type,
        )

        return record

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup_old(self, max_age_days: int = 7) -> None:
        """Remove local screenshot files older than *max_age_days*.

        Walks the screenshots directory and deletes dated subdirectories
        whose name indicates they are older than the threshold.
        """
        if not self._screenshots_dir.exists():
            return

        cutoff = datetime.now(timezone.utc)
        try:
            from datetime import timedelta

            cutoff_date = (cutoff - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
        except Exception:
            return

        removed_count = 0
        try:
            for entry in os.scandir(self._screenshots_dir):
                if not entry.is_dir():
                    continue
                dir_name = entry.name
                # Expect YYYY-MM-DD format
                if len(dir_name) == 10 and dir_name < cutoff_date:
                    # Remove all files in the directory, then the directory
                    try:
                        for file_entry in os.scandir(entry.path):
                            if file_entry.is_file():
                                os.unlink(file_entry.path)
                                removed_count += 1
                        os.rmdir(entry.path)
                        logger.debug("Removed old screenshot directory: %s", dir_name)
                    except Exception:
                        logger.debug(
                            "Could not fully remove screenshot dir %s",
                            dir_name,
                            exc_info=True,
                        )
        except Exception:
            logger.debug("Error during screenshot cleanup", exc_info=True)

        if removed_count:
            logger.info("Cleaned up %d old screenshot files", removed_count)
