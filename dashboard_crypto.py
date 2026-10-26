#!/usr/bin/env python3
"""
dashboard_crypto.py - Tableau de bord A.I.T.B CRYPTO, version 2.

Copie conforme de l'interface du tableau de bord forex (dashboard.py, port
8080). Memes couleurs, memes composants, meme disposition, meme JavaScript.
Seules les DONNEES changent, et les rares elements propres au metier.

Ce qui differe du forex, et pourquoi
------------------------------------
  7 barres par jour au lieu de 5   le financement tombe toutes les 8 heures,
                                    week-end compris
  RISQUE DE LIQUIDATION            remplace MARGE PROP FIRM, meme role : dire
                                    combien de place reste avant que ca casse
  FRAICHEUR remplace UPTIME        aucun process d'arbitrage ne tourne en
                                    continu, l'age de la donnee est l'info
                                    la plus importante de la page
  favicon a barre BLEUE            pour distinguer l'onglet du forex, qui est
                                    vert

Les deux bots (arb_bot sur perpetuels globaux, arb_eu sur X-Perps europeens)
sont AGREGES en un seul jeu de chiffres, exactement comme le forex n'affiche
qu'un compte. Le detail par bot reste lisible ligne par ligne dans POSITIONS.

CE QUE CE TABLEAU DE BORD REFUSE DE CACHER
-------------------------------------------
Le net en poche est NEGATIF. Ce n'est pas un echec de la strategie, c'est sa
mecanique de demarrage : les frais d'entree se paient une fois, le financement
se percoit toutes les 8 heures. Tant que le second n'a pas rembourse le
premier, le net est negatif. L'objectif du jour et de la semaine mesurent donc
le FINANCEMENT ENCAISSE, qui est le revenu recurrent, et le net est montre a
part dans les indicateurs.

Lancement :  python dashboard_crypto.py   puis  http://127.0.0.1:8081

Lecture seule cote marche : ce script n'ouvre et ne ferme aucune position. Les
seuls ecrits sont les boutons ON / PAUSE / OFF et RELANCER, qui posent un
drapeau dans data/arb_control.json, STRICTEMENT separe du fichier du forex.
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

from aitb import arb_bot        # noqa: E402
from aitb import arb_control    # noqa: E402
from aitb import arb_eu         # noqa: E402
from aitb import ledger         # noqa: E402
from aitb import okx            # noqa: E402

PORT = 8081

# ECOUTE ET LISTE BLANCHE
# -----------------------
# En local, le tableau de bord n'ecoute que 127.0.0.1 : l'isolement est gratuit,
# personne d'autre ne peut l'atteindre. Sur le serveur maison il doit ecouter
# l'adresse Tailscale pour etre consultable depuis le PC principal, et A CET
# INSTANT il devient joignable par toute machine du tailnet.
#
# La liste blanche n'est donc pas une precaution theorique, c'est ce qui
# REMPLACE l'isolement qu'on perd en changeant d'adresse d'ecoute. Elle doit
# etre posee AVANT le deplacement, pas apres.
#
#   set AITB_CRYPTO_BIND=100.x.y.z     sur le serveur, avant de lancer
BIND = os.environ.get("AITB_CRYPTO_BIND", "127.0.0.1")
IPS_AUTORISEES = {
    "127.0.0.1",        # la machine elle-meme
    "::1",              # la meme, en IPv6 NATIVE
    "100.x.y.z",    # weasel-server, le serveur maison
    "100.x.y.w",   # lesa3w, le PC principal
}
# ::1 et ::ffff:127.0.0.1 sont DEUX choses differentes, et lister l'une ne
# couvre pas l'autre. ::1 est le bouclage IPv6 natif : il se liste. La seconde
# est une IPv4 transportee par une socket IPv6 : elle se traite par
# normalisation du prefixe dans _autorise(). Confondre les deux laisse un trou
# qu'on ne voit qu'en refusant une machine legitime.

# D'OU SORT CET OBJECTIF
# ----------------------
# Mesure du 2026-09-13, portefeuille bidirectionnel de 13 lignes (10 positives,
# 3 negatives) construit par arb_strategie.py sur 500 EUR : notionnel 1 340,90,
# rendement 33,6 %/an robuste (apres retrait des 5 meilleurs versements et
# deduction des couts d'emprunt), soit 3,52 /semaine simple et 4,18 /semaine en
# composant les 1095 versements annuels. On vise le compose.
#
# Ne PAS reprendre les 250 du forex : sur 500 EUR d'arbitrage la barre resterait
# a 1,7 % en permanence. Un objectif inatteignable ne mesure rien.
OBJECTIF_HEBDO = 4.18
JOURS_SEMAINE = 7                                  # la crypto ne ferme jamais
OBJECTIF_JOUR = OBJECTIF_HEBDO / JOURS_SEMAINE
SEUIL_MINI = 0.70
DEVISE = "USDT"

# Le lanceur d'arbitrage boucle toutes les 600 s. Au-dela de 20 minutes sans
# mise a jour, le bot ne tourne plus : ce n'est pas un ralentissement, c'est
# un arret.
FRAIS_MIN = 20 * 60
FRAIS_AMBRE = 2 * 3600

NOMS_JOURS = ("LUN", "MAR", "MER", "JEU", "VEN", "SAM", "DIM")
LONGS_JOURS = ("LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI",
               "SAMEDI", "DIMANCHE")

_cache = {"data": None, "ts": 0.0, "offset": None}
_lock = threading.Lock()

# Le risque de liquidation demande un appel reseau a OKX. Le mettre dans le
# chemin de rendu ferait dependre l'affichage de la latence d'un tiers : une
# page qui met 8 secondes a repondre parce qu'OKX rame est une page cassee.
# Il est donc calcule par un thread de fond et la page lit le dernier resultat.
_liq = {"lignes": [], "ts": 0.0, "ok": False, "erreur": None}
_liq_lock = threading.Lock()


# ==================== COLLECTE ====================

def _charger(chemin):
    try:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    except Exception:
        return None


def fraicheur(maj_ms):
    """Age de l'etat, et verdict. Un chiffre sans son age ne vaut rien ici."""
    if not maj_ms:
        return {"secondes": None, "libelle": "jamais", "vif": False}
    secondes = time.time() - maj_ms / 1000.0
    if secondes < 90:
        lib = "il y a %d s" % secondes
    elif secondes < 5400:
        lib = "il y a %d min" % (secondes / 60)
    elif secondes < 172800:
        lib = "il y a %.1f h" % (secondes / 3600)
    else:
        lib = "il y a %.1f j" % (secondes / 86400)
    return {"secondes": secondes, "libelle": lib, "vif": secondes < FRAIS_MIN}


