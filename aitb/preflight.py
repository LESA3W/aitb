# aitb/preflight.py
import os
from pathlib import Path
from config.settings import CONFIG
from utils.time_logs import log
from utils.colors import colors
import time

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


def _ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)


def check_environment() -> bool:
    ok = True

    # 📁 Dossiers requis
    for key in ("SCREENSHOTS_FOLDER", "REPORTS_FOLDER", "MACROS_FOLDER"):
        try:
            _ensure_dir(CONFIG[key])
        except Exception as e:
            log(colors(f"❌ Impossible de créer le dossier {CONFIG[key]}: {e}", "red"))
            ok = False

    time.sleep(1)

    # 📊 Tesseract
    tess = CONFIG.get("TESSERACT_PATH")
    if not tess or not os.path.exists(tess):
        log(colors("👁️ Tesseract --> 🔴 OFF", "red"))
        ok = False
    else:
        log(colors("👁️ Tesseract --> 🟢 ON", "green"))

    time.sleep(1)

    # 📊 MT5
    if mt5 is None:
        log(colors("📊 MT5 --> 🔴 OFF", "red"))
        ok = False
    else:
        log(colors("📊 MT5 --> 🟢 ON", "green"))

    time.sleep(1)

    # 📊 Macros
    macros_dir = CONFIG.get("MACROS_FOLDER")
    if not macros_dir or not os.path.isdir(macros_dir):
        log(colors("🐭 MMC --> 🔴 OFF", "red"))
        ok = False
    else:
        log(colors("🐭 MMC --> 🟢 ON", "green"))

    time.sleep(1)

    return ok

# ===========================================================================
# Controle pre-vol prop firm
# ===========================================================================
# check_environment() ci-dessus appartient a l'ancienne architecture (macros,
# Tesseract, captures) et reste en place pour aitb/scheduler.py. Ce qui suit
# sert au bot headless, avant de le brancher sur un compte FTMO.
#
# Les trois pannes deja vecues sur ce projet etaient toutes SILENCIEUSES :
#   - FILLING_MODE IOC accepte chez IC Markets, rejete par MetaQuotes (10030),
#     tous les ordres refuses sans message visible
#   - MAX_LOT fige a 0.10, calibre pour 1000 USD ; sur 10 000 USD le risque
#     reel tombait a 0.38% au lieu de 1%, sans une ligne d'alerte
#   - powercfg qui echouait sous Git Bash, donc un PC qui s'endort la nuit
#
# La bascule FTMO se fait un lundi matin avec un chrono de 14 jours qui
# demarre : c'est le pire moment pour decouvrir un de ces angles morts.
# Ce controle ne modifie rien, il regarde et il dit.

VERT, JAUNE, ROUGE = "OK", "ALERTE", "ECHEC"
_MARQUES = {VERT: "[ OK ]", JAUNE: "[ !! ]", ROUGE: "[FAIL]"}
_TEINTES = {VERT: "green", JAUNE: "yellow", ROUGE: "red"}


def _dire(etat, titre, detail=""):
    log(colors("  %s %-32s %s" % (_MARQUES[etat], titre, detail), _TEINTES[etat]))
    return etat


def _verifier_env():
    f = Path(__file__).resolve().parent.parent / ".env"
    if not f.exists():
        return _dire(JAUNE, "fichier .env", "absent, compte du terminal utilise")
    contenu = f.read_text(encoding="utf-8", errors="replace")
    manque = [k for k in ("FTMO_LOGIN", "FTMO_PASSWORD", "FTMO_SERVER")
              if k not in contenu]
    if manque:
        return _dire(ROUGE, "identifiants .env", "manque " + ", ".join(manque))
    return _dire(VERT, "identifiants .env", "login, mot de passe, serveur")


