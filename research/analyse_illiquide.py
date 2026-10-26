"""Analyse de la recherche illiquide. Etape 3 de la grille TheMacroTape.

Ce que ce fichier refuse de faire
---------------------------------
Il ne classe pas les combinaisons pour en sortir la meilleure et l'annoncer. C'est
exactement le geste que la note "backtest-d-une-strategie-sans-avantage" designe
comme la fabrique d'une strategie qui n'existe que dans le passe.

Il rapporte d'abord la DISTRIBUTION ENTIERE, gagnantes et perdantes ensemble, puis
seulement ensuite ce que devient une selection faite sur l'entrainement seul. Si la
mediane des combinaisons perd hors echantillon, alors une gagnante n'est qu'un tirage
dans une queue, et aucun classement ne peut le corriger.

Les trois questions, dans l'ordre
---------------------------------
1. Ou se situe la distribution ? Si elle est centree sous 1, l'univers n'a pas d'edge
   a offrir a cette famille d'idees, point final.
2. Le passe predit-il le futur ? La correlation pf_in / pf_out mesure si le classement
   porte de l'information. Zero signifie que choisir sur l'entrainement ne sert a rien.
3. Une selection faite sur l'entrainement bat-elle un tirage au hasard de meme taille ?
   C'est la seule facon de distinguer une competence d'une chance.

Usage
-----
    python research/analyse_illiquide.py
    python research/analyse_illiquide.py --top 10
"""

import argparse
import json
import pathlib
import sys

import numpy as np

RACINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

FICHIER = RACINE / "data" / "research" / "illiquide_resultats.jsonl"

# Barres de la grille TheMacroTape, posees avant de regarder les resultats.
MINI_TRADES = 1000
PF_MINI = 1.15


def lire():
    lignes = []
    for ligne in FICHIER.read_text(encoding="utf-8").splitlines():
        if not ligne.strip():
            continue
        try:
            d = json.loads(ligne)
        except Exception:
            continue
        if d["entrainement"]["pf"] is None or d["test"]["pf"] is None:
            continue
        lignes.append(d)
    return lignes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    if not FICHIER.exists():
        print("   aucun resultat : lancer research/recherche_illiquide.py d'abord")
        return
    d = lire()
    if not d:
        print("   fichier vide ou illisible")
        return

    pi = np.array([x["entrainement"]["pf"] for x in d])
    po = np.array([x["test"]["pf"] for x in d])
    ti = np.array([x["entrainement"]["trades"] for x in d])
    to = np.array([x["test"]["trades"] for x in d])

    print("   ANALYSE DE LA RECHERCHE ILLIQUIDE")
    print("   %d combinaisons evaluees. C'est le denominateur du biais de selection."
          % len(d))
    print()

    print("   1. OU SE SITUE LA DISTRIBUTION ENTIERE ?")
    print("   %-22s %9s %9s %9s %9s %9s"
          % ("", "p10", "mediane", "moyenne", "p90", "max"))
    for nom, v in (("profit factor IN", pi), ("profit factor OUT", po)):
        print("   %-22s %9.3f %9.3f %9.3f %9.3f %9.3f"
              % (nom, np.percentile(v, 10), np.median(v), v.mean(),
                 np.percentile(v, 90), v.max()))
    print()
    print("   combinaisons rentables hors echantillon (pf > 1) : %d sur %d  (%.1f %%)"
          % (int((po > 1).sum()), len(po), 100 * (po > 1).mean()))
    print("   combinaisons au-dessus de la barre pf %.2f        : %d  (%.1f %%)"
          % (PF_MINI, int((po >= PF_MINI).sum()), 100 * (po >= PF_MINI).mean()))
    if np.median(po) < 1.0:
        print()
        print("   La mediane perd hors echantillon. Toute gagnante trouvee ici est")
        print("   donc un tirage dans une queue, pas la preuve d'un edge.")
    print()

    print("   2. LE PASSE PREDIT-IL LE FUTUR ?")
    ok = np.isfinite(pi) & np.isfinite(po) & (pi < 5) & (po < 5)
    if ok.sum() > 10:
        c = float(np.corrcoef(pi[ok], po[ok])[0, 1])
        print("   correlation pf_in / pf_out : %.4f  sur %d combinaisons" % (c, ok.sum()))
        q = np.percentile(pi[ok], [20, 40, 60, 80])
        b = np.digitize(pi[ok], q)
        for i in range(5):
            m = b == i
            if m.sum():
                print("      quintile %d de pf_in (n=%3d) : pf_in %.3f -> pf_out %.3f"
                      % (i + 1, m.sum(), pi[ok][m].mean(), po[ok][m].mean()))
        if abs(c) < 0.15:
            print("   Correlation nulle : choisir sur l'entrainement n'apporte RIEN.")
    print()

    print("   3. UNE SELECTION SUR L'ENTRAINEMENT BAT-ELLE LE HASARD ?")
    n = min(args.top, len(d))
    ordre = np.argsort(-pi)
    choisies = ordre[:n]
    po_choisies = po[choisies].mean()
    rng = np.random.default_rng(11)
    tirages = np.array([po[rng.choice(len(po), size=n, replace=False)].mean()
                        for _ in range(5000)])
    z = (po_choisies - tirages.mean()) / tirages.std() if tirages.std() > 0 else 0.0
    print("   les %d meilleures a l'entrainement font pf_out moyen %.3f" % (n, po_choisies))
    print("   un tirage au hasard de %d fait %.3f en moyenne (ecart-type %.3f)"
          % (n, tirages.mean(), tirages.std()))
    print("   ecart : %+.2f sigma, percentile %.1f %%"
          % (z, 100 * (tirages < po_choisies).mean()))
    if z < 1.5:
        print("   La selection ne fait pas mieux que le hasard.")
    print()

    print("   4. LES %d MEILLEURES A L'ENTRAINEMENT, ET CE QU'ELLES VALENT AU TEST" % n)
    print("   %-44s %7s %7s %7s %7s"
          % ("combinaison", "tr in", "pf in", "tr out", "pf out"))
    print("   " + "-" * 78)
    for i in choisies:
        x = d[i]
        print("   %-44s %7d %7.3f %7d %7.3f"
              % (x["params"][:44], x["entrainement"]["trades"], pi[i],
                 x["test"]["trades"], po[i]))
    print()

    print("   5. LA GRILLE THEMACROTAPE")
    survivantes = [i for i in range(len(d))
                   if ti[i] + to[i] > MINI_TRADES and po[i] >= PF_MINI]
    print("   plus de %d trades ET pf hors echantillon >= %.2f : %d combinaisons"
          % (MINI_TRADES, PF_MINI, len(survivantes)))
    if survivantes:
        print("   %-44s %7s %7s %7s"
              % ("combinaison", "trades", "pf in", "pf out"))
        print("   " + "-" * 70)
        for i in sorted(survivantes, key=lambda k: -po[k])[:15]:
            x = d[i]
            print("   %-44s %7d %7.3f %7.3f"
                  % (x["params"][:44], ti[i] + to[i], pi[i], po[i]))
        print()
        print("   Ces survivantes doivent encore passer le Monte Carlo et le test nul.")
        print("   Aucune conclusion avant.")
    else:
        print("   Aucune combinaison ne franchit la grille. L'univers illiquide ne")
        print("   rend rien a cette bibliotheque de familles.")


if __name__ == "__main__":
    main()