def lire_bot(module, etiquette, cle_capital, taux_eur=1.0):
    """
    Etat normalise d'un bot d'arbitrage.

    Les deux modules exposent bilan() mais avec des noms de cles differents,
    heritage de leur ecriture separee :
        arb_bot : frais_entree  cumul_usdt  financement_annualise_pct
        arb_eu  : frais         cumul       annuel_pct
    On normalise ici plutot que dans les modules : ils tournent en production,
    renommer leurs cles casserait leur sortie console et leurs journaux.
    """
    etat = _charger(module.ETAT)
    if etat is None:
        return {"etiquette": etiquette, "absent": True, "positions": [],
                "capital": 0.0, "notionnel": 0.0, "frais": 0.0,
                "financement": 0.0, "net": 0.0, "versements": 0,
                "fraicheur": fraicheur(None)}

    b = module.bilan(etat)
    frais = b.get("frais_entree", b.get("frais", 0.0))
    net = b.get("cumul_usdt", b.get("cumul", 0.0))

    positions = []
    for inst, p in (etat.get("positions") or {}).items():
        pf = p.get("frais_payes", 0.0)
        pnet = p.get("cumul", 0.0)
        encaisse = pnet + pf
        positions.append({
            "actif": p.get("base") or inst.split("-")[0],
            "bot": etiquette,
            "notionnel": p.get("notionnel", 0.0),
            "contrats": p.get("contrats"),
            "versements": p.get("versements", 0),
            "frais": pf,
            "encaisse": encaisse,
            "net": pnet,
            "attendu": p.get("annuel_attendu"),
            # Part des frais d'entree de CETTE ligne deja remboursee.
            "progression": max(min(encaisse / pf * 100, 100), 0) if pf > 0 else 0.0,
        })

    return {
        "etiquette": etiquette, "absent": False,
        "capital": float(etat.get(cle_capital) or 0.0) * taux_eur,
        "notionnel": b["notionnel"], "frais": frais,
        "financement": b["financement"], "net": net,
        "versements": b["versements"],
        "fraicheur": fraicheur(etat.get("maj")),
        "positions": positions,
    }


def _lire_journal():
    """
    Tous les versements journalises, les plus anciens d'abord.

    Format de ligne : {t, inst, type, taux, gain, cumul}. Seul arb_bot tient ce
    journal. arb_eu n'a jamais execute un cycle complet, son fichier journal
    n'a donc jamais ete cree.
    """
    out = []
    for source, etiquette in ((arb_bot.JOURNAL, "OKX USDT"),
                              (arb_eu.JOURNAL, "X-PERPS")):
        try:
            lignes = Path(source).read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for ligne in lignes:
            try:
                e = json.loads(ligne)
                quand = datetime.fromtimestamp(e["t"] / 1000.0)
            except Exception:
                continue
            out.append({
                "dt": quand, "bot": etiquette,
                "actif": e.get("inst", "?").split("-")[0],
                "taux": e.get("taux", 0.0),
                "gain": e.get("gain", 0.0),
            })
    out.sort(key=lambda e: e["dt"])
    return out


def _sonde_liquidation():
    """
    Marge consommee par chaque short, en tache de fond.

    On n'appelle pas arb_eu.risque_liquidation() directement : cette fonction
    renvoie une liste vide AUSSI BIEN quand rien n'est alarmant que quand OKX
    est injoignable. Pour un tableau de bord, confondre "tout va bien" et
    "je n'ai pas pu regarder" est exactement le genre de silence qui coute cher.

    On reutilise en revanche ses seuils (MARGE, SEUIL_FERMETURE) : la politique
    de risque reste definie a un seul endroit, dans arb_eu.
    """
    while True:
        etat = _charger(arb_eu.ETAT)
        positions = (etat or {}).get("positions") or {}
        if not positions:
            with _liq_lock:
                _liq.update(lignes=[], ts=time.time(), ok=True, erreur=None)
            time.sleep(60)
            continue
        try:
            px = {}
            for d in okx._get("/api/v5/market/tickers?instType=FUTURES"):
                v = d.get("last") or d.get("markPx") or ""
                try:
                    px[d["instId"]] = float(v)
                except (TypeError, ValueError):
                    pass
            lignes = []
            for inst, p in positions.items():
                p0, p1 = p.get("prix_entree"), px.get(inst)
                if not p0 or not p1:
                    continue
                hausse = p1 / p0 - 1.0
                conso = hausse / arb_eu.MARGE if arb_eu.MARGE > 0 else 0.0
                lignes.append({
                    "actif": p.get("base") or inst.split("-")[0],
                    "entree": p0, "actuel": p1,
                    "hausse": hausse * 100, "conso": conso * 100,
                    "verdict": ("FERMER" if conso >= 1.0
                                else "ALERTE" if conso >= arb_eu.SEUIL_FERMETURE
                                else "OK"),
                })
            lignes.sort(key=lambda x: -x["conso"])
            with _liq_lock:
                _liq.update(lignes=lignes, ts=time.time(), ok=True, erreur=None)
        except Exception as e:
            with _liq_lock:
                _liq.update(ts=time.time(), ok=False, erreur=str(e)[:120])
        time.sleep(60)


