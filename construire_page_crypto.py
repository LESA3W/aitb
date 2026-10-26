#!/usr/bin/env python3
# construire_page_crypto.py
"""
Genere aitb-web-crypto/app/page.js a partir du CSS de dashboard_crypto.py.

POURQUOI UN GENERATEUR PLUTOT QU'UNE COPIE MANUELLE
----------------------------------------------------
Le CSS du tableau de bord fait pres de 10 000 caracteres. Le recopier a la main
dans un fichier JavaScript garantit une coquille tot ou tard, et une coquille
CSS ne leve aucune erreur : elle produit un affichage subtilement faux que
personne ne remarque avant des semaines.

L'extraire du fichier source garantit la parite EXACTE, et surtout elle se
maintient : toute modification du tableau de bord local se repropage en
relancant ce script, au lieu de creer une derive silencieuse entre les deux.

Usage :  python construire_page_crypto.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "dashboard_crypto.py"
CIBLE = ROOT.parent / "aitb-web-crypto" / "app" / "page.js"


def extraire_css():
    """Le bloc css = \"\"\"...\"\"\" de page(), tel quel."""
    src = SOURCE.read_text(encoding="utf-8")
    m = re.search(r'\n    css = """\n(.*?)\n"""\n', src, re.S)
    if not m:
        raise SystemExit("bloc CSS introuvable dans dashboard_crypto.py")
    return m.group(1)


# NOTE HISTORIQUE, 2026-09-17 : une fonction nettoyer() retirait ici les regles
# .sw, .ctrl et .b-restart, parce que le miroir etait en lecture seule.
#
# Le raisonnement d'alors etait : "ces boutons poussent sur /mode et /restart,
# qui n'ont aucune authentification". C'etait vrai du tableau de bord LOCAL, ou
# la protection est la liste blanche d'IP. Ca ne dit rien du miroir, qui a son
# propre mot de passe et ne partage aucune route avec le serveur.
#
# Le miroir a maintenant ses controles, via /api/commande qui exige le cookie de
# session, et une file relevee par le pousseur. Le CSS passe donc en entier :
# c'est justement ce que le generateur existe pour garantir. Retirer des regles
# a la main etait la seule chose ici qui pouvait deriver du tableau local.


def main():
    css = extraire_css()
    # Le CSS part dans un gabarit JS : proteger les backticks et ${...}.
    css_js = css.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

    page = GABARIT.replace("@@CSS@@", css_js)
    CIBLE.parent.mkdir(parents=True, exist_ok=True)
    CIBLE.write_text(page, encoding="utf-8")
    print("  ecrit   : %s" % CIBLE)
    print("  CSS     : %d caracteres, INTEGRAL (aucune regle retiree)"
          % len(css_js))
    # Controle explicite : les regles des controles doivent etre presentes,
    # sinon les boutons s'afficheraient sans style et personne ne le verrait
    # avant d'ouvrir la page.
    for regle in (".ctrl{", ".sw{", ".sw-thumb", ".b-restart{"):
        print("  %-14s %s" % (regle, "present" if regle in css else "ABSENT"))
    print("  source  : %s" % SOURCE)
    return 0


