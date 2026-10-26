#!/usr/bin/env python3
"""
dashboard.py - Tableau de bord A.I.T.B, version 2.

Terminal de trading : fond noir, chiffres tabulaires, vert et rouge comme
seules couleurs de donnees. Deux objectifs suivis en permanence, le jour et
la semaine. Semaine de bourse du lundi au vendredi uniquement, le forex est
ferme le week-end.

Lancement :  python dashboard.py     puis  http://127.0.0.1:8080

Lecture seule cote marche : ce script n'ouvre et ne ferme aucune position.
Les seuls ecrits sont les boutons ON / PAUSE / OFF, qui posent un drapeau dans
data/bot_control.json que le bot et le watchdog relisent.
"""

import html
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5   # noqa: E402

from aitb import control
from aitb import ledger    # noqa: E402
from aitb import propfirm_guard   # noqa: E402
from config.settings import CONFIG   # noqa: E402

PORT = 8080

# Adresse d'ecoute. 127.0.0.1 par defaut : la page n'est alors joignable
# QUE depuis la machine elle-meme.
#
# NE PAS mettre 0.0.0.0 avec une redirection de port sur la box. Cette page
# expose deux POST sans aucune authentification, /mode et /restart, qui
# arretent et relancent un bot qui trade sur un compte prop firm. Ouverte sur
# Internet, n'importe qui la trouvant pourrait couper le bot.
#
# Pour y acceder a distance, passer par un reseau prive type Tailscale et
# poser ici l'adresse de l'interface privee :
#     set AITB_DASHBOARD_BIND=100.x.y.z
# La page reste alors joignable uniquement depuis les appareils du reseau.
BIND = os.environ.get("AITB_DASHBOARD_BIND", "127.0.0.1")

# Liste blanche d'adresses. DEUXIEME verrou, volontairement redondant avec
# BIND ci-dessus, et c'est le but : BIND depend d'une variable d'environnement
# posee par un .bat. Le jour ou ce .bat est modifie, copie ou lance a la main
# sans la variable, l'ecoute retombe sur 127.0.0.1... ou sur autre chose. Ici,
# meme une ecoute elargie par erreur ne sert plus rien a personne d'autre.
#
# Ca couvre aussi le cas ou un appareil tiers rejoint le reseau Tailscale :
# appartenir au tailnet ne suffit plus, il faut etre dans cette liste.
#
# Toute autre adresse recoit un 403 nu, sans une seule donnee de compte.
#
# POUR AJOUTER UN APPAREIL sans toucher au code, par exemple l'iPhone une fois
# Tailscale installe dessus (son IP n'existe pas tant qu'il n'a pas rejoint le
# reseau, elle se lit avec "tailscale status") :
#     set AITB_DASHBOARD_IPS=100.x.y.z
IPS_AUTORISEES = {
    "127.0.0.1",        # le serveur lui-meme
    "::1",
    "100.x.y.z",    # weasel-server, la machine qui heberge le bot
    "100.x.y.w",   # lesa3w, le portable principal
}
IPS_AUTORISEES |= {ip.strip() for ip
                   in os.environ.get("AITB_DASHBOARD_IPS", "").split(",")
                   if ip.strip()}

OBJECTIF_HEBDO = 250.0
JOURS_BOURSE = 5                                  # lundi a vendredi
OBJECTIF_JOUR = OBJECTIF_HEBDO / JOURS_BOURSE     # 50, devise du compte
SEUIL_MINI = 0.70
LOG_FILE = ROOT / "logs" / "console_logs.txt"
HEARTBEAT = ROOT / "data" / "heartbeat.json"

_cache = {"data": None, "ts": 0.0, "offset": None}
_lock = threading.Lock()


# ==================== COLLECTE ====================

def etat_propfirm(equity, balance):
    """
    Marge restante avant les limites de la firme, SANS RIEN ECRIRE.

    On appelle evaluer() en sans_ecrire, jamais en mode normal. Elle fige
    sinon le solde de debut de journee dans data/propfirm_state.json au premier
    appel du jour : un tableau de bord qui se rafraichit toutes les 3 secondes
    prendrait donc la main sur le bot pour poser la reference que la firme
    utilise pour juger la perte journaliere, avec l'equity de l'instant ou la
    page a ete ouverte. Seul le processus qui trade a le droit de la fixer.

    Le calcul lui-meme n'est PAS reecrit ici : le dupliquer ferait deriver deux
    versions de la politique de risque. On lit juste, en plus, si le bot a deja
    pose la reference du jour, pour le dire au lieu de le laisser croire.
    """
    g = propfirm_guard.PropFirmGuard(CONFIG)
    if not g.actif or g.solde_initial <= 0:
        return None

    etat = g.etat or {}
    officielle = (bool(etat.get("solde_debut"))
                  and etat.get("jour") == g._jour_firme())

    v = g.evaluer(equity, balance, sans_ecrire=True)
    return {
        "statut": v["statut"],
        "conso": v["consomme_pct"],
        "conso_jour": v["consomme_jour_pct"],
        "conso_total": v["consomme_total_pct"],
        "qui_mord": ("journalière" if v["limite_qui_mord"] == "journaliere"
                     else "totale"),
        "plancher_total": v["plancher_total"],
        "plancher_jour": v["plancher_jour"],
        "marge_jour": max(equity - v["plancher_jour"], 0.0),
        "marge_total": max(equity - v["plancher_total"], 0.0),
        "solde_initial": g.solde_initial,
        "seuil_souple": g.seuil_souple * 100,
        "seuil_dur": g.seuil_dur * 100,
        "reference_officielle": officielle,
    }


