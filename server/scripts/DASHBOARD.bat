@echo off
REM Ouvre le tableau de bord temps reel du bot.
REM N'arrete pas le bot : c'est un afficheur en lecture seule.
setlocal
cd /d "%~dp0"
title A.I.T.B Dashboard

set PYTHONIOENCODING=utf-8
call ".venv\Scripts\activate"

start "" "http://127.0.0.1:8080"
python dashboard.py

pause
