"""Pontua as carteiras do leaderboard do Polymarket e grava um ranking por copiabilidade.

Uso: python3 scan_leaderboard.py [--period MONTH] [--order PNL] [--n 100] [--category ...] [--out ranking]
Gera ranking.csv (uma linha por carteira, ordenado pelo score) e ranking.json (métricas completas).
"""
import argparse
import csv
import json
import sys

import pm_api as api
from pm_metrics import metrics
from score_wallet import score, summary

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="MONTH", help="DAY, WEEK, MONTH ou ALL")
    ap.add_argument("--order", default="PNL", help="PNL ou VOL")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--category", default=None, help="ex.: POLITICS, SPORTS, CRYPTO")
    ap.add_argument("--delay", type=int, default=120)
    ap.add_argument("--out", default="ranking")
    a = ap.parse_args()

    board = api.leaderboard(a.period, a.order, a.n, a.category)
    rows, full = [], []
    for i, r in enumerate(board, 1):
        addr = r["proxyWallet"].lower()
        try:
            m = metrics(addr, delay=a.delay)
        except Exception as e:  # carteira com erro na API: segue para a próxima
            print(f"{i}/{len(board)} {addr} erro: {e}", file=sys.stderr, flush=True)
            continue
        s = score(m)
        full.append({"leaderboard": r, "score": s, "metrics": m})
        rows.append({"address": addr, "userName": r.get("userName"), "rank": r.get("rank"),
                     "score": s["score"], "raw": s["raw"], "tag": s["tag"], "caps": "; ".join(s["caps"]),
                     **s["components"], **summary(m)})
        print(f"{i}/{len(board)} {addr} {r.get('userName')}: {s['score']} ({s['tag']})", file=sys.stderr, flush=True)

    rows.sort(key=lambda x: -x["score"])
    if rows:
        with open(a.out + ".csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    with open(a.out + ".json", "w") as f:
        json.dump(full, f)
    for x in rows[:20]:
        print(f"{x['score']:5.1f}  {x['tag'] or '-':9} {x['address']}  {x['userName']}")
