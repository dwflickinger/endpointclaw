"""Filesystem watcher for EndpointClaw agent.

Uses the *watchdog* library to monitor configured directories for file
creation, modification, deletion, and moves.  Events are debounced (500 ms)
and dispatched to the :class:`FileScanner` for re-indexing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False

if TYPE_CHECKING:
    from ..core.config import AgentConfig
    from ..core.database import Database
    from .scanner import FileScanner

logger = logging.getLogger("endpointclaw.indexing.watcher")

# ---------------------------------------------------------------------------
# Exclusion helpers
# ---------------------------------------------------------------------------

_EXCLUDED_DIRS = frozenset({
    "__pycache__",
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    ".vscode",
    ".idea",
    "$RECYCLE.BIN",
    "System Volume Information",
})

_EXCLUDED_PREFIXES = ("~$", "~", ".~")
_TEMP_EXTENSIONS = frozenset({".tmp", ".temp", ".bak", ".swp", ".swo", ".crdownload", ".part"})


def _is_excluded_path(path_str: str) -> bool:
    """Return True if *path_str* belongs to a hidden, system, or temp location."""
    parts = Path(path_str).parts
    for part in parts:
        if part in _EXCLUDED_DIRS:
            return True
        # Hidden files/dirs (starts with ".")
        if part.startswith(".") and part not in (".", ".."):
            return True
    return False


# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------

class _EndpointClawHandler(FileSystemEventHandler):
    """Translates watchdog filesystem events into asyncio callbacks."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        watcher: FileWatcher,
    ) -> None:
        super().__init__()
        self._loop = loop
        self._watcher = watcher

    # -- watchdog callbacks (called from watchdog's thread) -------------

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path, "created")

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path, "modified")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path, "deleted")

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path, "moved", dest_path=getattr(event, "dest_path", None))

    def _schedule(self, src_path: str, event_type: str, dest_path: Optional[str] = None) -> None:
        """Schedule the event processing on the asyncio event loop."""
        if not self._watcher._should_process(src_path):
            return
        self._loop.call_soon_threadsafe(
            asyncio.ensure_future,
            self._watcher._debounced_process(src_path, event_type, dest_path),
        )


# ---------------------------------------------------------------------------
# FileWatcher
# ---------------------------------------------------------------------------

class FileWatcher:
    """Monitors filesystem changes using watchdog and dispatches to the scanner."""

    DEBOUNCE_SECONDS: float = 0.5

    def __init__(
        self,
        config: AgentConfig,
        database: Database,
        scanner: FileScanner,
    ) -> None:
        self.config = config
        self.db = database
        self.scanner = scanner

        self._observer: Optional[Observer] = None  # type: ignore[assignment]
        self._pending: dict[str, float] = {}  # path -> timestamp of last event
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog observer in a background thread."""
        if not _WATCHDOG_AVAILABLE:
            logger.error(
                "watchdog library is not installed — filesystem watching disabled. "
                "Install it with: pip install watchdog"
            )
            return

        monitored_paths: list[str] = getattr(self.config, "monitored_paths", [])
        if not monitored_paths:
            logger.warning("No monitored_paths configured — filesystem watcher not started")
            return

        loop = asyncio.get_event_loop()
        handler = _EndpointClawHandler(loop, self)

        self._observer = Observer()
        for path_str in monitored_paths:
            path = Path(path_str)
            if not path.exists():
                logger.warning("Monitored path does not exist, skipping: %s", path)
                continue
            self._observer.schedule(handler, str(path), recursive=True)
            logger.info("Watching directory: %s", path)

        self._observer.daemon = True
        self._observer.start()
        self._running = True
        logger.info("Filesystem watcher started")

    def stop(self) -> None:
        """Stop the watchdog observer."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            logger.info("Filesystem watcher stopped")
        self._running = False

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _should_process(self, path_str: str) -> bool:
        """Check whether a file event should be processed.

        Returns False for:
        - Files without a monitored extension
        - Hidden files / system files
        - Temp files
        - Paths in excluded directories
        """
        p = Path(path_str)

        # Extension filter
        monitored_extensions: list[str] = getattr(self.config, "monitored_extensions", [])
        ext = p.suffix.lower()
        if monitored_extensions and ext not in monitored_extensions:
            return False

        # Excluded prefixes (Office lock files, temp files)
        if any(p.name.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            return False

        # Temp extensions
        if ext in _TEMP_EXTENSIONS:
            return False

        # Excluded directories
        if _is_excluded_path(path_str):
            return False

        return True

    # ------------------------------------------------------------------
    # Debounced processing
    # ------------------------------------------------------------------

    async def _debounced_process(
        self,
        file_path: str,
        event_type: str,
        dest_path: Optional[str] = None,
    ) -> None:
        """Wait for the debounce window then process the event.

        If another event for the same file arrives within 500 ms, the timer
        resets — only the final event is processed.
        """
        now = time.monotonic()
        self._pending[file_path] = now

        await asyncio.sleep(self.DEBOUNCE_SECONDS)

        # If a newer event superseded this one, skip
        if self._pending.get(file_path) != now:
            return
        self._pending.pop(file_path, None)

        logger.debug("Processing %s event for %s", event_type, file_path)

        try:
            if event_type in ("created", "modified"):
                await self._handle_create_modify(file_path)
            elif event_type == "deleted":
                await self._handle_delete(file_path)
            elif event_type == "moved":
                await self._handle_move(file_path, dest_path)
        except Exception:
            logger.exception(
                "Error processing %s event for %s", event_type, file_path
            )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _handle_create_modify(self, file_path: str) -> None:
        """Re-scan a created or modified file."""
        path = Path(file_path)
        if not path.exists():
            logger.debug("File no longer exists (transient): %s", file_path)
            return
        await self.scanner.scan_file(path)
        logger.debug("Indexed file: %s", file_path)

    async def _handle_delete(self, file_path: str) -> None:
        """Remove a deleted file from the database."""
        try:
            await self.db.delete_file_by_path(file_path)
            logger.debug("Removed deleted file from index: %s", file_path)
        except Exception:
            logger.exception("Failed to remove file record for %s", file_path)

    async def _handle_move(self, old_path: str, new_path: Optional[str]) -> None:
        """Update the file path in the database after a move/rename."""
        if new_path is None:
            # Treat as delete if we don't know the destination
            await self._handle_delete(old_path)
            return

        # Check if the new path should be processed
        if not self._should_process(new_path):
            # Destination is outside our scope — treat as delete from index
            await self._handle_delete(old_path)
            return

        try:
            await self.db.update_file_path(old_path, new_path)
            logger.debug("Updated file path: %s -> %s", old_path, new_path)
        except Exception:
            logger.exception(
                "Failed to update file path %s -> %s; falling back to re-scan",
                old_path,
                new_path,
            )
            # Fallback: delete old record and re-scan the new path
            try:
                await self.db.delete_file_by_path(old_path)
            except Exception:
                pass
            await self._handle_create_modify(new_path)
