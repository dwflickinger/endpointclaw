"""EndpointClaw Agent — local SQLite database manager.

Provides async access (via *aiosqlite*) to a WAL-mode SQLite database that
stores indexed files, activity events, keystroke chunks, screenshots,
conversations, and a sync queue.  Includes an FTS5 virtual table for fast
full-text search over the file index.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger("endpointclaw.database")

# ---------------------------------------------------------------------------
# SQL definitions
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT UNIQUE NOT NULL,
    filename        TEXT,
    file_type       TEXT,
    file_size       INTEGER,
    modified_at     TEXT,
    content_hash    TEXT,
    content_extract TEXT,
    inferred_project TEXT,
    inferred_customer TEXT,
    tags            TEXT,          -- JSON array
    indexed_at      TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS activity_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id     TEXT,
    event_type      TEXT,
    application     TEXT,
    window_title    TEXT,
    file_path       TEXT,
    duration_ms     INTEGER,
    metadata        TEXT,          -- JSON object
    created_at      TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS keystroke_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_start     TEXT,
    chunk_end       TEXT,
    text_content    TEXT,
    application     TEXT,
    window_title    TEXT,
    char_count      INTEGER,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS screenshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at       TEXT,
    storage_path      TEXT,
    trigger_type      TEXT,
    active_application TEXT,
    window_title      TEXT,
    file_size_bytes   INTEGER,
    synced_at         TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    messages        TEXT,          -- JSON array
    summary         TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS sync_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name      TEXT,
    record_id       TEXT,
    operation       TEXT,
    payload         TEXT,          -- JSON object
    created_at      TEXT,
    attempts        INTEGER DEFAULT 0
);
"""

_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    filename,
    content_extract,
    inferred_project,
    inferred_customer,
    tags,
    content=files,
    content_rowid=id
);
"""

# FTS triggers keep the virtual table in sync with the base table.
_CREATE_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, filename, content_extract, inferred_project, inferred_customer, tags)
    VALUES (new.id, new.filename, new.content_extract, new.inferred_project, new.inferred_customer, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, filename, content_extract, inferred_project, inferred_customer, tags)
    VALUES ('delete', old.id, old.filename, old.content_extract, old.inferred_project, old.inferred_customer, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, filename, content_extract, inferred_project, inferred_customer, tags)
    VALUES ('delete', old.id, old.filename, old.content_extract, old.inferred_project, old.inferred_customer, old.tags);
    INSERT INTO files_fts(rowid, filename, content_extract, inferred_project, inferred_customer, tags)
    VALUES (new.id, new.filename, new.content_extract, new.inferred_project, new.inferred_customer, new.tags);
END;
"""


