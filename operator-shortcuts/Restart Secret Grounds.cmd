@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\restart-secret-grounds.ps1" -NoBrowser
