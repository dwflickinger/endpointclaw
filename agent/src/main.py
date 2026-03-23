"""EndpointClaw Agent — entry point.

Parses CLI arguments, configures logging, initializes the local database,
starts the orchestrator, and handles graceful shutdown on all platforms.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from .core.config import AgentConfig
from .core.database import Database
from .core.orchestrator import AgentOrchestrator

logger = logging.getLogger("endpointclaw")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _get_log_dir() -> Path:
    """Return the platform-appropriate log directory, creating it if needed."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        log_dir = base / "EndpointClaw" / "logs"
    else:
        log_dir = Path.home() / ".endpointclaw" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with a rotating file handler and a console handler."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric_level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler — 5 MB per file, keep 5 backups
    log_file = _get_log_dir() / "agent.log"
    fh = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(numeric_level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    logger.info("Logging initialised — level=%s, file=%s", level, log_file)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="endpointclaw",
        description="EndpointClaw — lightweight endpoint agent for file indexing, "
                    "activity monitoring, and AI-assisted search.",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="User email address (required on first run).",
    )
    parser.add_argument(
        "--company",
        type=str,
        default="corvex",
        help="Company / tenant identifier (default: corvex).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a JSON config file (overrides default location).",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the interactive setup wizard.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Shutdown helpers
# ---------------------------------------------------------------------------

_orchestrator_ref: Optional[AgentOrchestrator] = None


def _request_shutdown(signame: str) -> None:
    """Signal callback — requests graceful orchestrator shutdown."""
    logger.info("Received %s — initiating graceful shutdown", signame)
    if _orchestrator_ref is not None:
        asyncio.ensure_future(_orchestrator_ref.stop())


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Install SIGINT / SIGTERM handlers (Unix) or console-ctrl handler (Windows)."""
    if platform.system() == "Windows":
        # On Windows asyncio does not support add_signal_handler; fall back to
        # the signal module which works with the default event-loop on 3.10+.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda s, _f: _request_shutdown(signal.Signals(s).name))
        # Also try to handle the Windows service stop event via win32api.
        try:
            import win32api  # type: ignore[import-untyped]

            def _console_handler(ctrl_type: int) -> bool:
                _request_shutdown(f"CTRL_EVENT({ctrl_type})")
                return True

            win32api.SetConsoleCtrlHandler(_console_handler, True)
            logger.debug("Installed win32api console-ctrl handler")
        except ImportError:
            logger.debug("win32api not available — service stop events will not be caught")
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_shutdown, sig.name)


# ---------------------------------------------------------------------------
# Setup wizard (minimal interactive flow)
# ---------------------------------------------------------------------------

def _run_setup(config: AgentConfig) -> None:
    """Prompt the user for essential settings and persist them."""
    print("\n=== EndpointClaw Setup Wizard ===\n")

    config.user_email = input(f"Email [{config.user_email}]: ").strip() or config.user_email
    config.company_id = input(f"Company ID [{config.company_id}]: ").strip() or config.company_id
    config.supabase_key = (
        input(f"Supabase anon key [{config.supabase_key[:8] + '...' if config.supabase_key else ''}]: ").strip()
        or config.supabase_key
    )
    config.anthropic_api_key = (
        input(f"Anthropic API key [{config.anthropic_api_key[:8] + '...' if config.anthropic_api_key else ''}]: ").strip()
        or config.anthropic_api_key
    )

    config.save()
    print(f"\nConfig saved to {config.get_config_path()}\n")


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------

async def _run(args: argparse.Namespace) -> None:
    """Core async routine — boots the agent and waits for shutdown."""
    global _orchestrator_ref

    # --- Config ----------------------------------------------------------
    config = AgentConfig()
    if args.config:
        config._config_path_override = Path(args.config)
    config.load()

    # CLI overrides
    if args.email:
        config.user_email = args.email
    if args.company:
        config.company_id = args.company
    if args.debug:
        config.log_level = "DEBUG"

    # Persist any CLI-provided values
    config.save()

    # Reconfigure logging if the level changed
    if args.debug:
        setup_logging("DEBUG")

    # --- Setup wizard ----------------------------------------------------
    if args.setup:
        _run_setup(config)

    # Validate that we have an email on first run
    if not config.user_email:
        logger.error("No --email provided and none stored in config. Run with --email <addr> or --setup.")
        sys.exit(1)

    # --- Database --------------------------------------------------------
    db = Database(config.data_dir / "agent.db")
    await db.init()

    # --- Orchestrator ----------------------------------------------------
    orchestrator = AgentOrchestrator(config, db)
    _orchestrator_ref = orchestrator

    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop)

    try:
        await orchestrator.start()
        # Block until the orchestrator is told to stop
        while orchestrator.running:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    finally:
        await orchestrator.stop()
        await db.close()
        logger.info("Agent shutdown complete")


# ---------------------------------------------------------------------------
# Synchronous entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments, set up logging, and launch the async event loop."""
    args = parse_args()
    log_level = "DEBUG" if args.debug else "INFO"
    setup_logging(log_level)
    logger.info("EndpointClaw agent starting up")

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — exiting")
    except Exception:
        logger.exception("Fatal error in agent")
        sys.exit(1)


if __name__ == "__main__":
    main()
