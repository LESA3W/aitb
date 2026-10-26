@echo off
setlocal
cd /d "%~dp0"

call ".venv\Scripts\activate"
python -m aitb.live %*

pause
