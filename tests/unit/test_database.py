"""Tests for agent.src.core.database.Database.

Exercises the async SQLite database layer: table creation, WAL mode,
file CRUD with FTS, activity events, conversations, and the sync queue.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest

from agent.src.core.database import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[Database, None]:
    """Create, initialise, yield, and close a Database backed by a temp file."""
    db_path = tmp_path / "test_agent.db"
    database = Database(db_path)
    await database.init()
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_file(path: str = "/tmp/test/sample.txt", **overrides) -> dict:
    """Return a minimal file record dict, merging any *overrides*."""
    record = {
        "file_path": path,
        "filename": Path(path).name,
        "file_type": "other",
        "file_size": 1024,
        "modified_at": "2025-01-15T10:00:00+00:00",
        "content_hash": "abc123",
        "content_extract": "sample text content for testing",
        "inferred_project": "TestProject",
        "inferred_customer": "Acme Corp",
        "tags": ["roofing", "estimate"],
    }
    record.update(overrides)
    return record


def _sample_activity(**overrides) -> dict:
    record = {
        "endpoint_id": "device-001",
        "event_type": "file_open",
        "application": "Excel",
        "window_title": "estimate.xlsx",
        "file_path": "/tmp/estimate.xlsx",
        "duration_ms": 5000,
        "metadata": {"source": "watcher"},
        "created_at": "2025-01-15T10:00:00+00:00",
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestInit:
    @pytest.mark.asyncio
    async def test_init_creates_tables(self, db: Database) -> None:
        """After init(), all expected tables must exist in sqlite_master."""
        rows = await db.conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {r["name"] for r in rows}
        expected = {
            "files",
            "activity_events",
            "keystroke_chunks",
            "screenshots",
            "conversations",
            "sync_queue",
        }
        assert expected.issubset(table_names)

    @pytest.mark.asyncio
    async def test_wal_mode(self, db: Database) -> None:
        rows = await db.conn.execute_fetchall("PRAGMA journal_mode;")
        mode = rows[0][0] if rows else None
        assert mode == "wal"


# ---------------------------------------------------------------------------
# File CRUD
# ---------------------------------------------------------------------------


class TestFiles:
    @pytest.mark.asyncio
    async def test_upsert_file_insert(self, db: Database) -> None:
        row_id = await db.upsert_file(_sample_file())
        assert isinstance(row_id, int)
        assert row_id > 0

        stored = await db.get_file_by_path("/tmp/test/sample.txt")
        assert stored is not None
        assert stored["filename"] == "sample.txt"
        assert stored["file_type"] == "other"

    @pytest.mark.asyncio
    async def test_upsert_file_update(self, db: Database) -> None:
        path = "/tmp/test/update_me.txt"
        await db.upsert_file(_sample_file(path=path, file_size=100))
        await db.upsert_file(_sample_file(path=path, file_size=200))

        stored = await db.get_file_by_path(path)
        assert stored is not None
        assert stored["file_size"] == 200

    @pytest.mark.asyncio
    async def test_search_files_fts(self, db: Database) -> None:
        await db.upsert_file(
            _sample_file(
                path="/tmp/a.txt",
                content_extract="TPO membrane roofing materials",
            )
        )
        await db.upsert_file(
            _sample_file(
                path="/tmp/b.txt",
                content_extract="office supplies and printer paper",
            )
        )

        results = await db.search_files("roofing")
        assert len(results) >= 1
        assert any(r["file_path"] == "/tmp/a.txt" for r in results)

    @pytest.mark.asyncio
    async def test_search_files_by_type(self, db: Database) -> None:
        await db.upsert_file(
            _sample_file(path="/tmp/est.xlsx", file_type="estimate", content_extract="estimate data")
        )
        await db.upsert_file(
            _sample_file(path="/tmp/inv.pdf", file_type="invoice", content_extract="invoice data")
        )

        results = await db.search_files("data", file_type="estimate")
        assert all(r["file_type"] == "estimate" for r in results)

    @pytest.mark.asyncio
    async def test_get_unsynced_files(self, db: Database) -> None:
        await db.upsert_file(_sample_file(path="/tmp/unsynced.txt"))
        unsynced = await db.get_unsynced_files()
        assert len(unsynced) >= 1
        assert any(r["file_path"] == "/tmp/unsynced.txt" for r in unsynced)

    @pytest.mark.asyncio
    async def test_mark_files_synced(self, db: Database) -> None:
        row_id = await db.upsert_file(_sample_file(path="/tmp/to_sync.txt"))
        await db.mark_files_synced([row_id])

        unsynced = await db.get_unsynced_files()
        assert not any(r["file_path"] == "/tmp/to_sync.txt" for r in unsynced)

    @pytest.mark.asyncio
    async def test_get_file_stats(self, db: Database) -> None:
        await db.upsert_file(_sample_file(path="/tmp/s1.txt", file_type="estimate", file_size=100))
        await db.upsert_file(_sample_file(path="/tmp/s2.txt", file_type="estimate", file_size=200))
        await db.upsert_file(_sample_file(path="/tmp/s3.txt", file_type="photo", file_size=300))

        stats = await db.get_file_stats()
        assert stats["total_files"] == 3
        assert stats["total_size"] == 600
        assert stats["by_type"]["estimate"] == 2
        assert stats["by_type"]["photo"] == 1

    @pytest.mark.asyncio
    async def test_delete_file(self, db: Database) -> None:
        path = "/tmp/delete_me.txt"
        await db.upsert_file(_sample_file(path=path))
        await db.delete_file(path)

        stored = await db.get_file_by_path(path)
        assert stored is None

    @pytest.mark.asyncio
    async def test_get_file_by_path(self, db: Database) -> None:
        path = "/tmp/exact_lookup.txt"
        await db.upsert_file(_sample_file(path=path, filename="exact_lookup.txt"))

        stored = await db.get_file_by_path(path)
        assert stored is not None
        assert stored["file_path"] == path
        assert stored["filename"] == "exact_lookup.txt"


# ---------------------------------------------------------------------------
# Activity events
# ---------------------------------------------------------------------------


class TestActivity:
    @pytest.mark.asyncio
    async def test_insert_activity(self, db: Database) -> None:
        await db.insert_activity(_sample_activity())

        rows = await db.conn.execute_fetchall("SELECT * FROM activity_events")
        assert len(rows) == 1
        assert rows[0]["event_type"] == "file_open"

    @pytest.mark.asyncio
    async def test_get_unsynced_activity(self, db: Database) -> None:
        await db.insert_activity(_sample_activity())
        unsynced = await db.get_unsynced_activity()
        assert len(unsynced) >= 1
        assert unsynced[0]["application"] == "Excel"


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class TestConversations:
    @pytest.mark.asyncio
    async def test_save_and_get_conversation(self, db: Database) -> None:
        messages = [
            {"role": "user", "content": "Find the latest estimate"},
            {"role": "assistant", "content": "Here is the file..."},
        ]
        await db.save_conversation("conv-001", messages, summary="Estimate search")

        conv = await db.get_conversation("conv-001")
        assert conv is not None
        assert conv["id"] == "conv-001"
        assert len(conv["messages"]) == 2
        assert conv["summary"] == "Estimate search"

    @pytest.mark.asyncio
    async def test_get_recent_conversations(self, db: Database) -> None:
        await db.save_conversation("conv-a", [{"role": "user", "content": "hi"}])
        await db.save_conversation("conv-b", [{"role": "user", "content": "hello"}])

        recent = await db.get_recent_conversations(limit=10)
        assert len(recent) == 2
        # Most recent first.
        ids = [c["id"] for c in recent]
        assert "conv-a" in ids
        assert "conv-b" in ids


# ---------------------------------------------------------------------------
# Sync queue
# ---------------------------------------------------------------------------


class TestSyncQueue:
    @pytest.mark.asyncio
    async def test_queue_sync(self, db: Database) -> None:
        await db.queue_sync("files", "rec-1", "upsert", {"file_path": "/tmp/x.txt"})

        pending = await db.get_pending_sync()
        assert len(pending) >= 1
        assert pending[0]["table_name"] == "files"

    @pytest.mark.asyncio
    async def test_get_pending_sync(self, db: Database) -> None:
        await db.queue_sync("files", "rec-1", "upsert", {"a": 1})
        await db.queue_sync("activity_events", "rec-2", "insert", {"b": 2})

        pending = await db.get_pending_sync()
        assert len(pending) == 2
        tables = {p["table_name"] for p in pending}
        assert tables == {"files", "activity_events"}

    @pytest.mark.asyncio
    async def test_remove_sync_item(self, db: Database) -> None:
        await db.queue_sync("files", "rec-del", "upsert", {"c": 3})

        pending = await db.get_pending_sync()
        sync_id = pending[0]["id"]
        await db.remove_sync_item(sync_id)

        remaining = await db.get_pending_sync()
        assert not any(r["id"] == sync_id for r in remaining)