def collecte(offset_semaine=0):
    if not mt5.initialize():
        return {"erreur": "MT5 n'est pas lance ou n'est pas connecte au compte."}

    try:
        acc = mt5.account_info()
        if acc is None:
            return {"erreur": "Impossible de lire le compte."}

        now = datetime.now()
        debut_jour = now.replace(hour=0, minute=0, second=0, microsecond=0)
        lundi_courant = debut_jour - timedelta(days=debut_jour.weekday())
        debut_semaine = lundi_courant + timedelta(weeks=offset_semaine)
        fin_semaine = debut_semaine + timedelta(days=7)
        est_courante = offset_semaine == 0

        deals = mt5.history_deals_get(now - timedelta(days=400), now) or []
        fermes = [d for d in deals if d.entry == 1]

        def net(d):
            return d.profit + d.commission + d.swap

        def somme(depuis, jusqu_a):
            return sum(net(d) for d in fermes
                       if depuis <= datetime.fromtimestamp(d.time) < jusqu_a)

        positions = []
        for p in mt5.positions_get() or []:
            info = mt5.symbol_info(p.symbol)
            pip = 10 * info.point if info else 0.0001
            digits = info.digits if info else 5
            avance = (p.price_current - p.price_open) / pip * (1 if p.type == 0 else -1)
            risque = abs(p.price_open - p.sl) / pip if p.sl else 0
            cible = abs(p.tp - p.price_open) / pip if p.tp else 0
            positions.append({
                "symbole": p.symbol,
                "sens": "ACHAT" if p.type == 0 else "VENTE",
                "volume": p.volume,
                "entree": p.price_open, "actuel": p.price_current,
                "digits": digits, "gain": p.profit, "pips": avance,
                "risque": risque, "cible": cible,
                "progression": max(min(avance / cible * 100, 100), 0) if cible else 0,
            })

        de_la_semaine = [d for d in fermes
                         if debut_semaine <= datetime.fromtimestamp(d.time) < fin_semaine]
        recents = [{
            "quand": datetime.fromtimestamp(d.time).strftime("%a %d/%m  %H:%M"),
            "symbole": d.symbol, "gain": net(d),
        } for d in sorted(de_la_semaine, key=lambda x: x.time, reverse=True)[:10]]

        # Lundi a vendredi seulement : le forex ferme le week-end.
        flottant = acc.equity - acc.balance
        noms = ["LUN", "MAR", "MER", "JEU", "VEN"]
        jours = []
        for k in range(JOURS_BOURSE):
            j = debut_semaine + timedelta(days=k)
            v = somme(j, j + timedelta(days=1))
            cest_aujourdhui = j.date() == now.date()
            # Seuls les trades CLOTURES comptent. Un gain flottant n'est pas
            # un gain : la position peut encore toucher son stop. Le flottant
            # est affiche a part, jamais additionne a l'objectif.
            du_jour = [x for x in fermes
                       if j <= datetime.fromtimestamp(x.time) < j + timedelta(days=1)]
            jours.append({
                "nom": noms[k], "date": j.strftime("%d/%m"), "gain": v,
                "futur": j > now,
                "aujourdhui": cest_aujourdhui,
                "pct": max(min(v / OBJECTIF_JOUR * 100, 100), 0),
                "nb": len(du_jour),
                "gagnants": sum(1 for x in du_jour if net(x) > 0),
                "perdants": sum(1 for x in du_jour if net(x) < 0),
                "trades": [{
                    "h": datetime.fromtimestamp(x.time).strftime("%H:%M"),
                    "sym": x.symbol, "gain": net(x),
                } for x in sorted(du_jour, key=lambda x: x.time, reverse=True)],
            })

        encaisse = somme(debut_semaine, fin_semaine)
        semaine = encaisse                       # encaisse uniquement
        jour = somme(debut_jour, debut_jour + timedelta(days=1))

        hb = {}
        try:
            hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            pass
        vivant = bool(hb.get("updated_ts")) and (time.time() - hb["updated_ts"]) < 180

        return {
            "compte": acc.login, "serveur": acc.server, "devise": acc.currency,
            "ledger": ledger.bilan(),
            "propfirm": etat_propfirm(acc.equity, acc.balance),
            "balance": acc.balance, "equity": acc.equity, "flottant": flottant,
            "jour": jour,
            "semaine": semaine, "encaisse": encaisse,
            "obj_jour": OBJECTIF_JOUR, "obj_semaine": OBJECTIF_HEBDO,
            "pct_jour": max(min(jour / OBJECTIF_JOUR * 100, 100), 0),
            "pct_semaine": max(min(semaine / OBJECTIF_HEBDO * 100, 100), 0),
            "reste_jour": max(OBJECTIF_JOUR - jour, 0),
            "reste_semaine": max(OBJECTIF_HEBDO - semaine, 0),
            "seuil_mini": OBJECTIF_HEBDO * SEUIL_MINI,
            "offset": offset_semaine, "est_courante": est_courante,
            "libelle": f"{debut_semaine:%d/%m} — {debut_semaine + timedelta(days=4):%d/%m/%Y}",
            "nb_semaine": len(de_la_semaine),
            "positions": positions, "recents": recents, "jours": jours,
            "mode": control.mode(), "vivant": vivant,
            "uptime": hb.get("uptime_h", 0), "restarts": hb.get("restarts", 0),
            "maj": now.strftime("%H:%M:%S"),
        }
    finally:
        mt5.shutdown()


def etat(offset=0):
    with _lock:
        if (_cache["data"] is None or time.time() - _cache["ts"] > 3
                or _cache["offset"] != offset):
            _cache.update(data=collecte(offset), ts=time.time(), offset=offset)
        return _cache["data"]


def logs(n=40):
    try:
        return LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return ["En attente du premier cycle du bot..."]


# ==================== RENDU ====================

def favicon(vert=True):
    """Favicon SVG en data URI. Vert si la semaine est positive, rouge sinon."""
    c = "%2300e58a" if vert else "%23ff3b52"
    svg = (
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
        "%3Crect width='32' height='32' rx='8' fill='%23101315'/%3E"
        "%3Crect x='8' y='9' width='3' height='14' rx='1.5' fill='%234a545e'/%3E"
        "%3Crect x='14.5' y='5' width='3' height='22' rx='1.5' fill='" + c + "'/%3E"
        "%3Crect x='21' y='12' width='3' height='11' rx='1.5' fill='%234a545e'/%3E"
        "%3C/svg%3E")
    return "data:image/svg+xml," + svg


def cls(v):
    return "up" if v > 0 else ("down" if v < 0 else "flat")


def arc(pct, taille=132, epaisseur=7):
    """Jauge en arc de 270 degres, dessinee en SVG."""
    r = (taille - epaisseur) / 2
    c = taille / 2
    total = 2 * 3.14159265 * r * 0.75
    rempli = total * min(max(pct, 0), 100) / 100
    return (f'<svg class="arc" viewBox="0 0 {taille} {taille}" '
            f'width="{taille}" height="{taille}">'
            f'<circle cx="{c}" cy="{c}" r="{r:.2f}" class="arc-bg" '
            f'stroke-dasharray="{total:.2f} 999" stroke-width="{epaisseur}"/>'
            f'<circle cx="{c}" cy="{c}" r="{r:.2f}" class="arc-fg" '
            f'stroke-dasharray="{rempli:.2f} 999" stroke-width="{epaisseur}"/></svg>')


