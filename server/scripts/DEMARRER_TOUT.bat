@echo off
REM ===========================================================
REM  A.I.T.B - demarrage complet du cote FOREX, sur le SERVEUR.
REM
REM  Lance, dans cet ordre et seulement si necessaire :
REM     1. le watchdog, qui lance et surveille le bot
REM     2. le tableau de bord prive (100.x.y.z:8080)
REM     3. l'envoi vers le miroir Vercel
REM
REM  CE FICHIER NE LANCE RIEN A L'AVEUGLE, ET C'EST TOUT SON INTERET
REM  ---------------------------------------------------------------
REM  Deux watchdogs sur le meme compte FTMO envoient chaque ordre EN
REM  DOUBLE. Sur un compte d'evaluation, ca ne se repare pas. Chaque
REM  bloc commence donc par CONSTATER l'etat reel et ne demarre que ce
REM  qui manque. Relancer ce fichier dix fois de suite est sans effet.
REM
REM  Il privilegie les taches planifiees aux fenetres console : le
REM  2026-09-14 le watchdog est mort deux fois avec sa fenetre, sans
REM  laisser de trace. Une tache survit a la fermeture, au logoff et
REM  au redemarrage.
REM
REM  POURQUOI DES CODES DE SORTIE ET PAS DES for /f
REM  ----------------------------------------------
REM  Premiere version de ce fichier : la detection lisait la sortie de
REM  Python via for /f. Pour eviter que cmd prenne le "<" de la
REM  comparaison pour une redirection, j'y avais mis " lss ", qui est
REM  un operateur BATCH et n'existe pas en Python. La ligne levait donc
REM  une SyntaxError, la detection tombait toujours sur "aucun
REM  watchdog", et le fichier aurait lance le deuxieme bot qu'il est
REM  cense empecher.
REM  Ici Python ne renvoie QUE son code de sortie, il n'y a rien a
REM  capturer, et les "<" restent proteges par les guillemets.
REM ===========================================================
setlocal EnableExtensions
cd /d "%~dp0"
title A.I.T.B - demarrage complet
set PY=.venv\Scripts\python.exe

echo.
echo  ===========================================================
echo    A.I.T.B - DEMARRAGE COMPLET (FOREX)
echo  ===========================================================
echo.

if not exist "%PY%" (
  echo  [ECHEC] .venv absent. Lance d'abord INSTALL_SERVEUR.bat
  echo.
  pause
  exit /b 1
)

REM ===========================================================
REM  1. LE WATCHDOG, donc le bot
REM ===========================================================
echo  [....] 1/3  watchdog
echo.

REM Le heartbeat est la seule preuve fiable qu'un watchdog TOURNE.
REM Compter les processus ment : le shim du .venv et ce script
REM lui-meme apparaissent dans les lignes de commande.
REM Code 0 = un watchdog bat. Code 1 = aucun battement recent.
"%PY%" -c "import json,time,pathlib,sys;p=pathlib.Path('data/heartbeat.json');d=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {};ts=d.get('updated_ts');sys.exit(0 if ts and (time.time()-ts) < 180 else 1)" 2>nul
if errorlevel 1 goto :watchdog_absent

echo         Un watchdog bat deja. RIEN N'EST RELANCE.
echo         C'est voulu : deux watchdogs enverraient chaque ordre
echo         en double sur le compte d'evaluation.
goto :dashboard

:watchdog_absent
echo         Aucun battement recent, demarrage.
schtasks /query /tn "AITB_Watchdog" >nul 2>&1
if errorlevel 1 goto :watchdog_fenetre
schtasks /run /tn "AITB_Watchdog" >nul 2>&1
if errorlevel 1 (
  echo  [ECHEC] la tache AITB_Watchdog existe mais refuse de demarrer
) else (
  echo  [ OK ]  tache AITB_Watchdog demarree
)
goto :dashboard

:watchdog_fenetre
echo         Tache AITB_Watchdog absente, lancement en fenetre.
echo         A CORRIGER : INSTALL_TACHE.bat en administrateur, sinon
echo         ce watchdog mourra avec sa fenetre, sans laisser de trace.
start "A.I.T.B Watchdog" /min "%~dp0WATCHDOG_SERVEUR.bat"
echo  [ OK ]  watchdog lance en fenetre minimisee

REM ===========================================================
REM  2. LE TABLEAU DE BORD PRIVE
REM ===========================================================
:dashboard
echo.
echo  [....] 2/3  tableau de bord prive
echo.

REM On teste le PORT, pas le processus : un processus vivant qui
REM n'ecoute pas ne sert a rien, et c'est la reponse sur le port qui
REM compte pour l'utilisateur. Code 0 = quelque chose ecoute.
"%PY%" -c "import socket,sys;s=socket.socket();s.settimeout(3);r=s.connect_ex(('127.0.0.1',8080));s.close();sys.exit(0 if r == 0 else 1)" 2>nul
if errorlevel 1 goto :dashboard_absent

