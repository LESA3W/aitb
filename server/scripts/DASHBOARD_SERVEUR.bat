@echo off
REM ===========================================================
REM  Tableau de bord forex, version SERVEUR.
REM
REM  DASHBOARD.bat est prevu pour un poste de travail et ne
REM  fonctionne PAS ici. Trois differences, toutes necessaires :
REM
REM  1. Il lancait "python dashboard.py", donc le Python SYSTEME.
REM     Sur ce serveur, MetaTrader5 n'est installe QUE dans le
REM     .venv. Le dashboard mourait sur ModuleNotFoundError.
REM     On appelle donc l'interpreteur du .venv par son chemin.
REM  2. Il ouvrait un navigateur sur la machine locale, avant meme
REM     que le serveur n'ecoute. Un serveur n'a personne devant
REM     son ecran.
REM  3. Il ecoutait sur 127.0.0.1, donc joignable uniquement
REM     depuis le serveur lui-meme.
REM
REM  AVANT DE CHANGER L'ADRESSE D'ECOUTE, LIRE CECI
REM  ----------------------------------------------
REM  100.x.y.z est l'adresse privee Tailscale de cette machine.
REM  La page reste donc joignable UNIQUEMENT depuis les appareils
REM  de ton reseau Tailscale, qui sont les tiens.
REM  Ne JAMAIS mettre 0.0.0.0 ici. Les routes /mode et /restart du
REM  dashboard n'ont AUCUNE authentification : exposees, elles
REM  laisseraient n'importe qui arreter ou relancer le bot qui
REM  tient le compte d'evaluation.
REM ===========================================================
setlocal
cd /d "%~dp0"
title A.I.T.B Dashboard (serveur)

set PYTHONIOENCODING=utf-8
set AITB_DASHBOARD_BIND=100.x.y.z

if not exist "logs" mkdir "logs"

REM Meme raison que dans WATCHDOG_SERVEUR.bat : wmic n'existe plus
REM sur Windows 11 24H2 et au-dela, PowerShell si.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set JOUR=%%i
set SORTIE=logs\dashboard_%JOUR%.log

echo. >> "%SORTIE%"
echo ============================================================ >> "%SORTIE%"
echo  DEMARRAGE %date% %time% >> "%SORTIE%"
echo ============================================================ >> "%SORTIE%"

echo.
echo  ===========================================================
echo    TABLEAU DE BORD A.I.T.B
echo  ===========================================================
echo.
echo    Depuis n'importe laquelle de tes machines Tailscale :
echo       http://100.x.y.z:8080
echo.
echo    Sortie ecrite dans %SORTIE%
echo.
echo    Ctrl+C ferme l'AFFICHAGE, pas le bot. Le bot tourne dans
echo    la tache planifiee AITB_Watchdog, independamment d'ici.
echo.

REM 2>&1 capture aussi les exceptions Python, qui partent sur stderr.
".venv\Scripts\python.exe" -u dashboard.py >> "%SORTIE%" 2>&1
REM Le code de sortie est capture ICI, immediatement apres Python, et rendu par
REM exit /b a la toute fin. Sans ca, le .bat se termine sur un echo et rend le
REM code de l ECHO, donc 0, quoi qu il soit arrive. Mesure du 2026-09-16 : un
REM Python sortant en 42 donnait un code de .bat de 0. Le planificateur affiche
REM alors Last Result: 0 sur une tache qui n a rien pu lancer, et le seul champ
REM qu on regarde pour juger une tache devient un mensonge.
set CODE=%ERRORLEVEL%

echo. >> "%SORTIE%"
echo  ARRET %date% %time% code=%CODE% >> "%SORTIE%"

exit /b %CODE%