# JavaScript de rafraichissement. Sorti de la f-string du rendu : les accolades
# du JS entreraient en conflit avec le formatage Python.
JS = r"""
<script>
const OFFSET = new URLSearchParams(location.search).get('s') || '0';
const CHAMPS = ['j-amt','s-amt','j-pct','s-pct','j-left','s-left','balance',
  'flottant','equity','nb','nb-pos','maj','uptime',
  'led-out','led-in','led-net','led-tr'];
const BLOCS = ['positions','jours','jdetail','trades','journal','j-arc','s-arc','pf'];
const CLASSES = {'j-amt':'j_cls','s-amt':'s_cls','j-pct':'j_pct_cls','s-pct':'s_pct_cls',
  'flottant':'flot_cls','switch':'sw_cls','pulse':'pulse_cls',
  'led-net':'led_net_cls'};

function poser(id, valeur){
  const el = document.getElementById(id);
  if (!el || el.textContent === valeur) return;
  el.textContent = valeur;
}

async function tick(){
  try{
    const r = await fetch('/api?s=' + OFFSET, {cache:'no-store'});
    if(!r.ok) return;
    const f = await r.json();
    if(f.erreur) return;

    // On ne remplace le journal que s'il a change, sinon on perd la position
    // de defilement de l'utilisateur a chaque tick.
    const pre = document.getElementById('journal');
    const enBas = pre && (pre.scrollHeight - pre.scrollTop - pre.clientHeight) < 40;

    for(const id of CHAMPS){
      const k = id.replace(/-/g,'_');
      if(f[k] !== undefined) poser(id, f[k]);
    }
    for(const id of BLOCS){
      const k = id.replace(/-/g,'_');
      const el = document.getElementById(id);
      if(el && f[k] !== undefined && el.innerHTML !== f[k]) el.innerHTML = f[k];
    }
    for(const [id, k] of Object.entries(CLASSES)){
      const el = document.getElementById(id);
      if(el && f[k] && el.className !== f[k]) el.className = f[k];
    }
    if(pre && enBas) pre.scrollTop = pre.scrollHeight;
    appliquerJour();
  }catch(e){ /* le serveur redemarre, on retentera au prochain tick */ }
}

// Selection d'un jour de la semaine. Les blocs sont remplaces a chaque tick,
// la selection est donc reappliquee apres chaque mise a jour.
let jourSel = null;

function appliquerJour(){
  const barres = document.querySelectorAll('#jours .day');
  if(!barres.length) return;
  if(jourSel === null){
    const auj = document.querySelector('#jours .day.today');
    jourSel = auj ? auj.dataset.day : barres[barres.length-1].dataset.day;
  }
  barres.forEach(b => b.classList.toggle('sel', b.dataset.day === jourSel));
  document.querySelectorAll('#jdetail .dd').forEach(d => {
    d.hidden = d.dataset.dd !== jourSel;
  });
}

document.addEventListener('click', function(e){
  const b = e.target.closest && e.target.closest('#jours .day');
  if(!b) return;
  jourSel = b.dataset.day;
  appliquerJour();
});

// Les animations d'entree ne jouent qu'une fois. Passe ce delai, tout
// remplacement de contenu apparait instantanement : plus de clignotement ni
// de rejeu des animations a chaque rafraichissement.
window.addEventListener('load', function(){
  appliquerJour();
  setTimeout(function(){ document.body.classList.add('ready'); }, 1400);
  setInterval(tick, 2000);
});
</script>
"""


