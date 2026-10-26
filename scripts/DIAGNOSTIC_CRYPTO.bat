@echo off
REM ===========================================================================
REM  Diagnostic des deux taches CRYPTO. NE MODIFIE RIEN, lecture seule.
REM
REM  A LANCER SUR LE SERVEUR, en administrateur, depuis n'importe ou.
REM  Il ecrit tout dans diagnostic_crypto.txt a cote de lui, puis l'affiche.
REM
REM  POURQUOI CE FICHIER EXISTE
REM  Le 16/09, l'installation s'est annoncee reussie et rien ne tournait :
REM  port 8081 muet, et le miroir Vercel avec 28 h de retard. Or le pousseur
REM  n'ecoute aucun port et ne depend pas de Tailscale, donc aucun pare-feu ne
REM  peut l'expliquer. La cause est commune aux deux taches et en amont. Les
REM  trois candidates sont :
REM    - les taches pointent vers un dossier qui n'est pas C:\AITB
REM    - ce dossier n'a pas de .venv, donc python n'existe pas pour SYSTEM
REM    - les taches ont ete creees mais jamais demarrees
REM  La ligne "Taches a executer" de la section 1 tranche les trois d'un coup.
REM ===========================================================================
setlocal
cd /d "%~dp0"
title A.I.T.B - Diagnostic CRYPTO
set OUT=%~dp0diagnostic_crypto.txt

REM CHEMINS COMPLETS POUR find ET findstr, ce n'est pas du zele.
REM "find" existe AUSSI sous Unix. Lance depuis un shell qui a mis les outils
REM Git/MSYS devant dans le PATH, cmd appelle le find d'Unix, qui ne comprend
REM ni /c ni /v et part explorer l'arborescence : le script pend au lieu de
REM repondre. Mesure le 16/09 : 2 secondes avec findstr seul, 101 secondes et
REM un arret force des que "find" a ete ajoute. findstr, lui, n'a pas de jumeau
REM Unix, ce qui explique que le defaut soit apparu si tard.
set FIND=%SystemRoot%\System32\find.exe
set FINDSTR=%SystemRoot%\System32\findstr.exe

break > "%OUT%"
echo Diagnostic en cours, quelques secondes...

echo ===== DIAGNOSTIC CRYPTO ===== >> "%OUT%"
powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'" >> "%OUT%" 2>&1
echo. >> "%OUT%"
echo Ce fichier est lance depuis : %~dp0 >> "%OUT%"
echo. >> "%OUT%"

echo ===== 1. OU POINTENT LES TACHES, ET QU ONT ELLES RENVOYE ===== >> "%OUT%"
echo --- AITB_Dashboard_Crypto --- >> "%OUT%"
schtasks /query /tn AITB_Dashboard_Crypto /fo LIST /v >> "%OUT%" 2>&1
echo. >> "%OUT%"
echo --- AITB_Envoi_Crypto --- >> "%OUT%"
schtasks /query /tn AITB_Envoi_Crypto /fo LIST /v >> "%OUT%" 2>&1
echo. >> "%OUT%"

echo ===== 2. LE PYTHON EXISTE-T-IL LA OU LA TACHE POINTE ===== >> "%OUT%"
echo -- dans le dossier de CE fichier -- >> "%OUT%"
if exist "%~dp0.venv\Scripts\python.exe" (echo   .venv present ICI >> "%OUT%") else (echo   .venv ABSENT ICI >> "%OUT%")
echo -- dans C:\AITB -- >> "%OUT%"
if exist "C:\AITB\.venv\Scripts\python.exe" (echo   .venv present dans C:\AITB >> "%OUT%") else (echo   .venv ABSENT dans C:\AITB >> "%OUT%")
if exist "C:\AITB\dashboard_crypto.py" (echo   dashboard_crypto.py present dans C:\AITB >> "%OUT%") else (echo   dashboard_crypto.py ABSENT dans C:\AITB >> "%OUT%")
if exist "C:\AITB\jeton_envoi_crypto.txt" (echo   jeton_envoi_crypto.txt present dans C:\AITB >> "%OUT%") else (echo   jeton_envoi_crypto.txt ABSENT dans C:\AITB >> "%OUT%")
echo -- python vu par le compte courant -- >> "%OUT%"
where python >> "%OUT%" 2>&1
echo. >> "%OUT%"

echo ===== 3. QUELQUE CHOSE ECOUTE-T-IL LE PORT 8081 ===== >> "%OUT%"
netstat -ano | "%FINDSTR%" ":8081" >> "%OUT%" 2>&1
echo (aucune ligne ci-dessus = rien n ecoute le 8081) >> "%OUT%"
echo -- pour comparaison, le 8080 du forex qui lui fonctionne -- >> "%OUT%"
netstat -ano | "%FINDSTR%" ":8080" >> "%OUT%" 2>&1
echo. >> "%OUT%"

echo ===== 4. LE PARE-FEU LAISSE-T-IL ENTRER LE 8081 ===== >> "%OUT%"
REM AUTO-VERIFICATION. Une section vide veut dire deux choses OPPOSEES :
REM "aucune regle ne correspond" ou "la commande a echoue". A l'ecran les deux
REM sont identiques et menent a des conclusions contraires. On compte donc
REM d'abord les lignes lues. Sur un Windows sain ce total vaut plusieurs
REM milliers : mesure 5412 sur le PC principal. Un total a 0 signifie que la
REM commande est morte, pas que le pare-feu est vide.
echo -- total de lignes lues par netsh, si 0 la commande a echoue -- >> "%OUT%"
netsh advfirewall firewall show rule name=all dir=in | "%FIND%" /c /v "" >> "%OUT%" 2>&1
echo -- lignes mentionnant 8081, 8080 ou AITB -- >> "%OUT%"
netsh advfirewall firewall show rule name=all dir=in | "%FINDSTR%" /i "8081 8080 AITB" >> "%OUT%" 2>&1
echo. >> "%OUT%"
echo Rien juste au-dessus ALORS QUE le total est grand = aucune regle pour ces >> "%OUT%"
echo ports, donc entrant bloque par defaut. C'est attendu pour une tache SYSTEM, >> "%OUT%"
echo qui n'a aucun bureau ou afficher la demande d'autorisation Windows. >> "%OUT%"
echo. >> "%OUT%"

echo ===== 5. LES JOURNAUX ===== >> "%OUT%"
if exist "%~dp0logs" (dir /b /o-d "%~dp0logs" >> "%OUT%" 2>&1) else (echo   PAS DE DOSSIER logs ici >> "%OUT%")
echo. >> "%OUT%"
if exist "C:\AITB\logs" (dir /b /o-d "C:\AITB\logs" >> "%OUT%" 2>&1) else (echo   PAS DE DOSSIER logs dans C:\AITB >> "%OUT%")
echo. >> "%OUT%"
echo -- contenu des journaux crypto trouves -- >> "%OUT%"
for %%f in ("%~dp0logs\*crypto*.log" "C:\AITB\logs\*crypto*.log") do (
  echo --- %%f --- >> "%OUT%"
  type "%%f" >> "%OUT%" 2>&1
  echo. >> "%OUT%"
)
echo ===== FIN ===== >> "%OUT%"

cls
type "%OUT%"
echo.
echo   ------------------------------------------------------------
echo    Tout ceci est aussi enregistre dans :
echo      %OUT%
echo    Ouvre ce fichier et colle son contenu dans la conversation.
echo   ------------------------------------------------------------
echo.
pause
