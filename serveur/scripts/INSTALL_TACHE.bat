@echo off
REM ===========================================================
REM  Enregistre le watchdog comme TACHE PLANIFIEE Windows.
REM  A lancer EN ADMINISTRATEUR, une seule fois.
REM
REM  Pourquoi une tache plutot qu'une fenetre lancee a la main :
REM  le 2026-09-14 le watchdog est mort quand sa console a
REM  disparu. Une fenetre ne survit ni a la fermeture, ni au
REM  logoff, ni au redemarrage de la machine. Une tache survit
REM  aux trois.
REM ===========================================================
setlocal
cd /d "%~dp0"
title A.I.T.B - installation de la tache planifiee

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo  [ECHEC] Ce script doit etre lance EN ADMINISTRATEUR.
  echo          Clic droit sur le fichier, "Executer en tant
  echo          qu'administrateur", puis relance.
  echo.
  pause
  exit /b 1
)

set TACHE=AITB_Watchdog
set SCRIPT=%~dp0WATCHDOG_SERVEUR.bat

if not exist "%SCRIPT%" (
  echo  [ECHEC] WATCHDOG_SERVEUR.bat introuvable a cote de ce script.
  pause
  exit /b 1
)

echo.
echo  Tache      : %TACHE%
echo  Script     : %SCRIPT%
echo  Declencheur: a l'ouverture de session
echo.

schtasks /query /tn "%TACHE%" >nul 2>&1
if not errorlevel 1 (
  echo  [....] une tache %TACHE% existe deja, remplacement
  schtasks /delete /tn "%TACHE%" /f >nul 2>&1
)

REM ONLOGON et pas ONSTART : MT5 exige une session graphique
REM ouverte. Une tache ONSTART demarrerait avant l'ouverture de
REM session, et le terminal ne pourrait pas s'afficher.
schtasks /create /tn "%TACHE%" /tr "\"%SCRIPT%\"" /sc onlogon /rl highest /f
if errorlevel 1 (
  echo  [ECHEC] creation de la tache impossible
  pause
  exit /b 1
)

echo.
echo  [ OK ] tache creee.
echo.
schtasks /query /tn "%TACHE%" /fo list | findstr /i "TaskName Status Next"
echo.
echo  ===========================================================
echo    IL RESTE UNE CHOSE A FAIRE, ET ELLE EST IMPORTANTE
echo  ===========================================================
echo.
echo    La tache se declenche a l'OUVERTURE DE SESSION. Apres une
echo    coupure de courant, la machine redemarre mais reste sur
echo    l'ecran de connexion : la session ne s'ouvre pas toute
echo    seule, donc le bot ne repart pas.
echo.
echo    Active l'ouverture de session automatique :
echo      Win+R, tape  netplwiz , decoche la case
echo      "Les utilisateurs doivent entrer un nom d'utilisateur
echo       et un mot de passe", puis saisis le mot de passe.
echo.
echo    Sans ca, un serveur 24/7 ne redemarre pas seul apres une
echo    coupure, et c'est precisement ce qu'on cherche a eviter.
echo.
echo    Pour verifier plus tard  : schtasks /query /tn %TACHE%
echo    Pour lancer tout de suite: schtasks /run   /tn %TACHE%
echo    Pour supprimer           : schtasks /delete /tn %TACHE% /f
echo.
pause