def fragments(d):
    """
    Morceaux de HTML recalcules a chaque rafraichissement.
    La page initiale et l'API renvoient exactement les memes, ce qui garantit
    qu'un rechargement complet et une mise a jour de fond affichent la meme chose.
    """
    if d["positions"]:
        pos = "".join(
            '<article class="pos rise" style="--i:%d">'
            '<header><div class="pos-id">'
            '<span class="tag %s">%s</span><b>%s</b><span class="vol">%s lot</span></div>'
            '<div class="pos-pl %s">%+.2f</div></header>'
            '<div class="pos-track"><div class="pos-fill %s" style="--w:%.1f%%;width:%.1f%%"></div></div>'
            '<footer><span>%.*f <i>&rarr;</i> %.*f</span>'
            '<span class="%s">%+.0f pips</span>'
            '<span class="muted">cible %.0f · stop %.0f</span></footer></article>'
            % (i, "buy" if p["sens"] == "ACHAT" else "sell", p["sens"],
               html.escape(p["symbole"]), p["volume"],
               cls(p["gain"]), p["gain"],
               cls(p["pips"]), p["progression"], p["progression"],
               p["digits"], p["entree"], p["digits"], p["actuel"],
               cls(p["pips"]), p["pips"], p["cible"], p["risque"])
            for i, p in enumerate(d["positions"]))
    else:
        pos = ('<div class="empty rise"><div class="radar"><i></i><i></i><i></i></div>'
               '<p>Aucune position ouverte</p>'
               '<small>Le bot analyse le marché et attend un signal</small></div>')

    maxi = max([abs(j["gain"]) for j in d["jours"]] + [d["obj_jour"]])
    jours = "".join(
        '<button type="button" class="day %s %s rise" style="--i:%d" data-day="%d">'
        '<div class="day-val %s">%s</div>'
        '<div class="day-col"><div class="day-goal" style="--h:%.0f%%"></div>'
        '<div class="day-bar %s" style="--h:%.0f%%;height:%.0f%%"></div></div>'
        '<div class="day-lab"><b>%s</b><span>%s</span></div></button>'
        % ("today" if j["aujourdhui"] else "", "ahead" if j["futur"] else "", i, i,
           cls(j["gain"]), "" if j["futur"] else "%+.0f" % j["gain"],
           d["obj_jour"] / maxi * 100, cls(j["gain"]),
           abs(j["gain"]) / maxi * 100, abs(j["gain"]) / maxi * 100,
           j["nom"], j["date"])
        for i, j in enumerate(d["jours"]))

    # Panneau de detail : un bloc par jour, masque, revele au clic sur la barre.
    # Tout est rendu cote serveur pour que la selection survive au rafraichissement.
    longs = ("LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI")
    detail = "".join(
        '<div class="dd" data-dd="%d" hidden>'
        '<div class="dd-head"><b>%s %s</b><span class="dd-amt num %s">%s</span></div>'
        '<div class="dd-sub">%s</div>%s</div>'
        % (i, longs[i], j["date"], cls(j["gain"]),
           "&mdash;" if j["futur"] else "%+.2f %s" % (j["gain"], d["devise"]),
           ("jour à venir" if j["futur"] else
            "aucun trade clôturé" if not j["nb"] else
            "%d clôturé%s · %d gagnant%s · %d perdant%s · objectif %.0f %s" % (
                j["nb"], "s" if j["nb"] > 1 else "",
                j["gagnants"], "s" if j["gagnants"] > 1 else "",
                j["perdants"], "s" if j["perdants"] > 1 else "",
                d["obj_jour"], d["devise"])),
           ("" if not j["trades"] else
            '<ul class="dd-list">' + "".join(
                '<li><span class="muted">%s</span><b>%s</b>'
                '<span class="%s">%+.2f</span></li>'
                % (t["h"], html.escape(t["sym"]), cls(t["gain"]), t["gain"])
                for t in j["trades"]) + "</ul>"))
        for i, j in enumerate(d["jours"]))

    trades = "".join(
        '<li class="rise" style="--i:%d"><span class="muted">%s</span>'
        '<b>%s</b><span class="%s">%+.2f</span></li>'
        % (i, html.escape(r["quand"]), html.escape(r["symbole"]),
           cls(r["gain"]), r["gain"])
        for i, r in enumerate(d["recents"])) or         '<li class="none">Aucun trade clôturé cette semaine</li>'

    mode = d["mode"]
    txt = {"ON": "Le bot prend de nouveaux trades.",
           "PAUSE": "Aucune nouvelle entrée. Les positions ouvertes restent gérées.",
           "OFF": "Bot arrêté. Les stops et objectifs restent actifs chez le broker."}[mode]
    en_ligne = d["vivant"] and mode != "OFF"

    # Marge prop firm. Sur un compte finance, ce qui tue n'est pas de rater un
    # objectif hebdomadaire, c'est de toucher une limite de perte. Les deux
    # limites sont montrees separement parce qu'elles ne se declenchent pas au
    # meme rythme : la journaliere se recharge chaque nuit, la totale jamais.
    pf = d.get("propfirm")
    if not pf:
        bloc_pf = ('<div class="pf-off">Garde-fou prop firm désactivé. '
                   'Aucune limite n\'est surveillée.</div>')
    else:
        etiquette = {"OK": "DANS LES CLOUS",
                     "STOP_ENTREES": "PLUS AUCUNE ENTRÉE",
                     "FERMER_TOUT": "FERMETURE DE TOUT"}[pf["statut"]]
        teinte = {"OK": "ok", "STOP_ENTREES": "warn",
                  "FERMER_TOUT": "bad"}[pf["statut"]]
        note = ("" if pf["reference_officielle"] else
                '<div class="pf-note">Le bot n\'a pas encore figé la référence '
                'du jour. La limite journalière ci-dessous est provisoire, '
                'la limite totale reste exacte.</div>')
        bloc_pf = (
            '<div class="pf-head"><span class="pf-badge %s">%s</span>'
            '<em>la limite %s est celle qui mord</em></div>'
            '<div class="pf-grid">'
            '<div class="pf-cell"><span>MARGE DU JOUR</span><b class="num">%s</b>'
            '<div class="pf-track"><i class="%s" style="width:%.0f%%"></i></div>'
            '<em>%.0f%% consommé · plancher %s</em></div>'
            '<div class="pf-cell"><span>MARGE TOTALE</span><b class="num">%s</b>'
            '<div class="pf-track"><i class="%s" style="width:%.0f%%"></i></div>'
            '<em>%.0f%% consommé · plancher %s</em></div>'
            '<div class="pf-cell"><span>ARRÊT DES ENTRÉES À</span>'
            '<b class="num dim">%.0f%%</b><em>de la limite consommée</em></div>'
            '<div class="pf-cell"><span>FERMETURE FORCÉE À</span>'
            '<b class="num dim">%.0f%%</b><em>de la limite consommée</em></div>'
            '</div>%s'
            % (teinte, etiquette, pf["qui_mord"],
               format(pf["marge_jour"], ",.0f"), teinte,
               min(pf["conso_jour"], 100), pf["conso_jour"],
               format(pf["plancher_jour"], ",.0f"),
               format(pf["marge_total"], ",.0f"), teinte,
               min(pf["conso_total"], 100), pf["conso_total"],
               format(pf["plancher_total"], ",.0f"),
               pf["seuil_souple"], pf["seuil_dur"], note))

    return {
        "pf": bloc_pf,
        "positions": pos, "jours": jours, "jdetail": detail, "trades": trades,
        "journal": chr(10).join(html.escape(l) for l in logs()),
        "j_amt": "%+.2f" % d["jour"], "j_cls": "amt num " + cls(d["jour"]),
        "s_amt": "%+.2f" % d["semaine"], "s_cls": "amt num " + cls(d["semaine"]),
        "j_pct": "%.0f%%" % d["pct_jour"], "s_pct": "%.0f%%" % d["pct_semaine"],
        "j_pct_cls": "arc-pct num " + cls(d["jour"]),
        "s_pct_cls": "arc-pct num " + cls(d["semaine"]),
        "j_arc": arc(d["pct_jour"]), "s_arc": arc(d["pct_semaine"]),
        # Le flottant est montre a part, jamais additionne : tant qu'un trade
        # n'est pas cloture, il ne compte pas dans l'objectif.
        # La devise vient du compte. Le symbole $ etait code en dur et mentait
        # des la bascule sur un compte FTMO libelle en EUR.
        "j_left": "reste %.2f %s" % (d["reste_jour"], d["devise"]) + (
            " · %+.2f %s ouverts non comptés" % (d["flottant"], d["devise"])
            if d["est_courante"] and abs(d["flottant"]) > 0.004 else ""),
        "s_left": "%d trade%s clôturé%s · seuil mini %.0f %s" % (
            d["nb_semaine"], "s" if d["nb_semaine"] > 1 else "",
            "s" if d["nb_semaine"] > 1 else "", d["seuil_mini"], d["devise"]),
        "balance": format(d["balance"], ",.2f"), "equity": format(d["equity"], ",.2f"),
        "flottant": "%+.2f" % d["flottant"], "flot_cls": "num " + cls(d["flottant"]),
        "led_net": "%+.2f" % d["ledger"]["net"],
        "led_net_cls": "num " + cls(d["ledger"]["net"]),
        "led_out": format(d["ledger"]["depenses"], ",.2f"),
        "led_in": format(d["ledger"]["recettes"], ",.2f"),
        "led_tr": ("%d j / %d" % (d["ledger"]["jours"], ledger.TRACK_RECORD_JOURS)
                   if d["ledger"]["debut"] else "pas demarre"),
        "led_bar": "%.0f%%" % d["ledger"]["track_record_pct"],
        "nb": str(d["nb_semaine"]), "nb_pos": "%d ouverte(s)" % len(d["positions"]),
        "maj": d["maj"], "uptime": "%.1f h" % d["uptime"],
        # La position du curseur du switch est portee par une classe : le CSS
        # translate le pouce, aucune valeur inline a recalculer cote JS.
        "sw_cls": "sw sw-" + mode.lower(),
        # Pastille de vie : le badge EN LIGNE a saute, mais l'info reste. Rouge
        # si le watchdog ne bat plus ou si le bot est OFF, sinon vert pulsant.
        "pulse_cls": "pulse " + ("on" if en_ligne else "off"),
        "pulse_ttl": ("Bot actif — %s" % txt) if en_ligne else "Bot arrêté ou watchdog muet",
    }