GABARIT = r'''"use client";

// ATTENTION : fichier GENERE par A.I.T.B_2/construire_page_crypto.py.
// Ne pas modifier le CSS ici, il serait ecrase. Modifier dashboard_crypto.py
// puis relancer le generateur, pour que le miroir et le tableau de bord local
// ne derivent jamais l'un de l'autre.

import { useCallback, useEffect, useRef, useState } from "react";

const CSS = `@@CSS@@`;

const H = (s) => ({ dangerouslySetInnerHTML: { __html: s || "" } });

export default function Page() {
  const [etat, setEtat] = useState(null);
  const [besoinMdp, setBesoinMdp] = useState(false);
  const [pret, setPret] = useState(false);
  const [mdp, setMdp] = useState("");
  const [err, setErr] = useState("");
  const [envoi, setEnvoi] = useState(false);
  const [jourSel, setJourSel] = useState(null);
  const [cmd, setCmd] = useState("");
  const racine = useRef(null);

  const charger = useCallback(async () => {
    try {
      const r = await fetch("/api/etat", { cache: "no-store" });
      if (r.status === 401) { setBesoinMdp(true); setPret(true); return; }
      const d = await r.json();
      setBesoinMdp(false); setEtat(d); setPret(true);
    } catch { setPret(true); }
  }, []);

  useEffect(() => {
    charger();
    const t = setInterval(charger, 15000);
    return () => clearInterval(t);
  }, [charger]);

  // Les blocs sont remplaces a chaque rafraichissement, donc la selection du
  // jour est reappliquee apres chaque rendu, sinon elle sauterait toutes les
  // 15 secondes. Meme raison que appliquerJour() cote local.
  useEffect(() => {
    const n = racine.current;
    if (!n) return;
    const barres = n.querySelectorAll("#jours .day");
    if (!barres.length) return;
    let sel = jourSel;
    if (sel === null) {
      const auj = n.querySelector("#jours .day.today");
      sel = auj ? auj.dataset.day : barres[barres.length - 1].dataset.day;
    }
    barres.forEach((b) => b.classList.toggle("sel", b.dataset.day === sel));
    n.querySelectorAll("#jdetail .dd").forEach((d) => {
      d.hidden = d.dataset.dd !== sel;
    });
  });

  useEffect(() => {
    const t = setTimeout(() => document.body.classList.add("ready"), 1400);
    return () => clearTimeout(t);
  }, []);

  function clicJour(e) {
    const b = e.target.closest && e.target.closest("#jours .day");
    if (b) setJourSel(b.dataset.day);
  }

  // Depose une intention. Elle ne prend PAS effet tout de suite : le serveur de
  // la maison la releve dans la reponse de son prochain envoi, donc sous 60 s.
  // On le dit a l'ecran plutot que de laisser croire a un bouton instantane :
  // un bouton qui semble ne rien faire est un bouton sur lequel on reappuie.
  async function commander(action) {
    setCmd(action);
    try {
      const r = await fetch("/api/commande", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (r.status === 401) { setBesoinMdp(true); setCmd(""); return; }
      if (!r.ok) { setCmd("refusée"); return; }
    } catch { setCmd("réseau indisponible"); return; }
    // L'etat affiche vient du serveur, pas d'ici : quand il aura applique la
    // commande, sw_cls changera tout seul au rafraichissement suivant.
    setTimeout(() => setCmd(""), 65000);
  }

  async function connexion(e) {
    e.preventDefault();
    setErr(""); setEnvoi(true);
    try {
      const r = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mdp }),
      });
      const d = await r.json();
      if (!r.ok) { setErr(d.erreur || "refuse"); setEnvoi(false); return; }
      setMdp(""); setEnvoi(false); await charger();
    } catch { setErr("reseau indisponible"); setEnvoi(false); }
  }

  if (!pret) return <><style>{CSS}</style><div className="gate" /></>;

  if (besoinMdp) {
    return (
      <>
        <style>{CSS}</style>
        <div className="gate">
          <form onSubmit={connexion}>
            <h1>A.I.T.B Crypto</h1>
            <p>Tableau de bord privé. Accès par mot de passe.</p>
            <input type="password" value={mdp} autoFocus
                   autoComplete="current-password" placeholder="mot de passe"
                   onChange={(e) => setMdp(e.target.value)} />
            <button type="submit" disabled={envoi || !mdp}>
              {envoi ? "VÉRIFICATION…" : "ENTRER"}
            </button>
            <div className="err">{err}</div>
          </form>
        </div>
      </>
    );
  }

  const f = etat || {};

  if (f.erreur) {
    return (
      <>
        <style>{CSS}</style>
        <div className="shell">
          <div className="top"><div className="brand">
            <span className="pulse off" /><h1>A.I.T.B Crypto</h1></div></div>
          <div className="avert">
            Le serveur a répondu une erreur. Aucun chiffre n&apos;est affiché,
            car aucun chiffre ne serait fiable.
            <div style={{ marginTop: 10, color: "var(--faint)", wordBreak: "break-word" }}>
              {String(f.erreur)}
            </div>
          </div>
        </div>
      </>
    );
  }

  if (f.vide) {
    return (
      <>
        <style>{CSS}</style>
        <div className="shell">
          <div className="top"><div className="brand">
            <span className="pulse off" /><h1>A.I.T.B Crypto</h1></div></div>
          <div className="avert">
            Aucun instantané reçu. Le serveur de la maison n&apos;a encore rien
            envoyé : vérifie que ENVOI_VERCEL_CRYPTO.bat tourne là-bas.
          </div>
        </div>
      </>
    );
  }

  // Deux fraicheurs distinctes, a ne jamais confondre :
  //  - age       : depuis quand Vercel n'a plus recu d'envoi du serveur
  //  - pulse_cls : les bots d'arbitrage etaient-ils frais au moment de l'envoi
  // Les bots peuvent etre morts avec un envoi frais, et l'inverse.
  const age = f._ageSec ?? 99999;
  const frais = age < 180;
  const pulse = frais ? (f.pulse_cls || "pulse off") : "pulse off";

  return (
    <>
      <style>{CSS}</style>
      <div className="shell" ref={racine} onClick={clicJour}>

        <header className="top rise" style={{ "--i": 0 }}>
          <div className="brand"><span className={pulse} /><h1>A.I.T.B Crypto</h1></div>
          {/* Controles identiques au tableau de bord local. Les <form> sont
              CONSERVES alors qu'ils ne soumettent rien : le CSS partage cible
              .sw form:nth-of-type(1|2|3) button pour colorer le bouton actif.
              Des <button> nus casseraient cette coloration en silence, et le
              generateur ne pourrait plus garantir la parite. */}
          <div className="ctrl">
            <div className={f.sw_cls || "sw sw-on"} id="switch">
              <span className="sw-thumb" />
              {["ON", "PAUSE", "OFF"].map((m) => (
                <form key={m} onSubmit={(e) => { e.preventDefault(); commander(m); }}>
                  <button type="submit">{m}</button>
                </form>
              ))}
            </div>
            <form onSubmit={(e) => { e.preventDefault(); commander("RELANCER"); }}>
              <button className="b-restart" type="submit"
                      title="Relance le cycle sans fermer les positions">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none"
                     stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                  <path d="M20 11a8 8 0 1 0-.6 4" /><path d="M20 4v7h-7" />
                </svg>RELANCER
              </button>
            </form>
          </div>
          <div className="meta">
            <span>REÇU IL Y A <b className={frais ? "" : "vieux"}>
              {age < 120 ? `${age} s` : `${Math.round(age / 60)} min`}</b></span>
            <span>FRAÎCHEUR <b>{f.uptime || "—"}</b></span>
            <span>MAJ <b>{f.maj || "—"}</b></span>
          </div>
        </header>

        {/* Sans ce bloc, un clic sur ON ne produirait AUCUN effet visible
            pendant 60 secondes, et on reappuierait en croyant a une panne.
            Le commentaire de commander() promettait cet affichage, le rendu
            manquait : une promesse en commentaire n'est pas du code. */}
        {cmd && (
          <div className="avert" style={{ borderColor: "var(--up)" }}>
            Commande <b>{cmd}</b> envoyée. Le serveur de la maison la relève à
            son prochain envoi, donc sous 60 secondes. L&apos;interrupteur
            ci-dessus montre l&apos;état RÉEL du serveur, pas la commande : il
            changera de lui-même une fois appliquée. S&apos;il ne change pas,
            c&apos;est que rien n&apos;a été appliqué, et il faut réappuyer.
          </div>
        )}

        {!frais && (
          <div className="avert">
            Dernier envoi il y a {Math.round(age / 60)} minutes. Ces chiffres ne
            sont plus à jour : le serveur de la maison n&apos;envoie plus rien.
          </div>
        )}

        <section className="goals">
          <div className="goal j rise" style={{ "--i": 1 }}>
            <div className="goal-body">
              <h2><span className="ic">24H</span>Objectif du jour</h2>
              <div className="scope">AUJOURD&apos;HUI</div>
              <div className="amt-row"><span className={f.j_cls}>{f.j_amt}</span></div>
              <div className="left">{f.j_left}</div>
            </div>
            <div className="arc-wrap"
                 {...H(`${f.j_arc || ""}<span class="${f.j_pct_cls || ""}">${f.j_pct || ""}</span>`)} />
          </div>
          <div className="goal s rise" style={{ "--i": 2 }}>
            <div className="goal-body">
              <h2><span className="ic">7J</span>Objectif de la semaine</h2>
              <div className="scope">LUNDI → DIMANCHE</div>
              <div className="amt-row"><span className={f.s_cls}>{f.s_amt}</span></div>
              <div className="left">{f.s_left}</div>
            </div>
            <div className="arc-wrap"
                 {...H(`${f.s_arc || ""}<span class="${f.s_pct_cls || ""}">${f.s_pct || ""}</span>`)} />
          </div>
        </section>

        <section className="kpis">
          <div className="kpi rise" style={{ "--i": 3 }}>
            <span>CAPITAL</span><b className="num">{f.balance}</b></div>
          <div className="kpi rise" style={{ "--i": 4 }}>
            <span>NOTIONNEL OUVERT</span><b className={f.flot_cls}>{f.flottant}</b></div>
          <div className="kpi rise" style={{ "--i": 5 }}>
            <span>NET EN POCHE</span><b className="num">{f.equity}</b></div>
          <div className="kpi rise" style={{ "--i": 6 }}>
            <span>VERSEMENTS REÇUS</span><b className="num">{f.nb}</b></div>
        </section>

        <section className="ledger rise" style={{ "--i": 7 }}>
          <div className="led-head">
            <h3>COÛT RÉEL DU PROJET</h3>
            <em>commun aux deux bots — un euro dépensé est un euro dépensé</em>
          </div>
          <div className="led-grid">
            <div className="led-cell"><span>SORTIES</span>
              <b className="num down">{f.led_out}</b></div>
            <div className="led-cell"><span>ENTRÉES</span>
              <b className="num up">{f.led_in}</b></div>
            <div className="led-cell big"><span>NET RÉEL</span>
              <b className={f.led_net_cls}>{f.led_net}</b></div>
            <div className="led-cell"><span>TRACK RECORD</span>
              <b className="num">{f.led_tr}</b>
              <div className="led-track"><i style={{ width: f.led_bar || "0%" }} /></div></div>
          </div>
        </section>

        <section className="propfirm rise" style={{ "--i": 8 }}>
          <div className="pf-title"><h3>RISQUE DE LIQUIDATION</h3>
            <em>ce qui tue ici n&apos;est pas de rater une cible, c&apos;est qu&apos;un short mange sa marge</em>
          </div>
          <div {...H(f.pf)} />
        </section>

        <section className="grid">
          <div className="panel">
            <h3>POSITIONS <em>{f.nb_pos}</em></h3>
            <div {...H(f.positions)} />
          </div>
          <div className="panel">
            <h3>PAR JOUR <em>clique un jour</em></h3>
            <div className="days" id="jours" {...H(f.jours)} />
            <div className="dd-wrap" id="jdetail" {...H(f.jdetail)} />
          </div>
        </section>

        <section className="grid">
          <div className="panel">
            <h3>VERSEMENTS ENCAISSÉS <em>semaine affichée</em></h3>
            <ul className="trades" {...H(f.trades)} />
          </div>
          <div className="log">
            <h3>JOURNAL TEMPS RÉEL</h3>
            <pre>{f.journal}</pre>
          </div>
        </section>

      </div>
    </>
  );
}
'''


if __name__ == "__main__":
    sys.exit(main() or 0)
