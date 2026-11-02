@echo off
setlocal

echo =================================================================
echo 🌴 TIKI KARAOKE LOUNGE - WINDOWS LAUNCHER
echo =================================================================

pushd "%~dp0"
set SCRIPT_DIR=%CD%

set PYTHON_BIN=python

%PYTHON_BIN% --version >nul 2>&1
if errorlevel 1 (
    if exist "%SCRIPT_DIR%\packaging\windows\portable_python\python.exe" (
        set PYTHON_BIN="%SCRIPT_DIR%\packaging\windows\portable_python\python.exe"
    ) else (
        echo [Error] Python not found. Run packaging\windows\run_windows_tests.ps1 via PowerShell to set up.
        popd
        pause
        exit /b 1
    )
)

%PYTHON_BIN% "%SCRIPT_DIR%\main.py" %*
if errorlevel 1 (
    pause
)
popd
