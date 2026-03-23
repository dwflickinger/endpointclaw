"""EndpointClaw Agent — sync manager.

Coordinates periodic synchronisation of local data (files, activity events,
keystroke chunks, screenshots) to the Supabase backend via the
:class:`~comms.relay.RelayClient`.

Each sync category runs independently — a failure in one does not block the
others.  The manager tracks last-sync timestamps and pending counts so that
the orchestrator and chat UI can report sync status.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .relay import RelayClient
from ..core.config import AgentConfig

logger = logging.getLogger("endpointclaw.comms.sync")

__all__ = ["SyncManager"]


class SyncManager:
    """Orchestrates data synchronisation between the local database and
    the Supabase backend.

    Parameters
    ----------
    config:
        The global :class:`AgentConfig`.
    database:
        The local :class:`Database` instance (expected to expose async
        query helpers).
    relay:
        An initialised :class:`RelayClient`.
    """

    def __init__(
        self,
        config: AgentConfig,
        database: Any,
        relay: RelayClient,
    ) -> None:
        self._config = config
        self._db = database
        self._relay = relay

        # Track last-sync wall-clock times per category
        self.last_file_sync: Optional[datetime] = None
        self.last_activity_sync: Optional[datetime] = None
        self.last_keystroke_sync: Optional[datetime] = None
        self.last_screenshot_sync: Optional[datetime] = None

        # Running counters for reporting
        self._files_synced_total: int = 0
        self._activity_synced_total: int = 0
        self._keystrokes_synced_total: int = 0
        self._screenshots_synced_total: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def sync_all(self) -> dict[str, Any]:
        """Run all sync operations sequentially.

        Each category is wrapped in its own ``try/except`` so that a failure
        in one does not prevent the others from executing.

        Returns a summary dict with counts and timing for each category.
        """
        summary: dict[str, Any] = {}

        summary["files"] = await self._run_safe("files", self.sync_files)
        summary["activity"] = await self._run_safe("activity", self.sync_activity)
        summary["keystrokes"] = await self._run_safe("keystrokes", self.sync_keystrokes)
        summary["screenshots"] = await self._run_safe("screenshots", self.sync_screenshots)
        summary["offline_queue"] = await self._run_safe(
            "offline_queue", self.process_offline_queue
        )

        return summary

    # ------------------------------------------------------------------
    # Individual sync methods
    # ------------------------------------------------------------------

    async def sync_files(self) -> int:
        """Sync unsynced file index records to Supabase.

        Returns the number of records synced.
        """
        t0 = time.monotonic()

        rows = await self._db.get_unsynced_files()
        if not rows:
            logger.debug("No unsynced files to sync")
            return 0

        # Transform local DB rows into the Supabase-ready dicts
        records: list[dict[str, Any]] = []
        row_ids: list[Any] = []
        for row in rows:
            records.append(self._file_row_to_record(row))
            row_ids.append(row["id"] if isinstance(row, dict) else row[0])

        await self._relay.sync_files(records)

        # Mark as synced locally
        await self._db.mark_files_synced(row_ids)

        elapsed = time.monotonic() - t0
        count = len(records)
        self._files_synced_total += count
        self.last_file_sync = datetime.now(timezone.utc)
        logger.info("File sync complete — %d record(s) in %.2fs", count, elapsed)
        return count

    async def sync_activity(self) -> int:
        """Sync unsynced activity events to Supabase.

        Returns the number of events synced.
        """
        t0 = time.monotonic()

        rows = await self._db.get_unsynced_activity()
        if not rows:
            logger.debug("No unsynced activity events to sync")
            return 0

        events: list[dict[str, Any]] = []
        row_ids: list[Any] = []
        for row in rows:
            events.append(self._activity_row_to_record(row))
            row_ids.append(row["id"] if isinstance(row, dict) else row[0])

        await self._relay.sync_activity(events)

        await self._db.mark_activity_synced(row_ids)

        elapsed = time.monotonic() - t0
        count = len(events)
        self._activity_synced_total += count
        self.last_activity_sync = datetime.now(timezone.utc)
        logger.info("Activity sync complete — %d event(s) in %.2fs", count, elapsed)
        return count

    async def sync_keystrokes(self) -> int:
        """Sync unsynced keystroke chunks to Supabase.

        Returns the number of chunks synced.
        """
        t0 = time.monotonic()

        rows = await self._db.get_unsynced_keystrokes()
        if not rows:
            logger.debug("No unsynced keystroke chunks to sync")
            return 0

        chunks: list[dict[str, Any]] = []
        row_ids: list[Any] = []
        for row in rows:
            chunks.append(self._keystroke_row_to_record(row))
            row_ids.append(row["id"] if isinstance(row, dict) else row[0])

        await self._relay.sync_keystrokes(chunks)

        await self._db.mark_keystrokes_synced(row_ids)

        elapsed = time.monotonic() - t0
        count = len(chunks)
        self._keystrokes_synced_total += count
        self.last_keystroke_sync = datetime.now(timezone.utc)
        logger.info("Keystroke sync complete — %d chunk(s) in %.2fs", count, elapsed)
        return count

    async def sync_screenshots(self) -> int:
        """Upload unsynced screenshots to Supabase Storage and sync metadata.

        Returns the number of screenshots synced.
        """
        t0 = time.monotonic()

        rows = await self._db.get_unsynced_screenshots()
        if not rows:
            logger.debug("No unsynced screenshots to sync")
            return 0

        count = 0
        for row in rows:
            row_dict = row if isinstance(row, dict) else dict(row)
            file_path = row_dict.get("file_path", "")
            metadata = {
                "trigger_type": row_dict.get("trigger_type"),
                "active_application": row_dict.get("active_application"),
                "window_title": row_dict.get("window_title"),
            }

            storage_path = await self._relay.upload_screenshot(file_path, metadata)
            if storage_path:
                row_id = row_dict.get("id")
                await self._db.mark_screenshot_synced(row_id, storage_path)
                count += 1
            else:
                logger.warning("Failed to upload screenshot: %s", file_path)

        elapsed = time.monotonic() - t0
        self._screenshots_synced_total += count
        self.last_screenshot_sync = datetime.now(timezone.utc)
        logger.info("Screenshot sync complete — %d screenshot(s) in %.2fs", count, elapsed)
        return count

    async def process_offline_queue(self) -> int:
        """Replay any operations that were queued due to connectivity issues.

        Returns the number of successfully replayed operations.
        """
        replayed = await self._relay.process_offline_queue()
        if replayed:
            logger.info("Processed offline queue — %d operation(s) replayed", replayed)
        return replayed

    # ------------------------------------------------------------------
    # State introspection
    # ------------------------------------------------------------------

    def get_sync_state(self) -> dict[str, Any]:
        """Return a snapshot of all sync state for heartbeats and UI display.

        Returns
        -------
        dict
            Contains ``last_*_sync`` timestamps, cumulative counts, offline
            queue size, and the relay's online status.
        """
        return {
            "last_file_sync": (
                self.last_file_sync.isoformat() if self.last_file_sync else None
            ),
            "last_activity_sync": (
                self.last_activity_sync.isoformat() if self.last_activity_sync else None
            ),
            "last_keystroke_sync": (
                self.last_keystroke_sync.isoformat() if self.last_keystroke_sync else None
            ),
            "last_screenshot_sync": (
                self.last_screenshot_sync.isoformat() if self.last_screenshot_sync else None
            ),
            "files_synced_total": self._files_synced_total,
            "activity_synced_total": self._activity_synced_total,
            "keystrokes_synced_total": self._keystrokes_synced_total,
            "screenshots_synced_total": self._screenshots_synced_total,
            "offline_queue_size": self._relay.offline_queue_size,
            "is_online": self._relay.is_online,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_safe(
        self, label: str, coro_fn: Any
    ) -> dict[str, Any]:
        """Run a sync coroutine and capture its result or error."""
        t0 = time.monotonic()
        try:
            count = await coro_fn()
            elapsed = time.monotonic() - t0
            return {"status": "ok", "count": count, "elapsed_s": round(elapsed, 3)}
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.exception("Sync error in %s", label)
            return {
                "status": "error",
                "error": str(exc),
                "elapsed_s": round(elapsed, 3),
            }

    # ------------------------------------------------------------------
    # Row -> record transformers
    # ------------------------------------------------------------------
    # These methods convert local SQLite rows (dicts or Row objects) into
    # the shape expected by the Supabase REST API.  The exact mapping
    # depends on the local Database schema which may evolve, so the
    # transformers are intentionally tolerant of missing keys.

    def _file_row_to_record(self, row: Any) -> dict[str, Any]:
        """Convert a local file index row to a Supabase-ready dict."""
        d = row if isinstance(row, dict) else dict(row)
        return {
            "endpoint_id": self._relay.endpoint_id,
            "file_path": d.get("file_path", ""),
            "filename": d.get("filename", ""),
            "file_type": d.get("file_type"),
            "file_size": d.get("file_size"),
            "modified_at": d.get("modified_at"),
            "content_hash": d.get("content_hash"),
            "content_extract": d.get("content_extract"),
            "inferred_project": d.get("inferred_project"),
            "inferred_customer": d.get("inferred_customer"),
            "tags": d.get("tags"),
            "company_id": self._config.company_id,
            "source": d.get("source", "local"),
        }

    def _activity_row_to_record(self, row: Any) -> dict[str, Any]:
        d = row if isinstance(row, dict) else dict(row)
        return {
            "endpoint_id": self._relay.endpoint_id,
            "event_type": d.get("event_type"),
            "application": d.get("application"),
            "window_title": d.get("window_title"),
            "file_path": d.get("file_path"),
            "file_id": d.get("file_id"),
            "duration_ms": d.get("duration_ms"),
            "metadata": d.get("metadata"),
            "company_id": self._config.company_id,
            "created_at": d.get("created_at"),
        }

    def _keystroke_row_to_record(self, row: Any) -> dict[str, Any]:
        d = row if isinstance(row, dict) else dict(row)
        return {
            "endpoint_id": self._relay.endpoint_id,
            "chunk_start": d.get("chunk_start"),
            "chunk_end": d.get("chunk_end"),
            "text_content": d.get("text_content"),
            "application": d.get("application"),
            "window_title": d.get("window_title"),
            "char_count": d.get("char_count"),
            "company_id": self._config.company_id,
        }
