@echo off
REM ===========================================================
REM  Envoie l'instantane du tableau de bord vers Vercel.
REM  A lancer sur le SERVEUR, il tourne en continu.
REM
REM  Ce script ne trade pas et ne touche a aucun ordre. Il lit,
REM  il expurge, il envoie. S'il meurt, le bot continue : la
REM  page Vercel affichera "donnees figees" avec l'age du
REM  dernier envoi, ce qui est honnete.
REM ===========================================================
setlocal
cd /d "%~dp0"
title A.I.T.B - envoi vers Vercel

set PYTHONIOENCODING=utf-8

REM ----------- ADRESSE -----------
REM Alias de PRODUCTION, verifie le 2026-09-15 : HTTP 200.
REM Ne PAS utiliser l'URL longue a suffixe aleatoire ni celle en
REM -lesa3ws-projects : ce sont des URL de deploiement, protegees par le SSO
REM Vercel, elles repondent 302 vers une page de connexion.
set AITB_VERCEL_URL=https://EXEMPLE.vercel.app

REM ----------- JETON -----------
REM LE JETON N'EST PAS DANS CE FICHIER, ET C'EST DELIBERE.
REM
REM Les deux versions precedentes le contenaient en clair. A chaque
REM modification du script, l'outil d'edition affichait le diff du fichier,
REM donc la ligne du jeton, donc le secret se retrouvait dans la conversation.
REM Il a fallu le revoquer deux fois. Le probleme n'etait pas le jeton, c'etait
REM l'endroit ou il etait range : un secret ne doit pas vivre dans un fichier
REM qu'on edite.
REM
REM Il vit donc a cote, dans jeton_envoi.txt, qui n'est jamais ouvert ni
REM modifie par personne. Pour le changer : regenerer sur Vercel, puis
REM reecrire ce fichier, sans toucher a ce script.
if not exist "jeton_envoi.txt" (
  echo.
  echo  [ECHEC] jeton_envoi.txt est introuvable a cote de ce script.
  echo          Il doit se trouver dans le meme dossier que ce fichier.
  echo.
  pause
  exit /b 1
)
set /p AITB_PUSH_TOKEN=<jeton_envoi.txt
if "%AITB_PUSH_TOKEN%"=="" (
  echo.
  echo  [ECHEC] jeton_envoi.txt est vide.
  echo.
  pause
  exit /b 1
)

set AITB_ENVOI_PERIODE=60

if not exist "logs" mkdir "logs"

REM wmic n'existe plus sur Windows 11 24H2 et au-dela. Voir
REM WATCHDOG_SERVEUR.bat pour le detail de ce piege.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set JOUR=%%i
set SORTIE=logs\envoi_%JOUR%.log

echo. >> "%SORTIE%"
echo ============================================================ >> "%SORTIE%"
echo  DEMARRAGE %date% %time% >> "%SORTIE%"
echo ============================================================ >> "%SORTIE%"

echo.
echo  Envoi vers %AITB_VERCEL_URL% toutes les 60 s.
echo  Sortie ecrite dans %SORTIE%
echo  Ctrl+C arrete l'envoi, PAS le bot.
echo.

".venv\Scripts\python.exe" -u pousser.py >> "%SORTIE%" 2>&1
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
