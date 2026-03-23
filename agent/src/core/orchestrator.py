"""EndpointClaw Agent — main orchestrator.

Manages all background tasks (heartbeat, sync, command polling, file scanning,
activity tracking, keystroke monitoring, screenshot capture, and the local
chat server).  Each subsystem is started as an ``asyncio.Task`` and
gracefully cancelled on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .config import AgentConfig
from .database import Database
from ..indexing.scanner import FileScanner
from ..indexing.watcher import FileWatcher
from ..comms.relay import RelayClient
from ..comms.sync import SyncManager
from ..monitoring.activity import ActivityTracker
from ..monitoring.keystrokes import KeystrokeMonitor
from ..monitoring.screenshots import ScreenshotEngine
from ..chat.server import ChatServer
from ..chat.claude import ClaudeClient

logger = logging.getLogger("endpointclaw.orchestrator")


class AgentOrchestrator:
    """Central coordinator for every subsystem of the endpoint agent.

    Owns the shared ``httpx.AsyncClient``, spawns background loops for
    heartbeat / sync / command-polling, and delegates to subsystem modules
    (scanner, watcher, relay, activity tracker, etc.).  All long-running
    work is expressed as ``asyncio.Task`` objects gathered and cancelled in
    :meth:`stop`.
    """

    def __init__(self, config: AgentConfig, database: Database) -> None:
        self.config = config
        self.db = database

        self.running: bool = False
        self._monitoring_paused: bool = False
        self._tasks: list[asyncio.Task[None]] = []
        self._http: Optional[httpx.AsyncClient] = None

        # Subsystem references — populated lazily in start()
        self._scanner: Any = None
        self._watcher: Any = None
        self._relay: Any = None
        self._sync_manager: Any = None
        self._activity_tracker: Any = None
        self._keystroke_monitor: Any = None
        self._screenshot_engine: Any = None
        self._chat_server: Any = None
        self._tray_icon: Any = None
        self._tray_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Boot every subsystem and start background loops."""
        logger.info("Orchestrator starting")
        self.running = True
        self._http = httpx.AsyncClient(timeout=30)

        # 1. Pull remote company config
        try:
            await self.config.pull_company_config(self._http)
        except Exception as exc:
            logger.warning("Could not pull company config on start: %s", exc)

        # 2. System tray (runs in its own daemon thread)
        self._start_tray()

        # 3. Filesystem watcher
        await self._start_watcher()

        # 4. Initial file scan (background)
        self._tasks.append(asyncio.create_task(self._initial_scan(), name="initial_scan"))

        # 5. Core loops
        self._tasks.append(asyncio.create_task(self._heartbeat_loop(), name="heartbeat"))
        self._tasks.append(asyncio.create_task(self._sync_loop(), name="sync"))
        self._tasks.append(asyncio.create_task(self._command_poll_loop(), name="command_poll"))

        # 6. Activity tracker (Phase 2 — placeholder)
        self._tasks.append(asyncio.create_task(self._activity_tracker_loop(), name="activity_tracker"))

        # 7. Keystroke monitor (if enabled)
        if self.config.keystroke_enabled:
            self._tasks.append(asyncio.create_task(self._keystroke_loop(), name="keystroke"))

        # 8. Screenshot engine (if enabled)
        if self.config.screenshot_enabled:
            self._tasks.append(asyncio.create_task(self._screenshot_loop(), name="screenshot"))

        # 9. Local chat server
        self._tasks.append(asyncio.create_task(self._chat_server_loop(), name="chat_server"))

        logger.info(
            "Orchestrator started — %d background tasks, monitoring_paused=%s",
            len(self._tasks),
            self._monitoring_paused,
        )

    async def stop(self) -> None:
        """Gracefully cancel all background tasks and close resources."""
        if not self.running:
            return
        logger.info("Orchestrator stopping — cancelling %d tasks", len(self._tasks))
        self.running = False

        for task in self._tasks:
            task.cancel()

        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        for task, result in zip(self._tasks, results):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.error("Task %s raised during shutdown: %s", task.get_name(), result)
        self._tasks.clear()

        # Stop the tray icon
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                logger.debug("Tray icon stop error (non-critical)", exc_info=True)

        # Close HTTP client
        if self._http:
            await self._http.aclose()
            self._http = None

        logger.info("Orchestrator stopped")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return a snapshot of the agent's current operational status."""
        return {
            "running": self.running,
            "monitoring_paused": self._monitoring_paused,
            "active_tasks": [t.get_name() for t in self._tasks if not t.done()],
            "device_name": self.config.device_name,
            "company_id": self.config.company_id,
            "user_email": self.config.user_email,
            "chat_port": self.config.chat_port,
            "supabase_url": self.config.supabase_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def pause_monitoring(self) -> None:
        """Pause file scanning and activity tracking."""
        self._monitoring_paused = True
        logger.info("Monitoring paused")
        if self._tray_icon:
            try:
                self._tray_icon.update_status("paused")
            except Exception:
                pass

    def resume_monitoring(self) -> None:
        """Resume file scanning and activity tracking."""
        self._monitoring_paused = False
        logger.info("Monitoring resumed")
        if self._tray_icon:
            try:
                self._tray_icon.update_status("connected")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Tray icon
    # ------------------------------------------------------------------

    def _start_tray(self) -> None:
        """Launch the system-tray icon in a daemon thread."""
        try:
            from .tray import TrayIcon

            self._tray_icon = TrayIcon(self)
            self._tray_thread = threading.Thread(
                target=self._tray_icon.start, daemon=True, name="tray"
            )
            self._tray_thread.start()
            logger.info("System tray icon started")
        except Exception as exc:
            logger.warning("Could not start tray icon (non-critical): %s", exc)

    # ------------------------------------------------------------------
    # Filesystem watcher
    # ------------------------------------------------------------------

    async def _start_watcher(self) -> None:
        """Initialise the filesystem watcher."""
        try:
            self._scanner = FileScanner(self.config, self.db)
            self._watcher = FileWatcher(self.config, self.db, self._scanner)
            self._watcher.start()
            logger.info("Filesystem watcher started")
        except Exception as exc:
            logger.warning("Filesystem watcher failed to start: %s", exc)

    async def _initial_scan(self) -> None:
        """Run a one-time full scan of monitored paths."""
        try:
            if self._scanner is None:
                self._scanner = FileScanner(self.config, self.db)
            logger.info("Starting initial file scan")
            await self._scanner.scan_all()
            logger.info("Initial file scan complete")
        except asyncio.CancelledError:
            logger.debug("Initial scan cancelled")
        except Exception:
            logger.exception("Initial file scan error")

    # ------------------------------------------------------------------
    # Core background loops
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Send a periodic heartbeat to Supabase."""
        while self.running:
            try:
                await self._send_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Heartbeat error")
            await asyncio.sleep(self.config.heartbeat_interval)

    async def _send_heartbeat(self) -> None:
        """POST a heartbeat record to the Supabase endpoints table."""
        if not self.config.supabase_key or self._http is None:
            return

        url = f"{self.config.supabase_url}/rest/v1/endpoints"
        headers = {
            "apikey": self.config.supabase_key,
            "Authorization": f"Bearer {self.config.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {
            "id": self.config.device_id,
            "device_name": self.config.device_name,
            "company_id": self.config.company_id,
            "user_email": self.config.user_email,
            "status": "paused" if self._monitoring_paused else "online",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "agent_version": "0.1.0",
        }
        try:
            resp = await self._http.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            logger.debug("Heartbeat sent successfully")
            if self._tray_icon:
                self._tray_icon.update_status("connected")
        except httpx.HTTPStatusError as exc:
            logger.warning("Heartbeat HTTP error %s: %s", exc.response.status_code, exc.response.text[:200])
            if self._tray_icon:
                self._tray_icon.update_status("disconnected")
        except httpx.RequestError as exc:
            logger.warning("Heartbeat request error: %s", exc)
            if self._tray_icon:
                self._tray_icon.update_status("disconnected")

    async def _sync_loop(self) -> None:
        """Periodically push unsynced data to Supabase."""
        while self.running:
            try:
                await self._run_sync()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sync loop error")
            await asyncio.sleep(self.config.sync_interval)

    async def _run_sync(self) -> None:
        """Push unsynced files, activity, keystrokes, and screenshots."""
        if not self.config.supabase_key or self._http is None:
            return

        if self._tray_icon:
            self._tray_icon.update_status("syncing")

        headers = {
            "apikey": self.config.supabase_key,
            "Authorization": f"Bearer {self.config.supabase_key}",
            "Content-Type": "application/json",
        }

        # --- Files --------------------------------------------------------
        unsynced_files = await self.db.get_unsynced_files()
        if unsynced_files:
            logger.info("Syncing %d file record(s)", len(unsynced_files))
            url = f"{self.config.supabase_url}/rest/v1/endpoint_file_index"
            synced_ids: list[int] = []
            for f in unsynced_files:
                # Tags are stored as JSON string in SQLite but Supabase
                # expects a native Postgres text[] array.
                raw_tags = f["tags"]
                if isinstance(raw_tags, str):
                    try:
                        import json as _json
                        raw_tags = _json.loads(raw_tags)
                    except Exception:
                        raw_tags = None
                if not isinstance(raw_tags, list):
                    raw_tags = None

                payload = {
                    "endpoint_id": self.config.device_id,
                    "company_id": self.config.company_id,
                    "file_path": f["file_path"],
                    "filename": f["filename"],
                    "file_type": f["file_type"],
                    "file_size": f["file_size"],
                    "modified_at": f["modified_at"],
                    "content_hash": f["content_hash"],
                    "content_extract": f["content_extract"],
                    "inferred_project": f["inferred_project"],
                    "inferred_customer": f["inferred_customer"],
                    "tags": raw_tags,
                }
                try:
                    resp = await self._http.post(url, json=payload, headers={**headers, "Prefer": "resolution=merge-duplicates"})
                    resp.raise_for_status()
                    synced_ids.append(f["id"])
                except Exception as exc:
                    logger.warning("Failed to sync file id=%s: %s", f["id"], exc)
            if synced_ids:
                await self.db.mark_files_synced(synced_ids)

        # --- Activity events ----------------------------------------------
        unsynced_activity = await self.db.get_unsynced_activity()
        if unsynced_activity:
            logger.info("Syncing %d activity event(s)", len(unsynced_activity))
            url = f"{self.config.supabase_url}/rest/v1/endpoint_activity"
            synced_ids = []
            for evt in unsynced_activity:
                payload = {
                    "endpoint_id": self.config.device_id,
                    "company_id": self.config.company_id,
                    "event_type": evt["event_type"],
                    "application": evt["application"],
                    "window_title": evt["window_title"],
                    "file_path": evt["file_path"],
                    "duration_ms": evt["duration_ms"],
                    "metadata": evt["metadata"],
                    "created_at": evt["created_at"],
                }
                try:
                    resp = await self._http.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    synced_ids.append(evt["id"])
                except Exception as exc:
                    logger.warning("Failed to sync activity id=%s: %s", evt["id"], exc)
            if synced_ids:
                await self.db.mark_activity_synced(synced_ids)

        # --- Keystrokes ---------------------------------------------------
        unsynced_ks = await self.db.get_unsynced_keystrokes()
        if unsynced_ks:
            logger.info("Syncing %d keystroke chunk(s)", len(unsynced_ks))
            url = f"{self.config.supabase_url}/rest/v1/endpoint_keystrokes"
            synced_ids = []
            for chunk in unsynced_ks:
                payload = {
                    "endpoint_id": self.config.device_id,
                    "company_id": self.config.company_id,
                    "chunk_start": chunk["chunk_start"],
                    "chunk_end": chunk["chunk_end"],
                    "text_content": chunk["text_content"],
                    "application": chunk["application"],
                    "window_title": chunk["window_title"],
                    "char_count": chunk["char_count"],
                }
                try:
                    resp = await self._http.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    synced_ids.append(chunk["id"])
                except Exception as exc:
                    logger.warning("Failed to sync keystroke id=%s: %s", chunk["id"], exc)
            if synced_ids:
                await self.db.mark_keystrokes_synced(synced_ids)

        # --- Screenshots --------------------------------------------------
        unsynced_ss = await self.db.get_unsynced_screenshots()
        if unsynced_ss:
            logger.info("Syncing %d screenshot record(s)", len(unsynced_ss))
            url = f"{self.config.supabase_url}/rest/v1/endpoint_screenshots"
            synced_ids = []
            for ss in unsynced_ss:
                payload = {
                    "endpoint_id": self.config.device_id,
                    "company_id": self.config.company_id,
                    "captured_at": ss["captured_at"],
                    "storage_path": ss["storage_path"],
                    "trigger_type": ss["trigger_type"],
                    "active_application": ss["active_application"],
                    "window_title": ss["window_title"],
                    "file_size_bytes": ss["file_size_bytes"],
                }
                try:
                    resp = await self._http.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    synced_ids.append(ss["id"])
                except Exception as exc:
                    logger.warning("Failed to sync screenshot id=%s: %s", ss["id"], exc)
            if synced_ids:
                await self.db.mark_screenshots_synced(synced_ids)

        if self._tray_icon:
            self._tray_icon.update_status("connected")

    # ------------------------------------------------------------------
    # Command polling
    # ------------------------------------------------------------------

    async def _command_poll_loop(self) -> None:
        """Poll Supabase for pending commands addressed to this endpoint."""
        while self.running:
            try:
                await self._poll_commands()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Command poll error")
            await asyncio.sleep(self.config.command_poll_interval)

    async def _poll_commands(self) -> None:
        """Fetch and process any pending commands from the server."""
        if not self.config.supabase_key or self._http is None:
            return

        url = (
            f"{self.config.supabase_url}/rest/v1/endpoint_commands"
            f"?endpoint_id=eq.{self.config.device_id}&status=eq.pending&select=*"
        )
        headers = {
            "apikey": self.config.supabase_key,
            "Authorization": f"Bearer {self.config.supabase_key}",
            "Accept": "application/json",
        }
        try:
            resp = await self._http.get(url, headers=headers)
            resp.raise_for_status()
            commands = resp.json()
        except Exception as exc:
            logger.debug("Command poll failed: %s", exc)
            return

        for cmd in commands:
            try:
                await self._process_command(cmd)
                # Mark the command as completed
                await self._ack_command(cmd["id"], "completed")
            except Exception:
                logger.exception("Error processing command id=%s", cmd.get("id"))
                await self._ack_command(cmd["id"], "failed")

    async def _process_command(self, command: dict[str, Any]) -> None:
        """Dispatch a command by its ``command_type`` field."""
        cmd_type = command.get("command_type", "")
        payload = command.get("payload", {})
        if isinstance(payload, str):
            import json
            payload = json.loads(payload)

        logger.info("Processing command type=%s id=%s", cmd_type, command.get("id"))

        if cmd_type == "query":
            query = payload.get("query", "")
            file_type = payload.get("file_type")
            results = await self.db.search_files(query, file_type=file_type)
            logger.info("Query '%s' returned %d results", query, len(results))

        elif cmd_type == "action":
            action = payload.get("action")
            if action == "pause":
                self.pause_monitoring()
            elif action == "resume":
                self.resume_monitoring()
            else:
                logger.warning("Unknown action: %s", action)

        elif cmd_type == "config_update":
            for key, value in payload.items():
                if hasattr(self.config, key) and not key.startswith("_"):
                    setattr(self.config, key, value)
            self.config.save()
            logger.info("Config updated remotely with keys: %s", list(payload.keys()))

        elif cmd_type == "screenshot_now":
            logger.info("On-demand screenshot requested (not yet implemented)")

        elif cmd_type == "file_retrieve":
            file_path = payload.get("file_path")
            if file_path:
                record = await self.db.get_file_by_path(file_path)
                logger.info("File retrieve: %s — found=%s", file_path, record is not None)

        elif cmd_type == "playbook_trigger":
            playbook_id = payload.get("playbook_id")
            logger.info("Playbook trigger: %s (not yet implemented)", playbook_id)

        else:
            logger.warning("Unknown command type: %s", cmd_type)

    async def _ack_command(self, command_id: str, status: str) -> None:
        """Update the command status on the server."""
        if not self.config.supabase_key or self._http is None:
            return
        url = f"{self.config.supabase_url}/rest/v1/endpoint_commands?id=eq.{command_id}"
        headers = {
            "apikey": self.config.supabase_key,
            "Authorization": f"Bearer {self.config.supabase_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = await self._http.patch(url, json={"status": status}, headers=headers)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to ack command %s: %s", command_id, exc)

    # ------------------------------------------------------------------
    # Phase-2 subsystems
    # ------------------------------------------------------------------

    async def _activity_tracker_loop(self) -> None:
        """Activity tracking loop — captures foreground app, window title, idle state."""
        try:
            self._activity_tracker = ActivityTracker(self.config, self.db)
            logger.info("Activity tracker starting")
            await self._activity_tracker.start()
            # start() launches an internal task and returns immediately —
            # keep this wrapper alive until cancellation so the task persists.
            while self._running:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Activity tracker error")
        finally:
            if self._activity_tracker:
                await self._activity_tracker.stop()

    async def _keystroke_loop(self) -> None:
        """Keystroke monitoring loop."""
        try:
            self._keystroke_monitor = KeystrokeMonitor(self.config, self.db)
            self._keystroke_monitor.start()  # starts hook thread + flush loop
            logger.info("Keystroke monitor started")
            while self.running:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Keystroke monitor error")
        finally:
            if self._keystroke_monitor:
                self._keystroke_monitor.stop()

    async def _screenshot_loop(self) -> None:
        """Screenshot capture loop."""
        try:
            self._screenshot_engine = ScreenshotEngine(self.config, self.db)
            await self._screenshot_engine.start()
            logger.info("Screenshot engine started — interval=%ds", self.config.screenshot_interval)
            while self.running:
                if not self._monitoring_paused:
                    await self._screenshot_engine.capture_now(trigger_type="interval")
                await asyncio.sleep(self.config.screenshot_interval)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Screenshot engine error")
        finally:
            if self._screenshot_engine:
                await self._screenshot_engine.stop()

    async def _chat_server_loop(self) -> None:
        """Local chat/API server with Claude integration."""
        try:
            claude_client = ClaudeClient(self.config, self.db)
            self._chat_server = ChatServer(self.config, self.db, claude_client)
            self._chat_server.start()
            logger.info("Chat server started on port %d", self.config.chat_port)
            while self.running:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Chat server error")
        finally:
            if self._chat_server:
                try:
                    self._chat_server.stop()
                except Exception:
                    pass
