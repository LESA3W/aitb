@echo off
REM ===========================================================
REM  Cree le fichier .env SUR CETTE MACHINE, en le saisissant.
REM
REM  Pourquoi ne pas simplement copier le .env de l'autre PC :
REM  il contient le mot de passe maitre du compte FTMO en clair.
REM  Le faire transiter par une cle USB, un cloud, un chat ou un
REM  paquet de migration multiplie les endroits ou il traine.
REM  On le retape une fois, ici, et il ne bouge plus.
REM
REM  ATTENTION : ce que tu tapes reste VISIBLE a l'ecran. Fais-le
REM  quand personne ne regarde et ne partage pas ton ecran.
REM ===========================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title A.I.T.B - creation du fichier .env

echo.
echo  ===========================================================
echo    CREATION DU FICHIER .env
echo  ===========================================================
echo.

if exist ".env" (
  echo  [!] Un fichier .env existe deja ici.
  echo.
  set /p ECRASER="    L'ecraser ? (o/N) : "
  if /i not "!ECRASER!"=="o" (
    echo.
    echo    Annule, rien n'a ete modifie.
    pause
    exit /b 0
  )
  copy /y ".env" ".env.bak" >nul
  echo    Ancien fichier sauvegarde en .env.bak
  echo.
)

echo  Les trois valeurs viennent de l'email FTMO.
echo.
set /p FLOGIN="    Login FTMO (chiffres)        : "
set /p FPASS="    Mot de passe MAITRE          : "
set /p FSERV="    Serveur (ex: FTMO-Demo)      : "

if "!FLOGIN!"=="" goto :vide
if "!FPASS!"=="" goto :vide
if "!FSERV!"=="" goto :vide

REM Le mot de passe INVESTISSEUR est en lecture seule : la connexion
REM reussit et TOUS les ordres sont refuses, sans message clair.
REM Le pre-vol le demasque via order_check, mais autant ne pas se
REM tromper ici.

(
  echo # Genere par CREER_ENV.bat. Ne JAMAIS commiter ni copier ce fichier.
  echo FTMO_LOGIN=!FLOGIN!
  echo FTMO_PASSWORD=!FPASS!
  echo FTMO_SERVER=!FSERV!
  echo.
  echo # Garde-fou prop firm.
  echo # PROPFIRM_BALANCE n'est PAS ecrit ici : il doit venir du solde
  echo # REEL du compte, jamais de memoire. Un solde faux rend tous les
  echo # seuils faux, et le garde-fou ne garde plus rien.
  echo # VERIF_SERVEUR.bat le detectera : le pre-vol compare le solde
  echo # configure au solde reel et passe en ECHEC au-dela de 1 %% d'ecart.
  echo PROPFIRM_ENABLED=true
  echo PROPFIRM_MAX_TOTAL_LOSS=10
  echo PROPFIRM_MAX_DAILY_LOSS=5
) > ".env"

echo.
echo  [ OK ] .env cree.
echo.
echo  [....] lecture du solde reel pour completer PROPFIRM_BALANCE
if not exist ".venv\Scripts\python.exe" (
  echo  [!] .venv absent, lance INSTALL_SERVEUR.bat puis relance ce script.
  pause
  exit /b 1
)
REM AUCUN caractere % dans la ligne qui suit, c'est deliberé.
REM Le 2026-09-15 cette ligne utilisait le formatage %s / %.2f. cmd
REM developpe les %VAR% AVANT de passer la commande a Python, y compris
REM a l'interieur des guillemets. Il a lu "%s @ %" comme une variable
REM nommee "s @ " qui n'existe pas, l'a supprimee, et Python a recu une
REM chaine dont le guillemet n'etait jamais ferme :
REM   SyntaxError: unterminated string literal
REM Doubler en %% marcherait, mais il faut alors ne jamais en oublier un.
REM Les f-strings n'ont pas de %, le probleme ne peut plus se poser.
".venv\Scripts\python.exe" -c "import MetaTrader5 as m,pathlib,sys;from aitb.trade_manager import initialize_mt5;ok=initialize_mt5();a=m.account_info() if ok else None;sys.exit(1) if not a else None;p=pathlib.Path('.env');l=[x for x in p.read_text(encoding='utf-8').splitlines() if not x.startswith('PROPFIRM_BALANCE=')];l.append(f'PROPFIRM_BALANCE={a.balance:.2f}');p.write_text(chr(10).join(l)+chr(10),encoding='utf-8');print(f'  [ OK ] {a.login} @ {a.server} | solde {a.balance:.2f} {a.currency} ecrit dans .env');m.shutdown()"
if errorlevel 1 (
  echo.
  echo  [!] Connexion impossible. Verifie que MT5 est lance et que le
  echo      serveur FTMO a bien ete ajoute via Fichier ^> Ouvrir un
  echo      compte. Relance ce script ensuite.
  pause
  exit /b 1
)

echo.
echo  Etape suivante : VERIF_SERVEUR.bat
echo.
pause
exit /b 0

:vide
echo.
echo  [ECHEC] Une valeur est vide, rien n'a ete ecrit.
pause
exit /b 1
