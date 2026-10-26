@echo off
REM ===========================================================
REM  A.I.T.B - controle AVANT de demarrer quoi que ce soit.
REM  Ne lance AUCUN bot, ne passe AUCUN ordre. Il regarde.
REM ===========================================================
setlocal
cd /d "%~dp0"
title A.I.T.B - verification serveur
set PYTHONIOENCODING=utf-8

echo.
echo  ===========================================================
echo    VERIFICATION DU SERVEUR
echo  ===========================================================
echo.

REM ---------- 1. environnement ----------
if not exist ".venv\Scripts\python.exe" (
  echo  [ECHEC] .venv absent. Lance d'abord INSTALL_SERVEUR.bat
  pause
  exit /b 1
)
echo  [ OK ] environnement virtuel present

".venv\Scripts\python.exe" -c "import MetaTrader5,numpy,psutil,pytz" 2>nul
if errorlevel 1 (
  echo  [ECHEC] une dependance manque. Relance INSTALL_SERVEUR.bat
  pause
  exit /b 1
)
echo  [ OK ] les 4 dependances repondent

REM ---------- 2. identifiants ----------
if not exist ".env" (
  echo  [ECHEC] fichier .env absent a la racine.
  echo          Lance CREER_ENV.bat : il te demande les 3 valeurs
  echo          de l'email FTMO et ecrit le fichier ici. Il n'est
  echo          jamais copie entre machines, il contient le mot de
  echo          passe maitre en clair.
  pause
  exit /b 1
)
echo  [ OK ] .env present

REM ---------- 3. le terminal MT5 repond-il ? ----------
echo  [....] connexion au terminal MT5
REM Meme piege que dans CREER_ENV.bat : aucun caractere % ici, cmd les
REM mangerait avant Python. Voir l'explication detaillee la-bas.
".venv\Scripts\python.exe" -c "import MetaTrader5 as m,sys;ok=m.initialize();ti=m.terminal_info() if ok else None;a=m.account_info() if ok else None;print(f'  [ OK ] {a.login} @ {a.server} | {a.balance:.2f} {a.currency}') if a else print('  [ECHEC] pas de compte');print('  [ETEINT] AutoTrading' if (ti and not ti.trade_allowed) else '  [ OK ] AutoTrading') if ti else None;m.shutdown();sys.exit(0 if (ok and a and ti and ti.trade_allowed) else 1)"
if errorlevel 1 (
  echo.
  echo  [ECHEC] MT5 ne repond pas, ou AutoTrading est eteint.
  echo          - le terminal doit etre LANCE et connecte
  echo          - AutoTrading doit etre VERT (Ctrl+E dans MT5)
  echo          - si la connexion boucle : le serveur FTMO n'est
  echo            pas installe. Fichier ^> Ouvrir un compte, PAS Login.
  pause
  exit /b 1
)

REM ---------- 4. pre-vol complet ----------
echo.
echo  [....] pre-vol complet (8 controles, dont un order_check reel)
echo.
".venv\Scripts\python.exe" -m aitb.preflight
if errorlevel 1 (
  echo.
  echo  [ECHEC] le pre-vol refuse le demarrage. NE PAS lancer le bot.
  pause
  exit /b 1
)

echo.
echo  ===========================================================
echo    SERVEUR PRET
echo  ===========================================================
echo.
echo    DEUX CHOSES AVANT DE DEMARRER, dans cet ordre :
echo.
echo    1. VERIFIE QUE LE BOT EST ARRETE SUR L'AUTRE MACHINE.
echo       Deux bots sur le meme compte FTMO envoient des ordres
echo       en double. C'est deja arrive sur ce projet.
echo.
echo    2. Demarre par la TACHE PLANIFIEE, pas a la main :
echo          schtasks /run /tn AITB_Watchdog
echo.
echo       Si la tache n'existe pas encore, lance INSTALL_TACHE.bat
echo       en administrateur. Ne lance JAMAIS le watchdog dans une
echo       fenetre console : le 2026-09-14 a 20h55 il est mort avec
echo       sa fenetre, en silence, sans laisser la moindre trace.
echo       La tache survit a la fermeture, au logoff et au reboot.
echo.
pause
