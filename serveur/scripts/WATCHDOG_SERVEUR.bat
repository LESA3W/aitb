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
REM
REM Ne PAS revenir a wmic. Il a ete retire de Windows 11 24H2 (build
REM 26100 et au-dela). Verifie le 2026-09-15 sur un poste en 26200 :
REM la commande n'existe simplement plus. LDT restait vide, le journal
REM tombait dans watchdog_--.log et la rotation par jour disparaissait
REM sur la machine meme ou elle sert le plus, celle qui tourne 24/7.
REM Echec silencieux de plus, exactement ce que ce fichier existe pour
REM eviter. PowerShell est present sur tout Windows 10 et 11.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set JOUR=%%i
set SORTIE=logs\watchdog_%JOUR%.log

echo. >> "%SORTIE%"
echo ============================================================ >> "%SORTIE%"
echo  DEMARRAGE %date% %time% >> "%SORTIE%"
echo ============================================================ >> "%SORTIE%"

REM 2>&1 capture aussi les exceptions Python, qui partent sur stderr.
".venv\Scripts\python.exe" -u -m aitb.watchdog >> "%SORTIE%" 2>&1
REM Le code de sortie est capture ICI, immediatement apres Python, et rendu par
REM exit /b a la toute fin. Sans ca, le .bat se termine sur un echo et rend le
REM code de l ECHO, donc 0, quoi qu il soit arrive. Mesure du 2026-09-16 : un
REM Python sortant en 42 donnait un code de .bat de 0. Le planificateur affiche
REM alors Last Result: 0 sur une tache qui n a rien pu lancer, et le seul champ
REM qu on regarde pour juger une tache devient un mensonge.
set CODE=%ERRORLEVEL%

echo. >> "%SORTIE%"
echo  ARRET %date% %time% code=%CODE% >> "%SORTIE%"

REM Pas de pause : ce script doit pouvoir etre relance par le
REM Planificateur de taches sans intervention humaine.

exit /b %CODE%