def collecte(offset_semaine=0):
    # EUR vers USD au meme taux que arb_eu.plan(), pour que les deux modules
    # ne puissent pas diverger sur la conversion.
    usdt = lire_bot(arb_bot, "OKX USDT", "capital_usdt")
    eur = lire_bot(arb_eu, "X-PERPS", "capital_eur", taux_eur=1.09)
    bots = [usdt, eur]
    vivants = [b for b in bots if not b["absent"]]

    now = datetime.now()
    debut_jour = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lundi_courant = debut_jour - timedelta(days=debut_jour.weekday())
    debut_semaine = lundi_courant + timedelta(weeks=offset_semaine)
    fin_semaine = debut_semaine + timedelta(days=7)
    est_courante = offset_semaine == 0

    tous = _lire_journal()
    de_la_semaine = [e for e in tous if debut_semaine <= e["dt"] < fin_semaine]

    def somme(depuis, jusqu_a):
        return sum(e["gain"] for e in tous if depuis <= e["dt"] < jusqu_a)

    recents = [{
        "quand": e["dt"].strftime("%a %d/%m  %H:%M"),
        "symbole": e["actif"], "gain": e["gain"],
    } for e in sorted(de_la_semaine, key=lambda x: x["dt"], reverse=True)[:10]]

    jours = []
    for k in range(JOURS_SEMAINE):
        j = debut_semaine + timedelta(days=k)
        fin_j = j + timedelta(days=1)
        v = somme(j, fin_j)
        du_jour = [e for e in tous if j <= e["dt"] < fin_j]
        jours.append({
            "nom": NOMS_JOURS[k], "date": j.strftime("%d/%m"), "gain": v,
            "futur": j > now,
            "aujourdhui": j.date() == now.date(),
            "pct": max(min(v / OBJECTIF_JOUR * 100, 100), 0),
            "nb": len(du_jour),
            "gagnants": sum(1 for x in du_jour if x["gain"] > 0),
            "perdants": sum(1 for x in du_jour if x["gain"] < 0),
            "trades": [{
                "h": x["dt"].strftime("%H:%M"), "sym": x["actif"],
                "gain": x["gain"],
            } for x in sorted(du_jour, key=lambda x: x["dt"], reverse=True)],
        })

    semaine = sum(e["gain"] for e in de_la_semaine)
    jour = somme(debut_jour, debut_jour + timedelta(days=1))

    capital = sum(b["capital"] for b in vivants)
    notionnel = sum(b["notionnel"] for b in vivants)
    frais = sum(b["frais"] for b in vivants)
    financement = sum(b["financement"] for b in vivants)
    net = sum(b["net"] for b in vivants)
    versements = sum(b["versements"] for b in vivants)

    positions = []
    for b in vivants:
        positions.extend(b["positions"])
    positions.sort(key=lambda p: -p["notionnel"])

    # La pastille de vie prend la PIRE des deux fraicheurs. Afficher la
    # meilleure laisserait croire que tout tourne alors qu'un bot est mort.
    ages = [b["fraicheur"] for b in vivants]
    pire = max(ages, key=lambda f: f["secondes"] if f["secondes"] else 1e12,
               default=fraicheur(None))

    with _liq_lock:
        liq = dict(_liq)
        liq["lignes"] = list(_liq["lignes"])

    return {
        "compte": "OKX CRYPTO", "devise": DEVISE,
        "ledger": ledger.bilan(),
        "liq": liq,
        "balance": capital, "equity": net, "flottant": notionnel,
        "frais": frais, "financement": financement,
        "jour": jour, "semaine": semaine,
        "obj_jour": OBJECTIF_JOUR, "obj_semaine": OBJECTIF_HEBDO,
        "pct_jour": max(min(jour / OBJECTIF_JOUR * 100, 100), 0),
        "pct_semaine": max(min(semaine / OBJECTIF_HEBDO * 100, 100), 0),
        "reste_jour": max(OBJECTIF_JOUR - jour, 0),
        "reste_semaine": max(OBJECTIF_HEBDO - semaine, 0),
        "seuil_mini": OBJECTIF_HEBDO * SEUIL_MINI,
        "offset": offset_semaine, "est_courante": est_courante,
        "libelle": f"{debut_semaine:%d/%m} — {debut_semaine + timedelta(days=6):%d/%m/%Y}",
        "nb_semaine": len(de_la_semaine),
        "positions": positions, "recents": recents, "jours": jours,
        "bots": bots,
        "mode": arb_control.mode(),
        "vivant": bool(pire["secondes"]) and pire["vif"],
        "uptime": pire["libelle"], "versements": versements,
        "maj": now.strftime("%H:%M:%S"),
    }


