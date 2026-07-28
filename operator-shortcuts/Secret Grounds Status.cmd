@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0..\scripts\status-secret-grounds.ps1"
