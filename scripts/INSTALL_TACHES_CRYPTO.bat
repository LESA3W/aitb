@echo off
REM ===========================================================================
REM  Installe les DEUX taches planifiees du cote CRYPTO, en une seule fois.
REM
REM  POURQUOI CE FICHIER EXISTE
REM  Le 15/09 a 22:52, la session utilisateur du serveur s'est fermee. Les
REM  services Windows ont survecu (RustDesk repondait en 5 ms, SMB en 15 ms),
REM  mais TOUT ce qui tournait en fenetre console est mort d'un coup : les deux
REM  tableaux de bord et le pousseur forex. Diagnostic par les ports : 21118 et
REM  445 ouverts, 8080 et 8081 en delai depasse. Reseau sain, processus morts.
REM
REM  Une fenetre console est un processus attache a une session. Une tache
REM  planifiee ne l'est pas. C'est toute la difference, et elle vient de couter
REM  une nuit de track record.
REM
REM  A LANCER EN ADMINISTRATEUR, sur le serveur, depuis C:\AITB.
REM  Clic droit sur ce fichier > Executer en tant qu'administrateur.
REM ===========================================================================
setlocal
cd /d "%~dp0"
title A.I.T.B - Installation des taches CRYPTO

echo.
echo   ========================================================
echo    INSTALLATION DES TACHES PLANIFIEES CRYPTO
echo   ========================================================
echo.

REM Creer une tache qui s'execute en compte SYSTEM exige une elevation. Sans
REM droits admin, schtasks echoue avec un message peu clair. On le dit avant,
REM plutot que de laisser deviner.
net session >nul 2>&1
if errorlevel 1 (
  echo   ERREUR : ce script doit etre lance EN ADMINISTRATEUR.
  echo.
  echo   Ferme cette fenetre, fais un clic droit sur
  echo   INSTALL_TACHES_CRYPTO.bat puis "Executer en tant qu'administrateur".
  echo.
  pause
  exit /b 1
)

set MANQUE=0
if not exist "DASHBOARD_CRYPTO_SERVEUR.bat" (echo   MANQUE : DASHBOARD_CRYPTO_SERVEUR.bat & set MANQUE=1)
if not exist "ENVOI_VERCEL_CRYPTO.bat"      (echo   MANQUE : ENVOI_VERCEL_CRYPTO.bat & set MANQUE=1)
if not exist "dashboard_crypto.py"          (echo   MANQUE : dashboard_crypto.py & set MANQUE=1)
if not exist "pousser_crypto.py"            (echo   MANQUE : pousser_crypto.py & set MANQUE=1)
if not exist "jeton_envoi_crypto.txt"       (echo   MANQUE : jeton_envoi_crypto.txt & set MANQUE=1)
REM LE .venv EST LE PLUS IMPORTANT DE LA LISTE, et il y manquait le 16/09.
REM Les deux .bat font "call .venv\Scripts\activate" puis "python". Sans
REM .venv dans CE dossier, l'activation echoue, python n'existe pas pour le
REM compte SYSTEM, et les deux taches meurent a la seconde ou elles demarrent.
REM On obtient alors une installation qui s'annonce reussie avec deux taches
REM creees, un port 8081 muet et un miroir Vercel fige. Symptomes exacts du
REM 16/09. Mieux vaut refuser ici que creer deux taches mortes.
if not exist ".venv\Scripts\python.exe"     (echo   MANQUE : .venv\Scripts\python.exe & set MANQUE=1)
if "%MANQUE%"=="1" (
  echo.
  echo   Copie les fichiers manquants dans %~dp0 avant de relancer.
  echo.
  echo   SAUF le .venv : un environnement Python ne se copie pas, il se cree.
  echo   S'il manque, lance INSTALL_SERVEUR.bat dans ce meme dossier. Il est
  echo   SANS DANGER a relancer : si un .venv en Python 3.12 existe deja il le
  echo   garde, et les 4 dependances sont epinglees, donc rien ne bouge pour le
  echo   bot forex qui partage cet environnement.
  echo.
  echo   NE LANCE PAS CREER_ENV.bat pour ca. Malgre son nom il ne cree aucun
  echo   environnement Python : il demande le login et le mot de passe MAITRE
  echo   FTMO et reecrit le .env du bot forex, sur la machine qui tient
  echo   l'evaluation.
  echo.
  pause
  exit /b 2
)

echo   Tous les fichiers requis sont presents, .venv compris.
echo.
REM Afficher le dossier n'est pas cosmetique. Les taches sont enregistrees avec
REM %%~dp0, donc elles pointent vers l'endroit d'ou CE fichier est lance. Lance
REM depuis un dossier de telechargement, l'installation reussit et pointe vers
REM le mauvais endroit, sans que rien ne le signale.
echo   Les taches vont pointer vers ce dossier :
echo       %~dp0
echo   Si ce n'est pas C:\AITB\, ferme cette fenetre et relance le fichier
echo   depuis le bon dossier.
echo.
pause

