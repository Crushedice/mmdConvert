@echo off
rem Same as convert.bat, but also renders preview PNGs (T-pose front/side + a test pose) per model.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0convert.ps1" %* -Preview
echo.
pause
