@echo off
if "%~2"=="" (
  echo Usage: proxy-windows.cmd PROXY_PORT USER@DEVICE
  echo Example: proxy-windows.cmd 7890 user@192.0.2.10
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0proxy-windows.ps1" -ProxyPort "%~1" -Target "%~2"
exit /b %errorlevel%