REM /f ecrase une tache existante sans poser de question : relancer ce script
REM apres une mise a jour doit etre sans risque et sans effet de bord.
REM GUILLEMETS SIMPLES, PAS D'ANTISLASH. cmd n'utilise PAS l'antislash comme
REM caractere d'echappement : un /tr "\"chemin\"" partirait litteralement avec
REM ses antislashs et la tache pointerait vers un chemin invalide. La tache
REM AITB_Watchdog, qui fonctionne depuis ce matin, utilise cette forme simple.
REM %~dp0 se termine deja par un antislash, donc pas de doublon a craindre.
REM
REM POURQUOI /ru SYSTEM /sc onstart, ET PAS /sc onlogon
REM Une tache /sc onlogon /rl highest SANS /ru SYSTEM s'execute DANS LA SESSION
REM de l'utilisateur connecte. Si la session se ferme, le processus meurt avec
REM elle, exactement comme une fenetre console. Une telle tache n'aurait donc
REM PAS empeche la panne du 15/09 a 22h52, tout en donnant la certitude d'etre
REM protege. C'est pire que pas de tache du tout.
REM
REM /ru SYSTEM s'execute hors de toute session et survit a sa fermeture.
REM
REM Cote FOREX cette option est INDISPONIBLE : MT5 exige un bureau interactif,
REM et le bot comme son tableau de bord lisent MT5. Les taches forex doivent
REM rester en session utilisateur. C'est une contrainte subie, pas un choix.
REM
REM Cote CRYPTO elle est disponible, et c'est VERIFIE et non suppose. La
REM fermeture transitive des imports depuis les deux points d'entree couvre
REM 7 fichiers : dashboard_crypto, pousser_crypto, aitb/arb_bot, aitb/arb_eu,
REM aitb/arb_control, aitb/ledger, aitb/okx. Aucun n'importe MetaTrader5, ne
REM touche ctypes.windll, n'ouvre d'interface graphique, ni ne lit une variable
REM de session. Seule dependance tierce : numpy. Les chemins sont derives de
REM __file__, donc absolus, et non du repertoire courant.
REM
REM Un premier controle n'avait parcouru que 2 fichiers : il resolvait mal la
REM forme "from aitb import X" et n'avait donc jamais ouvert le dossier aitb/.
REM Sa conclusion etait la bonne par chance, pas par preuve.
REM
REM NE PAS transposer ce choix aux taches forex : la difference est reelle.
echo   [1/4] Tableau de bord crypto (port 8081)...
schtasks /create /tn AITB_Dashboard_Crypto /tr "%~dp0DASHBOARD_CRYPTO_SERVEUR.bat" /ru SYSTEM /sc onstart /f >nul
if errorlevel 1 (echo         ECHEC) else (echo         cree)

echo   [2/4] Envoi vers le miroir Vercel (toutes les 60 s)...
schtasks /create /tn AITB_Envoi_Crypto /tr "%~dp0ENVOI_VERCEL_CRYPTO.bat" /ru SYSTEM /sc onstart /f >nul
if errorlevel 1 (echo         ECHEC) else (echo         cree)

REM REGLE DE PARE-FEU, obligatoire des lors qu'on passe en /ru SYSTEM.
REM Quand un programme ecoute un port pour la premiere fois, Windows affiche
REM "Autoriser l'acces ?" et attend un clic. Un service SYSTEM n'a AUCUN bureau
REM ou afficher cette fenetre : la demande n'est jamais accordee, et l'entrant
REM reste bloque en silence. Ca ne se voit pas comme un refus mais comme un
REM DELAI DEPASSE, alors qu'un port simplement ferme repond tout de suite.
REM Le dashboard forex sur 8080 n'a pas ce probleme parce que quelqu'un a
REM clique sur sa fenetre, a l'epoque ou il tournait dans une session.
REM Le controle d'acces reste la liste blanche d'IP dans dashboard_crypto.py,
REM pas le pare-feu. On ouvre le port, la liste blanche filtre derriere.
echo   [3/4] Regle de pare-feu pour le port 8081...
netsh advfirewall firewall delete rule name="AITB Dashboard Crypto 8081" >nul 2>&1
netsh advfirewall firewall add rule name="AITB Dashboard Crypto 8081" dir=in action=allow protocol=TCP localport=8081 >nul
if errorlevel 1 (echo         ECHEC) else (echo         creee)

REM REGLAGES PAR DEFAUT DE schtasks, QU'IL FAUT DEFAIRE. Lus le 16/09 sur la
REM tache reellement creee, pas supposes :
REM   Power Management : Stop On Battery Mode, No Start On Batteries
REM   Stop Task If Runs X Hours and X Mins : 72:00:00
REM Le serveur est un PC PORTABLE. Avec ces valeurs, une coupure de courant le
REM fait passer sur batterie et Windows ARRETE les deux taches, puis refuse de
REM les redemarrer tant qu'il y reste. C'est exactement la panne contre laquelle
REM on se protege, reintroduite par la porte de derriere. Et la limite de 72 h
REM tue un tableau de bord permanent tous les trois jours.
REM
REM schtasks n'expose aucun commutateur pour ces trois reglages, d'ou PowerShell.
REM RestartCount relance la tache si le programme meurt, ce qu'aucune des deux
REM versions precedentes ne faisait.
echo   [4/4] Reglages de robustesse (batterie, duree, relance)...
powershell -NoProfile -Command "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); foreach ($t in 'AITB_Dashboard_Crypto','AITB_Envoi_Crypto') { Set-ScheduledTask -TaskName $t -Settings $s | Out-Null }"
if errorlevel 1 (echo         ECHEC) else (echo         appliques)

