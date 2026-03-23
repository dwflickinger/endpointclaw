"""EndpointClaw Agent — Claude AI integration.

Wraps the Anthropic Python SDK to provide file-aware conversational AI for
the local chat interface.  Supports tool use (file search, file read, file
open, recent files listing) so that Claude can interact with the user's
local file index.
"""

from __future__ import annotations

import logging
import os
import subprocess
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import anthropic

from ..core.config import AgentConfig

logger = logging.getLogger("endpointclaw.chat.claude")

__all__ = ["ClaudeClient"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL = "claude-sonnet-4-20250514"
_MAX_HISTORY_TOKENS = 50_000
# Rough estimate: 1 token ~ 4 chars (English).  We use this to decide when
# to trim conversation history.
_CHARS_PER_TOKEN = 4
_MAX_FILE_READ_CHARS = 5_000
_MAX_TOOL_ROUNDS = 5

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_CORVEX_SYSTEM_PROMPT = (
    "You are an AI assistant for Corvex Roofing Solutions. You help estimators "
    "find files, understand documents, and manage their work. You understand "
    "commercial roofing systems (TPO, EPDM, modified bitumen, metal), "
    "measurement terminology (squares, linear feet, penetrations), and the "
    "estimating workflow. You have access to the user's local file index and "
    "can search for, read, and open files. Be concise and helpful. When "
    "referencing files, include the full path."
)


# ---------------------------------------------------------------------------
# ClaudeClient
# ---------------------------------------------------------------------------

class ClaudeClient:
    """Anthropic Claude integration for the EndpointClaw local chat.

    Parameters
    ----------
    config:
        The global :class:`AgentConfig`.  Must have ``anthropic_api_key`` set.
    database:
        The local :class:`Database` instance for file index queries.
    """

    def __init__(self, config: AgentConfig, database: Any) -> None:
        self._config = config
        self._db = database
        self._client = anthropic.AsyncAnthropic(
            api_key=config.anthropic_api_key,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        message: str,
        conversation_history: list[dict[str, Any]],
        file_context: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Process a single chat turn, including tool-use loops.

        Parameters
        ----------
        message:
            The latest user message.
        conversation_history:
            Full list of prior messages (``role`` / ``content`` dicts).
        file_context:
            Optional list of file-index records to include as context.

        Returns
        -------
        dict
            ``{"response": str, "files": list[dict]}``
        """
        system_prompt = await self._build_system_prompt(file_context)
        messages = self._prepare_messages(conversation_history)
        tools = self._build_tools()

        referenced_files: list[dict[str, Any]] = []

        # Agentic tool-use loop
        for _round in range(_MAX_TOOL_ROUNDS):
            try:
                resp = await self._client.messages.create(
                    model=_MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                )
            except anthropic.APIError as exc:
                logger.error("Anthropic API error: %s", exc)
                return {
                    "response": (
                        "I'm sorry, I encountered an error connecting to the "
                        "AI service. Please try again in a moment."
                    ),
                    "files": [],
                }

            # Check for tool use
            if resp.stop_reason == "tool_use":
                # Append the assistant message (may contain text + tool_use blocks)
                messages.append({"role": "assistant", "content": resp.content})

                # Process each tool call
                tool_results: list[dict[str, Any]] = []
                for block in resp.content:
                    if block.type == "tool_use":
                        result_text, files = await self._handle_tool_call(
                            block.name, block.input
                        )
                        referenced_files.extend(files)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_text,
                            }
                        )

                messages.append({"role": "user", "content": tool_results})
                continue  # Next round with tool results

            # No tool use — extract final text response
            text_parts: list[str] = []
            for block in resp.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)

            return {
                "response": "\n".join(text_parts),
                "files": referenced_files,
            }

        # Exhausted tool rounds — return whatever we have
        logger.warning("Exhausted %d tool-use rounds", _MAX_TOOL_ROUNDS)
        return {
            "response": (
                "I've done extensive searching but couldn't fully complete "
                "the request. Here's what I found so far."
            ),
            "files": referenced_files,
        }

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    async def _build_system_prompt(
        self,
        file_context: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """Construct the system prompt with company context, user context,
        file summary, and current date/time.
        """
        parts: list[str] = []

        # Company context
        parts.append(await self._get_company_prompt())

        # User context
        parts.append(
            f"\nUser: {self._config.user_email} on {self._config.device_name}."
        )

        # File index summary
        file_summary = await self._get_file_summary()
        if file_summary:
            parts.append(f"\n{file_summary}")

        # Injected file context (from the chat handler's pre-search)
        if file_context:
            context_lines = ["\nRelevant files from the user's index:"]
            for f in file_context[:10]:
                name = f.get("filename", "")
                path = f.get("file_path", "")
                ftype = f.get("file_type", "")
                context_lines.append(f"  - {name} ({ftype}) — {path}")
            parts.append("\n".join(context_lines))

        # Recent activity summary
        activity_summary = await self._get_activity_summary()
        if activity_summary:
            parts.append(f"\n{activity_summary}")

        # Date / time
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"\nCurrent date/time: {now}")

        return "\n".join(parts)

    async def _get_company_prompt(self) -> str:
        """Return the company-specific system prompt.

        Tries the centrally-configured ``ai_system_prompt`` first; falls back
        to the built-in Corvex prompt.
        """
        try:
            config_row = await self._db.get_company_config(self._config.company_id)
            if config_row and config_row.get("ai_system_prompt"):
                return config_row["ai_system_prompt"]
        except Exception:
            pass

        return _CORVEX_SYSTEM_PROMPT

    async def _get_file_summary(self) -> str:
        """Generate a short statistical summary of the file index for the
        system prompt.
        """
        try:
            stats = await self._db.get_file_stats()
            if not stats:
                return ""

            total = stats.get("total_files", 0)
            by_type = stats.get("by_type", {})
            recent = stats.get("recent_files", [])

            lines = [f"File index: {total} files indexed."]

            if by_type:
                type_parts = [f"{k}: {v}" for k, v in by_type.items()]
                lines.append("By type: " + ", ".join(type_parts))

            if recent:
                lines.append("Recently modified:")
                for rf in recent[:5]:
                    name = rf.get("filename", "unknown")
                    mod = rf.get("modified_at", "")
                    lines.append(f"  - {name} (modified {mod})")

            return "\n".join(lines)
        except Exception:
            return ""

    async def _get_activity_summary(self) -> str:
        """Summarise the user's recent activity for system-prompt context."""
        try:
            if not hasattr(self._db, "get_recent_activity_summary"):
                return ""
            summary = await self._db.get_recent_activity_summary(limit=10)
            if not summary:
                return ""
            lines = ["Recent user activity:"]
            for entry in summary:
                app = entry.get("application", "unknown")
                title = entry.get("window_title", "")
                lines.append(f"  - {app}: {title}")
            return "\n".join(lines)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Message preparation
    # ------------------------------------------------------------------

    def _prepare_messages(
        self, conversation_history: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Convert the conversation history into the Anthropic messages
        format, trimming old messages if the token budget is exceeded.
        """
        messages: list[dict[str, Any]] = []

        for msg in conversation_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Only keep user and assistant roles
            if role not in ("user", "assistant"):
                continue

            messages.append({"role": role, "content": content})

        # Trim from the front if we exceed the token budget
        messages = self._trim_history(messages)
        return messages

    def _trim_history(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Remove oldest messages until total estimated tokens are under
        the budget.  Always keeps the most recent message.
        """
        max_chars = _MAX_HISTORY_TOKENS * _CHARS_PER_TOKEN

        def _total_chars(msgs: list[dict[str, Any]]) -> int:
            total = 0
            for m in msgs:
                c = m.get("content", "")
                if isinstance(c, str):
                    total += len(c)
                elif isinstance(c, list):
                    for block in c:
                        if isinstance(block, dict):
                            total += len(str(block.get("text", "")))
                        else:
                            total += len(str(block))
            return total

        while len(messages) > 1 and _total_chars(messages) > max_chars:
            messages.pop(0)

        return messages

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    @staticmethod
    def _build_tools() -> list[dict[str, Any]]:
        """Return Claude tool definitions for file operations."""
        return [
            {
                "name": "search_files",
                "description": (
                    "Search the user's local file index by keyword or filename. "
                    "Returns matching files with paths and metadata."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (filename, project name, keyword).",
                        },
                        "file_type": {
                            "type": "string",
                            "description": (
                                "Optional file type filter: estimate, proposal, "
                                "takeoff, invoice, photo, measurement, price_sheet, "
                                "correspondence, drawing, permit, other."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "read_file",
                "description": (
                    "Read the text content of a file from the user's machine. "
                    "Returns the first 5000 characters. Works with text-based "
                    "files (.txt, .csv, .json, .xml, etc.) and content extracts "
                    "from the file index for other formats."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Full filesystem path to the file.",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "open_file",
                "description": (
                    "Open a file on the user's desktop in its default application. "
                    "Use this when the user asks to view, edit, or open a file."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Full filesystem path to the file.",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "list_recent_files",
                "description": (
                    "List recently modified files from the user's file index, "
                    "optionally filtered by file type."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": "integer",
                            "description": "Number of recent files to return (default 10).",
                        },
                        "file_type": {
                            "type": "string",
                            "description": "Optional file type filter.",
                        },
                    },
                    "required": [],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _handle_tool_call(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Execute a tool call and return ``(result_text, file_references)``.

        Returns
        -------
        tuple[str, list[dict]]
            The textual result for Claude, and any file references to surface
            in the chat response.
        """
        files: list[dict[str, Any]] = []

        try:
            if tool_name == "search_files":
                return await self._tool_search_files(tool_input, files)
            elif tool_name == "read_file":
                return await self._tool_read_file(tool_input, files)
            elif tool_name == "open_file":
                return self._tool_open_file(tool_input, files)
            elif tool_name == "list_recent_files":
                return await self._tool_list_recent_files(tool_input, files)
            else:
                return f"Unknown tool: {tool_name}", files
        except Exception as exc:
            logger.exception("Tool call failed: %s", tool_name)
            return f"Error executing {tool_name}: {exc}", files

    async def _tool_search_files(
        self, inp: dict[str, Any], files: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        query = inp.get("query", "")
        file_type = inp.get("file_type")

        results = await self._db.search_files(
            query=query, file_type=file_type, limit=20
        )

        if not results:
            return f"No files found matching '{query}'.", files

        lines: list[str] = [f"Found {len(results)} file(s):"]
        for r in results:
            r_dict = r if isinstance(r, dict) else dict(r)
            name = r_dict.get("filename", "")
            path = r_dict.get("file_path", "")
            ftype = r_dict.get("file_type", "")
            size = r_dict.get("file_size", 0)
            modified = r_dict.get("modified_at", "")
            lines.append(
                f"  - {name} ({ftype}, {self._human_size(size)}) — {path} "
                f"(modified {modified})"
            )
            files.append(r_dict)

        return "\n".join(lines), files

    async def _tool_read_file(
        self, inp: dict[str, Any], files: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        file_path = inp.get("file_path", "")
        p = Path(file_path)

        # First, try reading the actual file (for text-based formats)
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[
                    :_MAX_FILE_READ_CHARS
                ]
                files.append({"file_path": file_path, "filename": p.name})
                truncated = " (truncated)" if len(text) == _MAX_FILE_READ_CHARS else ""
                return f"Content of {p.name}{truncated}:\n\n{text}", files
            except Exception:
                pass

        # Fall back to the stored content_extract from the file index
        try:
            record = await self._db.get_file_by_path(file_path)
            if record:
                r_dict = record if isinstance(record, dict) else dict(record)
                extract = r_dict.get("content_extract", "")
                if extract:
                    files.append(r_dict)
                    return (
                        f"Content extract for {r_dict.get('filename', file_path)}:\n\n"
                        f"{extract[:_MAX_FILE_READ_CHARS]}"
                    ), files
        except Exception:
            pass

        return f"Could not read file: {file_path}", files

    def _tool_open_file(
        self, inp: dict[str, Any], files: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        file_path = inp.get("file_path", "")
        p = Path(file_path)

        if not p.exists():
            return f"File not found: {file_path}", files

        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(file_path)  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])

            files.append({"file_path": file_path, "filename": p.name})
            return f"Opened {p.name} in the default application.", files
        except Exception as exc:
            return f"Failed to open {file_path}: {exc}", files

    async def _tool_list_recent_files(
        self, inp: dict[str, Any], files: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        count = inp.get("count", 10)
        file_type = inp.get("file_type")

        results = await self._db.get_recent_files(
            limit=count, file_type=file_type
        )

        if not results:
            return "No recently modified files found.", files

        lines = [f"Last {len(results)} recently modified file(s):"]
        for r in results:
            r_dict = r if isinstance(r, dict) else dict(r)
            name = r_dict.get("filename", "")
            path = r_dict.get("file_path", "")
            ftype = r_dict.get("file_type", "")
            modified = r_dict.get("modified_at", "")
            lines.append(f"  - {name} ({ftype}) — {path} (modified {modified})")
            files.append(r_dict)

        return "\n".join(lines), files

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _human_size(size_bytes: int | None) -> str:
        """Format a byte count as a human-readable string."""
        if not size_bytes:
            return "unknown size"
        for unit in ("B", "KB", "MB", "GB"):
            if abs(size_bytes) < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024  # type: ignore[assignment]
        return f"{size_bytes:.1f} TB"
