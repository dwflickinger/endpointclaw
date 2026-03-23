#!/usr/bin/env python3
"""
EndpointClaw Installation Script

Installs EndpointClaw on Windows workstations. Uses only standard library
modules since this runs before any dependencies are installed.

Usage:
    python install.py
    python install.py --silent --email user@company.com --supabase-key <key>
    python install.py --company corvex --email user@company.com
"""

import argparse
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import uuid
import webbrowser
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME = "EndpointClaw"
DEFAULT_COMPANY = "corvex"
DEFAULT_SUPABASE_URL = "https://twgdhuimqspfoimfmyxz.supabase.co"
CHAT_PORT = 8742
SERVICE_NAME = "EndpointClaw"
SERVICE_DISPLAY_NAME = "EndpointClaw Agent"
SERVICE_DESCRIPTION = "EndpointClaw local AI agent for endpoint monitoring and assistance"
REGISTRY_RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"

IS_WINDOWS = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(APP_NAME)

# ---------------------------------------------------------------------------
# Helper — data directory
# ---------------------------------------------------------------------------


def get_data_directory() -> Path:
    """Return the platform-appropriate data directory for EndpointClaw."""
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            appdata = str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / APP_NAME
    else:
        # macOS / Linux fallback (for development / testing)
        return Path.home() / f".{APP_NAME.lower()}"


def get_install_directory() -> Path:
    """Return the directory where the EndpointClaw executable is installed."""
    if IS_WINDOWS:
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        return Path(program_files) / APP_NAME
    else:
        return Path("/opt") / APP_NAME.lower()


# ---------------------------------------------------------------------------
# 1. Create data directory
# ---------------------------------------------------------------------------