def page(offset=0):
    d = etat(offset)
    if "erreur" in d:
        return ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
                '<meta http-equiv="refresh" content="8"><title>A.I.T.B</title>'
                '<style>body{background:#08090a;color:#e8eaed;'
                'font:15px/1.6 ui-monospace,monospace;display:grid;place-items:center;'
                'height:100vh;margin:0;text-align:center}h1{font-size:15px;'
                'letter-spacing:.3em;color:#ff3b52;margin-bottom:14px}p{color:#6b7280}'
                '</style></head><body><div><h1>SIGNAL PERDU</h1><p>'
                + html.escape(d["erreur"]) +
                '</p><p style="margin-top:18px;font-size:12px">Reconnexion automatique...'
                '</p></div></body></html>')

    f = fragments(d)
    suiv = ('<a href="/?s=%d" class="nav-btn">Suivante &rarr;</a>' % (d["offset"] + 1)
            if d["offset"] < 0 else '<span class="nav-btn off">Suivante &rarr;</span>')
    css = """
:root{
  --bg:#08090a; --bg2:#0c0e10; --surf:#101315; --surf2:#14181b;
  --line:#1c2226; --line2:#262e34;
  --txt:#e9edf0; --dim:#7c8894; --faint:#4a545e;
  --up:#00e58a; --up-glow:rgba(0,229,138,.16);
  --down:#ff3b52; --down-glow:rgba(255,59,82,.16);
  --gold:#f0b429;
  --out:cubic-bezier(.23,1,.32,1);
  --inout:cubic-bezier(.77,0,.175,1);
  --mono:'JetBrains Mono',ui-monospace,monospace;
  --disp:'Bricolage Grotesque',system-ui,sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box}
html{background:var(--bg)}
body{font-family:var(--disp);color:var(--txt);min-height:100vh;padding:26px 22px 40px;
  background:radial-gradient(900px 480px at 12% -8%,rgba(0,229,138,.055),transparent 62%),
    radial-gradient(760px 420px at 92% 4%,rgba(255,59,82,.04),transparent 60%),var(--bg);
  -webkit-font-smoothing:antialiased}
body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:99;opacity:.5;
  background-image:repeating-linear-gradient(0deg,rgba(255,255,255,.014) 0 1px,transparent 1px 3px)}
.shell{max-width:1280px;margin:0 auto}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--dim)}
.muted{color:var(--dim)}

.rise{opacity:0;transform:translateY(10px);
  animation:rise .5s var(--out) forwards;animation-delay:calc(var(--i,0)*45ms + 60ms)}
@keyframes rise{to{opacity:1;transform:none}}

.top{display:flex;align-items:center;gap:18px;flex-wrap:wrap;
  padding-bottom:18px;border-bottom:1px solid var(--line);margin-bottom:26px}
.brand{display:flex;align-items:center;gap:10px}
.brand h1{font:800 21px/1 var(--disp);letter-spacing:-.03em}
.pulse{width:7px;height:7px;border-radius:50%;flex:none;
  transition:background .3s var(--out),box-shadow .3s var(--out)}
.pulse.on{background:var(--up);box-shadow:0 0 0 3px var(--up-glow);
  animation:pulse 2.2s var(--inout) infinite}
.pulse.off{background:var(--down);box-shadow:0 0 0 3px var(--down-glow)}

.ctrl{display:flex;align-items:center;gap:10px}
.ctrl form{display:contents}

/* Switch a trois positions. Le pouce est un element unique translate en CSS :
   une seule surface qui glisse, au lieu de trois boutons qui s'allument. */
.sw{position:relative;display:flex;padding:3px;border-radius:11px;
  background:var(--surf);border:1px solid var(--line);isolation:isolate}
.sw-thumb{position:absolute;z-index:0;top:3px;bottom:3px;left:3px;width:var(--w,62px);
  border-radius:8px;transition:transform .34s var(--out),background .28s var(--out),
  box-shadow .28s var(--out)}
.sw button{position:relative;z-index:1;width:var(--w,62px);padding:8px 0;
  font:700 10px/1 var(--mono);letter-spacing:.14em;background:none;border:0;
  color:var(--faint);cursor:pointer;-webkit-tap-highlight-color:transparent;
  transition:color .24s var(--out),transform .14s var(--out)}
.sw button:active{transform:scale(.94)}
@media (hover:hover) and (pointer:fine){.sw button:hover{color:var(--dim)}}

.sw-on .sw-thumb{transform:translateX(0);background:var(--up-glow);
  box-shadow:inset 0 0 0 1px var(--up)}
.sw-pause .sw-thumb{transform:translateX(var(--w,62px));background:rgba(240,180,41,.14);
  box-shadow:inset 0 0 0 1px var(--gold)}
.sw-off .sw-thumb{transform:translateX(calc(var(--w,62px) * 2));background:var(--down-glow);
  box-shadow:inset 0 0 0 1px var(--down)}
.sw-on form:nth-of-type(1) button{color:var(--up)}
.sw-pause form:nth-of-type(2) button{color:var(--gold)}
.sw-off form:nth-of-type(3) button{color:var(--down)}

.b-restart{display:flex;align-items:center;gap:7px;font:700 10px/1 var(--mono);
  letter-spacing:.14em;padding:10px 15px;border-radius:10px;cursor:pointer;
  background:var(--surf);border:1px solid var(--line);color:var(--dim);
  -webkit-tap-highlight-color:transparent;
  transition:color .2s var(--out),border-color .2s var(--out),transform .14s var(--out)}
.b-restart svg{transition:transform .45s var(--out)}
.b-restart:active{transform:scale(.96)}
.b-restart:active svg{transform:rotate(-180deg)}
@media (hover:hover) and (pointer:fine){
  .b-restart:hover{color:var(--txt);border-color:var(--dim)}
  .b-restart:hover svg{transform:rotate(-90deg)}}

.goals{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:820px){.goals{grid-template-columns:1fr}}
.goal{position:relative;overflow:hidden;background:linear-gradient(160deg,var(--surf2),var(--surf));
  border:1px solid var(--line);border-radius:18px;padding:26px 28px 26px 32px;
  display:flex;align-items:center;gap:26px}
.goal::after{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--k)}
.goal::before{content:'';position:absolute;inset:0;opacity:.55;pointer-events:none;
  background:radial-gradient(360px 170px at 88% 0%,var(--halo),transparent 70%)}
.goal.j{--halo:rgba(0,229,138,.13);--k:var(--up)}
.goal.s{--halo:rgba(240,180,41,.12);--k:var(--gold)}
.goal-body{flex:1;min-width:0;position:relative}
.goal h2{display:flex;align-items:center;gap:11px;font:800 26px/1 var(--disp);
  letter-spacing:-.035em;color:var(--txt);margin-bottom:5px}
.goal h2 .ic{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;
  background:var(--halo);color:var(--k);font:700 11px/1 var(--mono);letter-spacing:0;
  border:1px solid var(--k);flex-shrink:0}
.goal .scope{font:500 11px/1 var(--mono);letter-spacing:.2em;color:var(--k);
  opacity:.85;margin-bottom:18px}
.goal .amt{font:700 50px/1 var(--mono);letter-spacing:-.05em;display:block}
.goal .of{font:500 13px/1 var(--mono);color:var(--faint);margin-top:10px}
.goal .left{font:400 12px/1.5 var(--mono);color:var(--dim);margin-top:13px}
.arc-wrap{position:relative;flex-shrink:0;display:grid;place-items:center}
.arc{transform:rotate(135deg)}
.arc circle{fill:none;stroke-linecap:round}
.arc-bg{stroke:var(--line2)}
.arc-fg{stroke:var(--k);filter:drop-shadow(0 0 7px var(--k));animation:draw 1.1s var(--out) forwards}
@keyframes draw{from{stroke-dasharray:0 999}}
.arc-pct{position:absolute;font:700 17px/1 var(--mono);letter-spacing:-.03em}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--surf);border:1px solid var(--line);border-radius:13px;padding:16px 18px}
.kpi span{display:block;font:500 9px/1 var(--mono);letter-spacing:.2em;color:var(--faint);margin-bottom:9px}
.kpi b{font:700 24px/1 var(--mono);letter-spacing:-.035em}

.grid{display:grid;grid-template-columns:1.35fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:1020px){.grid{grid-template-columns:1fr}}
.panel{background:var(--surf);border:1px solid var(--line);border-radius:16px;
  padding:20px 22px;min-width:0}
.panel>h3{font:500 10px/1 var(--mono);letter-spacing:.24em;color:var(--faint);
  margin-bottom:17px;display:flex;justify-content:space-between;align-items:center}
.panel>h3 em{font-style:normal;color:var(--dim);letter-spacing:.06em}

.pos{border:1px solid var(--line);border-radius:12px;padding:15px 17px;margin-bottom:11px;
  background:var(--bg2);transition:border-color .22s ease,transform .22s var(--out)}
.pos:last-child{margin-bottom:0}
@media (hover:hover) and (pointer:fine){.pos:hover{border-color:var(--line2);transform:translateY(-1px)}}
.pos header{display:flex;justify-content:space-between;align-items:center;gap:12px}
.pos-id{display:flex;align-items:center;gap:10px;font:700 15px/1 var(--disp)}
.tag{font:700 9px/1 var(--mono);letter-spacing:.14em;padding:4px 8px;border-radius:5px}
.tag.buy{color:var(--up);background:var(--up-glow)}
.tag.sell{color:var(--down);background:var(--down-glow)}
.vol{font:400 11px/1 var(--mono);color:var(--faint)}
.pos-pl{font:700 21px/1 var(--mono);letter-spacing:-.035em}
.pos-track{height:3px;background:var(--line);border-radius:2px;margin:14px 0 11px;overflow:hidden}
.pos-fill{height:100%;border-radius:2px;width:0;background:currentColor;
  animation:fill .9s var(--out) forwards}
@keyframes fill{to{width:var(--w)}}
.pos footer{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
  font:400 11px/1 var(--mono);color:var(--dim)}
.pos footer i{color:var(--faint);font-style:normal;margin:0 3px}

.empty{text-align:center;padding:46px 20px;color:var(--dim)}
.empty p{font:500 14px/1 var(--disp);margin-bottom:6px}
.empty small{font:400 11px/1 var(--mono);color:var(--faint)}
.radar{position:relative;width:52px;height:52px;margin:0 auto 20px}
.radar i{position:absolute;inset:0;border:1px solid var(--line2);border-radius:50%;
  animation:ping 2.8s var(--out) infinite}
.radar i:nth-child(2){animation-delay:.9s} .radar i:nth-child(3){animation-delay:1.8s}
@keyframes ping{from{transform:scale(.42);opacity:.9}to{transform:scale(1);opacity:0}}

.days{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;height:210px}
.day{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:9px;background:none;border:0;padding:4px 2px 0;margin:0;font:inherit;color:inherit;cursor:pointer;border-radius:9px;-webkit-tap-highlight-color:transparent;transition:background .18s var(--out),transform .14s var(--out)}
.day:active{transform:scale(.96)}
@media (hover:hover){.day:hover{background:rgba(255,255,255,.035)}}
.day.sel{background:rgba(255,255,255,.06)}
.day.sel .day-lab b{color:var(--txt)}
.day.sel .day-col{box-shadow:inset 0 0 0 1px var(--line2)}
.ledger{margin:20px 0;padding:16px 18px;border-radius:14px;
  background:var(--surf);border:1px solid var(--line)}
.led-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.led-head h3{font:700 11px/1 var(--mono);letter-spacing:.16em;color:var(--dim)}
.led-head em{font:400 11px/1 var(--mono);font-style:normal;color:var(--faint)}
.led-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}
.led-cell{display:flex;flex-direction:column;gap:6px;padding:11px 13px;border-radius:10px;
  background:var(--card2)}
.led-cell span{font:600 9px/1 var(--mono);letter-spacing:.14em;color:var(--faint)}
.led-cell b{font:700 17px/1 var(--mono);letter-spacing:-.02em}
.led-cell.big b{font-size:22px}
.led-track{height:3px;border-radius:2px;background:var(--line2);overflow:hidden;margin-top:3px}
.led-track i{display:block;height:100%;background:var(--dim);
  transition:width .5s var(--out)}
.amt-row{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}
.goal-of{font:600 15px/1 var(--mono);color:var(--faint);letter-spacing:-.02em}
.dd-wrap{margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
.dd{animation:ddin .24s var(--out) both}
@keyframes ddin{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.dd-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
.dd-head b{font:700 11px/1 var(--mono);letter-spacing:.14em;color:var(--dim)}
.dd-amt{font-size:17px;font-weight:700;letter-spacing:-.02em}
.dd-sub{margin-top:5px;font:400 11px/1.5 var(--mono);color:var(--faint)}
.dd-list{list-style:none;margin:11px 0 0;padding:0;display:grid;gap:1px}
.dd-list li{display:grid;grid-template-columns:52px 1fr auto;align-items:center;gap:10px;
  padding:7px 9px;border-radius:7px;background:var(--card2);font:400 12px/1 var(--mono)}
.dd-list li b{font-weight:600;letter-spacing:.02em}
.dd-list li span:last-child{font-weight:700;letter-spacing:-.02em}
.day-val{font:700 12px/1 var(--mono);height:13px;letter-spacing:-.02em}
.day-col{position:relative;width:100%;flex:1;display:flex;align-items:flex-end;
  background:var(--bg2);border-radius:7px;overflow:hidden}
.day-goal{position:absolute;left:0;right:0;bottom:var(--h);height:1px;
  background:repeating-linear-gradient(90deg,var(--line2) 0 4px,transparent 4px 8px)}
.day-bar{width:100%;height:0;border-radius:6px 6px 0 0;background:currentColor;opacity:.85;
  animation:grow 1s var(--out) forwards;animation-delay:calc(var(--i)*60ms + 200ms)}
@keyframes grow{to{height:var(--h)}}
.day-lab{text-align:center;line-height:1.4}
.day-lab b{display:block;font:700 10px/1 var(--mono);letter-spacing:.14em;color:var(--dim)}
.day-lab span{font:400 9px/1 var(--mono);color:var(--faint)}
.day.today .day-lab b{color:var(--txt)}
.day.today .day-col{box-shadow:inset 0 0 0 1px var(--line2)}
.day.ahead{opacity:.34}

.trades{list-style:none}
.trades li{display:flex;align-items:center;gap:12px;padding:11px 0;
  border-bottom:1px solid var(--line);font:400 12px/1 var(--mono)}
.trades li:last-child{border-bottom:none}
.trades li b{flex:1;font:700 12px/1 var(--mono);letter-spacing:.02em}
.trades li span:last-child{font-weight:700}
.trades .none{color:var(--faint);justify-content:center;padding:34px 0}

.weeknav{display:flex;align-items:center;justify-content:space-between;gap:14px;
  background:var(--surf);border:1px solid var(--line);border-radius:12px;
  padding:12px 18px;margin-bottom:16px}
.weeknav b{font:700 13px/1 var(--disp);letter-spacing:-.01em}
.tag-now{font:500 9px/1 var(--mono);letter-spacing:.16em;color:var(--up);
  background:var(--up-glow);padding:4px 8px;border-radius:5px;margin-left:9px}
.nav-btn{font:500 11px/1 var(--mono);letter-spacing:.1em;color:var(--dim);
  text-decoration:none;padding:8px 13px;border-radius:7px;border:1px solid var(--line2);
  transition:color .18s ease,border-color .18s ease,transform .14s var(--out)}
.nav-btn:active{transform:scale(.96)}
@media (hover:hover) and (pointer:fine){.nav-btn:hover{color:var(--txt);border-color:var(--dim)}}
.nav-btn.off{opacity:.28;pointer-events:none}

.propfirm{margin-bottom:16px;padding:18px 20px;border-radius:16px;
  background:var(--surf);border:1px solid var(--line)}
.pf-title{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:15px}
.pf-title h3{font:500 10px/1 var(--mono);letter-spacing:.24em;color:var(--faint)}
.pf-title em{font:400 11px/1 var(--mono);font-style:normal;color:var(--faint)}
.pf-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.pf-badge{font:700 10px/1 var(--mono);letter-spacing:.14em;padding:7px 11px;border-radius:7px}
.pf-badge.ok{color:var(--up);background:var(--up-glow)}
.pf-badge.warn{color:var(--gold);background:rgba(240,180,41,.14)}
.pf-badge.bad{color:var(--down);background:var(--down-glow)}
.pf-head em{font:400 11px/1 var(--mono);font-style:normal;color:var(--dim)}
.pf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.pf-cell{padding:13px 15px;border-radius:11px;background:var(--bg2);border:1px solid var(--line)}
.pf-cell span{display:block;font:600 9px/1 var(--mono);letter-spacing:.14em;
  color:var(--faint);margin-bottom:9px}
.pf-cell b{display:block;font:700 22px/1 var(--mono);letter-spacing:-.035em}
.pf-cell em{display:block;font:400 10px/1.4 var(--mono);font-style:normal;
  color:var(--faint);margin-top:8px}
.pf-track{height:4px;border-radius:2px;background:var(--line2);overflow:hidden;margin-top:10px}
.pf-track i{display:block;height:100%;background:var(--up);transition:width .5s var(--out)}
.pf-track i.warn{background:var(--gold)} .pf-track i.bad{background:var(--down)}
.pf-note{margin-top:12px;padding:10px 13px;border-radius:9px;
  background:rgba(240,180,41,.07);border:1px solid rgba(240,180,41,.28);
  font:400 11px/1.6 var(--mono);color:var(--gold)}
.pf-off{font:400 12px/1.6 var(--mono);color:var(--faint);text-align:center;padding:22px 0}

.log{background:#050607;border:1px solid var(--line);border-radius:16px;padding:18px 20px}
.log h3{font:500 10px/1 var(--mono);letter-spacing:.24em;color:var(--faint);margin-bottom:14px}
.log pre{font:400 11.5px/1.75 var(--mono);color:#93a1ad;max-height:250px;overflow:auto;
  white-space:pre-wrap;word-break:break-word}
.log pre::-webkit-scrollbar{width:7px}
.log pre::-webkit-scrollbar-thumb{background:var(--line2);border-radius:4px}

body.ready .rise,body.ready .day-bar,body.ready .pos-fill,body.ready .arc-fg{
  animation:none!important;opacity:1;transform:none}
body.ready .day-bar{height:var(--h)} body.ready .pos-fill{width:var(--w)}
body.ready .arc-fg{transition:stroke-dasharray .6s var(--out)}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-delay:0ms!important;
    transition-duration:.01ms!important}
  .rise{opacity:1;transform:none}
  .day-bar{height:var(--h)} .pos-fill{width:var(--w)}
}
"""

    return f'''<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A.I.T.B — {d['semaine']:+.0f} {d['devise']}</title>
<link rel="icon" href="{favicon(d['semaine'] >= 0)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css}</style></head><body><div class="shell">

<header class="top rise" style="--i:0">
  <div class="brand"><span class="{f["pulse_cls"]}" id="pulse"></span><h1>A.I.T.B</h1></div>
  <div class="ctrl">
    <div class="{f["sw_cls"]}" id="switch">
      <span class="sw-thumb"></span>
      <form method="post" action="/mode"><input type="hidden" name="m" value="ON">
        <button type="submit">ON</button></form>
      <form method="post" action="/mode"><input type="hidden" name="m" value="PAUSE">
        <button type="submit">PAUSE</button></form>
      <form method="post" action="/mode"><input type="hidden" name="m" value="OFF">
        <button type="submit">OFF</button></form>
    </div>
    <form method="post" action="/restart">
      <button class="b-restart" title="Relance le bot sans fermer les positions">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
             stroke-width="2.4" stroke-linecap="round"><path d="M20 11a8 8 0 1 0-.6 4"/>
        <path d="M20 4v7h-7"/></svg>RELANCER</button></form>
  </div>
  <div class="meta">
    <span>COMPTE <b>{d['compte']}</b></span>
    <span>UPTIME <b id="uptime">{f["uptime"]}</b></span>
    <span>MAJ <b id="maj">{f["maj"]}</b></span>
  </div>
</header>

<section class="goals">
  <div class="goal j rise" style="--i:1">
    <div class="goal-body">
      <h2><span class="ic">24H</span>Objectif du jour</h2>
      <div class="scope">AUJOURD'HUI</div>
      <div class="amt-row"><span class="{f["j_cls"]}" id="j-amt">{f["j_amt"]}</span><span class="goal-of">/{d['obj_jour']:.0f} {d['devise']}</span></div>
      <div class="left" id="j-left">{f["j_left"]}</div>
    </div>
    <div class="arc-wrap" id="j-arc">{f["j_arc"]}<span class="{f["j_pct_cls"]}" id="j-pct">{f["j_pct"]}</span></div>
  </div>
  <div class="goal s rise" style="--i:2">
    <div class="goal-body">
      <h2><span class="ic">7J</span>Objectif de la semaine</h2>
      <div class="scope">LUNDI &rarr; VENDREDI</div>
      <div class="amt-row"><span class="{f["s_cls"]}" id="s-amt">{f["s_amt"]}</span><span class="goal-of">/{d['obj_semaine']:.0f} {d['devise']}</span></div>
      <div class="left" id="s-left">{f["s_left"]}</div>
    </div>
    <div class="arc-wrap" id="s-arc">{f["s_arc"]}<span class="{f["s_pct_cls"]}" id="s-pct">{f["s_pct"]}</span></div>
  </div>
</section>

<section class="kpis">
  <div class="kpi rise" style="--i:3"><span>SOLDE</span><b class="num" id="balance">{f["balance"]}</b></div>
  <div class="kpi rise" style="--i:4"><span>POSITIONS OUVERTES</span>
    <b class="{f["flot_cls"]}" id="flottant">{f["flottant"]}</b></div>
  <div class="kpi rise" style="--i:5"><span>TOTAL</span><b class="num" id="equity">{f["equity"]}</b></div>
  <div class="kpi rise" style="--i:6"><span>TRADES FERMÉS</span><b class="num" id="nb">{f["nb"]}</b></div>
</section>

<section class="ledger rise" style="--i:6">
  <div class="led-head">
    <h3>COÛT RÉEL DU PROJET</h3>
    <em>tout ce qui sort, tout ce qui rentre — pas seulement les payouts</em>
  </div>
  <div class="led-grid">
    <div class="led-cell"><span>SORTIES</span><b class="num down" id="led-out">{f["led_out"]}</b></div>
    <div class="led-cell"><span>ENTRÉES</span><b class="num up" id="led-in">{f["led_in"]}</b></div>
    <div class="led-cell big"><span>NET RÉEL</span>
      <b class="{f["led_net_cls"]}" id="led-net">{f["led_net"]}</b></div>
    <div class="led-cell"><span>TRACK RECORD</span><b class="num" id="led-tr">{f["led_tr"]}</b>
      <div class="led-track"><i style="width:{f["led_bar"]}"></i></div></div>
  </div>
</section>

<section class="propfirm rise" style="--i:7">
  <div class="pf-title"><h3>MARGE PROP FIRM</h3>
    <em>ce qui tue un compte n'est pas de rater une cible, c'est de toucher une limite</em></div>
  <div id="pf">{f["pf"]}</div>
</section>

<nav class="weeknav rise" style="--i:7">
  <a href="/?s={d['offset']-1}" class="nav-btn">&larr; Précédente</a>
  <b>{html.escape(d['libelle'])}{'<span class="tag-now">EN COURS</span>' if d['est_courante'] else ''}</b>
  {suiv}
</nav>

<section class="grid">
  <div class="panel"><h3>POSITIONS <em id="nb-pos">{f["nb_pos"]}</em></h3><div id="positions">{f["positions"]}</div></div>
  <div class="panel"><h3>PAR JOUR <em>clique un jour</em></h3>
    <div class="days" id="jours">{f["jours"]}</div>
    <div class="dd-wrap" id="jdetail">{f["jdetail"]}</div></div>
</section>

<section class="grid">
  <div class="panel"><h3>TRADES CLÔTURÉS <em>semaine affichée</em></h3>
    <ul class="trades" id="trades">{f["trades"]}</ul></div>
  <div class="log"><h3>JOURNAL TEMPS RÉEL</h3><pre id="journal">{f["journal"]}</pre></div>
</section>

</div>{JS}</body></html>'''