def _verifier_connexion():
    from aitb.trade_manager import _identifiants
    ids = _identifiants()
    if ids:
        login, mdp, serveur, chemin = ids
        kw = {"login": login, "password": mdp, "server": serveur}
        if chemin:
            kw["path"] = chemin
        ok = mt5.initialize(**kw)
    else:
        ok = mt5.initialize()
    if not ok:
        return _dire(ROUGE, "connexion MT5", str(mt5.last_error())[:44]), None
    info = mt5.account_info()
    if info is None:
        return _dire(ROUGE, "compte MT5", "aucune information"), None
    genre = {0: "DEMO", 1: "CONCOURS", 2: "REEL"}.get(
        getattr(info, "trade_mode", None), "INCONNU")
    etat = VERT if genre in ("DEMO", "CONCOURS") else JAUNE
    _dire(etat, "compte connecte", "%s @ %s | %s | %.2f %s"
          % (info.login, info.server, genre, info.balance, info.currency))
    return etat, info


def _verifier_solde(info):
    pf = CONFIG.get("PROPFIRM", {})
    if not pf.get("ENABLED"):
        return _dire(JAUNE, "garde-fou prop firm", "DESACTIVE, aucune limite surveillee")
    attendu = float(pf.get("INITIAL_BALANCE", 0))
    reel = float(getattr(info, "balance", 0))
    # Un ecart de plus de 1% fausse tous les seuils : un plancher calcule sur
    # 50 000 alors que le compte en fait 80 000 ne protege rien du tout.
    if attendu <= 0 or abs(attendu - reel) / max(reel, 1) > 0.01:
        return _dire(ROUGE, "solde PROPFIRM vs compte",
                     "config %.0f, compte %.0f" % (attendu, reel))
    return _dire(VERT, "solde PROPFIRM vs compte",
                 "%.0f, perte max %.0f%% / jour %.0f%%"
                 % (reel, pf["MAX_TOTAL_LOSS_PCT"], pf["MAX_DAILY_LOSS_PCT"]))


def _verifier_sizing(info):
    from aitb.risk_manager import calc_lot, max_lot_for
    capital = float(getattr(info, "balance", 0))
    risque = float(CONFIG.get("RISK_PCT", 1.0))
    # Deux problemes de gravite tres differente, longtemps confondus :
    #   absents : le symbole n'existe pas chez ce broker. _ligne_portable()
    #             ecarte la ligne au demarrage, rien ne part de travers.
    #   soucis  : le lot envoye ne correspond pas au lot voulu. La ligne
    #             tradera avec un risque fausse, sans une alerte. C'est grave.
    soucis, absents = [], []
    for ligne in CONFIG.get("LIVE_BOOK", []):
        sym, tf = ligne["symbol"], ligne["timeframe"]
        mt5.symbol_select(sym, True)
        si = mt5.symbol_info(sym)
        if si is None:
            absents.append(sym)
            continue
        # Stop REEL de la ligne, calcule sur l'ATR et son sl_atr. Une valeur
        # devinee (160 pips pour tout H4) faisait crier une incoherence sur
        # XAUUSD H8, dont le stop fait 851 pips : le lot "theorique" etait
        # faux, pas le lot envoye.
        from aitb.live import _stop_estime
        pips = _stop_estime(sym, tf) or (160.0 if tf.startswith("H") else 40.0)
        vpip = (si.point * 10 / si.trade_tick_size) * si.trade_tick_value
        voulu = (capital * risque / 100.0) / (vpip * pips)
        envoye = calc_lot(capital, risque, pips, sym)
        pas = float(getattr(si, "volume_step", 0.01) or 0.01)
        if voulu > 0 and (voulu - envoye) > pas * 1.5:
            soucis.append("%s %s: %.2f voulu, %.2f envoye" % (sym, tf, voulu, envoye))
    if soucis:
        _dire(ROUGE, "sizing du book", "%d ligne(s) ecrasee(s)" % len(soucis))
        for x in soucis:
            log(colors("         " + x, "yellow"))
        return ROUGE
    total = len(CONFIG.get("LIVE_BOOK", []))
    if absents:
        return _dire(JAUNE, "sizing du book",
                     "%d lignes au bon calibre, %d ignoree(s) ici : %s"
                     % (total - len(absents), len(absents), ", ".join(absents)))
    return _dire(VERT, "sizing du book", "%d lignes au bon calibre, plafond %.2f lot"
                 % (total, max_lot_for(capital)))


