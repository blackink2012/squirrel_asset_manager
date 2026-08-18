@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "SCRIPT=%~dp0standalone_preview.py"
set "FOUND="

REM ===== 1) 独立 Python + PySide6 =====
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

REM ===== 3) Maya mayapy 兜底（自带 PySide，不启动 Maya GUI）=====
for %%M in (2026 2025 2024 2023 2022) do (
    if exist "C:\Program Files\Autodesk\Maya%%M\bin\mayapy.exe" (
        set "FOUND=1"
        "C:\Program Files\Autodesk\Maya%%M\bin\mayapy.exe" "%SCRIPT%"
        goto :end
    )
)

echo [错误] 未找到可用 Python 环境。
echo 请安装 Python 并执行:  pip install PySide6
echo 或安装任意 Maya 版本（使用其自带 mayapy）。

:end
echo.
pause
endlocal