def _now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Async SQLite database wrapper for all local agent storage.

    Uses WAL mode for concurrent readers, FTS5 for full-text search,
    and a sync queue for reliable upload to Supabase.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Open the database, enable WAL mode, and create tables."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row

        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(_CREATE_TABLES)
        await self._conn.executescript(_CREATE_FTS)
        await self._conn.executescript(_CREATE_FTS_TRIGGERS)
        await self._conn.commit()
        logger.info("Database initialised at %s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not initialised — call init() first"
        return self._conn

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def upsert_file(self, file_data: dict[str, Any]) -> int:
        """Insert or update a file record and refresh the FTS index.

        Returns the row *id* of the upserted record.
        """
        tags = file_data.get("tags")
        if isinstance(tags, list):
            tags = json.dumps(tags)

        now = _now()
        sql = """
            INSERT INTO files
                (file_path, filename, file_type, file_size, modified_at,
                 content_hash, content_extract, inferred_project,
                 inferred_customer, tags, indexed_at)
            VALUES
                (:file_path, :filename, :file_type, :file_size, :modified_at,
                 :content_hash, :content_extract, :inferred_project,
                 :inferred_customer, :tags, :indexed_at)
            ON CONFLICT(file_path) DO UPDATE SET
                filename         = excluded.filename,
                file_type        = excluded.file_type,
                file_size        = excluded.file_size,
                modified_at      = excluded.modified_at,
                content_hash     = excluded.content_hash,
                content_extract  = excluded.content_extract,
                inferred_project = excluded.inferred_project,
                inferred_customer= excluded.inferred_customer,
                tags             = excluded.tags,
                indexed_at       = excluded.indexed_at,
                synced_at        = NULL
        """
        params = {
            "file_path": file_data["file_path"],
            "filename": file_data.get("filename"),
            "file_type": file_data.get("file_type"),
            "file_size": file_data.get("file_size"),
            "modified_at": file_data.get("modified_at"),
            "content_hash": file_data.get("content_hash"),
            "content_extract": file_data.get("content_extract"),
            "inferred_project": file_data.get("inferred_project"),
            "inferred_customer": file_data.get("inferred_customer"),
            "tags": tags,
            "indexed_at": now,
        }
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def search_files(
        self, query: str, file_type: Optional[str] = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Full-text search across the file index, optionally filtered by *file_type*."""
        if file_type:
            sql = """
                SELECT f.* FROM files f
                JOIN files_fts fts ON f.id = fts.rowid
                WHERE files_fts MATCH :query AND f.file_type = :file_type
                ORDER BY rank
                LIMIT :limit
            """
            params: dict[str, Any] = {"query": query, "file_type": file_type, "limit": limit}
        else:
            sql = """
                SELECT f.* FROM files f
                JOIN files_fts fts ON f.id = fts.rowid
                WHERE files_fts MATCH :query
                ORDER BY rank
                LIMIT :limit
            """
            params = {"query": query, "limit": limit}

        rows = await self.conn.execute_fetchall(sql, params)
        return [dict(r) for r in rows]

    async def get_unsynced_files(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return file records that have not yet been synced."""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM files WHERE synced_at IS NULL ORDER BY indexed_at LIMIT :limit",
            {"limit": limit},
        )
        return [dict(r) for r in rows]

    async def mark_files_synced(self, file_ids: list[int]) -> None:
        """Mark the given file records as synced with the current timestamp."""
        if not file_ids:
            return
        now = _now()
        placeholders = ",".join("?" for _ in file_ids)
        await self.conn.execute(
            f"UPDATE files SET synced_at = ? WHERE id IN ({placeholders})",
            [now, *file_ids],
        )
        await self.conn.commit()

    async def get_file_by_path(self, path: str) -> Optional[dict[str, Any]]:
        """Retrieve a single file record by its full path."""
        row = await self.conn.execute_fetchall(
            "SELECT * FROM files WHERE file_path = :path LIMIT 1",
            {"path": path},
        )
        return dict(row[0]) if row else None

    async def get_file_stats(self) -> dict[str, Any]:
        """Return aggregate statistics about the file index."""
        total_row = await self.conn.execute_fetchall("SELECT COUNT(*) AS cnt, COALESCE(SUM(file_size),0) AS total_size FROM files")
        total = dict(total_row[0])

        type_rows = await self.conn.execute_fetchall(
            "SELECT file_type, COUNT(*) AS cnt FROM files GROUP BY file_type ORDER BY cnt DESC"
        )
        by_type = {r["file_type"]: r["cnt"] for r in type_rows}

        return {
            "total_files": total["cnt"],
            "total_size": total["total_size"],
            "by_type": by_type,
        }

    async def delete_file(self, file_path: str) -> None:
        """Delete a file record by path."""
        await self.conn.execute("DELETE FROM files WHERE file_path = :path", {"path": file_path})
        await self.conn.commit()

    # ------------------------------------------------------------------
    # Activity events
    # ------------------------------------------------------------------

    async def insert_activity(self, event: dict[str, Any]) -> None:
        """Insert a single activity event."""
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            metadata = json.dumps(metadata)
        await self.conn.execute(
            """INSERT INTO activity_events
               (endpoint_id, event_type, application, window_title, file_path,
                duration_ms, metadata, created_at)
               VALUES (:endpoint_id, :event_type, :application, :window_title,
                       :file_path, :duration_ms, :metadata, :created_at)""",
            {
                "endpoint_id": event.get("endpoint_id"),
                "event_type": event.get("event_type"),
                "application": event.get("application"),
                "window_title": event.get("window_title"),
                "file_path": event.get("file_path"),
                "duration_ms": event.get("duration_ms"),
                "metadata": metadata,
                "created_at": event.get("created_at", _now()),
            },
        )
        await self.conn.commit()

    async def get_unsynced_activity(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return activity events not yet synced."""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM activity_events WHERE synced_at IS NULL ORDER BY created_at LIMIT :limit",
            {"limit": limit},
        )
        return [dict(r) for r in rows]

    async def mark_activity_synced(self, event_ids: list[int]) -> None:
        """Mark activity events as synced."""
        if not event_ids:
            return
        now = _now()
        placeholders = ",".join("?" for _ in event_ids)
        await self.conn.execute(
            f"UPDATE activity_events SET synced_at = ? WHERE id IN ({placeholders})",
            [now, *event_ids],
        )
        await self.conn.commit()

    # ------------------------------------------------------------------
    # Keystroke chunks
    # ------------------------------------------------------------------

    async def insert_keystroke_chunk(self, chunk: dict[str, Any]) -> None:
        """Insert a single keystroke chunk."""
        await self.conn.execute(
            """INSERT INTO keystroke_chunks
               (chunk_start, chunk_end, text_content, application, window_title, char_count)
               VALUES (:chunk_start, :chunk_end, :text_content, :application,
                       :window_title, :char_count)""",
            {
                "chunk_start": chunk.get("chunk_start"),
                "chunk_end": chunk.get("chunk_end"),
                "text_content": chunk.get("text_content"),
                "application": chunk.get("application"),
                "window_title": chunk.get("window_title"),
                "char_count": chunk.get("char_count"),
            },
        )
        await self.conn.commit()

    async def get_unsynced_keystrokes(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return keystroke chunks not yet synced."""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM keystroke_chunks WHERE synced_at IS NULL ORDER BY chunk_start LIMIT :limit",
            {"limit": limit},
        )
        return [dict(r) for r in rows]

    async def mark_keystrokes_synced(self, ids: list[int]) -> None:
        """Mark keystroke chunks as synced."""
        if not ids:
            return
        now = _now()
        placeholders = ",".join("?" for _ in ids)
        await self.conn.execute(
            f"UPDATE keystroke_chunks SET synced_at = ? WHERE id IN ({placeholders})",
            [now, *ids],
        )
        await self.conn.commit()

    # ------------------------------------------------------------------
    # Screenshots
    # ------------------------------------------------------------------

    async def insert_screenshot(self, data: dict[str, Any]) -> None:
        """Insert a screenshot record."""
        await self.conn.execute(
            """INSERT INTO screenshots
               (captured_at, storage_path, trigger_type, active_application,
                window_title, file_size_bytes)
               VALUES (:captured_at, :storage_path, :trigger_type,
                       :active_application, :window_title, :file_size_bytes)""",
            {
                "captured_at": data.get("captured_at", _now()),
                "storage_path": data.get("storage_path"),
                "trigger_type": data.get("trigger_type"),
                "active_application": data.get("active_application"),
                "window_title": data.get("window_title"),
                "file_size_bytes": data.get("file_size_bytes"),
            },
        )
        await self.conn.commit()

    async def get_unsynced_screenshots(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return screenshot records not yet synced."""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM screenshots WHERE synced_at IS NULL ORDER BY captured_at LIMIT :limit",
            {"limit": limit},
        )
        return [dict(r) for r in rows]

    async def mark_screenshots_synced(self, ids: list[int]) -> None:
        """Mark screenshot records as synced."""
        if not ids:
            return
        now = _now()
        placeholders = ",".join("?" for _ in ids)
        await self.conn.execute(
            f"UPDATE screenshots SET synced_at = ? WHERE id IN ({placeholders})",
            [now, *ids],
        )
        await self.conn.commit()

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def save_conversation(
        self, conv_id: str, messages: list[dict[str, Any]], summary: Optional[str] = None
    ) -> None:
        """Create or update a conversation."""
        now = _now()
        messages_json = json.dumps(messages)
        await self.conn.execute(
            """INSERT INTO conversations (id, messages, summary, created_at, updated_at)
               VALUES (:id, :messages, :summary, :now, :now)
               ON CONFLICT(id) DO UPDATE SET
                   messages   = excluded.messages,
                   summary    = excluded.summary,
                   updated_at = excluded.updated_at""",
            {"id": conv_id, "messages": messages_json, "summary": summary, "now": now},
        )
        await self.conn.commit()

    async def get_conversation(self, conv_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a conversation by id."""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM conversations WHERE id = :id LIMIT 1",
            {"id": conv_id},
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["messages"] = json.loads(row["messages"]) if row["messages"] else []
        return row

    async def get_recent_conversations(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent conversations."""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT :limit",
            {"limit": limit},
        )
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["messages"] = json.loads(d["messages"]) if d["messages"] else []
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Sync queue
    # ------------------------------------------------------------------

    async def queue_sync(
        self, table: str, record_id: str, operation: str, payload: dict[str, Any]
    ) -> None:
        """Add an item to the sync queue."""
        await self.conn.execute(
            """INSERT INTO sync_queue (table_name, record_id, operation, payload, created_at)
               VALUES (:table_name, :record_id, :operation, :payload, :created_at)""",
            {
                "table_name": table,
                "record_id": record_id,
                "operation": operation,
                "payload": json.dumps(payload),
                "created_at": _now(),
            },
        )
        await self.conn.commit()

    async def get_pending_sync(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return queued sync items ordered by creation time."""
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM sync_queue ORDER BY created_at LIMIT :limit",
            {"limit": limit},
        )
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
            results.append(d)
        return results

    async def remove_sync_item(self, sync_id: int) -> None:
        """Remove a sync queue item after successful processing."""
        await self.conn.execute(
            "DELETE FROM sync_queue WHERE id = :id", {"id": sync_id}
        )
        await self.conn.commit()