def etat(offset=0):
    with _lock:
        if (_cache["data"] is None or time.time() - _cache["ts"] > 3
                or _cache["offset"] != offset):
            _cache.update(data=collecte(offset), ts=time.time(), offset=offset)
        return _cache["data"]


def logs(d, n=40):
    """
    Journal temps reel. Le forex lit la console du bot ; ici aucun process
    d'arbitrage ne tourne en continu, on reconstitue donc le fil a partir des
    versements journalises et de l'etat des deux bots.
    """
    out = []
    for b in d["bots"]:
        if b["absent"]:
            out.append("[%s] aucun etat sur disque, ce bot n'a jamais ete lance"
                       % b["etiquette"])
        else:
            out.append("[%s] %d positions, notionnel %.2f, %d versements, maj %s"
                       % (b["etiquette"], len(b["positions"]), b["notionnel"],
                          b["versements"], b["fraicheur"]["libelle"]))
    if not d["vivant"]:
        out.append("[ALERTE] donnees figees : aucun cycle d'arbitrage ne tourne.")
    out.append("[mode] interrupteur sur %s" % d["mode"])
    out.append("")
    for e in sorted(_lire_journal(), key=lambda x: x["dt"])[-n:]:
        out.append("%s  %-9s %-8s taux %+.5f%%  gain %+.6f  " % (
            e["dt"].strftime("%d/%m %H:%M"), e["bot"], e["actif"],
            e["taux"] * 100, e["gain"]))
    return out or ["En attente du premier cycle du bot..."]


# ==================== RENDU ====================

