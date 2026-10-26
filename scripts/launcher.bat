@echo off
setlocal

rem Aller dans le dossier du .bat
cd /d "%~dp0"

rem Activer le venv
call ".venv\Scripts\activate"

rem Lancer le launcher
python "launcher.py"

exit