def _verifier_filling():
    from aitb.trade_manager import _get_filling_mode
    noms = {mt5.ORDER_FILLING_FOK: "FOK", mt5.ORDER_FILLING_IOC: "IOC",
            mt5.ORDER_FILLING_RETURN: "RETURN"}
    vus = {}
    for ligne in CONFIG.get("LIVE_BOOK", []):
        sym = ligne["symbol"]
        mt5.symbol_select(sym, True)
        try:
            vus[sym] = noms.get(_get_filling_mode(sym), "?")
        except Exception as e:
            vus[sym] = "erreur " + str(e)[:18]
    if not vus:
        return _dire(JAUNE, "mode de remplissage", "book vide")
    if any(v == "?" or v.startswith("erreur") for v in vus.values()):
        return _dire(ROUGE, "mode de remplissage", str(vus))
    return _dire(VERT, "mode de remplissage",
                 ", ".join("%s=%s" % (k, v) for k, v in vus.items()))


def _verifier_marge(info):
    from aitb.risk_manager import calc_lot
    from aitb.live import _stop_estime
    capital = float(getattr(info, "balance", 0))
    risque = float(CONFIG.get("RISK_PCT", 1.0))
    total = 0.0
    for ligne in CONFIG.get("LIVE_BOOK", []):
        sym, tf = ligne["symbol"], ligne["timeframe"]
        si = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        if si is None or tick is None:
            continue
        # BUG CORRIGE LE 2026-09-14, MEME PIEGE QUE LE CONTROLE DE COHERENCE.
        # Ce calcul devinait le stop : 160 pips en H4, 40 pips partout ailleurs.
        # Le stop reel de XAUUSD H8 fait 851 pips, soit un lot 20 fois plus
        # petit. La marge totale annoncee etait 132 657 EUR sur un compte de
        # 80 000, ce qui bloquait le demarrage pour une contrainte inexistante.
        # _verifier_sizing et _verifier_ordres utilisaient deja _stop_estime,
        # seul ce controle avait ete oublie.
        pips = _stop_estime(sym, tf) or (160.0 if tf == "H4" else 40.0)
        lot = calc_lot(capital, risque, pips, sym)
        m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, lot, tick.ask)
        total += float(m or 0.0)
    part = total / max(capital, 1) * 100
    # Au-dela de 60%, un mouvement adverse declenche un appel de marge avant
    # meme que les stops ne soient touches.
    etat = VERT if part < 60 else (JAUNE if part < 80 else ROUGE)
    return _dire(etat, "marge des lignes ouvertes",
                 "%.0f sur %.0f = %.1f%% du capital" % (total, capital, part))


def _verifier_garde_fou(info):
    from aitb.propfirm_guard import (PropFirmGuard, OK as G_OK,
                                     STOP_ENTREES, FERMER_TOUT)
    g = PropFirmGuard(CONFIG)
    if not g.actif:
        return _dire(JAUNE, "seuils du garde-fou", "inactif")
    solde = float(getattr(info, "balance", 0))
    marge = solde * g.perte_jour_pct / 100.0
    erreurs = []
    for part, attendu in ((0.30, G_OK), (0.60, STOP_ENTREES), (0.90, FERMER_TOUT)):
        equity = solde - marge * part
        # sans_ecrire : ce test simule des equity qui n'ont jamais existe. Sans
        # lui, chaque pre-vol figeait la reference de journee dans
        # data/propfirm_state.json avec une valeur fabriquee, lisible et
        # plausible, que personne n'aurait identifiee comme un artefact de test.
        obtenu = g.evaluer(equity, solde, sans_ecrire=True)["statut"]
        if obtenu != attendu:
            erreurs.append("equity %.0f -> %s au lieu de %s" % (equity, obtenu, attendu))
    if erreurs:
        _dire(ROUGE, "seuils du garde-fou", "%d cas faux" % len(erreurs))
        for e in erreurs:
            log(colors("         " + e, "yellow"))
        return ROUGE
    return _dire(VERT, "seuils du garde-fou", "stop entrees a -%.0f, fermeture a -%.0f"
                 % (marge * g.seuil_souple, marge * g.seuil_dur))


