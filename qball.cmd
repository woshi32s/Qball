@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0qball.ps1" %*
endlocal
