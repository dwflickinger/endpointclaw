"""EndpointClaw Agent — Supabase communication relay.

Handles all HTTP communication with the Supabase backend: endpoint
registration, heartbeats, data synchronisation (files, activity,
keystrokes, screenshots), command polling, and company config retrieval.

All network operations are async via :mod:`httpx`.  A transparent retry
layer with exponential back-off and an offline queue ensure resilience
when the network is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from ..core.config import AgentConfig

logger = logging.getLogger("endpointclaw.comms.relay")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_INITIAL_BACKOFF_S = 1.0
_BACKOFF_FACTOR = 2.0
_REQUEST_TIMEOUT_S = 30.0
_OFFLINE_QUEUE_MAX = 1000
_UPLOAD_TIMEOUT_S = 120.0
_BATCH_SIZE = 50

__all__ = ["RelayClient"]


# ---------------------------------------------------------------------------
# Offline queue item
# ---------------------------------------------------------------------------

class _QueuedOperation:
    """Lightweight container for an operation that failed due to connectivity."""

    __slots__ = ("method", "path", "kwargs", "created_at")

    def __init__(self, method: str, path: str, kwargs: dict[str, Any]) -> None:
        self.method = method
        self.path = path
        self.kwargs = kwargs
        self.created_at = time.monotonic()


# ---------------------------------------------------------------------------
# RelayClient
# ---------------------------------------------------------------------------

class RelayClient:
    """Async HTTP relay to the Supabase backend.

    Parameters
    ----------
    config:
        The global :class:`AgentConfig` instance.  Must have ``supabase_url``,
        ``supabase_key``, and ``api_key`` populated before :meth:`init` is
        called.
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._endpoint_id: Optional[str] = None
        self._is_online: bool = False
        self._offline_queue: deque[_QueuedOperation] = deque(maxlen=_OFFLINE_QUEUE_MAX)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def endpoint_id(self) -> str:
        """The UUID of this endpoint in the ``endpoints`` table.

        Raises :class:`RuntimeError` if called before successful registration.
        """
        if self._endpoint_id is None:
            raise RuntimeError("endpoint_id is not available — call init() first")
        return self._endpoint_id

    @property
    def is_online(self) -> bool:
        """Whether the last network request succeeded."""
        return self._is_online

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Create the HTTP client and register the endpoint if needed."""
        self._client = httpx.AsyncClient(
            base_url=self._config.supabase_url,
            timeout=httpx.Timeout(_REQUEST_TIMEOUT_S),
        )
        logger.info("HTTP client created — base_url=%s", self._config.supabase_url)

        await self.register_endpoint()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("HTTP client closed")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register_endpoint(self) -> str:
        """Register (or upsert) this device in the ``endpoints`` table.

        Uses the ``api_key`` as the conflict target so that re-registration
        on the same machine is idempotent.

        Returns
        -------
        str
            The ``endpoint_id`` (UUID) assigned by the database.
        """
        payload = {
            "device_name": self._config.device_name,
            "user_name": os.getlogin(),
            "user_email": self._config.user_email,
            "company_id": self._config.company_id,
            "api_key": self._config.api_key,
            "status": "active",
            "agent_version": self._agent_version(),
            "os_version": f"{platform.system()} {platform.version()}",
            "ip_address": self._local_ip(),
            "monitored_paths": self._config.monitored_paths,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        }

        headers = self._headers()
        # Upsert: on conflict(api_key) do update
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"

        resp = await self._request(
            "POST",
            "/rest/v1/endpoints",
            headers=headers,
            json=payload,
        )
        if resp is not None:
            rows = resp.json()
            if rows and isinstance(rows, list):
                self._endpoint_id = rows[0]["id"]
            elif isinstance(rows, dict):
                self._endpoint_id = rows["id"]
            logger.info("Endpoint registered — id=%s", self._endpoint_id)
        else:
            logger.warning("Endpoint registration failed — will retry on next heartbeat")

        return self._endpoint_id or ""

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def send_heartbeat(self, status_data: dict[str, Any]) -> None:
        """Send a heartbeat to the ``endpoint_heartbeats`` table and update
        ``endpoints.last_heartbeat``.

        Parameters
        ----------
        status_data:
            Dict with keys such as ``cpu_percent``, ``ram_used_mb``,
            ``disk_used_gb``, ``sync_state``, ``status``.
        """
        if self._endpoint_id is None:
            logger.debug("Skipping heartbeat — not yet registered")
            return

        heartbeat = {
            "endpoint_id": self._endpoint_id,
            "cpu_percent": status_data.get("cpu_percent"),
            "ram_used_mb": status_data.get("ram_used_mb"),
            "disk_used_gb": status_data.get("disk_used_gb"),
            "sync_state": status_data.get("sync_state"),
            "status": status_data.get("status", "active"),
            "agent_version": self._agent_version(),
        }

        await self._request(
            "POST",
            "/rest/v1/endpoint_heartbeats",
            headers=self._headers(),
            json=heartbeat,
        )

        # Also update last_heartbeat on the endpoints row.
        now_iso = datetime.now(timezone.utc).isoformat()
        patch_headers = self._headers()
        patch_headers["Prefer"] = "return=minimal"
        await self._request(
            "PATCH",
            f"/rest/v1/endpoints?id=eq.{self._endpoint_id}",
            headers=patch_headers,
            json={"last_heartbeat": now_iso, "status": status_data.get("status", "active")},
        )

        logger.debug("Heartbeat sent — status=%s", status_data.get("status"))

    # ------------------------------------------------------------------
    # File sync
    # ------------------------------------------------------------------

    async def sync_files(self, file_records: list[dict[str, Any]]) -> None:
        """Upsert file index records to ``endpoint_file_index``.

        Records are sent in batches of :data:`_BATCH_SIZE` (50) to avoid
        oversized payloads.  Each record must include ``endpoint_id`` and
        ``file_path``; the ``Prefer`` header enables upsert on the
        ``(endpoint_id, file_path)`` unique constraint.
        """
        if not file_records:
            return

        headers = self._headers()
        headers["Prefer"] = "resolution=merge-duplicates"

        for i in range(0, len(file_records), _BATCH_SIZE):
            batch = file_records[i : i + _BATCH_SIZE]
            # Ensure each record carries the endpoint_id
            for rec in batch:
                rec.setdefault("endpoint_id", self._endpoint_id)
                rec.setdefault("company_id", self._config.company_id)

            await self._request(
                "POST",
                "/rest/v1/endpoint_file_index",
                headers=headers,
                json=batch,
            )

        logger.info("Synced %d file record(s) to Supabase", len(file_records))

    # ------------------------------------------------------------------
    # Activity sync
    # ------------------------------------------------------------------

    async def sync_activity(self, events: list[dict[str, Any]]) -> None:
        """Batch-insert activity events into ``endpoint_activity``."""
        if not events:
            return

        for ev in events:
            ev.setdefault("endpoint_id", self._endpoint_id)
            ev.setdefault("company_id", self._config.company_id)

        await self._request(
            "POST",
            "/rest/v1/endpoint_activity",
            headers=self._headers(),
            json=events,
        )

        logger.info("Synced %d activity event(s) to Supabase", len(events))

    # ------------------------------------------------------------------
    # Keystroke sync
    # ------------------------------------------------------------------

    async def sync_keystrokes(self, chunks: list[dict[str, Any]]) -> None:
        """Batch-insert keystroke chunks into ``endpoint_keystrokes``."""
        if not chunks:
            return

        for chunk in chunks:
            chunk.setdefault("endpoint_id", self._endpoint_id)
            chunk.setdefault("company_id", self._config.company_id)

        await self._request(
            "POST",
            "/rest/v1/endpoint_keystrokes",
            headers=self._headers(),
            json=chunks,
        )

        logger.info("Synced %d keystroke chunk(s) to Supabase", len(chunks))

    # ------------------------------------------------------------------
    # Screenshot upload
    # ------------------------------------------------------------------

    async def upload_screenshot(
        self, file_path: str, metadata: dict[str, Any]
    ) -> str:
        """Upload a screenshot image to Supabase Storage and insert a record
        into ``endpoint_screenshots``.

        Parameters
        ----------
        file_path:
            Local filesystem path to the screenshot image.
        metadata:
            Additional metadata (``trigger_type``, ``active_application``,
            ``window_title``, etc.).

        Returns
        -------
        str
            The ``storage_path`` within the bucket.
        """
        path_obj = Path(file_path)
        if not path_obj.is_file():
            logger.error("Screenshot file not found: %s", file_path)
            return ""

        # Build a unique storage path: <endpoint_id>/<YYYY-MM-DD>/<uuid>.<ext>
        now = datetime.now(timezone.utc)
        date_prefix = now.strftime("%Y-%m-%d")
        ext = path_obj.suffix or ".png"
        object_name = f"{self._endpoint_id}/{date_prefix}/{uuid.uuid4()}{ext}"

        file_size = path_obj.stat().st_size

        # --- Upload to Supabase Storage ---
        upload_url = f"/storage/v1/object/endpointclaw-screenshots/{object_name}"
        upload_headers = self._headers()
        upload_headers["Content-Type"] = "application/octet-stream"

        with open(file_path, "rb") as fh:
            file_bytes = fh.read()

        resp = await self._request(
            "POST",
            upload_url,
            headers=upload_headers,
            content=file_bytes,
            timeout=httpx.Timeout(_UPLOAD_TIMEOUT_S),
        )

        if resp is None:
            logger.error("Failed to upload screenshot to storage: %s", file_path)
            return ""

        storage_path = object_name

        # --- Insert metadata record ---
        record = {
            "endpoint_id": self._endpoint_id,
            "captured_at": now.isoformat(),
            "storage_path": storage_path,
            "trigger_type": metadata.get("trigger_type"),
            "active_application": metadata.get("active_application"),
            "window_title": metadata.get("window_title"),
            "file_size_bytes": file_size,
            "company_id": self._config.company_id,
            "metadata": metadata,
        }

        await self._request(
            "POST",
            "/rest/v1/endpoint_screenshots",
            headers=self._headers(),
            json=record,
        )

        logger.info("Screenshot uploaded — storage_path=%s", storage_path)
        return storage_path

    # ------------------------------------------------------------------
    # Command polling
    # ------------------------------------------------------------------

    async def poll_commands(self) -> list[dict[str, Any]]:
        """Fetch pending commands for this endpoint.

        Returns a (possibly empty) list of command rows ordered by
        ``priority`` then ``created_at``.
        """
        if self._endpoint_id is None:
            return []

        path = (
            f"/rest/v1/endpoint_commands"
            f"?endpoint_id=eq.{self._endpoint_id}"
            f"&status=eq.pending"
            f"&order=priority,created_at"
        )

        resp = await self._request("GET", path, headers=self._headers())
        if resp is None:
            return []

        commands: list[dict[str, Any]] = resp.json()
        if commands:
            logger.info("Polled %d pending command(s)", len(commands))
        return commands

    async def acknowledge_command(self, command_id: str) -> None:
        """Mark a command as acknowledged."""
        patch_headers = self._headers()
        patch_headers["Prefer"] = "return=minimal"
        await self._request(
            "PATCH",
            f"/rest/v1/endpoint_commands?id=eq.{command_id}",
            headers=patch_headers,
            json={
                "status": "acknowledged",
                "acknowledged_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.debug("Command acknowledged — id=%s", command_id)

    async def complete_command(
        self, command_id: str, result: dict[str, Any]
    ) -> None:
        """Mark a command as completed with its result payload."""
        patch_headers = self._headers()
        patch_headers["Prefer"] = "return=minimal"
        await self._request(
            "PATCH",
            f"/rest/v1/endpoint_commands?id=eq.{command_id}",
            headers=patch_headers,
            json={
                "status": "completed",
                "result": result,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info("Command completed — id=%s", command_id)

    async def fail_command(self, command_id: str, error: str) -> None:
        """Mark a command as failed with an error message."""
        patch_headers = self._headers()
        patch_headers["Prefer"] = "return=minimal"
        await self._request(
            "PATCH",
            f"/rest/v1/endpoint_commands?id=eq.{command_id}",
            headers=patch_headers,
            json={
                "status": "failed",
                "result": {"error": error},
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.warning("Command failed — id=%s error=%s", command_id, error)

    # ------------------------------------------------------------------
    # Company config
    # ------------------------------------------------------------------

    async def pull_company_config(self) -> dict[str, Any]:
        """Fetch the company configuration row for the current ``company_id``.

        Returns the full row dict, or an empty dict on failure.
        """
        path = (
            f"/rest/v1/company_configs"
            f"?id=eq.{self._config.company_id}"
            f"&select=*"
        )
        resp = await self._request("GET", path, headers=self._headers())
        if resp is None:
            return {}

        rows = resp.json()
        if rows and isinstance(rows, list):
            logger.info("Pulled company config for %s", self._config.company_id)
            return rows[0]
        return {}

    # ------------------------------------------------------------------
    # Offline queue
    # ------------------------------------------------------------------

    @property
    def offline_queue_size(self) -> int:
        """Number of operations waiting to be retried."""
        return len(self._offline_queue)

    async def process_offline_queue(self) -> int:
        """Replay queued operations.  Returns the number of successfully
        replayed operations.
        """
        if not self._offline_queue:
            return 0

        replayed = 0
        remaining: deque[_QueuedOperation] = deque(maxlen=_OFFLINE_QUEUE_MAX)

        while self._offline_queue:
            op = self._offline_queue.popleft()
            try:
                resp = await self._do_request(op.method, op.path, **op.kwargs)
                if resp is not None:
                    replayed += 1
                else:
                    # Still failing — put it back
                    remaining.append(op)
            except Exception:
                remaining.append(op)

        # Put un-replayed items back
        self._offline_queue = remaining

        if replayed:
            logger.info(
                "Offline queue: replayed %d operation(s), %d remaining",
                replayed,
                len(self._offline_queue),
            )
        return replayed

    # ------------------------------------------------------------------
    # Internal: headers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build the standard Supabase + EndpointClaw auth headers."""
        return {
            "apikey": self._config.supabase_key,
            "Authorization": f"Bearer {self._config.supabase_key}",
            "Content-Type": "application/json",
            "X-Endpoint-Key": self._config.api_key,
        }

    # ------------------------------------------------------------------
    # Internal: request with retry + offline buffering
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        _queue_on_failure: bool = True,
        **kwargs: Any,
    ) -> Optional[httpx.Response]:
        """Issue an HTTP request with automatic retry and offline buffering.

        Parameters
        ----------
        method:
            HTTP method (GET, POST, PATCH, DELETE).
        path:
            URL path relative to the Supabase base URL.
        _queue_on_failure:
            If ``True`` (the default), failed write operations (POST, PATCH)
            are pushed onto the offline queue for later retry.
        **kwargs:
            Forwarded to :meth:`httpx.AsyncClient.request`.

        Returns
        -------
        httpx.Response or None
            The response on success, or ``None`` if all retries failed.
        """
        resp = await self._do_request(method, path, **kwargs)

        if resp is not None:
            return resp

        # Queue write operations for later replay
        if _queue_on_failure and method.upper() in ("POST", "PATCH", "PUT"):
            self._offline_queue.append(_QueuedOperation(method, path, kwargs))
            logger.debug(
                "Queued offline operation: %s %s (queue size=%d)",
                method,
                path,
                len(self._offline_queue),
            )

        return None

    async def _do_request(
        self, method: str, path: str, **kwargs: Any
    ) -> Optional[httpx.Response]:
        """Execute an HTTP request with up to :data:`_MAX_RETRIES` attempts and
        exponential back-off.
        """
        if self._client is None:
            logger.error("HTTP client is not initialised — call init() first")
            return None

        backoff = _INITIAL_BACKOFF_S

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self._client.request(method, path, **kwargs)

                if resp.status_code < 400:
                    self._is_online = True
                    return resp

                # 4xx client errors are not retried (except 429)
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    logger.warning(
                        "%s %s returned %d: %s",
                        method,
                        path,
                        resp.status_code,
                        resp.text[:500],
                    )
                    self._is_online = True
                    return None

                # 429 or 5xx — retry
                logger.warning(
                    "%s %s returned %d (attempt %d/%d) — retrying in %.1fs",
                    method,
                    path,
                    resp.status_code,
                    attempt,
                    _MAX_RETRIES,
                    backoff,
                )

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                self._is_online = False
                logger.warning(
                    "%s %s network error (attempt %d/%d): %s — retrying in %.1fs",
                    method,
                    path,
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    backoff,
                )
            except httpx.HTTPError as exc:
                self._is_online = False
                logger.error(
                    "%s %s unexpected HTTP error (attempt %d/%d): %s",
                    method,
                    path,
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )

            if attempt < _MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= _BACKOFF_FACTOR

        # All retries exhausted
        self._is_online = False
        logger.error(
            "%s %s failed after %d attempts — marking offline",
            method,
            path,
            _MAX_RETRIES,
        )
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _agent_version() -> str:
        """Return the agent's semantic version string."""
        try:
            from .. import __version__  # type: ignore[import-not-found]
            return __version__
        except Exception:
            return "0.1.0"

    @staticmethod
    def _local_ip() -> str:
        """Best-effort local IP address."""
        import socket

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            s.connect(("10.254.254.254", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
