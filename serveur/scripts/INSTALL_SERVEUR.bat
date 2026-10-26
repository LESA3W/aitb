@echo off
REM ===========================================================
REM  A.I.T.B - installation sur le serveur. A lancer UNE FOIS.
REM  Ne demarre AUCUN bot : il prepare seulement la machine.
REM ===========================================================
setlocal
cd /d "%~dp0"
title A.I.T.B - installation serveur

echo.
echo  ===========================================================
echo    INSTALLATION A.I.T.B
echo  ===========================================================
echo.

REM ---------- 1. Python, ET SA VERSION ----------
REM La version compte vraiment : MetaTrader5 est distribue en roues
REM compilees PAR version de Python. Le 2026-09-14 une tentative sur
REM Python 3.14 a echoue avec "No matching distribution found for
REM MetaTrader5==5.0.5260", ce numero n'existant pas pour 3.14.
REM Le bot est valide sur 3.12.10, on reproduit cet environnement.
python --version >nul 2>&1
if errorlevel 1 (
  echo  [ECHEC] Python introuvable dans le PATH.
  echo.
  echo          Installe Python 3.12 en 64 bits depuis python.org
  echo          et COCHE la case "Add python.exe to PATH" pendant
  echo          l'installation. Puis relance ce script.
  echo.
  pause
  exit /b 1
)
REM /c: et pas un motif nu : sans lui, findstr traite le point comme
REM un joker d'expression reguliere et "3.12." matcherait "3x12y".
python --version 2>&1 | findstr /b /c:"Python 3.12." >nul
if errorlevel 1 (
  echo  [ECHEC] mauvaise version de Python. Detectee :
  python --version
  echo.
  echo          Il faut du 3.12. MetaTrader5 5.0.5260 n'existe pas
  echo          pour les autres versions.
  echo          Installe Python 3.12 en 64 bits depuis python.org
  echo          et COCHE "Add python.exe to PATH".
  echo.
  echo          Si plusieurs Python cohabitent, utilise le lanceur :
  echo             py -3.12 -m venv .venv
  echo          puis relance ce script.
  echo.
  pause
  exit /b 1
)
echo  [ OK ] Python 3.12 confirme :
python --version

REM ---------- 2. environnement virtuel ----------
REM Piege reel : un .venv existant peut avoir ete cree avec une AUTRE
REM version de Python. On verifie son interpreteur, on ne se contente
REM pas de constater qu'il existe.
REM Aucune variable lue dans le bloc ou elle est posee : cmd developpe
REM les %VAR% en ANALYSANT le bloc, pas en l'executant, donc elle
REM serait vide. On compare directement la sortie de la commande.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" --version 2>&1 | findstr /b /c:"Python 3.12." >nul
  if errorlevel 1 (
    echo  [ECHEC] le .venv existant n'est pas en Python 3.12. Sa version :
    ".venv\Scripts\python.exe" --version
    echo.
    echo          C'est le piege de ce soir : un .venv cree avec une
    echo          autre version serait reutilise en silence. Supprime-le
    echo          puis relance ce script :
    echo             rmdir /s /q .venv
    echo.
    pause
    exit /b 1
  )
  echo  [ OK ] .venv deja present, en Python 3.12
) else (
  echo  [....] creation de l'environnement virtuel
  python -m venv .venv
  if errorlevel 1 (
    echo  [ECHEC] creation du venv impossible
    pause
    exit /b 1
  )
  echo  [ OK ] .venv cree
)

REM ---------- 3. dependances ----------
echo  [....] installation des 4 dependances
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements-serveur.txt
if errorlevel 1 (
  echo  [ECHEC] pip a echoue
  pause
  exit /b 1
)

REM ---------- 4. controle ----------
echo.
echo  [....] verification des imports
".venv\Scripts\python.exe" -c "import MetaTrader5,numpy,psutil,pytz;print('  [ OK ] les 4 paquets repondent')"
if errorlevel 1 (
  echo  [ECHEC] un paquet ne repond pas
  pause
  exit /b 1
)

echo.
echo  ===========================================================
echo    INSTALLATION TERMINEE. RESTE A FAIRE, DANS CET ORDRE :
echo  ===========================================================
echo.
echo    1. Installer MetaTrader 5 (metatrader5.com)
echo.
echo    2. Dans MT5 : Fichier ^> Ouvrir un compte ^> chercher FTMO
echo       puis choisir le serveur, puis entrer les identifiants.
echo       ATTENTION : surtout PAS Fichier ^> Login. Ce chemin
echo       boucle sans fin sur un serveur que le terminal ne
echo       connait pas encore, et fige le terminal.
echo.
echo    3. Activer AutoTrading dans MT5 (Ctrl+E, le bouton doit
echo       etre VERT). Sans ca tout ordre part en erreur 10027.
echo.
echo    4. Lancer CREER_ENV.bat. Il demande les 3 valeurs de
echo       l'email FTMO, ecrit le .env ici, puis lit le solde
echo       REEL du compte pour completer PROPFIRM_BALANCE.
echo       Le .env n'est jamais copie entre machines : il
echo       contient le mot de passe maitre en clair.
echo.
echo    5. Lancer VERIF_SERVEUR.bat. Il refuse de valider tant
echo       qu'un controle echoue, ne pas passer outre.
echo.
echo    6. Lancer INSTALL_TACHE.bat EN ADMINISTRATEUR. Il
echo       enregistre le watchdog en tache planifiee Windows.
echo       C'est ce qui le fait survivre a la fermeture de la
echo       fenetre, au logoff et au redemarrage de la machine.
echo.
echo    7. Activer l'ouverture de session automatique (netplwiz),
echo       sinon la machine ne repart pas seule apres une coupure
echo       de courant. INSTALL_TACHE.bat rappelle la marche a
echo       suivre a la fin.
echo.
echo  -----------------------------------------------------------
echo    LE SEUL LANCEUR A UTILISER EST WATCHDOG_SERVEUR.bat,
echo    et de preference via la tache planifiee.
echo    Il ecrit sa sortie dans logs\watchdog_AAAA-MM-JJ.log.
echo    L'ancien aitb_watchdog.bat a ete RETIRE de ce paquet :
echo    il n'ecrivait nulle part, et le 2026-09-14 le bot est
echo    mort sans laisser aucune trace exploitable.
echo  -----------------------------------------------------------
echo.
echo    NE PAS demarrer le bot ici tant qu'il tourne encore sur
echo    l'autre machine : deux bots sur le meme compte FTMO
echo    envoient des ordres en double.
echo.
pause