# ==================== SERVEUR ====================

class Handler(BaseHTTPRequestHandler):
    def _offset(self):
        try:
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            for part in q.split("&"):
                if part.startswith("s="):
                    return max(-104, min(0, int(part[2:])))
        except Exception:
            pass
        return 0

    def _autorise(self) -> bool:
        """
        Filtre par adresse source. Repond 403 et rend False si refuse.

        Applique a do_GET ET do_POST. Le POST est le plus important des deux :
        /mode et /restart n'ont aucune authentification, et arretent un bot qui
        tient un compte d'evaluation.
        """
        ip = self.client_address[0]
        # Une adresse IPv4 arrivee par une socket IPv6 se presente sous la
        # forme ::ffff:100.x.y.z. Sans cette normalisation, la liste blanche
        # refuserait une machine pourtant autorisee, et le symptome serait un
        # 403 incomprehensible depuis le bon appareil.
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        if ip in IPS_AUTORISEES:
            return True
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_POST(self):
        if not self._autorise():
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n).decode("utf-8", "replace")
            if self.path.startswith("/restart"):
                # Le watchdog consomme la demande sous 30 s et relance le bot.
                # Aucune position n'est fermee : stops et objectifs sont chez
                # le broker, le bot relit l'etat du compte au demarrage.
                control.demander_redemarrage(par="dashboard")
            for part in body.split("&"):
                if part.startswith("m="):
                    m = part[2:].upper()
                    if m in control.ETATS:
                        control.ecrire(m, par="dashboard")
            with _lock:
                _cache["ts"] = 0.0
        except Exception:
            pass
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def do_GET(self):
        if not self._autorise():
            return
        if self.path.startswith("/api"):
            try:
                d = etat(self._offset())
                charge = json.dumps({"erreur": d["erreur"]} if "erreur" in d
                                    else fragments(d), ensure_ascii=False)
            except Exception as e:
                charge = json.dumps({"erreur": str(e)})
            corps = charge.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)
            return
        try:
            corps = page(self._offset()).encode("utf-8")
        except Exception as e:
            corps = (f"<pre style='color:#f66;background:#08090a;padding:30px'>"
                     f"{html.escape(str(e))}</pre>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *a):
        pass


def main():
    print()
    print("  " + "=" * 46)
    print("   A.I.T.B")
    print("  " + "=" * 46)
    print(f"   http://{BIND}:{PORT}")
    # Idee de la session CRYPTO Bot, meilleure que ce qu'il y avait ici.
    # Une reponse 200 ne distingue PAS "filtre present et appelant autorise"
    # de "filtre absent" : depuis une adresse listee, les deux se ressemblent.
    # Et le test negatif a distance est impossible, les adresses 100.64.0.0/10
    # n'etant pas routables depuis internet, une machine hors tailnet n'atteint
    # meme pas le port. Afficher le compte au demarrage rend donc une liste
    # blanche absente ou videe par erreur visible immediatement, dans le
    # journal, sans avoir a la deduire d'une reponse.
    print(f"   {len(IPS_AUTORISEES)} adresse(s) autorisee(s), tout le reste recoit 403")
    if BIND != "127.0.0.1":
        print("   ecoute NON locale : cette page expose /mode et /restart")
        print("   sans authentification. A reserver a un reseau prive.")
    # Pas de symbole ici : la devise n'est connue qu'une fois le compte lu.
    print(f"   Objectif jour     {OBJECTIF_JOUR:.0f}")
    print(f"   Objectif semaine  {OBJECTIF_HEBDO:.0f}  (lundi au vendredi)")
    print("   Devise affichee   celle du compte connecte")
    print("   Ctrl+C ferme l'affichage, PAS le bot")
    print()
    # UN SEUL dashboard a la fois, et sous Windows ca ne va PAS de soi.
    #
    # socketserver.TCPServer met allow_reuse_address a False, mais HTTPServer
    # le force a True. Sous Unix ce drapeau evite seulement le TIME_WAIT ; sous
    # Windows il autorise un SECOND processus a se lier a un port DEJA ecoute,
    # sans la moindre erreur. Mesure du 2026-09-15 sur cette machine : deux
    # HTTPServer lies au meme 127.0.0.1:port, aucune exception.
    #
    # Consequence concrete ici : si la tache planifiee lance le dashboard et que
    # quelqu'un double-clique aussi DASHBOARD_SERVEUR.bat, deux instances se
    # partagent le trafic au hasard. Les POST /mode et /restart, qui arretent et
    # relancent un bot sur un compte d'evaluation, partiraient tantot a l'une
    # tantot a l'autre, et la page afficherait un etat incoherent sans qu'aucun
    # message n'apparaisse nulle part.
    #
    # Repasser le drapeau a False suffit a l'empecher, et ne casse pas la
    # relance rapide : un listener ferme proprement ne passe pas en TIME_WAIT,
    # verifie dans le meme test.
    class ServeurUnique(HTTPServer):
        allow_reuse_address = False

    try:
        serveur = ServeurUnique((BIND, PORT), Handler)
    except OSError as e:
        # Message explicite plutot qu'une trace : la cause est presque toujours
        # un dashboard deja lance, pas une panne.
        print()
        print("  " + "=" * 46)
        print("   DEMARRAGE REFUSE : le port %d est deja pris." % PORT)
        print()
        print("   Un tableau de bord tourne deja, tres probablement lance par")
        print("   la tache planifiee AITB_Dashboard. Celui-ci s'arrete pour ne")
        print("   pas creer un doublon qui se partagerait le trafic.")
        print()
        print("   Pour voir la page          : http://%s:%d" % (BIND, PORT))
        print("   Pour verifier la tache     : schtasks /query /tn AITB_Dashboard")
        print("   Pour arreter l'existant    : schtasks /end /tn AITB_Dashboard")
        print()
        print("   detail systeme : %s" % e)
        print("  " + "=" * 46)
        print()
        raise SystemExit(1)

    serveur.serve_forever()


if __name__ == "__main__":
    main()
