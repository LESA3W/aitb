@echo off
REM ===========================================================
REM  Enregistre le TABLEAU DE BORD en tache planifiee Windows.
REM  A lancer EN ADMINISTRATEUR, une seule fois.
REM
REM  POURQUOI CE FICHIER EXISTE
REM  --------------------------
REM  Le 2026-09-15, DASHBOARD_SERVEUR.bat a ete lance a la main
REM  dans une fenetre console sur le serveur. Moins d'une heure
REM  plus tard le port 8080 ne repondait plus : trois delais
REM  d'attente a 15 s alors que le ping passait a 38 ms, donc
REM  plus rien n'ecoutait. Le bot, lui, tournait toujours.
REM
REM  C'est exactement la panne qui a tue le watchdog deux fois
REM  dans la meme journee. Une fenetre console ne survit ni a sa
REM  fermeture, ni au logoff, ni au redemarrage. Une tache si.
REM
REM  A RETENIR : le tableau de bord est un processus SEPARE du
REM  bot. Sa mort n'arrete aucun trade et ne ferme aucune
REM  position, mais elle te prive de l'interrupteur ON/PAUSE/OFF
REM  et du bouton RELANCER, qui ne sont QUE la. Le miroir Vercel
REM  est en lecture seule, il ne les remplace pas.
REM ===========================================================
setlocal
cd /d "%~dp0"
title A.I.T.B - tache planifiee du tableau de bord

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

set TACHE=AITB_Dashboard
set SCRIPT=%~dp0DASHBOARD_SERVEUR.bat

if not exist "%SCRIPT%" (
  echo  [ECHEC] DASHBOARD_SERVEUR.bat introuvable a cote de ce script.
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

REM ONLOGON comme pour le watchdog : le tableau de bord lit MT5,
REM qui exige une session graphique ouverte.
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
echo  -----------------------------------------------------------
echo    Pour le demarrer tout de suite :
echo       schtasks /run /tn %TACHE%
echo.
echo    Il ecoute alors sur http://100.x.y.z:8080
echo    depuis tes appareils Tailscale uniquement.
echo.
echo    COMMENT LIRE UNE PANNE, la distinction compte :
echo       delai d'attente / aucune reponse  = le processus est
echo          mort, plus rien n'ecoute sur le port.
echo       reponse 403                        = le processus vit,
echo          mais ton adresse n'est pas dans IPS_AUTORISEES
echo          dans dashboard.py. Ajoute-la avec la variable
echo          AITB_DASHBOARD_IPS, sans toucher au code.
echo.
echo    Sortie ecrite dans logs\dashboard_AAAA-MM-JJ.log
echo  -----------------------------------------------------------
echo.
pause
