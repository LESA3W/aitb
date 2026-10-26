@echo off
REM ===========================================================
REM  Lanceur du watchdog, version SERVEUR.
REM
REM  C'est le SEUL lanceur de watchdog de ce paquet, et voici
REM  pourquoi l'ancien a ete retire plutot que corrige :
REM  le 2026-09-14 a 20h55, sur la machine de travail, le
REM  watchdog est mort sans laisser la moindre trace. Son
REM  lanceur faisait "python -u -m aitb.watchdog" sans aucune
REM  redirection, donc toute la sortie partait dans la fenetre
REM  console, et la fenetre a disparu avec le processus.
REM  Aucune autopsie possible, ni ce jour-la ni jamais.
REM
REM  Ici la sortie est ECRITE SUR DISQUE, horodatee, en ajout.
REM  Une mort laissera donc toujours ses dernieres lignes.
REM ===========================================================
setlocal
cd /d "%~dp0"
title A.I.T.B Watchdog (serveur)

set PYTHONIOENCODING=utf-8
if not exist "logs" mkdir "logs"

REM Date au format AAAA-MM-JJ, independant des reglages regionaux.
for /f "tokens=2 delims==" %%i in ('wmic os get LocalDateTime /value') do set LDT=%%i
set JOUR=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2%
set SORTIE=logs\watchdog_%JOUR%.log

echo. >> "%SORTIE%"
echo ============================================================ >> "%SORTIE%"
echo  DEMARRAGE %date% %time% >> "%SORTIE%"
echo ============================================================ >> "%SORTIE%"

REM 2>&1 capture aussi les exceptions Python, qui partent sur stderr.
".venv\Scripts\python.exe" -u -m aitb.watchdog >> "%SORTIE%" 2>&1

echo. >> "%SORTIE%"
echo  ARRET %date% %time% code=%ERRORLEVEL% >> "%SORTIE%"

REM Pas de pause : ce script doit pouvoir etre relance par le
REM Planificateur de taches sans intervention humaine.
