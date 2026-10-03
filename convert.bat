@echo off
rem Drag one or more .pmx files (or folders) onto this file.
rem Double-click with nothing dropped = convert everything in .\input
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0convert.ps1" %*
echo.
pause