echo.
REM NE PAS FAIRE TAIRE CES DEUX COMMANDES. Elles etaient redirigees vers nul,
REM et c'etait une faute : avec /sc onstart, une tache qui ne demarre pas ici ne
REM demarrera pas avant le PROCHAIN REDEMARRAGE de la machine. Cacher la sortie
REM de la seule commande qui lance quoi que ce soit transforme un echec bruyant
REM en attente silencieuse de plusieurs heures. C'est exactement ce qui s'est
REM passe le 16/09 : installation annoncee reussie, et rien qui tournait.
echo   Demarrage immediat, sans attendre le prochain demarrage de la machine...
echo.
REM Pas de %%errorlevel%% dans le message : a l'interieur d'un bloc entre
REM parentheses, cmd developpe la variable quand il ANALYSE le bloc, donc il
REM afficherait la valeur d'avant la commande. Il faudrait enabledelayedexpansion
REM et des points d'exclamation. On se passe du code, le mot ECHEC suffit.
schtasks /run /tn AITB_Dashboard_Crypto
if errorlevel 1 (echo         ECHEC : LE DASHBOARD N A PAS DEMARRE)
schtasks /run /tn AITB_Envoi_Crypto
if errorlevel 1 (echo         ECHEC : L ENVOI N A PAS DEMARRE)
echo.
echo   Etat 8 secondes apres le lancement :
ping -n 9 127.0.0.1 >nul
REM PAS de findstr sur "TaskName" ni "Status" : sur un Windows en francais ces
REM champs s'appellent "Nom de la tache" et "Etat". Le filtre ne trouverait
REM rien et cet ecran resterait VIDE, donnant l'impression que l'installation a
REM echoue alors qu'elle a reussi. Un filtre ecrit dans la langue d'un seul
REM Windows est un piege sur tous les autres.
schtasks /query /tn AITB_Dashboard_Crypto /fo TABLE /nh
schtasks /query /tn AITB_Envoi_Crypto     /fo TABLE /nh
echo.
echo   Un statut "Ready" au lieu de "Running" ici veut dire que le programme
echo   a demarre PUIS s'est arrete tout de suite. Dans ce cas la cause est
echo   dans le journal, pas dans la tache.
echo.

echo   ========================================================
echo    ENSUITE
echo   ========================================================
echo   Dans 90 secondes, depuis le PC principal :
echo     http://100.x.y.z:8081            doit repondre
echo     https://aitb-c-9wc0bmr8ynxr66.vercel.app   doit montrer un envoi recent
echo.
echo   Journaux si quelque chose ne demarre pas :
echo     %~dp0logs\dashboard_crypto_AAAA-MM-JJ.log
echo     %~dp0logs\envoi_crypto_AAAA-MM-JJ.log
echo.
echo   CE QUI CHANGE : ces deux taches tournent en compte SYSTEM, hors de
echo   toute session. Une fermeture de session comme celle du 15/09 a 22h52
echo   ne les arrete plus, et elles repartent seules a chaque demarrage de
echo   la machine, sans attendre que quelqu'un ouvre une session.
echo.
echo   CONSEQUENCE VISIBLE : leurs fenetres n'apparaissent PLUS dans ta
echo   session. C'est normal. Ne les relance pas a la main en croyant
echo   qu'elles sont mortes : tu aurais deux instances. Pour verifier
echo   qu'elles tournent, ouvre l'adresse du tableau de bord, ou lis les
echo   journaux indiques plus haut.
echo.
echo   SI LE TABLEAU DE BORD NE REPOND PAS, son journal donne la reponse :
echo     "indisponible (errno 10049" = adresse Tailscale pas encore assignee
echo        au demarrage. Le programme reessaie seul pendant 15 minutes.
echo     "indisponible (errno 10048" = une autre instance tient deja le port.
echo     une erreur sur .venv ou sur python = le compte SYSTEM n'ATTEINT pas
echo        l'environnement Python. Attention a la nuance : le controle du
echo        debut verifie que .venv EXISTE, vu par ton compte a toi. Il ne
echo        peut pas prouver que SYSTEM a le droit de le lire. C'est le seul
echo        point qui ne se verifie qu'au premier lancement reel.
echo.
echo   En cas de doute, DIAGNOSTIC_CRYPTO.bat repond a tout d'un coup :
echo   ou pointent les taches, ce qu'elles ont renvoye, si python est la,
echo   si quelque chose ecoute le 8081 et ce que disent les journaux.
echo.
pause
