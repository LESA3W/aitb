@echo off
REM Lanceur du watchdog A.I.T.B.
REM Pas de "pause" a la fin : ce script doit pouvoir etre relance
REM automatiquement par le Planificateur de taches Windows.
setlocal
cd /d "%~dp0"
title A.I.T.B Watchdog

set PYTHONIOENCODING=utf-8
call ".venv\Scripts\activate"
python -u -m aitb.watchdog

REM Si le watchdog lui-meme sort, on laisse la fenetre visible 30 secondes
REM pour pouvoir lire l'erreur, puis on rend la main.
timeout /t 30 /nobreak >nul