def favicon():
    """
    Favicon SVG en data URI, identique a celle du forex a une couleur pres :
    la barre du milieu est BLEUE la ou le forex la met verte.

    Elle ne vire PAS au rouge quand la semaine est negative, contrairement au
    forex. L'onglet crypto doit se reconnaitre A SA COULEUR : une icone qui
    change de teinte selon le resultat ne sert plus a identifier l'onglet, qui
    est sa fonction premiere quand deux tableaux de bord tournent cote a cote.
    Le signe du resultat est deja dans le titre de l'onglet et partout dans la
    page, il n'a pas besoin d'etre repete dans 32 pixels.
    """
    c = "%232f81f7"
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
# du JS entreraient en conflit avec le formatage Python. Identique au forex.
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
            '<span class="tag neutre">%s</span><b>%s</b>'
            '<span class="vol">%s %s</span></div>'
            '<div class="pos-pl %s">%+.4f</div></header>'
            '<div class="pos-track"><div class="pos-fill %s" style="--w:%.1f%%;width:%.1f%%"></div></div>'
            '<footer><span>%d versement%s <i>&rarr;</i> %+.4f encaissé</span>'
            '<span class="%s">%.0f%% des frais</span>'
            '<span class="muted">frais %.4f · attendu %s</span></footer></article>'
            % (i, html.escape(p["bot"]), html.escape(p["actif"]),
               format(p["notionnel"], ",.2f"), d["devise"],
               cls(p["net"]), p["net"],
               cls(p["encaisse"]), p["progression"], p["progression"],
               p["versements"], "s" if p["versements"] > 1 else "",
               p["encaisse"],
               cls(p["progression"]), p["progression"],
               p["frais"],
               ("%.1f%%/an" % p["attendu"]) if p["attendu"] else "n/c")
            for i, p in enumerate(d["positions"]))
    else:
        pos = ('<div class="empty rise"><div class="radar"><i></i><i></i><i></i></div>'
               '<p>Aucune position ouverte</p>'
               '<small>Le bot scanne le financement et attend une ligne rentable</small></div>')

    maxi = max([abs(j["gain"]) for j in d["jours"]] + [d["obj_jour"]])
    jours = "".join(
        '<button type="button" class="day %s %s rise" style="--i:%d" data-day="%d">'
        '<div class="day-val %s">%s</div>'
        '<div class="day-col"><div class="day-goal" style="--h:%.0f%%"></div>'
        '<div class="day-bar %s" style="--h:%.0f%%;height:%.0f%%"></div></div>'
        '<div class="day-lab"><b>%s</b><span>%s</span></div></button>'
        % ("today" if j["aujourdhui"] else "", "ahead" if j["futur"] else "", i, i,
           cls(j["gain"]), "" if j["futur"] else "%+.3f" % j["gain"],
           d["obj_jour"] / maxi * 100, cls(j["gain"]),
           abs(j["gain"]) / maxi * 100, abs(j["gain"]) / maxi * 100,
           j["nom"], j["date"])
        for i, j in enumerate(d["jours"]))

    # Panneau de detail : un bloc par jour, masque, revele au clic sur la barre.
    # Tout est rendu cote serveur pour que la selection survive au rafraichissement.
    detail = "".join(
        '<div class="dd" data-dd="%d" hidden>'
        '<div class="dd-head"><b>%s %s</b><span class="dd-amt num %s">%s</span></div>'
        '<div class="dd-sub">%s</div>%s</div>'
        % (i, LONGS_JOURS[i], j["date"], cls(j["gain"]),
           "&mdash;" if j["futur"] else "%+.4f %s" % (j["gain"], d["devise"]),
           ("jour à venir" if j["futur"] else
            "aucun versement" if not j["nb"] else
            "%d versement%s · objectif %.3f %s" % (
                j["nb"], "s" if j["nb"] > 1 else "",
                d["obj_jour"], d["devise"])),
           ("" if not j["trades"] else
            '<ul class="dd-list">' + "".join(
                '<li><span class="muted">%s</span><b>%s</b>'
                '<span class="%s">%+.5f</span></li>'
                % (t["h"], html.escape(t["sym"]), cls(t["gain"]), t["gain"])
                for t in j["trades"]) + "</ul>"))
        for i, j in enumerate(d["jours"]))

    trades = "".join(
        '<li class="rise" style="--i:%d"><span class="muted">%s</span>'
        '<b>%s</b><span class="%s">%+.5f</span></li>'
        % (i, html.escape(r["quand"]), html.escape(r["symbole"]),
           cls(r["gain"]), r["gain"])
        for i, r in enumerate(d["recents"])) or (
        '<li class="none">Aucun versement sur la semaine affichée</li>')

    mode = d["mode"]
    txt = {"ON": "Le bot ouvre de nouvelles lignes.",
           "PAUSE": "Aucune nouvelle ligne. Les positions ouvertes continuent d'encaisser.",
           "OFF": "Bot arrêté. Les positions restent ouvertes, rien n'est fermé."}[mode]
    en_ligne = d["vivant"] and mode != "OFF"

    # Risque de liquidation. Equivalent crypto de la marge prop firm : ce qui
    # tue n'est pas de rater un objectif, c'est qu'une jambe short parte assez
    # haut pour manger la marge. Seul arb_eu est a levier, arb_bot ne l'est pas.
    liq = d["liq"]
    if not liq["ok"]:
        if liq["ts"] == 0:
            bloc_pf = ('<div class="pf-off">Première mesure en cours, '
                       'elle arrive sous une minute.</div>')
        else:
            bloc_pf = ('<div class="pf-off">Mesure indisponible : OKX est '
                       'injoignable. Ce n\'est PAS un feu vert, c\'est une '
                       'absence de mesure.<br><small>%s</small></div>'
                       % html.escape(liq["erreur"] or ""))
    elif not liq["lignes"]:
        bloc_pf = ('<div class="pf-off">Aucune position à levier à surveiller.</div>')
    else:
        pire = liq["lignes"][0]
        statut = pire["verdict"]
        etiquette = {"OK": "DANS LES CLOUS", "ALERTE": "MARGE ENTAMÉE",
                     "FERMER": "FERMETURE REQUISE"}[statut]
        teinte = {"OK": "ok", "ALERTE": "warn", "FERMER": "bad"}[statut]
        en_alerte = sum(1 for x in liq["lignes"] if x["verdict"] != "OK")
        note = ("" if not en_alerte else
                '<div class="pf-note">%d ligne%s au-delà du seuil d\'alerte. '
                'Une position d\'arbitrage reste delta-neutre : ce qui est '
                'menacé est la marge, pas le capital.</div>'
                % (en_alerte, "s" if en_alerte > 1 else ""))
        bloc_pf = (
            '<div class="pf-head"><span class="pf-badge %s">%s</span>'
            '<em>%d ligne%s à levier %.0fx surveillée%s</em></div>'
            '<div class="pf-grid">'
            '<div class="pf-cell"><span>PIRE LIGNE</span><b class="num">%s</b>'
            '<div class="pf-track"><i class="%s" style="width:%.0f%%"></i></div>'
            '<em>%.0f%% de marge consommée · entrée %.4f</em></div>'
            '<div class="pf-cell"><span>MARGE RESTANTE</span><b class="num %s">%.0f%%</b>'
            '<em>avant liquidation de cette ligne</em></div>'
            '<div class="pf-cell"><span>ALERTE À</span>'
            '<b class="num dim">%.0f%%</b><em>de la marge consommée</em></div>'
            '<div class="pf-cell"><span>FERMETURE À</span>'
            '<b class="num dim">100%%</b><em>de la marge consommée</em></div>'
            '</div>%s'
            % (teinte, etiquette,
               len(liq["lignes"]), "s" if len(liq["lignes"]) > 1 else "",
               arb_eu.LEVIER, "s" if len(liq["lignes"]) > 1 else "",
               html.escape(pire["actif"]), teinte,
               max(min(pire["conso"], 100), 0), pire["conso"], pire["entree"],
               cls(100 - pire["conso"]), max(100 - pire["conso"], 0),
               arb_eu.SEUIL_FERMETURE * 100, note))

    return {
        "pf": bloc_pf,
        "positions": pos, "jours": jours, "jdetail": detail, "trades": trades,
        "journal": chr(10).join(html.escape(l) for l in logs(d)),
        "j_amt": "%+.4f" % d["jour"], "j_cls": "amt num " + cls(d["jour"]),
        "s_amt": "%+.4f" % d["semaine"], "s_cls": "amt num " + cls(d["semaine"]),
        "j_pct": "%.0f%%" % d["pct_jour"], "s_pct": "%.0f%%" % d["pct_semaine"],
        "j_pct_cls": "arc-pct num " + cls(d["jour"]),
        "s_pct_cls": "arc-pct num " + cls(d["semaine"]),
        "j_arc": arc(d["pct_jour"]), "s_arc": arc(d["pct_semaine"]),
        # Les frais d'entree sont montres a part, jamais soustraits de
        # l'objectif : ils se paient une fois, le financement revient toutes
        # les 8 heures. Les fondre en un chiffre unique mentirait deux fois.
        "j_left": "reste %.4f %s" % (d["reste_jour"], d["devise"]) + (
            " · %.4f %s de frais non remboursés" % (
                d["frais"] - d["financement"], d["devise"])
            if d["frais"] - d["financement"] > 0 else ""),
        "s_left": "%d versement%s · seuil mini %.2f %s" % (
            d["nb_semaine"], "s" if d["nb_semaine"] > 1 else "",
            d["seuil_mini"], d["devise"]),
        "balance": format(d["balance"], ",.2f"),
        "equity": "%+.4f" % d["equity"],
        "flottant": format(d["flottant"], ",.2f"),
        "flot_cls": "num",
        "led_net": "%+.2f" % d["ledger"]["net"],
        "led_net_cls": "num " + cls(d["ledger"]["net"]),
        "led_out": format(d["ledger"]["depenses"], ",.2f"),
        "led_in": format(d["ledger"]["recettes"], ",.2f"),
        "led_tr": ("%d j / %d" % (d["ledger"]["jours"], ledger.TRACK_RECORD_JOURS)
                   if d["ledger"]["debut"] else "pas demarre"),
        "led_bar": "%.0f%%" % d["ledger"]["track_record_pct"],
        "nb": str(d["versements"]),
        "nb_pos": "%d ouverte(s)" % len(d["positions"]),
        "maj": d["maj"], "uptime": d["uptime"],
        # La position du curseur du switch est portee par une classe : le CSS
        # translate le pouce, aucune valeur inline a recalculer cote JS.
        "sw_cls": "sw sw-" + mode.lower(),
        # Pastille de vie. Rouge si les etats sont figes ou si le bot est OFF.
        "pulse_cls": "pulse " + ("on" if en_ligne else "off"),
        "pulse_ttl": ("Bot actif — %s" % txt) if en_ligne else "Données figées ou bot arrêté",
    }