echo         Le port 8080 repond deja, rien a faire.
goto :envoi

:dashboard_absent
schtasks /query /tn "AITB_Dashboard" >nul 2>&1
if errorlevel 1 goto :dashboard_fenetre
schtasks /run /tn "AITB_Dashboard" >nul 2>&1
if errorlevel 1 (
  echo  [ECHEC] la tache AITB_Dashboard refuse de demarrer
) else (
  echo  [ OK ]  tache AITB_Dashboard demarree
)
goto :envoi

:dashboard_fenetre
echo         Tache AITB_Dashboard absente, lancement en fenetre.
echo         A CORRIGER : INSTALL_TACHE_DASHBOARD.bat en administrateur.
start "A.I.T.B Dashboard" /min "%~dp0DASHBOARD_SERVEUR.bat"
echo  [ OK ]  tableau de bord lance en fenetre minimisee

REM ===========================================================
REM  3. L'ENVOI VERS LE MIROIR VERCEL
REM ===========================================================
:envoi
echo.
echo  [....] 3/3  envoi vers le miroir Vercel
echo.

REM Sans cet envoi la page Vercel se fige et affiche l'age du dernier
REM instantane. C'est elle qui a prouve le 2026-09-15 que le serveur
REM allait bien alors que le tunnel Tailscale etait tombe : elle ne
REM depend ni de Tailscale ni de RustDesk.
if not exist "pousser.py" (
  echo         pousser.py absent, envoi ignore.
  goto :fin
)
if not exist "jeton_envoi.txt" (
  echo         jeton_envoi.txt absent, envoi ignore.
  echo         Le miroir Vercel restera fige sans ce fichier.
  goto :fin
)

REM Code 0 = un envoi tourne deja.
REM
REM ON NE SCRUTE PAS LES LIGNES DE COMMANDE, ET C'EST UNE LECON PAYEE DEUX FOIS.
REM Chercher "pousser" dans les cmdline renvoyait TOUJOURS "deja en cours" sur
REM une machine ou rien ne tournait. Mesure du 2026-09-15 : 5 processus
REM portaient le motif, tous etaient le chercheur lui-meme ou ses ANCETRES, le
REM texte de la commande se transmettant le long de la chaine bash/python.
REM Exclure os.getpid() ne suffit donc pas, il faudrait exclure parents() et
REM children(), et la liste des exceptions n'aurait jamais de fin.
REM
REM On detecte donc par l'EFFET : pousser.py ecrit une ligne toutes les 60 s
REM dans logs\envoi_AAAA-MM-JJ.log. Un journal de moins de 150 s prouve non
REM seulement qu'il tourne, mais qu'il fonctionne. Meme principe que le
REM heartbeat du watchdog plus haut. Aucun self-match possible.
"%PY%" -c "import pathlib,time,sys;d=pathlib.Path('logs');f=sorted(d.glob('envoi_*.log'), key=lambda x: x.stat().st_mtime) if d.exists() else [];sys.exit(0 if f and (time.time()-f[-1].stat().st_mtime) < 150 else 1)" 2>nul
if errorlevel 1 goto :envoi_lancer

echo         Un envoi tourne deja, rien a faire.
goto :fin

:envoi_lancer
start "A.I.T.B Envoi Vercel" /min "%~dp0ENVOI_VERCEL.bat"
echo  [ OK ]  envoi vers Vercel lance

REM ===========================================================
REM  ETAT FINAL
REM ===========================================================
:fin
echo.
echo  ===========================================================
echo    ETAT, une quinzaine de secondes apres le demarrage
echo  ===========================================================
echo.
timeout /t 15 /nobreak >nul

"%PY%" -c "import json,time,pathlib;p=pathlib.Path('data/heartbeat.json');d=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {};ts=d.get('updated_ts');print('   watchdog   : battement il y a ' + str(int(time.time()-ts)) + ' s') if ts else print('   watchdog   : aucun battement, il demarre peut-etre encore')" 2>nul
"%PY%" -c "import socket;s=socket.socket();s.settimeout(3);r=s.connect_ex(('127.0.0.1',8080));s.close();print('   dashboard  : ' + ('repond sur 8080' if r == 0 else 'ne repond pas encore sur 8080'))" 2>nul
"%PY%" -c "import pathlib,time;d=pathlib.Path('logs');f=sorted(d.glob('envoi_*.log'), key=lambda x: x.stat().st_mtime) if d.exists() else [];print('   envoi      : ' + ('journal ecrit il y a ' + str(int(time.time()-f[-1].stat().st_mtime)) + ' s' if f else 'aucun journal, il demarre peut-etre encore'))" 2>nul

echo.
echo  -----------------------------------------------------------
echo    Tableau de bord prive  http://100.x.y.z:8080
echo.
echo    Ce fichier est SANS RISQUE a relancer : il ne demarre que
echo    ce qui manque. En cas de doute sur l'etat, relance-le.
echo  -----------------------------------------------------------
echo.
pause
