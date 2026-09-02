@echo off
cd /d "%~dp0"

where python >nul 2>nul
if not errorlevel 1 (
    python web_viewer.py
    goto :end
)

where py >nul 2>nul
if not errorlevel 1 (
    py web_viewer.py
    goto :end
)

for %%M in (2026 2025 2024 2023 2022) do (
    if exist "C:\Program Files\Autodesk\Maya%%M\bin\mayapy.exe" (
        "C:\Program Files\Autodesk\Maya%%M\bin\mayapy.exe" web_viewer.py
        goto :end
    )
)

echo [ERROR] No usable Python environment found.
echo Install Python 3.8+ (no extra packages required).

:end
echo.
pause
