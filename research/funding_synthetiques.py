# research/funding_synthetiques.py
"""
Test HORS ECHANTILLON de l'hypothese des produits synthetiques.

L'hypothese, formulee apres coup et donc suspecte
--------------------------------------------------
funding_clean.py, avec fenetres disjointes, a tue le signal moyen (t passe de
1,95 a 0,49). Mais les rares marches restes significatifs formaient un groupe
coherent, tous de meme signe : XAUUSDT, XAGUSDT, CLUSDT. De l'or, de l'argent,
du petrole tokenises. Pas de la crypto.

Explication proposee : sur BTC le financement est discipline par les
arbitragistes, puisque le spot existe et que le cash-and-carry est trivial. Toute
information contenue dans le financement y est donc deja arbitree. Sur de l'or
tokenise, on ne peut pas livrer de metal physique contre le perp : le canal
d'arbitrage n'existe pas, et le financement reste une mesure brute du
desequilibre de demande.

Le probleme, c'est que cette histoire a ete inventee APRES avoir vu les donnees.
Elle n'a aucune valeur tant qu'elle n'a pas produit une prediction verifiable.

La prediction
-------------
Si l'explication est bonne, le meme signal doit apparaitre sur les ACTIONS
tokenisees, qui partagent exactement la meme propriete : produit synthetique,
aucun canal d'arbitrage spot sur l'exchange.

Ces marches ne sont pas choisis par moi, ils sont designes par l'hypothese. Je
n'ai pas regarde leurs resultats avant d'ecrire ce script. C'est donc un vrai
test hors echantillon.

  - le signal est present  -> l'histoire tient, la piste continue
  - le signal est absent   -> c'etait du bruit, j'abandonne

Usage:
    python research/funding_synthetiques.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                              # noqa: E402

from research.funding_signal import _get, FAPI                  # noqa: E402
from research.funding_clean import analyser, HORIZONS_H         # noqa: E402

# Racines d'actions et d'ETF connues. Sert a separer les perps synthetiques
# des perps crypto sans avoir a deviner au cas par cas.
ACTIONS = (
    "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOGL", "GOOG", "NFLX",
    "AMD", "INTC", "MU", "SOXL", "SOXX", "SPCX", "SKHYNIX", "SNDK", "COIN",
    "MSTR", "HOOD", "PLTR", "SPY", "QQQ", "TSM", "BABA", "SMCI", "ARM",
    "ORCL", "CRCL", "IBIT", "GME", "AMC", "BRK", "JPM", "V", "MA",
)
MATIERES = ("XAU", "XAG", "XPT", "XPD", "CL", "BZ", "NG", "HG", "ZC", "ZS", "ZW")
CRYPTO_REF = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")


def classer(sym):
    base = sym[:-4] if sym.endswith("USDT") else sym
    base = base.lstrip("1234567890")
    if any(base.startswith(a) for a in ACTIONS):
        return "action"
    if any(base.startswith(m) for m in MATIERES):
        return "matiere"
    return "crypto"


def tous_les_perps():
    data = _get(f"{FAPI}/fapi/v1/ticker/24hr")
    out = []
    for d in data:
        s = d["symbol"]
        if not s.endswith("USDT"):
            continue
        try:
            vol = float(d["quoteVolume"])
        except Exception:
            continue
        out.append((s, vol, classer(s)))
    return out


def main():
    perps = tous_les_perps()
    actions = sorted([p for p in perps if p[2] == "action"],
                     key=lambda x: -x[1])[:14]
    matieres = sorted([p for p in perps if p[2] == "matiere"],
                      key=lambda x: -x[1])[:8]
    crypto = [p for p in perps if p[0] in CRYPTO_REF]

    print("=" * 96)
    print("  TEST HORS ECHANTILLON : le signal existe-t-il sur les ACTIONS tokenisees ?")
    print("=" * 96)
    print("  actions designees par l'hypothese : "
          + ", ".join(s for s, _, _ in actions))
    print("  matieres (groupe ou le signal a ete trouve) : "
          + ", ".join(s for s, _, _ in matieres))
    print("  crypto (groupe temoin, signal attendu ABSENT) : "
          + ", ".join(s for s, _, _ in crypto))
    print("=" * 96, flush=True)

    resultats = {"action": [], "matiere": [], "crypto": []}
    for groupe, lot in (("action", actions), ("matiere", matieres), ("crypto", crypto)):
        for sym, _, _ in lot:
            try:
                r = analyser(sym, 24)
            except Exception as e:
                print("  %-14s %-8s echec %s" % (sym, groupe, str(e)[:36]), flush=True)
                continue
            if r is None:
                print("  %-14s %-8s historique insuffisant" % (sym, groupe), flush=True)
                continue
            r["groupe"] = groupe
            resultats[groupe].append(r)
            d8 = r.get("h8", {})
            print("  %-14s %-8s pas %4.1fh  8h: n=%4d  t=%+6.2f"
                  % (sym, groupe, r["intervalle_h"], d8.get("n_disjoint", 0),
                     d8.get("t_fund_avec_prix", float("nan"))), flush=True)

    (PROJECT_ROOT / "data" / "funding" / "synthetiques.json").write_text(
        json.dumps(resultats, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print("  VERDICT PAR GROUPE  (t du financement, controle par le prix)")
    print("=" * 96)
    print("  %-10s %-8s %8s %10s %12s %14s %12s"
          % ("horizon", "groupe", "marches", "t moyen", "|t|>2", "% |t|>2", "signe +"))
    print("  " + "-" * 84)
    for H in HORIZONS_H:
        k = "h%d" % H
        for groupe in ("action", "matiere", "crypto"):
            lot = [r[k] for r in resultats[groupe] if k in r]
            if not lot:
                continue
            ts = [x["t_fund_avec_prix"] for x in lot]
            forts = [t for t in ts if abs(t) > 2]
            pos = sum(1 for t in forts if t > 0)
            print("  %-10s %-8s %8d %10.2f %12d %13.0f%% %10s"
                  % ("%dh" % H, groupe, len(ts), float(np.mean(ts)), len(forts),
                     len(forts) / len(ts) * 100,
                     "%d/%d" % (pos, len(forts)) if forts else "-"))
        print()

    print("  Attendu par hasard : environ 5% de marches a |t|>2, signes repartis")
    print("  au hasard. Pour valider l'hypothese il faut que le groupe ACTION")
    print("  ressemble au groupe MATIERE et se distingue nettement du groupe CRYPTO.")


if __name__ == "__main__":
    main()
