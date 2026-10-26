@echo off
REM ===========================================================================
REM  Tableau de bord CRYPTO, version SERVEUR MAISON.
REM
REM  Difference avec DASHBOARD_CRYPTO.bat, qui reste la version poste de travail :
REM    - ecoute l'adresse Tailscale et non 127.0.0.1, donc consultable depuis
REM      le PC principal
REM    - la liste blanche IPS_AUTORISEES devient donc active : 403 pour toute
REM      autre adresse. Elle REMPLACE l'isolement perdu en changeant d'ecoute.
REM    - n'ouvre AUCUN navigateur : sur un serveur sans personne devant, ca ne
REM      sert a rien et ca consomme de la memoire
REM    - redirige sortie ET erreurs vers un journal DATE
REM
REM  POURQUOI LA REDIRECTION EST OBLIGATOIRE, et pas un confort :
REM  le watchdog forex est mort deux fois en silence le 2026-09-15 parce que son
REM  lanceur n'avait aucune redirection. Le processus mourait avec sa fenetre,
REM  sans laisser une ligne. Impossible de savoir pourquoi. Un service qui meurt
REM  sans journal est un service qu'on ne peut pas reparer.
REM
REM  NE PAS INSTALLER A LA MAIN : lancer INSTALL_TACHES_CRYPTO.bat, qui pose
REM  aussi la regle de pare-feu du port 8081 et defait les reglages par defaut
REM  de schtasks, a savoir l'arret sur batterie et la limite de 72 heures.
REM  La forme correcte est /ru SYSTEM /sc onstart, PAS /sc onlogon /rl highest :
REM  une tache onlogon sans /ru SYSTEM s'execute DANS la session et meurt avec
REM  elle, donc elle ne protege de rien. Cet en-tete affichait encore l'ancienne
REM  commande, ce qui aurait fait recopier exactement l'erreur du 15/09.
REM
REM  Une fenetre console lancee a la main meurt a la fermeture de session. Le
REM  tableau de bord forex du serveur est tombe comme ca, pendant que son bot
REM  continuait de tourner. Le bot et le tableau de bord sont deux processus
REM  SEPARES : l'un peut mourir sans l'autre.
REM
REM  Lecture seule sur le marche. N'ouvre, ne ferme et ne modifie aucune
REM  position. Aucune cle API, aucun compte, mode papier strict.
REM ===========================================================================
setlocal
cd /d "%~dp0"
title A.I.T.B Crypto Dashboard (serveur)

REM wmic a disparu de Windows 11 24H2 et au-dela. Il produisait un nom de
REM journal casse en watchdog_--.log sans lever d'erreur.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set JOUR=%%i

if not exist "logs" mkdir "logs"

set PYTHONIOENCODING=utf-8

REM Sans cette ligne, Python met sa sortie en tampon de 8 Ko des l'instant ou
REM elle part vers un FICHIER au lieu d'une console. Les lignes de diagnostic
REM restent en memoire et disparaissent si le processus est tue : on obtient un
REM journal vide sur la panne exacte qu'on voulait comprendre. L'en-tete de ce
REM fichier reclame deja un journal exploitable, la redirection seule ne suffit
REM pas a l'obtenir. Ce defaut est invisible en lancement manuel, ou la sortie
REM va vers une console et sort donc ligne par ligne.
set PYTHONUNBUFFERED=1

REM Adresse Tailscale du serveur maison (weasel-server). Si elle change, c'est
REM la SEULE ligne a modifier ici, et il faut aussi ajuster IPS_AUTORISEES dans
REM dashboard_crypto.py.
set AITB_CRYPTO_BIND=100.x.y.z

echo. >> "logs\dashboard_crypto_%JOUR%.log"
echo ===== demarrage %DATE% %TIME% ===== >> "logs\dashboard_crypto_%JOUR%.log"

REM GARDE SUR LE .venv. Le 16/09, ce fichier a tourne depuis un dossier qui n'en
REM avait pas : l'activation a echoue, python n'existait pas, et la tache est
REM morte en une seconde. Le planificateur affichait pourtant "Last Result: 0",
REM parce que le dernier echo du fichier reussissait toujours. Un echec total
REM deguise en succes. On le dit franchement, et dans le journal.
if not exist ".venv\Scripts\activate.bat" (
  echo ERREUR : pas de .venv dans %CD% >> "logs\dashboard_crypto_%JOUR%.log"
  echo Cette tache pointe vers le mauvais dossier, ou l'environnement Python >> "logs\dashboard_crypto_%JOUR%.log"
  echo n'a jamais ete cree ici. Deux corrections possibles : >> "logs\dashboard_crypto_%JOUR%.log"
  echo   1. reinstaller les taches DEPUIS C:\AITB avec INSTALL_TACHES_CRYPTO.bat >> "logs\dashboard_crypto_%JOUR%.log"
  echo   2. si C:\AITB n'a pas de .venv, lancer INSTALL_SERVEUR.bat d'abord >> "logs\dashboard_crypto_%JOUR%.log"
  echo SURTOUT PAS CREER_ENV.bat : il ne cree aucun environnement Python, il >> "logs\dashboard_crypto_%JOUR%.log"
  echo demande les identifiants FTMO et reecrit le .env du bot forex. >> "logs\dashboard_crypto_%JOUR%.log"
  exit /b 3
)

call ".venv\Scripts\activate"
python dashboard_crypto.py >> "logs\dashboard_crypto_%JOUR%.log" 2>&1

REM Capturer le code AVANT le echo, sinon on publie le code du echo.
set CODE=%ERRORLEVEL%
echo ===== arret %DATE% %TIME% (code %CODE%) ===== >> "logs\dashboard_crypto_%JOUR%.log"
REM Remonter le vrai code au planificateur : c'est lui qui s'affiche dans
REM "Last Result". Sans cette ligne, toute panne apparait comme un succes.
exit /b %CODE%
