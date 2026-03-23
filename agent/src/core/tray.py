"""EndpointClaw Agent — system tray icon.

Provides a coloured status indicator in the system notification area and a
right-click context menu for common operations.  Uses *pystray* for
cross-platform tray support and *PIL / Pillow* for procedural icon
generation.  Gracefully degrades when these libraries are unavailable
(e.g. headless servers or non-Windows development machines).
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import webbrowser
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .orchestrator import AgentOrchestrator

logger = logging.getLogger("endpointclaw.tray")

# Colour palette for status icons
_COLOURS = {
    "connected": (76, 175, 80),     # green
    "syncing": (255, 193, 7),       # yellow / amber
    "disconnected": (244, 67, 54),  # red
    "paused": (158, 158, 158),      # grey
}

# Guard imports — pystray and PIL are optional dependencies.
try:
    import pystray                   # type: ignore[import-untyped]
    from PIL import Image, ImageDraw  # type: ignore[import-untyped]
    _HAS_TRAY = True
except ImportError:
    _HAS_TRAY = False
    logger.debug("pystray or Pillow not installed — tray icon disabled")


def _create_icon_image(colour: tuple[int, int, int], size: int = 64) -> Any:
    """Generate a solid-colour circle icon of the given *size* pixels."""
    if not _HAS_TRAY:
        return None
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(*colour, 255),
    )
    return image


class TrayIcon:
    """System-tray icon with status indicator and context menu.

    Designed to run in a dedicated daemon thread (see :meth:`start`).
    Communicates back to the :class:`AgentOrchestrator` to toggle
    monitoring, trigger shutdown, etc.
    """

    def __init__(self, orchestrator: AgentOrchestrator) -> None:
        self._orchestrator = orchestrator
        self._status: str = "disconnected"
        self._icon: Optional[Any] = None  # pystray.Icon
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Build and run the tray icon (blocking — call from a daemon thread)."""
        if not _HAS_TRAY:
            logger.info("Tray icon unavailable — pystray/Pillow not installed")
            return

        try:
            menu = pystray.Menu(
                pystray.MenuItem("Open Chat", self._on_open_chat, default=True),
                pystray.MenuItem("View Status", self._on_view_status),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda item: "Resume Monitoring" if self._orchestrator._monitoring_paused else "Pause Monitoring",
                    self._on_toggle_monitoring,
                ),
                pystray.MenuItem("Settings", self._on_open_settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("About", self._on_about),
                pystray.MenuItem("Quit", self._on_quit),
            )

            image = _create_icon_image(_COLOURS["disconnected"])
            self._icon = pystray.Icon(
                "EndpointClaw",
                image,
                "EndpointClaw Agent",
                menu,
            )
            logger.debug("Tray icon entering run loop")
            self._icon.run()
        except Exception:
            logger.exception("Tray icon run loop failed")

    def stop(self) -> None:
        """Stop the tray icon (safe to call from any thread)."""
        with self._lock:
            if self._icon is not None:
                try:
                    self._icon.stop()
                except Exception:
                    pass
                self._icon = None

    def update_status(self, status: str) -> None:
        """Change the icon colour to reflect the new *status*.

        Valid values: ``"connected"``, ``"syncing"``, ``"disconnected"``,
        ``"paused"``.
        """
        with self._lock:
            self._status = status
            if self._icon is None or not _HAS_TRAY:
                return
            colour = _COLOURS.get(status, _COLOURS["disconnected"])
            try:
                self._icon.icon = _create_icon_image(colour)
                self._icon.title = f"EndpointClaw — {status}"
            except Exception:
                logger.debug("Failed to update tray icon", exc_info=True)

    def show_notification(self, title: str, message: str) -> None:
        """Display a system notification balloon / toast."""
        with self._lock:
            if self._icon is None or not _HAS_TRAY:
                return
            try:
                self._icon.notify(message, title=title)
            except Exception:
                logger.debug("Failed to show notification", exc_info=True)

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    def _on_open_chat(self, icon: Any = None, item: Any = None) -> None:
        """Open the local chat UI in the default browser."""
        port = self._orchestrator.config.chat_port
        url = f"http://localhost:{port}"
        try:
            webbrowser.open(url)
        except Exception:
            logger.warning("Could not open browser for %s", url)

    def _on_view_status(self, icon: Any = None, item: Any = None) -> None:
        """Show a status summary dialog."""
        status = self._orchestrator.get_status()
        lines = [
            f"Status: {self._status}",
            f"Device: {status.get('device_name', 'unknown')}",
            f"Company: {status.get('company_id', 'unknown')}",
            f"User: {status.get('user_email', 'unknown')}",
            f"Chat port: {status.get('chat_port', '?')}",
            f"Active tasks: {len(status.get('active_tasks', []))}",
            f"Monitoring paused: {status.get('monitoring_paused', False)}",
        ]
        text = "\n".join(lines)

        # Try a tkinter messagebox; fall back to a logged message.
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("EndpointClaw Status", text)
            root.destroy()
        except Exception:
            logger.info("Agent status:\n%s", text)

    def _on_toggle_monitoring(self, icon: Any = None, item: Any = None) -> None:
        """Toggle between paused and active monitoring."""
        if self._orchestrator._monitoring_paused:
            self._orchestrator.resume_monitoring()
        else:
            self._orchestrator.pause_monitoring()

    def _on_open_settings(self, icon: Any = None, item: Any = None) -> None:
        """Open the config file in the system's default editor."""
        from core.config import get_config_path

        config_path = get_config_path()
        if not config_path.exists():
            # Ensure the file exists so the editor can open it
            self._orchestrator.config.save()

        try:
            if platform.system() == "Windows":
                os.startfile(str(config_path))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(config_path)])
            else:
                subprocess.Popen(["xdg-open", str(config_path)])
        except Exception:
            logger.warning("Could not open config file in editor: %s", config_path)

    def _on_about(self, icon: Any = None, item: Any = None) -> None:
        """Show version / about information."""
        try:
            from .. import __version__
        except ImportError:
            __version__ = "unknown"

        text = (
            f"EndpointClaw Agent v{__version__}\n\n"
            "Lightweight endpoint agent for file indexing,\n"
            "activity monitoring, and AI-assisted search.\n\n"
            "https://github.com/corvex/endpointclaw"
        )

        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("About EndpointClaw", text)
            root.destroy()
        except Exception:
            logger.info("About:\n%s", text)

    def _on_quit(self, icon: Any = None, item: Any = None) -> None:
        """Request a full agent shutdown."""
        logger.info("Quit requested from tray menu")
        self.stop()
        # Signal the orchestrator to stop.  Because we are in a non-async
        # thread we schedule the coroutine on the running event loop.
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self._orchestrator.stop(), loop)
            else:
                loop.run_until_complete(self._orchestrator.stop())
        except Exception:
            logger.debug("Could not schedule orchestrator stop from tray thread", exc_info=True)
