@echo off
REM Bot d'arbitrage de financement OKX, MODE PAPIER.
REM
REM Aucun compte, aucune cle API, aucun euro engage. Il lit les vrais taux de
REM financement sur OKX toutes les 10 minutes et tient la comptabilite exacte
REM de ce qu'une position neutre aurait rapporte.
REM
REM C'est le seul moyen de constituer un track record reel a cout nul. Le
REM financement tombe toutes les 8 heures, donc il faut le laisser tourner
REM plusieurs jours avant que le moindre chiffre ait du sens.
REM
REM Ctrl+C arrete l'affichage. L'etat est dans data\arb_state.json et survit.
setlocal
cd /d "%~dp0"
title A.I.T.B Arbitrage OKX (papier)
set PYTHONIOENCODING=utf-8

:boucle
python -m aitb.arb_bot --run
timeout /t 600 /nobreak >nul
goto boucle
