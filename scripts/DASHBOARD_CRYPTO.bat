@echo off
REM Ouvre le tableau de bord des bots d'arbitrage de financement.
REM N'arrete aucun bot : c'est un afficheur en lecture seule.
REM Le tableau de bord forex reste sur le port 8080, celui-ci est sur 8081.
setlocal
cd /d "%~dp0"
title A.I.T.B Crypto Dashboard

set PYTHONIOENCODING=utf-8
call ".venv\Scripts\activate"

start "" "http://127.0.0.1:8081"
python dashboard_crypto.py

pause