def create_data_directory() -> Path:
    """
    Create %APPDATA%/EndpointClaw with required subdirectories.

    Returns the root data directory path.
    """
    data_dir = get_data_directory()
    subdirs = ["logs", "screenshots", "data"]

    log.info("Creating data directory: %s", data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    for sub in subdirs:
        sub_path = data_dir / sub
        sub_path.mkdir(parents=True, exist_ok=True)
        log.info("  Created subdirectory: %s", sub_path)

    return data_dir


# ---------------------------------------------------------------------------
# 2. Generate API key
# ---------------------------------------------------------------------------


def generate_api_key() -> str:
    """Generate a UUID-based API key for this device."""
    api_key = f"ec-{uuid.uuid4().hex}"
    log.info("Generated API key: %s...%s", api_key[:8], api_key[-4:])
    return api_key


# ---------------------------------------------------------------------------
# 3. Create config
# ---------------------------------------------------------------------------


def create_config(
    data_dir: Path,
    company_id: str,
    email: str,
    api_key: str,
    supabase_url: str = DEFAULT_SUPABASE_URL,
    supabase_key: str = "",
    anthropic_api_key: str = "",
) -> Path:
    """
    Write the initial config.json into the data directory.

    Returns the path to the config file.
    """
    device_name = socket.gethostname()

    config = {
        "supabase_url": supabase_url,
        "supabase_key": supabase_key,
        "api_key": api_key,
        "company_id": company_id,
        "user_email": email,
        "device_name": device_name,
        "anthropic_api_key": anthropic_api_key,
        "chat_port": CHAT_PORT,
        "heartbeat_interval": 60,
        "sync_interval": 300,
        "command_poll_interval": 10,
        "monitored_paths": [],
        "monitored_extensions": [
            ".xlsx", ".pdf", ".docx", ".dwg", ".dxf",
            ".jpg", ".png", ".csv", ".txt",
        ],
        "excluded_patterns": [
            "node_modules", "__pycache__", ".git", ".venv",
            "AppData", "ProgramData",
        ],
        "max_cpu_percent": 15.0,
        "max_ram_mb": 500,
        "screenshot_enabled": False,
        "screenshot_interval": 300,
        "screenshot_quality": 70,
        "screenshot_max_age_days": 7,
        "keystroke_enabled": False,
        "keystroke_chunk_seconds": 30,
        "idle_threshold_minutes": 5,
        "log_level": "INFO",
        "auto_update_enabled": True,
        "auto_update_interval_hours": 24,
    }

    config_path = data_dir / "config.json"
    log.info("Writing config to: %s", config_path)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

    return config_path


# ---------------------------------------------------------------------------
# 4. Register Windows service
# ---------------------------------------------------------------------------


def register_service() -> bool:
    """
    Register EndpointClaw as a Windows service.

    Tries nssm first (better for Python services), falls back to sc.exe.
    Returns True on success, False otherwise.
    """
    if not IS_WINDOWS:
        log.warning("Skipping service registration (not on Windows)")
        return False

    exe_path = get_install_directory() / "EndpointClaw.exe"

    # Try nssm first
    nssm_path = shutil.which("nssm")
    if nssm_path:
        log.info("Registering service via nssm...")
        try:
            subprocess.run(
                [nssm_path, "install", SERVICE_NAME, str(exe_path), "--service"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [nssm_path, "set", SERVICE_NAME, "DisplayName", SERVICE_DISPLAY_NAME],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [nssm_path, "set", SERVICE_NAME, "Description", SERVICE_DESCRIPTION],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [nssm_path, "set", SERVICE_NAME, "Start", "SERVICE_AUTO_START"],
                check=True,
                capture_output=True,
                text=True,
            )
            log.info("Service registered successfully via nssm")
            return True
        except subprocess.CalledProcessError as exc:
            log.warning("nssm registration failed: %s", exc)
            # Fall through to sc.exe

    # Fallback: sc.exe
    log.info("Registering service via sc.exe...")
    try:
        subprocess.run(
            [
                "sc.exe", "create", SERVICE_NAME,
                f"binPath={exe_path} --service",
                f"DisplayName={SERVICE_DISPLAY_NAME}",
                "start=auto",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "sc.exe", "description", SERVICE_NAME,
                SERVICE_DESCRIPTION,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        log.info("Service registered successfully via sc.exe")
        return True
    except subprocess.CalledProcessError as exc:
        log.error("Service registration failed: %s", exc)
        return False
    except FileNotFoundError:
        log.error("sc.exe not found — cannot register service")
        return False


# ---------------------------------------------------------------------------
# 5. Create Start Menu shortcut
# ---------------------------------------------------------------------------


def create_shortcut() -> bool:
    """
    Create a Start Menu shortcut for EndpointClaw.

    Tries Windows COM (win32com.client) first, falls back to PowerShell.
    Returns True on success, False otherwise.
    """
    if not IS_WINDOWS:
        log.warning("Skipping shortcut creation (not on Windows)")
        return False

    exe_path = get_install_directory() / "EndpointClaw.exe"
    start_menu = Path(os.environ.get(
        "APPDATA", str(Path.home() / "AppData" / "Roaming")
    )) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME

    start_menu.mkdir(parents=True, exist_ok=True)
    shortcut_path = start_menu / f"{APP_NAME}.lnk"

    # Try COM approach
    try:
        import win32com.client  # type: ignore[import-untyped]

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(shortcut_path))
        shortcut.TargetPath = str(exe_path)
        shortcut.WorkingDirectory = str(exe_path.parent)
        shortcut.Description = SERVICE_DESCRIPTION
        shortcut.IconLocation = str(exe_path)
        shortcut.Save()
        log.info("Start Menu shortcut created via COM: %s", shortcut_path)
        return True
    except ImportError:
        log.info("win32com not available, trying PowerShell fallback...")
    except Exception as exc:
        log.warning("COM shortcut creation failed: %s", exc)

    # PowerShell fallback
    ps_script = f"""
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut('{shortcut_path}')
$sc.TargetPath = '{exe_path}'
$sc.WorkingDirectory = '{exe_path.parent}'
$sc.Description = '{SERVICE_DESCRIPTION}'
$sc.IconLocation = '{exe_path}'
$sc.Save()
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            check=True,
            capture_output=True,
            text=True,
        )
        log.info("Start Menu shortcut created via PowerShell: %s", shortcut_path)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.error("Shortcut creation failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# 6. Create startup registry entry
# ---------------------------------------------------------------------------


def create_startup_entry() -> bool:
    """
    Add EndpointClaw to the Windows startup (HKCU Run key).

    Returns True on success, False otherwise.
    """
    if not IS_WINDOWS:
        log.warning("Skipping startup entry (not on Windows)")
        return False

    exe_path = get_install_directory() / "EndpointClaw.exe"

    try:
        import winreg  # type: ignore[import-not-found]

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, str(exe_path))
        winreg.CloseKey(key)
        log.info("Startup registry entry created")
        return True
    except ImportError:
        log.warning("winreg not available (not on Windows?)")
        return False
    except OSError as exc:
        log.error("Failed to create startup entry: %s", exc)
        return False


# ---------------------------------------------------------------------------
# 7. Test Supabase connection
# ---------------------------------------------------------------------------


def test_connection(supabase_url: str, supabase_key: str) -> bool:
    """
    Test connectivity to the Supabase backend.

    Returns True if the connection succeeds, False otherwise.
    """
    if not supabase_url or not supabase_key:
        log.warning("Supabase URL or key not provided — skipping connection test")
        return False

    health_url = f"{supabase_url.rstrip('/')}/rest/v1/"
    log.info("Testing connection to: %s", health_url)

    try:
        req = Request(
            health_url,
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
            },
        )
        with urlopen(req, timeout=10) as resp:
            status = resp.getcode()
            if status and status < 400:
                log.info("Connection successful (HTTP %d)", status)
                return True
            else:
                log.warning("Connection returned HTTP %d", status)
                return False
    except HTTPError as exc:
        # A 400-level error from PostgREST still means we reached the server
        if exc.code in (400, 404):
            log.info("Connection reached server (HTTP %d) — OK", exc.code)
            return True
        log.error("Connection failed: HTTP %d", exc.code)
        return False
    except URLError as exc:
        log.error("Connection failed: %s", exc.reason)
        return False
    except Exception as exc:
        log.error("Connection test error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Main installation flow
# ---------------------------------------------------------------------------


def main() -> None:
    """Orchestrate the EndpointClaw installation process."""
    parser = argparse.ArgumentParser(
        description=f"Install {APP_NAME} on this workstation",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Run in silent mode (no prompts, no browser launch)",
    )
    parser.add_argument(
        "--company",
        default=DEFAULT_COMPANY,
        help=f"Company identifier (default: {DEFAULT_COMPANY})",
    )
    parser.add_argument(
        "--email",
        default="",
        help="User email address",
    )
    parser.add_argument(
        "--supabase-key",
        default="",
        help="Supabase anonymous/service key",
    )
    parser.add_argument(
        "--anthropic-api-key",
        default="",
        help="Anthropic API key for local AI features",
    )

    args = parser.parse_args()

    print()
    print("=" * 60)
    print(f"  {APP_NAME} Installer")
    print("=" * 60)
    print()

    # ---- Interactive prompts (unless --silent) ----
    company_id = args.company
    email = args.email
    supabase_key = args.supabase_key
    anthropic_api_key = args.anthropic_api_key

    if not args.silent:
        if not email:
            email = input("Enter your email address: ").strip()
        if not company_id or company_id == DEFAULT_COMPANY:
            entered = input(f"Enter company ID [{DEFAULT_COMPANY}]: ").strip()
            if entered:
                company_id = entered
        if not supabase_key:
            entered = input("Enter Supabase key (or press Enter to skip): ").strip()
            if entered:
                supabase_key = entered

    if not email:
        log.warning("No email provided — the agent will need to be configured later")

    # ---- Step 1: Create directories ----
    print("\n[1/7] Creating data directories...")
    try:
        data_dir = create_data_directory()
    except OSError as exc:
        log.error("Failed to create data directory: %s", exc)
        sys.exit(1)

    # ---- Step 2: Generate API key ----
    print("[2/7] Generating device API key...")
    api_key = generate_api_key()

    # ---- Step 3: Create config ----
    print("[3/7] Writing configuration...")
    try:
        config_path = create_config(
            data_dir=data_dir,
            company_id=company_id,
            email=email,
            api_key=api_key,
            supabase_key=supabase_key,
            anthropic_api_key=anthropic_api_key,
        )
    except OSError as exc:
        log.error("Failed to write config: %s", exc)
        sys.exit(1)

    # ---- Step 4: Register service ----
    print("[4/7] Registering Windows service...")
    service_ok = register_service()

    # ---- Step 5: Create shortcuts ----
    print("[5/7] Creating Start Menu shortcut...")
    shortcut_ok = create_shortcut()

    # ---- Step 6: Create startup entry ----
    print("[6/7] Adding startup entry...")
    startup_ok = create_startup_entry()

    # ---- Step 7: Test connection ----
    print("[7/7] Testing backend connection...")
    if supabase_key:
        conn_ok = test_connection(DEFAULT_SUPABASE_URL, supabase_key)
    else:
        log.info("No Supabase key provided — skipping connection test")
        conn_ok = False

    # ---- Summary ----
    print()
    print("=" * 60)
    print(f"  {APP_NAME} Installation Complete")
    print("=" * 60)
    print()
    print(f"  Data directory : {data_dir}")
    print(f"  Config file    : {config_path}")
    print(f"  API key        : {api_key[:8]}...{api_key[-4:]}")
    print(f"  Company ID     : {company_id}")
    print(f"  Email          : {email or '(not set)'}")
    print(f"  Service        : {'OK' if service_ok else 'SKIPPED'}")
    print(f"  Shortcut       : {'OK' if shortcut_ok else 'SKIPPED'}")
    print(f"  Startup entry  : {'OK' if startup_ok else 'SKIPPED'}")
    print(f"  Connection     : {'OK' if conn_ok else 'SKIPPED'}")
    print()

    if not IS_WINDOWS:
        print("  NOTE: Windows-specific steps were skipped on this OS.")
        print("        The configuration has been written and is ready.")
        print()

    # ---- Open browser to setup page ----
    if not args.silent:
        setup_url = f"http://localhost:{CHAT_PORT}/setup"
        print(f"  Opening setup page: {setup_url}")
        try:
            webbrowser.open(setup_url)
        except Exception:
            print(f"  Could not open browser. Visit {setup_url} manually.")
    else:
        print(f"  Visit http://localhost:{CHAT_PORT}/setup to complete setup.")

    print()
    print("  Installation finished successfully.")
    print()


if __name__ == "__main__":
    main()