def _verifier_ordres(info):
    """
    Soumet un ordre complet au serveur SANS l'executer, pour chaque ligne.

    C'est la seule verification qui teste la chaine entiere : volume, filling
    mode, marge, distance minimale du stop imposee par le broker, et symbole
    reellement ouvert au trading. Les autres controles regardent la config ;
    celui-ci demande au broker ce qu'il ferait.
    """
    from aitb.risk_manager import calc_lot
    from aitb.trade_manager import _get_filling_mode
    from aitb.live import _stop_estime

    capital = float(getattr(info, "balance", 0))
    risque = float(CONFIG.get("RISK_PCT", 1.0))
    refuses, fermes, ok = [], [], 0

    for ligne in CONFIG.get("LIVE_BOOK", []):
        sym, tf = ligne["symbol"], ligne.get("timeframe", "H1")
        mt5.symbol_select(sym, True)
        si = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        if si is None or tick is None or not tick.ask:
            fermes.append("%s: pas de cotation" % sym)
            continue

        sl_pips = _stop_estime(sym, tf) or 50.0
        lot = calc_lot(capital, risque, sl_pips, sym)
        prix = tick.ask
        sl = prix - sl_pips * si.point * 10

        # Distance minimale imposee par le broker entre le prix et le stop.
        mini = getattr(si, "trade_stops_level", 0) or 0
        if (prix - sl) / si.point < mini:
            refuses.append("%s: stop a %.0f points, minimum broker %d"
                           % (sym, (prix - sl) / si.point, mini))
            continue

        res = mt5.order_check({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": float(lot),
            "type": mt5.ORDER_TYPE_BUY, "price": prix, "sl": sl,
            "deviation": 20, "magic": 20260912, "comment": "preflight",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _get_filling_mode(sym),
        })
        if res is None:
            refuses.append("%s: order_check muet (%s)" % (sym, mt5.last_error()))
        elif res.retcode == 0:
            ok += 1
        elif res.retcode == 10018:
            # Marche ferme : normal le week-end, pas un defaut de configuration.
            fermes.append("%s: marche ferme" % sym)
        else:
            refuses.append("%s: refuse %d %s" % (sym, res.retcode, res.comment))

    total = len(CONFIG.get("LIVE_BOOK", []))
    if refuses:
        _dire(ROUGE, "validation d'ordre", "%d ligne(s) refusee(s) sur %d"
              % (len(refuses), total))
        for x in refuses:
            log(colors("         " + x, "yellow"))
        return ROUGE
    if fermes:
        return _dire(JAUNE, "validation d'ordre",
                     "%d acceptees, %d marches fermes" % (ok, len(fermes)))
    return _dire(VERT, "validation d'ordre", "%d lignes sur %d passeraient un ordre"
                 % (ok, total))


def controle_propfirm() -> int:
    """0 si le bot peut demarrer, 1 s'il y a un blocage."""
    log(colors("  " + "=" * 58, "cyan"))
    log(colors("   CONTROLE PRE-VOL PROP FIRM", "cyan"))
    log(colors("  " + "=" * 58, "cyan"))

    if mt5 is None:
        log(colors("  [FAIL] MetaTrader5 introuvable", "red"))
        return 1

    etats = [_verifier_env()]
    etat, info = _verifier_connexion()
    etats.append(etat)
    if info is not None:
        etats += [_verifier_solde(info), _verifier_sizing(info), _verifier_filling(),
                  _verifier_marge(info), _verifier_garde_fou(info),
                  _verifier_ordres(info)]
        try:
            mt5.shutdown()
        except Exception:
            pass

    log(colors("  " + "=" * 58, "cyan"))
    if ROUGE in etats:
        log(colors("   NE PAS DEMARRER : %d blocage(s)" % etats.count(ROUGE), "red"))
        return 1
    if JAUNE in etats:
        log(colors("   Demarrage possible, %d point(s) a verifier"
                   % etats.count(JAUNE), "yellow"))
        return 0
    log(colors("   Tout est vert, le bot peut etre branche", "green"))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(controle_propfirm())

