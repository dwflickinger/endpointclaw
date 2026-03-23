"""Tests for agent.src.core.config.AgentConfig.

Validates default values, JSON file round-tripping, environment variable
overrides, monitored paths, and platform-appropriate data directories.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.src.core.config import AgentConfig, get_default_monitored_paths


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_values(self) -> None:
        cfg = AgentConfig()

        assert cfg.supabase_url == "https://twgdhuimqspfoimfmyxz.supabase.co"
        assert cfg.chat_port == 8742
        assert cfg.heartbeat_interval == 60
        assert cfg.sync_interval == 300
        assert cfg.command_poll_interval == 10
        assert cfg.company_id == "corvex"
        assert cfg.max_cpu_percent == 15.0
        assert cfg.max_ram_mb == 500
        assert cfg.screenshot_interval == 300
        assert cfg.screenshot_quality == 70
        assert cfg.screenshot_enabled is False
        assert cfg.keystroke_enabled is False
        assert cfg.log_level == "INFO"

    def test_monitored_extensions(self) -> None:
        cfg = AgentConfig()
        expected = [
            ".xlsx", ".pdf", ".docx", ".dwg", ".dxf",
            ".jpg", ".png", ".csv", ".txt",
        ]
        assert cfg.monitored_extensions == expected

    def test_data_dir_default(self) -> None:
        cfg = AgentConfig()
        data_dir = cfg.data_dir
        assert isinstance(data_dir, Path)
        if platform.system() == "Windows":
            assert "EndpointClaw" in str(data_dir)
        else:
            assert ".endpointclaw" in str(data_dir)


# ---------------------------------------------------------------------------
# Load from file
# ---------------------------------------------------------------------------


class TestLoadFromFile:
    def test_load_from_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_data = {
            "company_id": "acme",
            "chat_port": 9999,
            "log_level": "DEBUG",
            "user_email": "test@example.com",
        }
        config_file.write_text(json.dumps(config_data), encoding="utf-8")

        cfg = AgentConfig(_config_path_override=config_file)
        cfg.load()

        assert cfg.company_id == "acme"
        assert cfg.chat_port == 9999
        assert cfg.log_level == "DEBUG"
        assert cfg.user_email == "test@example.com"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"
        cfg = AgentConfig(_config_path_override=missing)
        cfg.load()

        # Should silently fall back to defaults without raising.
        assert cfg.supabase_url == "https://twgdhuimqspfoimfmyxz.supabase.co"
        assert cfg.company_id == "corvex"


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


class TestEnvVarOverrides:
    def test_env_var_override(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text("{}", encoding="utf-8")

        with patch.dict(os.environ, {"ENDPOINTCLAW_COMPANY_ID": "env_company"}, clear=False):
            cfg = AgentConfig(_config_path_override=config_file)
            cfg.load()
            assert cfg.company_id == "env_company"

    def test_env_var_supabase_url(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text("{}", encoding="utf-8")

        with patch.dict(
            os.environ,
            {"ENDPOINTCLAW_SUPABASE_URL": "https://custom.supabase.co"},
            clear=False,
        ):
            cfg = AgentConfig(_config_path_override=config_file)
            cfg.load()
            assert cfg.supabase_url == "https://custom.supabase.co"

    def test_env_var_anthropic_key(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text("{}", encoding="utf-8")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key-123"}, clear=False):
            cfg = AgentConfig(_config_path_override=config_file)
            cfg.load()
            assert cfg.anthropic_api_key == "sk-test-key-123"


# ---------------------------------------------------------------------------
# Save / round-trip
# ---------------------------------------------------------------------------


class TestSave:
    def test_save_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "save_test.json"
        cfg = AgentConfig(_config_path_override=config_file)
        cfg.company_id = "saved_co"
        cfg.chat_port = 7777
        cfg.save()

        raw = json.loads(config_file.read_text(encoding="utf-8"))
        assert raw["company_id"] == "saved_co"
        assert raw["chat_port"] == 7777

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        config_file = tmp_path / "roundtrip.json"

        original = AgentConfig(_config_path_override=config_file)
        original.company_id = "roundtrip_co"
        original.chat_port = 5555
        original.user_email = "round@trip.com"
        original.log_level = "WARNING"
        original.save()

        loaded = AgentConfig(_config_path_override=config_file)
        loaded.load()

        assert loaded.company_id == "roundtrip_co"
        assert loaded.chat_port == 5555
        assert loaded.user_email == "round@trip.com"
        assert loaded.log_level == "WARNING"


# ---------------------------------------------------------------------------
# Monitored paths
# ---------------------------------------------------------------------------


class TestMonitoredPaths:
    def test_get_default_monitored_paths(self) -> None:
        paths = get_default_monitored_paths()
        assert isinstance(paths, list)
        # Each entry should be a string (not a Path object) — the function
        # returns str representations.
        for p in paths:
            assert isinstance(p, str)
        # On most developer machines at least Desktop or Documents exists.
        home = str(Path.home())
        assert any(home in p for p in paths) or len(paths) == 0