def page(offset=0):
    d = etat(offset)
    if "erreur" in d:
        return ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
                '<meta http-equiv="refresh" content="8"><title>A.I.T.B Crypto</title>'
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
  /* Le vert du forex devient du BLEU partout. C'est le seul marqueur visuel
     qui distingue l'onglet crypto de l'onglet forex, les deux pages etant
     identiques par ailleurs. Une seule variable a changer : tout ce qui etait
     vert en herite, la pastille de vie, le switch ON, le badge EN COURS, les
     montants positifs, les jauges et les etiquettes. Le rouge ne bouge pas,
     il n'a jamais ete du vert. */
  --up:#2f81f7; --up-glow:rgba(47,129,247,.16);
  --down:#ff3b52; --down-glow:rgba(255,59,82,.16);
  --gold:#f0b429;
  --cy:#2f81f7; --cy-glow:rgba(47,129,247,.14);
  /* Defini ici volontairement : dashboard.py l'utilise sans jamais le
     declarer, ses cellules de ledger et de detail ont donc un fond vide. */
  --card2:#0c0e10;
  --out:cubic-bezier(.23,1,.32,1);
  --inout:cubic-bezier(.77,0,.175,1);
  --mono:'JetBrains Mono',ui-monospace,monospace;
  --disp:'Bricolage Grotesque',system-ui,sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box}
