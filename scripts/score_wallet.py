"""Score de copiabilidade (0-100) de carteiras do Polymarket.

No Hyperliquid o score imitava o copyScore da Hyperdash com um modelo treinado. O Polymarket não tem
um score público equivalente para servir de alvo, então aqui o score é uma soma ponderada e transparente
das mesmas famílias de métricas (risco/retorno, drawdown, acerto, estilo, frequência, histórico),
mais o que é próprio de mercados de previsão: quanto da margem sobrevive a uma cópia atrasada e
quanto do lucro depende de poucas apostas. Pesos e travas estão em docs/copyscore.md.

Uso: python3 score_wallet.py 0xENDERECO [...] [--delay 120] [--full]
"""
import argparse
import json
import math

from pm_metrics import WINDOW, metrics

W = WINDOW


def lin(x, lo, hi):
    """0 em `lo`, 1 em `hi`, linear entre os dois (aceita lo > hi para métricas onde menor é melhor)."""
    if x is None:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def log_lin(x, lo, hi):
    return lin(math.log10(x), math.log10(lo), math.log10(hi)) if x and x > 0 else 0.0


# (nome, peso, função da métrica -> 0..1)
COMPONENTS = [
    ("sharpe", 20, lambda m: lin(m.get(f"sharpe{W}d"), 0, 4)),
    ("sortino", 5, lambda m: lin(m.get(f"sortino{W}d"), 0, 6)),
    ("recuperacao", 10, lambda m: lin(m.get(f"recovery{W}d"), 0, 5)),
    ("acerto", 5, lambda m: lin(m.get("winrate"), 40, 75)),
    ("profitFactor", 5, lambda m: lin(m.get("profitFactor"), 1, 3)),
    ("diversificacao", 10, lambda m: lin(m.get("top3Share"), 0.9, 0.3) if m.get("top3Share") is not None else 0.0),
    ("copiaAtrasada", 20, lambda m: lin(m.get("copyCapture"), 0, 0.8)),
    ("permanencia", 10, lambda m: log_lin(m.get("holdMedianHoursWeighted") or m.get("holdMedianHours"), 0.5, 48)),
    ("frequencia", 5, lambda m: lin(m.get(f"tradesPerDay{W}d"), 200, 20)),
    ("historico", 5, lambda m: min(lin(m.get("ageDays"), 30, 180), lin(m.get("positions"), 10, 60))),
    ("recencia", 5, lambda m: lin(m.get("daysSinceLastTrade"), 30, 3) if m.get("daysSinceLastTrade") is not None else 0.0),
]


def caps(m):
    """Travas: (limite, motivo). O score final é o menor entre a soma ponderada e cada limite aplicável."""
    out = []
    if m.get("tag") == "mm":
        out.append((0, "market maker / alta frequência / renda de recompensas"))
    if (m.get(f"pnl{W}d") or 0) <= 0:
        out.append((20, f"PnL de {W} dias não é positivo"))
    if m.get("copyCapture") is not None and m["copyCapture"] <= 0:
        out.append((25, "a cópia atrasada perde toda a margem"))
    if m.get("daysSinceLastTrade") is None or m["daysSinceLastTrade"] > 30:
        out.append((30, "sem operar há mais de 30 dias"))
    if (m.get("positions") or 0) < 10:
        out.append((50, f"menos de 10 posições resolvidas em {W} dias"))
    if m.get("top3Share") is not None and m["top3Share"] > 1:
        out.append((40, "sem as 3 maiores apostas o resultado seria negativo"))
    return out


def score(m):
    parts = {name: round(weight * f(m), 1) for name, weight, f in COMPONENTS}
    raw = sum(parts.values())
    c = caps(m)
    final = min([raw] + [lim for lim, _ in c])
    return {"address": m["address"], "score": round(final, 1), "raw": round(raw, 1), "tag": m.get("tag"),
            "components": parts, "caps": [why for lim, why in c if lim < raw]}


def summary(m):
    keys = [f"pnl{W}d", f"sharpe{W}d", f"recovery{W}d", "winrate", "profitFactor", "top3Share", "positions",
            "margin", "slipPerDollar", "copyCapture", "holdMedianHoursWeighted", f"tradesPerDay{W}d",
            "incomeShare", "ageDays", "daysSinceLastTrade", "portfolioValue"]
    return {k: (round(m[k], 3) if isinstance(m.get(k), float) else m.get(k)) for k in keys}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("addresses", nargs="+")
    ap.add_argument("--delay", type=int, default=120, help="atraso da cópia em segundos")
    ap.add_argument("--full", action="store_true", help="imprime todas as métricas")
    a = ap.parse_args()
    for addr in a.addresses:
        m = metrics(addr.lower(), delay=a.delay)
        print(json.dumps({**score(m), "metrics": m if a.full else summary(m)}, indent=1, ensure_ascii=False))
