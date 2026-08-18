@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "SCRIPT=%~dp0standalone_preview.py"
set "FOUND="

REM ===== 1) standalone python + PySide6 =====
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import PySide6" >nul 2>nul
    if not errorlevel 1 (
        set "FOUND=1"
        python "%SCRIPT%"
    )
)
if defined FOUND goto :end

REM ===== 2) py launcher + PySide6 =====
where py >nul 2>nul
if not errorlevel 1 (
    py -c "import PySide6" >nul 2>nul
    if not errorlevel 1 (
        set "FOUND=1"
        py "%SCRIPT%"
    )
)
if defined FOUND goto :end

REM ===== 3) Maya mayapy fallback (bundled PySide, no Maya GUI) =====
for %%M in (2026 2025 2024 2023 2022) do (
    if exist "C:\Program Files\Autodesk\Maya%%M\bin\mayapy.exe" (
        set "FOUND=1"
        "C:\Program Files\Autodesk\Maya%%M\bin\mayapy.exe" "%SCRIPT%"
        goto :end
    )
)

echo [ERROR] No usable Python environment found.
echo Install Python and run:  pip install PySide6
echo Or install any Maya version and use its bundled mayapy.

:end
echo.
pause
endlocal