html{background:var(--bg)}
body{font-family:var(--disp);color:var(--txt);min-height:100vh;padding:26px 22px 40px;
  background:radial-gradient(900px 480px at 12% -8%,rgba(47,129,247,.055),transparent 62%),
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
.pulse.on{background:var(--up);box-shadow:0 0 0 3px var(--up-glow)}
.pulse.off{background:var(--down);box-shadow:0 0 0 3px var(--down-glow)}
.meta{margin-left:auto;display:flex;gap:20px;font:400 10px/1 var(--mono);
  letter-spacing:.14em;color:var(--faint)}
.meta b{color:var(--dim);font-weight:600}

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
.goal.j{--halo:rgba(47,129,247,.13);--k:var(--cy)}
.goal.s{--halo:rgba(240,180,41,.12);--k:var(--gold)}
.goal-body{flex:1;min-width:0;position:relative}
.goal h2{display:flex;align-items:center;gap:11px;font:800 26px/1 var(--disp);
  letter-spacing:-.035em;color:var(--txt);margin-bottom:5px}
.goal h2 .ic{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;
  background:var(--halo);color:var(--k);font:700 11px/1 var(--mono);letter-spacing:0;
  border:1px solid var(--k);flex-shrink:0}
.goal .scope{font:500 11px/1 var(--mono);letter-spacing:.2em;color:var(--k);
  opacity:.85;margin-bottom:18px}
.goal .amt{font:700 44px/1 var(--mono);letter-spacing:-.05em;display:block}
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
/* Propre a la crypto : une position d'arbitrage n'est ni longue ni courte,
   elle est delta-neutre. Lui coller ACHAT ou VENTE serait un contresens. */
.tag.neutre{color:var(--cy);background:var(--cy-glow)}
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

/* Sept colonnes et non cinq : le financement tombe aussi le week-end. */
.days{display:grid;grid-template-columns:repeat(7,1fr);gap:7px;height:210px}
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
.day-val{font:700 11px/1 var(--mono);height:13px;letter-spacing:-.02em}
.day-col{position:relative;width:100%;flex:1;display:flex;align-items:flex-end;
  background:var(--bg2);border-radius:7px;overflow:hidden}
.day-goal{position:absolute;left:0;right:0;bottom:var(--h);height:1px;
  background:repeating-linear-gradient(90deg,var(--line2) 0 4px,transparent 4px 8px)}
.day-bar{width:100%;height:0;border-radius:6px 6px 0 0;background:currentColor;opacity:.85;
  animation:grow 1s var(--out) forwards;animation-delay:calc(var(--i)*60ms + 200ms)}
@keyframes grow{to{height:var(--h)}}
.day-lab{text-align:center;line-height:1.4}
.day-lab b{display:block;font:700 10px/1 var(--mono);letter-spacing:.1em;color:var(--dim)}
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
<title>A.I.T.B Crypto — {d['semaine']:+.2f} {d['devise']}</title>
<link rel="icon" href="{favicon()}">
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
      <button class="b-restart" title="Relance le cycle sans fermer les positions">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
             stroke-width="2.4" stroke-linecap="round"><path d="M20 11a8 8 0 1 0-.6 4"/>
        <path d="M20 4v7h-7"/></svg>RELANCER</button></form>
  </div>
  <div class="meta">
    <span>COMPTE <b>{d['compte']}</b></span>
    <span>FRAÎCHEUR <b id="uptime">{f["uptime"]}</b></span>
    <span>MAJ <b id="maj">{f["maj"]}</b></span>
  </div>
</header>

<section class="goals">
  <div class="goal j rise" style="--i:1">
    <div class="goal-body">
      <h2><span class="ic">24H</span>Objectif du jour</h2>
      <div class="scope">AUJOURD'HUI</div>
      <div class="amt-row"><span class="{f["j_cls"]}" id="j-amt">{f["j_amt"]}</span><span class="goal-of">/{d['obj_jour']:.3f} {d['devise']}</span></div>
      <div class="left" id="j-left">{f["j_left"]}</div>
    </div>
    <div class="arc-wrap" id="j-arc">{f["j_arc"]}<span class="{f["j_pct_cls"]}" id="j-pct">{f["j_pct"]}</span></div>
  </div>
  <div class="goal s rise" style="--i:2">
    <div class="goal-body">
      <h2><span class="ic">7J</span>Objectif de la semaine</h2>
      <div class="scope">LUNDI &rarr; DIMANCHE</div>
      <div class="amt-row"><span class="{f["s_cls"]}" id="s-amt">{f["s_amt"]}</span><span class="goal-of">/{d['obj_semaine']:.2f} {d['devise']}</span></div>
      <div class="left" id="s-left">{f["s_left"]}</div>
    </div>
    <div class="arc-wrap" id="s-arc">{f["s_arc"]}<span class="{f["s_pct_cls"]}" id="s-pct">{f["s_pct"]}</span></div>
  </div>
</section>

<section class="kpis">
  <div class="kpi rise" style="--i:3"><span>CAPITAL</span><b class="num" id="balance">{f["balance"]}</b></div>
  <div class="kpi rise" style="--i:4"><span>NOTIONNEL OUVERT</span>
    <b class="{f["flot_cls"]}" id="flottant">{f["flottant"]}</b></div>
  <div class="kpi rise" style="--i:5"><span>NET EN POCHE</span><b class="num" id="equity">{f["equity"]}</b></div>
  <div class="kpi rise" style="--i:6"><span>VERSEMENTS REÇUS</span><b class="num" id="nb">{f["nb"]}</b></div>
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
  <div class="pf-title"><h3>RISQUE DE LIQUIDATION</h3>
    <em>ce qui tue ici n'est pas de rater une cible, c'est qu'un short mange sa marge</em></div>
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
  <div class="panel"><h3>VERSEMENTS ENCAISSÉS <em>semaine affichée</em></h3>
    <ul class="trades" id="trades">{f["trades"]}</ul></div>
  <div class="log"><h3>JOURNAL TEMPS RÉEL</h3><pre id="journal">{f["journal"]}</pre></div>
</section>

</div>{JS}</body></html>'''


# ==================== SERVEUR ====================

class Handler(BaseHTTPRequestHandler):
    def _autorise(self):
        """
        Filtre par IP source. Applique sur do_GET ET do_POST, sans exception.

        Un filtre pose sur le seul do_GET laisserait /mode et /restart ouverts,
        c'est-a-dire precisement les deux routes qui ECRIVENT. La lecture est le
        moindre risque, ce sont les ecritures qu'il faut garder.

        NORMALISATION IPv6, sans quoi le filtre se retourne contre nous : une
        IPv4 arrivee par une socket IPv6 se presente sous la forme
        ::ffff:100.x.y.w. Comparee telle quelle a une liste d'IPv4 brutes,
        elle ne correspond a rien et une machine POURTANT AUTORISEE recoit un
        403. La panne est silencieuse et ressemble a une erreur de liste, pas a
        un probleme de format. HTTPServer ecoute en IPv4 par defaut donc le cas
        est rare, mais il coute une ligne a couvrir et une heure a diagnostiquer.
        """
        ip = self.client_address[0]
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        if ip in IPS_AUTORISEES:
            return True
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _offset(self):
        try:
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            for part in q.split("&"):
                if part.startswith("s="):
                    return max(-104, min(0, int(part[2:])))
        except Exception:
            pass
        return 0

    def do_POST(self):
        if not self._autorise():
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n).decode("utf-8", "replace")
            if self.path.startswith("/restart"):
                # Depose un drapeau que le runner d'arbitrage consomme.
                # Aucune position n'est fermee : une jambe d'arbitrage ne coute
                # des frais qu'a la fermeture, la maintenir ne coute rien.
                arb_control.demander_redemarrage(par="dashboard_crypto")
            for part in body.split("&"):
                if part.startswith("m="):
                    m = part[2:].upper()
                    if m in arb_control.ETATS:
                        arb_control.ecrire(m, par="dashboard_crypto")
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


# --- ouverture de l'ecoute, tolerante au demarrage de Windows ---------------
# Secondes entre deux tentatives, et duree totale avant abandon. 15 minutes
# couvrent largement l'assignation d'une adresse Tailscale au demarrage, y
# compris quand celle-ci attend l'ouverture de session automatique.
DELAI_BIND = 10
FENETRE_BIND = 900

# Un port deja pris ne se libere pas en attendant : patienter 15 minutes dessus
# ne sert qu'a retarder le message utile. On tolere seulement le chevauchement
# bref d'une instance en train de se terminer, puis on refuse franchement.
FENETRE_PORT_PRIS = 30


class _Serveur(HTTPServer):
    """
    HTTPServer avec allow_reuse_address remis a False.

    Python le laisse a True, ce qui a un sens sous Unix : le drapeau y evite
    l'attente TIME_WAIT. Sous Windows le MEME drapeau veut dire autre chose,
    il autorise un second processus a PRENDRE un port deja ecoute. Mesure le
    2026-09-15 sur cette machine : deux HTTPServer se sont lies a
    127.0.0.1:8099 sans la moindre erreur, le second volant silencieusement
    une partie du trafic du premier.

    Pourquoi ca devient serieux maintenant : avec la tache en /ru SYSTEM, le
    serveur tourne hors session et n'apparait dans aucune fenetre. Croire qu'il
    est mort et relancer le .bat a la main creerait une seconde instance sur le
    meme port, dont les requetes se repartiraient au hasard entre les deux,
    POST de l'interrupteur ON/OFF compris. A False, la seconde instance echoue
    franchement avec errno 10048 et l'ecrit dans son journal.
    """
    allow_reuse_address = False


def _ouvrir_ecoute(bind, port, gestionnaire, fenetre=FENETRE_BIND,
                   delai=DELAI_BIND):
    """
    Rend un HTTPServer ecoutant sur bind:port, en reessayant tant que
    l'adresse n'existe pas encore sur la machine.

    POURQUOI CETTE BOUCLE EXISTE
    Sur le serveur, BIND ne vaut pas 127.0.0.1 mais l'adresse Tailscale. Cette
    adresse n'est portee par aucune carte physique : elle apparait quand
    tailscaled l'assigne, plusieurs dizaines de secondes apres le demarrage de
    Windows. Lance en tache /sc onstart, ce programme demarre AVANT.

    Un HTTPServer((BIND, PORT)) appele trop tot leve WinError 10049, le
    processus meurt, et une tache onstart ne se redeclenche qu'au prochain
    demarrage. La machine resterait sans tableau de bord jusqu'au reboot
    suivant. Sans cette boucle, passer en onstart serait donc un RECUL par
    rapport a onlogon, pas un progres.

    Ce defaut est structurellement invisible en test : sur 127.0.0.1 l'adresse
    existe toujours, donc le bind reussit toujours du premier coup. Il ne se
    manifeste que sur la machine ou l'on ne regarde pas.
    """
    debut = time.time()
    tentative = 0
    while True:
        tentative += 1
        try:
            return _Serveur((bind, port), gestionnaire)
        except OSError as e:
            # Les deux erreurs frequentes ont des causes OPPOSEES, donc elles
            # appellent des reponses opposees.
            #   10049  l'adresse n'existe pas encore  -> attendre la repare
            #   10048  le port est deja pris          -> attendre ne repare RIEN
            # Les traiter pareil fait patienter un quart d'heure pour rien sur
            # le second cas, avant de finir sur une trace Python que personne ne
            # sait lire. Un refus comprehensible vaut mieux qu'un refus correct.
            port_pris = (e.errno == 10048)
            limite = min(fenetre, FENETRE_PORT_PRIS) if port_pris else fenetre
            if time.time() - debut >= limite:
                if port_pris:
                    print(f"   ARRET : le port {port} est deja pris, un tableau "
                          f"de bord tourne deja.", flush=True)
                    print(f"   Attendre n'y changera rien. Pour voir qui le "
                          f"tient, puis l'arreter :", flush=True)
                    print(f"     schtasks /query /tn AITB_Dashboard_Crypto",
                          flush=True)
                    print(f"     schtasks /end   /tn AITB_Dashboard_Crypto",
                          flush=True)
                else:
                    print(f"   ARRET : {bind}:{port} toujours indisponible apres "
                          f"{tentative} tentatives en {limite} s "
                          f"(errno {e.errno}).", flush=True)
                    print(f"   Si c'est une adresse Tailscale, verifier que "
                          f"tailscaled tourne sur cette machine.", flush=True)
                raise
            motif = ("port pris, peut-etre une instance qui se termine"
                     if port_pris else
                     "adresse sans doute pas encore assignee par Tailscale")
            print(f"   {bind}:{port} indisponible (errno {e.errno}, tentative "
                  f"{tentative}) : {motif}. Nouvel essai dans {delai} s.",
                  flush=True)
            time.sleep(delai)


def main():
    threading.Thread(target=_sonde_liquidation, daemon=True,
                     name="SondeLiquidation").start()
    print()
    print("  " + "=" * 46)
    print("   A.I.T.B CRYPTO")
    print("  " + "=" * 46)
    print(f"   http://{BIND}:{PORT}")
    print(f"   Objectif jour     {OBJECTIF_JOUR:.3f} {DEVISE}")
    print(f"   Objectif semaine  {OBJECTIF_HEBDO:.2f} {DEVISE}  (lundi au dimanche)")
    if BIND != "127.0.0.1":
        print(f"   Ecoute NON LOCALE : {len(IPS_AUTORISEES)} IP autorisees, 403 pour les autres")
        for ip in sorted(IPS_AUTORISEES):
            print(f"     {ip}")
    print("   Ctrl+C ferme l'affichage, PAS les bots")
    print()
    _ouvrir_ecoute(BIND, PORT, Handler).serve_forever()


if __name__ == "__main__":
    main()
