"""EndpointClaw Agent — local chat server.

Exposes a FastAPI application on ``localhost:{config.chat_port}`` (default
8742) that serves the chat web UI and provides REST endpoints for chat
interaction, file search, and agent status.

The server runs in a background thread so as not to block the async
orchestrator event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..core.config import AgentConfig

logger = logging.getLogger("endpointclaw.chat.server")

__all__ = ["ChatServer"]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    files: list[dict[str, Any]] = []


class SearchRequest(BaseModel):
    query: str
    file_type: Optional[str] = None


class OpenFileRequest(BaseModel):
    file_path: str


# ---------------------------------------------------------------------------
# ChatServer
# ---------------------------------------------------------------------------

class ChatServer:
    """Local HTTP server that exposes the chat UI and REST API.

    Parameters
    ----------
    config:
        The global :class:`AgentConfig`.
    database:
        The local :class:`Database` instance.
    claude_client:
        An initialised :class:`~chat.claude.ClaudeClient` for AI responses.
    """

    def __init__(
        self,
        config: AgentConfig,
        database: Any,
        claude_client: Any,
    ) -> None:
        self._config = config
        self._db = database
        self._claude = claude_client

        self._app = self._build_app()
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the uvicorn server in a daemon thread."""
        uvi_config = uvicorn.Config(
            app=self._app,
            host="127.0.0.1",
            port=self._config.chat_port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(uvi_config)

        self._thread = threading.Thread(
            target=self._server.run,
            name="endpointclaw-chat-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Chat server started — http://127.0.0.1:%d", self._config.chat_port
        )

    def stop(self) -> None:
        """Signal the uvicorn server to shut down."""
        if self._server is not None:
            self._server.should_exit = True
            logger.info("Chat server stop requested")
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
            logger.info("Chat server thread joined")

    # ------------------------------------------------------------------
    # FastAPI app construction
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        """Create and configure the FastAPI application."""
        app = FastAPI(
            title="EndpointClaw Chat",
            version="0.1.0",
            docs_url=None,
            redoc_url=None,
        )

        # ---- Routes ------------------------------------------------

        @app.get("/", response_class=HTMLResponse)
        async def root() -> HTMLResponse:
            """Serve the chat UI HTML page."""
            html = self._load_ui_html()
            return HTMLResponse(content=html)

        @app.get("/api/status")
        async def status() -> JSONResponse:
            """Return the current agent status."""
            return JSONResponse(content=await self._get_status())

        @app.post("/api/chat", response_model=ChatResponse)
        async def chat(req: ChatRequest) -> ChatResponse:
            """Process a user chat message and return an AI response."""
            return await self._handle_chat(req)

        @app.get("/api/conversations")
        async def list_conversations() -> JSONResponse:
            """Return a list of recent conversations."""
            convos = await self._db.get_recent_conversations(limit=20)
            return JSONResponse(content=convos)

        @app.get("/api/conversations/{conversation_id}")
        async def get_conversation(conversation_id: str) -> JSONResponse:
            """Return the full message history for a conversation."""
            convo = await self._db.get_conversation(conversation_id)
            if convo is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return JSONResponse(content=convo)

        @app.post("/api/search")
        async def search_files(req: SearchRequest) -> JSONResponse:
            """Search the local file index."""
            results = await self._db.search_files(
                query=req.query,
                file_type=req.file_type,
                limit=20,
            )
            return JSONResponse(content=results)

        @app.post("/api/open-file")
        async def open_file(req: OpenFileRequest) -> JSONResponse:
            """Open a file in the default desktop application."""
            return JSONResponse(content=self._open_file_on_desktop(req.file_path))

        @app.get("/api/quick-actions")
        async def quick_actions() -> JSONResponse:
            """Return available quick actions for the chat UI."""
            actions = [
                {
                    "label": "Find file...",
                    "prompt": "Find the file named ",
                    "icon": "search",
                },
                {
                    "label": "Summarize document...",
                    "prompt": "Summarize the document at ",
                    "icon": "document",
                },
                {
                    "label": "What did I work on today?",
                    "prompt": "What did I work on today?",
                    "icon": "clock",
                },
                {
                    "label": "Recent files",
                    "prompt": "Show me the most recently modified files.",
                    "icon": "folder",
                },
                {
                    "label": "Project files",
                    "prompt": "List all files for the project ",
                    "icon": "briefcase",
                },
            ]
            return JSONResponse(content=actions)

        @app.get("/api/files/stats")
        async def file_stats() -> JSONResponse:
            """Return file index statistics."""
            stats = await self._db.get_file_stats()
            return JSONResponse(content=stats)

        return app

    # ------------------------------------------------------------------
    # Chat handler
    # ------------------------------------------------------------------

    async def _handle_chat(self, req: ChatRequest) -> ChatResponse:
        """Process a single chat turn.

        1. Load or create conversation.
        2. Append user message.
        3. Call Claude with conversation history + file context.
        4. Save updated conversation to DB.
        5. Return response with any file references.
        """
        # Resolve or create conversation
        conversation_id = req.conversation_id or str(uuid.uuid4())
        conversation = await self._db.get_conversation(conversation_id)

        if conversation is None:
            conversation = {
                "id": conversation_id,
                "messages": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        messages: list[dict[str, Any]] = conversation.get("messages", [])

        # Append user message
        messages.append(
            {
                "role": "user",
                "content": req.message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Gather file context for the AI
        file_context = await self._gather_file_context(req.message)

        # Call Claude
        try:
            result = await self._claude.chat(
                message=req.message,
                conversation_history=messages,
                file_context=file_context,
            )
        except Exception as exc:
            logger.exception("Claude API call failed")
            result = {
                "response": (
                    f"I apologise, but I encountered an error processing your "
                    f"request: {exc}. Please try again."
                ),
                "files": [],
            }

        # Append assistant message
        messages.append(
            {
                "role": "assistant",
                "content": result["response"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "files": result.get("files", []),
            }
        )

        # Persist conversation
        conversation["messages"] = messages
        conversation["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._db.save_conversation(conversation)

        return ChatResponse(
            response=result["response"],
            conversation_id=conversation_id,
            files=result.get("files", []),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_status(self) -> dict[str, Any]:
        """Collect agent status for the /api/status endpoint."""
        file_count = await self._db.get_file_count()
        sync_state = await self._db.get_sync_state() if hasattr(self._db, "get_sync_state") else {}
        return {
            "status": "connected",
            "file_count": file_count,
            "sync_state": sync_state,
            "version": self._agent_version(),
            "company_id": self._config.company_id,
            "user_email": self._config.user_email,
            "device_name": self._config.device_name,
        }

    async def _gather_file_context(self, message: str) -> list[dict[str, Any]]:
        """Do a lightweight keyword search against the local file index so
        that Claude has relevant file context when responding.
        """
        try:
            results = await self._db.search_files(query=message, limit=10)
            return results if isinstance(results, list) else []
        except Exception:
            logger.debug("File context search failed — continuing without context")
            return []

    def _load_ui_html(self) -> str:
        """Load the chat UI HTML from the templates directory.

        Falls back to a minimal placeholder if the file is not found.
        """
        # Try the co-located templates directory first
        template_path = (
            Path(__file__).resolve().parent.parent / "ui" / "templates" / "index.html"
        )
        if template_path.is_file():
            return template_path.read_text(encoding="utf-8")

        # Fallback — try importlib.resources (for packaged installs)
        try:
            ref = importlib_resources.files("ui.templates").joinpath("index.html")
            return ref.read_text(encoding="utf-8")
        except Exception:
            pass

        # Minimal fallback page
        return (
            "<!DOCTYPE html><html><head><title>EndpointClaw Chat</title></head>"
            "<body><h1>EndpointClaw Chat</h1>"
            "<p>The chat UI template was not found. Please ensure "
            "<code>ui/templates/index.html</code> exists.</p>"
            "</body></html>"
        )

    @staticmethod
    def _open_file_on_desktop(file_path: str) -> dict[str, Any]:
        """Open a file using the desktop's default application."""
        p = Path(file_path)
        if not p.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(file_path)  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])
            return {"success": True, "file_path": file_path}
        except Exception as exc:
            logger.error("Failed to open file %s: %s", file_path, exc)
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _agent_version() -> str:
        try:
            from .. import __version__  # type: ignore[import-not-found]
            return __version__
        except Exception:
            return "0.1.0"
