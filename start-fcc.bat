@echo off
chcp 65001 >nul
setlocal

if not "%~1"=="" (
    set "PROJECT_DIR=%~1"
    goto :launch
)

for /f "delims=" %%i in ('powershell -NoProfile -Command ^
    "Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description = 'Select project folder for Claude Code'; $f.RootFolder = 'MyComputer'; if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath } else { exit 1 }"') do (
    set "PROJECT_DIR=%%i"
)

if "%PROJECT_DIR%"=="" (
    echo No folder selected. Exiting.
    pause
    exit /b 1
)

:launch
echo Project: %PROJECT_DIR%
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-fcc.ps1" -ProjectDir "%PROJECT_DIR%"
