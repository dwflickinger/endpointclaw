@echo off
REM ============================================================
REM EndpointClaw Quick Setup — Mark's Machine (Corvex Roofing)
REM Run as Administrator in Command Prompt
REM ============================================================

echo.
echo =============================================
echo   EndpointClaw Setup — Corvex Roofing
echo =============================================
echo.

REM --- Check for Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.12+ from python.org
    echo         CHECK "Add Python to PATH" during install!
    echo         Then re-run this script.
    pause
    exit /b 1
)
echo [OK] Python found

REM --- Check for Git ---
git --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] Git not found — downloading repo as ZIP...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/dwflickinger/endpointclaw/archive/refs/heads/main.zip' -OutFile '%USERPROFILE%\endpointclaw.zip'"
    powershell -Command "Expand-Archive -Path '%USERPROFILE%\endpointclaw.zip' -DestinationPath '%USERPROFILE%' -Force"
    if exist "%USERPROFILE%\endpointclaw" rmdir /s /q "%USERPROFILE%\endpointclaw"
    rename "%USERPROFILE%\endpointclaw-main" endpointclaw
    del "%USERPROFILE%\endpointclaw.zip"
    echo [OK] Downloaded and extracted
) else (
    echo [OK] Git found
    if exist "%USERPROFILE%\endpointclaw" (
        echo [INFO] Repo already exists — pulling latest...
        cd /d "%USERPROFILE%\endpointclaw"
        git pull
    ) else (
        echo [INFO] Cloning repo...
        cd /d "%USERPROFILE%"
        git clone https://github.com/dwflickinger/endpointclaw.git
    )
)

REM --- Install Python dependencies ---
echo.
echo [STEP] Installing Python dependencies...
cd /d "%USERPROFILE%\endpointclaw\agent"
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed. Check Python installation.
    pause
    exit /b 1
)
echo [OK] Dependencies installed

REM --- Collect API keys ---
echo.
echo =============================================
echo   Configuration
echo =============================================
echo.

set /p SUPABASE_URL="Supabase URL [https://twgdhuimqspfoimfmyxz.supabase.co]: "
if "%SUPABASE_URL%"=="" set SUPABASE_URL=https://twgdhuimqspfoimfmyxz.supabase.co

set /p SUPABASE_KEY="Supabase Anon Key: "
if "%SUPABASE_KEY%"=="" (
    echo [ERROR] Supabase key is required.
    pause
    exit /b 1
)

set /p ANTHROPIC_KEY="Anthropic API Key (for local AI chat): "
if "%ANTHROPIC_KEY%"=="" (
    echo [WARN] No Anthropic key — local chat will be disabled.
)

REM --- Run installer ---
echo.
echo [STEP] Running EndpointClaw installer...
cd /d "%USERPROFILE%\endpointclaw\installer"
python install.py --company corvex --email mark@corvexroofing.com --supabase-url "%SUPABASE_URL%" --supabase-key "%SUPABASE_KEY%" --anthropic-api-key "%ANTHROPIC_KEY%"

if errorlevel 1 (
    echo [ERROR] Installer failed. Check output above.
    pause
    exit /b 1
)

REM --- Set monitored paths ---
echo.
echo [STEP] Configuring monitored directories...
set CONFIG_PATH=%APPDATA%\EndpointClaw\config.json

REM Use PowerShell to update the JSON config with Mark's paths
powershell -Command ^
    "$c = Get-Content '%CONFIG_PATH%' | ConvertFrom-Json; ^
     $c.monitored_paths = @('%USERPROFILE%\Desktop', '%USERPROFILE%\Documents', '%USERPROFILE%\Downloads'); ^
     $c | ConvertTo-Json -Depth 10 | Set-Content '%CONFIG_PATH%'"

echo [OK] Monitoring: Desktop, Documents, Downloads

REM --- Start the agent ---
echo.
echo [STEP] Starting EndpointClaw agent...
cd /d "%USERPROFILE%\endpointclaw\agent"
start "EndpointClaw" python -m agent.src.main --email mark@corvexroofing.com

echo.
echo =============================================
echo   Setup Complete!
echo =============================================
echo.
echo   System tray icon should appear shortly.
echo   Chat UI: http://localhost:8742
echo.
echo   Monitored folders:
echo     - Desktop
echo     - Documents  
echo     - Downloads
echo.
echo   To stop: right-click tray icon ^> Exit
echo   To restart: run this script again
echo.
pause
