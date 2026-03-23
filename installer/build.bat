@echo off
echo Building EndpointClaw...
echo.

REM Activate virtual environment if it exists
if exist ..\venv\Scripts\activate.bat call ..\venv\Scripts\activate.bat

REM Install PyInstaller if needed
pip install pyinstaller

REM Build single-file executable
pyinstaller --onefile ^
    --name EndpointClaw ^
    --icon resources\icon.ico ^
    --add-data "..\agent\src\ui\templates;agent\src\ui\templates" ^
    --add-data "..\agent\config;agent\config" ^
    --hidden-import pystray._win32 ^
    --hidden-import PIL._tkinter_finder ^
    --hidden-import aiosqlite ^
    --hidden-import uvicorn.logging ^
    --hidden-import uvicorn.protocols.http ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.websockets ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.lifespan ^
    --hidden-import uvicorn.lifespan.on ^
    ..\agent\src\main.py

echo.
echo Build complete! Output: dist\EndpointClaw.exe
pause
