@echo off
REM ===========================================================================
REM  Envoi de l'instantane CRYPTO vers le miroir Vercel, toutes les 60 s.
REM
REM  LE JETON N'APPARAIT JAMAIS DANS CE FICHIER.
REM  Il vit dans jeton_envoi_crypto.txt, lu par set /p, et ce fichier-la ne doit
REM  JAMAIS etre ouvert dans un editeur pilote par une IA : l'outil d'edition
REM  affiche le contenu modifie, donc le secret arriverait dans la conversation.
REM  Cette erreur a deja coute deux revocations de jeton cote forex.
REM
REM  NOMS DISTINCTS DU FOREX, volontairement : les deux projets cohabitent dans
REM  C:\AITB. Sans suffixe _crypto on ecrase pousser.py et jeton_envoi.txt du
REM  forex, et on casse un miroir qui fonctionne.
REM
REM  NE PAS INSTALLER A LA MAIN : lancer INSTALL_TACHES_CRYPTO.bat, qui pose
REM  aussi la regle de pare-feu et defait les reglages par defaut de schtasks.
REM  La forme correcte est /ru SYSTEM /sc onstart, PAS /sc onlogon /rl highest :
REM  une tache onlogon sans /ru SYSTEM s'execute DANS la session et meurt avec
REM  elle, donc elle ne protege de rien. Cet en-tete affichait encore l'ancienne
REM  commande, ce qui aurait fait recopier exactement l'erreur du 15/09.
REM
REM  Une fenetre console meurt a la fermeture de session, sans laisser de trace.
REM  C'est exactement comme ca que le watchdog est mort deux fois le 2026-09-15.
REM ===========================================================================
setlocal
cd /d "%~dp0"
title A.I.T.B Envoi Vercel (crypto)

REM Le controle du jeton a ete DEPLACE plus bas, apres la creation du journal.
REM Deux raisons, toutes deux liees au passage en /ru SYSTEM :
REM   - il contenait un "pause". Une tache SYSTEM n'a aucune console : le pause
REM     peut ne jamais rendre la main. Avec ExecutionTimeLimit a PT0S plus rien
REM     ne viendrait la tuer, et RestartCount ne relance pas une tache encore
REM     "en cours". Une erreur visible devenait un blocage eternel.
REM   - il s'executait AVANT "mkdir logs", donc son message partait vers une
REM     console inexistante. Personne ne l'aurait jamais lu.

REM wmic a disparu de Windows 11 24H2 et au-dela. Il produisait un nom de
REM journal casse, sans lever la moindre erreur.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set JOUR=%%i

if not exist "logs" mkdir "logs"

set PYTHONIOENCODING=utf-8

REM Voir DASHBOARD_CRYPTO_SERVEUR.bat : sortie redirigee vers un fichier = Python
REM passe en tampon de 8 Ko et les lignes de diagnostic sont perdues en cas de
REM mort brutale. Ici ca compte double : ce programme journalise les echecs
REM d'envoi et les 401, c'est-a-dire precisement ce qu'on vient lire quand le
REM miroir cesse d'etre alimente.
set PYTHONUNBUFFERED=1

REM ALIAS DE PRODUCTION, pas l'URL de deploiement.
REM Vercel tronque les noms de projet longs : un nom de 48 caracteres donne un
REM alias de 35 et l'URL complete repond 404 DEPLOYMENT_NOT_FOUND. Le nom a donc
REM ete choisi court, 21 caracteres, et n'est pas tronque.
REM Une URL du type ...-o5hhj1s7q-lesa3ws-projects.vercel.app est une URL de
REM DEPLOIEMENT : elle change a chaque publication et elle est protegee par le
REM SSO Vercel. Ne jamais la mettre ici.
set URL_CRYPTO=https://aitb-c-9wc0bmr8ynxr66.vercel.app

echo. >> "logs\envoi_crypto_%JOUR%.log"
echo ===== demarrage %DATE% %TIME% ===== >> "logs\envoi_crypto_%JOUR%.log"

REM DEUX GARDES, dans cet ordre : le jeton puis le .venv. Le 16/09 les deux
REM taches pointaient vers un dossier sans environnement Python et sont mortes
REM en silence, avec un "Last Result: 0" parfaitement trompeur. Chaque garde
REM ecrit dans le journal ET rend un code distinct, 2 pour le jeton, 3 pour le
REM .venv, pour que le planificateur affiche autre chose que zero.
if not exist "jeton_envoi_crypto.txt" (
  echo ERREUR : jeton_envoi_crypto.txt introuvable dans %CD% >> "logs\envoi_crypto_%JOUR%.log"
  echo Ce fichier doit contenir, pour seul contenu, la valeur de PUSH_TOKEN >> "logs\envoi_crypto_%JOUR%.log"
  echo definie dans les variables d'environnement du projet Vercel crypto, >> "logs\envoi_crypto_%JOUR%.log"
  echo sans retour a la ligne ni espace. >> "logs\envoi_crypto_%JOUR%.log"
  exit /b 2
)

if not exist ".venv\Scripts\activate.bat" (
  echo ERREUR : pas de .venv dans %CD% >> "logs\envoi_crypto_%JOUR%.log"
  echo Cette tache pointe vers le mauvais dossier, ou l'environnement Python >> "logs\envoi_crypto_%JOUR%.log"
  echo n'a jamais ete cree ici. Deux corrections possibles : >> "logs\envoi_crypto_%JOUR%.log"
  echo   1. reinstaller les taches DEPUIS C:\AITB avec INSTALL_TACHES_CRYPTO.bat >> "logs\envoi_crypto_%JOUR%.log"
  echo   2. si C:\AITB n'a pas de .venv, lancer INSTALL_SERVEUR.bat d'abord >> "logs\envoi_crypto_%JOUR%.log"
  echo SURTOUT PAS CREER_ENV.bat : il ne cree aucun environnement Python, il >> "logs\envoi_crypto_%JOUR%.log"
  echo demande les identifiants FTMO et reecrit le .env du bot forex. >> "logs\envoi_crypto_%JOUR%.log"
  exit /b 3
)

call ".venv\Scripts\activate"
python pousser_crypto.py %URL_CRYPTO% >> "logs\envoi_crypto_%JOUR%.log" 2>&1

REM Capturer le code AVANT le echo, sinon on publie le code du echo.
set CODE=%ERRORLEVEL%
echo ===== arret %DATE% %TIME% (code %CODE%) ===== >> "logs\envoi_crypto_%JOUR%.log"
exit /b %CODE%
