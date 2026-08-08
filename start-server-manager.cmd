@echo off
setlocal
cd /d "%~dp0"
pyw -m factorio_server_manager.app
if errorlevel 1 pythonw -m factorio_server_manager.app
endlocal
