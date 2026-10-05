@echo off
chcp 65001 > nul
title WebDAV Drive Mounter

REM 1. Priority: Run ultra-fast compiled EXE if present (0.2s instant launch)
if exist "%~dp0dist\WebDAVDriveMounter\WebDAVDriveMounter.exe" (
    start "" "%~dp0dist\WebDAVDriveMounter\WebDAVDriveMounter.exe"
    exit /b 0
)

if exist "%~dp0dist\WebDAVDriveMounter_Single.exe" (
    start "" "%~dp0dist\WebDAVDriveMounter_Single.exe"
    exit /b 0
)

REM 2. Check if local virtual environment exists
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
    exit /b 0
)

if exist "%~dp0.venv\Scripts\python.exe" (
    start "" "%~dp0.venv\Scripts\python.exe" "%~dp0main.py"
    exit /b 0
)

REM Fallback to system Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [오류] Python이 설치되어 있지 않거나 환경 변수 PATH에 등록되지 않았습니다.
    pause
    exit /b 1
)

where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw main.py
) else (
    start "" python main.py
)

