"""EndpointClaw Agent — configuration manager.

Loads, merges, and persists agent configuration from a JSON file, environment
variables, and remote Supabase company settings.  All filesystem paths are
represented as ``pathlib.Path`` objects.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import socket
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("endpointclaw.config")

# Supabase project URL — baked-in default
_DEFAULT_SUPABASE_URL = "https://twgdhuimqspfoimfmyxz.supabase.co"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_config_path() -> Path:
    """Return the platform-appropriate path to *config.json*."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "EndpointClaw" / "config.json"
    return Path.home() / ".endpointclaw" / "config.json"


def _get_default_data_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "EndpointClaw"
    return Path.home() / ".endpointclaw"


def get_default_monitored_paths() -> list[str]:
    """Return platform-appropriate default paths to monitor."""
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
    ]
    # On Windows the well-known folders are typically under %USERPROFILE%.
    # On macOS / Linux they live directly under $HOME — same path structure.
    return [str(p) for p in candidates if p.exists() or platform.system() == "Windows"]


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Central configuration object for the EndpointClaw agent.

    Settings are resolved in the following priority (highest wins):
    1. Environment variables (``ENDPOINTCLAW_*``, ``ANTHROPIC_API_KEY``).
    2. Values stored in the JSON config file.
    3. Hardcoded defaults defined in this dataclass.

    Call :meth:`load` once at startup.  Call :meth:`save` to persist changes
    back to disk.
    """

    # --- Supabase / API ---------------------------------------------------
    supabase_url: str = _DEFAULT_SUPABASE_URL
    supabase_key: str = ""
    api_key: str = ""
    company_id: str = "corvex"
    user_email: str = ""

    # --- Device identification ---------------------------------------------
    device_name: str = field(default_factory=socket.gethostname)
    device_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # --- AI ----------------------------------------------------------------
    anthropic_api_key: str = ""

    # --- Networking --------------------------------------------------------
    chat_port: int = 8742
    heartbeat_interval: int = 60
    sync_interval: int = 300
    command_poll_interval: int = 10

    # --- Indexing / monitoring --------------------------------------------
    monitored_paths: list[str] = field(default_factory=get_default_monitored_paths)
    monitored_extensions: list[str] = field(
        default_factory=lambda: [
            ".xlsx", ".pdf", ".docx", ".dwg", ".dxf",
            ".jpg", ".png", ".csv", ".txt",
        ]
    )

    # --- Resource limits --------------------------------------------------
    max_cpu_percent: float = 15.0
    max_ram_mb: int = 500

    # --- Screenshots ------------------------------------------------------
    screenshot_interval: int = 300
    screenshot_quality: int = 70
    screenshot_enabled: bool = False

    # --- Keystrokes -------------------------------------------------------
    keystroke_enabled: bool = False
    keystroke_chunk_seconds: int = 30

    # --- Paths / logging --------------------------------------------------
    data_dir: Path = field(default_factory=_get_default_data_dir)
    log_level: str = "INFO"

    # Internal — not serialised
    _config_path_override: Optional[Path] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def _config_file(self) -> Path:
        return self._config_path_override or get_config_path()

    def _to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict of all public fields."""
        raw = asdict(self)
        raw.pop("_config_path_override", None)
        # Convert Path objects to strings for JSON
        for key, val in raw.items():
            if isinstance(val, Path):
                raw[key] = str(val)
        return raw

    @staticmethod
    def _coerce(key: str, value: Any, target_type: type) -> Any:
        """Best-effort coercion of *value* to *target_type*."""
        if target_type is bool:
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes")
            return bool(value)
        if target_type in (int, float):
            return target_type(value)
        if target_type is Path:
            return Path(value)
        return value

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Read configuration from JSON on disk, then apply env-var overrides."""
        cfg_path = self._config_file()
        if cfg_path.is_file():
            try:
                with open(cfg_path, "r", encoding="utf-8") as fh:
                    data: dict[str, Any] = json.load(fh)
                logger.info("Loaded config from %s", cfg_path)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read config file %s: %s", cfg_path, exc)
                data = {}
        else:
            logger.info("No config file found at %s — using defaults", cfg_path)
            data = {}

        # Merge file values into self
        for key, value in data.items():
            if key.startswith("_"):
                continue
            if hasattr(self, key):
                expected_type = type(getattr(self, key))
                try:
                    setattr(self, key, self._coerce(key, value, expected_type))
                except (ValueError, TypeError) as exc:
                    logger.warning("Ignoring invalid config value %s=%r: %s", key, value, exc)

        # --- Environment variable overrides --------------------------------
        env_map: dict[str, str] = {
            "ENDPOINTCLAW_SUPABASE_URL": "supabase_url",
            "ENDPOINTCLAW_SUPABASE_KEY": "supabase_key",
            "ENDPOINTCLAW_API_KEY": "api_key",
            "ENDPOINTCLAW_COMPANY_ID": "company_id",
            "ENDPOINTCLAW_EMAIL": "user_email",
            "ANTHROPIC_API_KEY": "anthropic_api_key",
        }
        for env_var, attr in env_map.items():
            env_val = os.environ.get(env_var)
            if env_val is not None:
                setattr(self, attr, env_val)
                logger.debug("Override from env: %s -> %s", env_var, attr)

        # Generate api_key on first run if missing
        if not self.api_key:
            self.api_key = str(uuid.uuid4())
            logger.info("Generated new api_key for this device")

        # Ensure data_dir exists
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        """Persist the current configuration to JSON on disk."""
        cfg_path = self._config_file()
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(self._to_dict(), fh, indent=2)
        logger.info("Config saved to %s", cfg_path)

    # ------------------------------------------------------------------
    # Remote config
    # ------------------------------------------------------------------

    async def pull_company_config(self, httpx_client: Any) -> None:
        """Fetch company-level overrides from the Supabase *company_configs* table.

        Merges returned fields into this config instance, then persists to
        disk so the agent can start offline next time.
        """
        if not self.supabase_url or not self.supabase_key:
            logger.warning("Cannot pull company config — supabase_url or supabase_key not set")
            return

        url = (
            f"{self.supabase_url}/rest/v1/company_configs"
            f"?company_id=eq.{self.company_id}&select=*"
        )
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Accept": "application/json",
        }
        try:
            resp = await httpx_client.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                logger.info("No company config found for company_id=%s", self.company_id)
                return

            remote = rows[0]
            config_payload: dict[str, Any] = remote.get("config", {})
            if not isinstance(config_payload, dict):
                logger.warning("company_configs.config is not a dict — skipping merge")
                return

            merged = 0
            for key, value in config_payload.items():
                if key.startswith("_"):
                    continue
                if hasattr(self, key):
                    expected_type = type(getattr(self, key))
                    try:
                        setattr(self, key, self._coerce(key, value, expected_type))
                        merged += 1
                    except (ValueError, TypeError):
                        logger.warning("Skipping invalid remote config key %s", key)

            logger.info("Merged %d field(s) from company config", merged)
            self.save()

        except Exception as exc:
            logger.warning("Failed to pull company config: %s", exc)
