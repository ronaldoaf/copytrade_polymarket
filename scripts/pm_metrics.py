"""Calcula, a partir das APIs públicas do Polymarket, as métricas de copiabilidade de uma carteira.

Base de cálculo (adaptada de hl_metrics.py do Hyperliquid_CopyTrade):
- PnL diário = variação do `position_pnl` acumulado da série v2/user-pnl (realizado + não realizado,
  já descontadas as taxas). Depósitos e saques não entram, e a renda que não se copia
  (rebates de maker, recompensas de liquidez, indicação) fica de fora e é medida à parte.
- O Polymarket não expõe o valor da conta ao longo do tempo, então Sharpe e Sortino são calculados
  sobre o PnL diário em dólares (equivale ao retorno se o capital for estável) e o drawdown vira
  fator de recuperação: PnL líquido da janela / maior queda do PnL acumulado.
- Acerto, profit factor e concentração usam as posições resolvidas/encerradas na janela. As perdas
  resolvidas e não resgatadas ficam em /positions (redeemable), por isso as duas listas são somadas.
- Cópia simulada: nas maiores operações da janela, o copiador executa `delay` segundos depois ao
  midpoint daquele momento mais meio spread. O custo por dólar negociado é comparado à margem do
  trader (PnL / volume): `copyCapture` = fração da margem que sobra para quem copia.

Uso: python3 pm_metrics.py 0xENDERECO [0xOUTRO ...]   (imprime JSON por carteira)
"""
import json
import math
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import pm_api as api

DAY = api.DAY
WINDOWS = (30, 90, 365)
WINDOW = 90            # janela principal (dias) das métricas de operação e da simulação
DELAY = 120            # atraso da cópia em segundos
HALF_SPREAD = 0.005    # meio spread pago pelo copiador ao cruzar o livro (US$ por cota)
SIM_TRADES = 40        # operações simuladas (as maiores da janela)
MAX_ACTIVITY = 20_000  # teto de registros de atividade lidos por carteira


def risk_stats(pnl):
    """Sharpe, Sortino (desvio só dos dias negativos, como em hl_metrics.py) e drawdown em dólares."""
    n = len(pnl)
    out = {"sharpe": None, "sortino": None, "maxDrawdownUsd": None, "recovery": None}
    if n < 5:
        return out
    mean = sum(pnl) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in pnl) / (n - 1))
    neg = [x for x in pnl if x < 0]
    nm = sum(neg) / len(neg) if neg else 0
    down = math.sqrt(sum((x - nm) ** 2 for x in neg) / (len(neg) - 1)) if len(neg) > 1 else 0
    cum = peak = mdd = 0.0
    for x in pnl:
        cum += x
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    k = math.sqrt(365)
    out["sharpe"] = mean / sd * k if sd else None
    out["sortino"] = mean / down * k if down else None
    out["maxDrawdownUsd"] = mdd
    out["recovery"] = cum / mdd if mdd else (None if cum <= 0 else 99.0)
    return out


def series_metrics(points, now):
    """Métricas da série diária acumulada do v2/user-pnl."""
    pts = sorted(points, key=lambda p: p["timestamp"])
    out = {"firstDay": pts[0]["timestamp"] if pts else None,
           "ageDays": (now - pts[0]["timestamp"]) / DAY if pts else 0}
    if len(pts) < 2:
        return out
    for w in WINDOWS:
        cut = now - w * DAY
        win = [p for p in pts if p["timestamp"] >= cut]
        base = max((p for p in pts if p["timestamp"] < cut), key=lambda p: p["timestamp"], default=None)
        seq = ([base] if base else []) + win
        if len(seq) < 2:
            continue
        first, last = seq[0], seq[-1]
        if base is None:  # carteira mais nova que a janela: conta desde o zero
            first = {k: 0.0 for k in last}
            seq = [first] + seq
        d = lambda k: float(last.get(k) or 0) - float(first.get(k) or 0)
        daily = [float(b["position_pnl"]) - float(a["position_pnl"]) for a, b in zip(seq, seq[1:])]
        s = risk_stats(daily)
        days = max((last["timestamp"] - (seq[1]["timestamp"] if base is None else first["timestamp"])) / DAY, 1)
        out.update({
            f"pnl{w}d": d("position_pnl"),
            f"income{w}d": d("wallet_income"),
            f"volume{w}d": d("volume_usdc"),
            f"trades{w}d": d("trade_count"),
            f"tradesPerDay{w}d": d("trade_count") / min(days, w),
            f"activeDays{w}d": sum(1 for x in daily if abs(x) > 1e-6),
            f"sharpe{w}d": s["sharpe"], f"sortino{w}d": s["sortino"],
            f"maxDrawdownUsd{w}d": s["maxDrawdownUsd"], f"recovery{w}d": s["recovery"],
        })
    w = WINDOW
    pnl, inc, vol = out.get(f"pnl{w}d", 0), out.get(f"income{w}d", 0), out.get(f"volume{w}d", 0)
    out["margin"] = pnl / vol if vol else None  # PnL por dólar negociado
    out["incomeShare"] = inc / (max(pnl, 0) + inc) if inc > 0 else 0.0
    last = pts[-1]
    out["pnlAllTime"] = float(last["position_pnl"])
    out["volumeAllTime"] = float(last.get("volume_usdc") or 0)
    out["tradesAllTime"] = int(last.get("trade_count") or 0)
    return out


def resolved_positions(closed, open_, since, now):
    """Lista (timestamp, pnl, custo) das posições encerradas ou resolvidas desde `since`."""
    out = [(c["timestamp"], float(c["realizedPnl"]), float(c["avgPrice"]) * float(c["totalBought"]))
           for c in closed]
    for p in open_:
        if not p.get("redeemable"):
            continue
        try:
            t = int(time.mktime(time.strptime(p["endDate"][:10], "%Y-%m-%d")))
        except (KeyError, TypeError, ValueError):
            t = now
        if t >= since:
            out.append((t, float(p["cashPnl"]) + float(p.get("realizedPnl") or 0), float(p["initialValue"])))
    return out


def outcome_metrics(res):
    if not res:
        return {"positions": 0, "winrate": None, "profitFactor": None, "top3Share": None}
    pnl = [r[1] for r in res]
    wins = [x for x in pnl if x > 0]
    gross_win, gross_loss = sum(wins), -sum(x for x in pnl if x < 0)
    net = sum(pnl)
    top3 = sum(sorted(wins, reverse=True)[:3])
    return {
        "positions": len(res),
        "winrate": 100 * len(wins) / len(res),
        "profitFactor": gross_win / gross_loss if gross_loss else (99.0 if gross_win else None),
        # Quanto do lucro líquido vem das 3 melhores posições (> 1 = sem elas o saldo é negativo).
        "top3Share": top3 / net if net > 0 else None,
        "avgPositionUsd": sum(r[2] for r in res) / len(res),
    }


def holding_metrics(acts, since, now):
    """Tempo de permanência: da primeira compra até a posição zerar (venda, merge ou resgate).

    Posições ainda abertas entram com o tempo decorrido até agora (é um piso: o trader ainda segura).
    """
    shares, opened, cost = defaultdict(float), {}, defaultdict(float)
    cond_assets = defaultdict(set)
    holds = []  # (horas, custo)

    def close(asset, t):
        if asset in opened:
            if opened[asset] >= since:
                holds.append(((t - opened[asset]) / 3600, cost[asset]))
            del opened[asset]
        shares[asset] = 0.0
        cost[asset] = 0.0

    for a in acts:
        t, typ, asset, size = a["timestamp"], a["type"], a.get("asset") or "", float(a.get("size") or 0)
        if typ == "TRADE" and asset:
            cond_assets[a["conditionId"]].add(asset)
            if a["side"] == "BUY":
                if shares[asset] <= 1e-6:
                    opened[asset] = t
                shares[asset] += size
                cost[asset] += float(a.get("usdcSize") or 0)
            elif a["side"] == "SELL":
                shares[asset] -= size
                if shares[asset] <= 1e-6:
                    close(asset, t)
        elif typ == "REDEEM":
            for x in list(cond_assets.get(a["conditionId"], ())):
                close(x, t)
        elif typ == "MERGE":
            for x in list(cond_assets.get(a["conditionId"], ())):
                shares[x] -= size
                if shares[x] <= 1e-6:
                    close(x, t)
    closed = len(holds)
    for asset, t in opened.items():
        if t >= since and shares[asset] > 1e-6:
            holds.append(((now - t) / 3600, cost[asset]))
    if not holds:
        return {"holdMedianHours": None, "holdMedianHoursWeighted": None, "closedHolds": 0, "openHolds": 0}
    holds.sort()
    total = sum(c for _, c in holds) or 1
    acc, wmed = 0.0, holds[-1][0]
    for h, c in holds:
        acc += c
        if acc >= total / 2:
            wmed = h
            break
    return {"holdMedianHours": holds[len(holds) // 2][0], "holdMedianHoursWeighted": wmed,
            "closedHolds": closed, "openHolds": len(holds) - closed}


def price_profile(trades):
    buys = [(float(a["price"]), float(a.get("usdcSize") or 0)) for a in trades if a["side"] == "BUY"]
    tot = sum(u for _, u in buys)
    if not tot:
        return {"buyShareAbove95": None, "buyShareBelow05": None, "avgBuyPrice": None}
    return {"buyShareAbove95": sum(u for p, u in buys if p >= 0.95) / tot,
            "buyShareBelow05": sum(u for p, u in buys if p <= 0.05) / tot,
            "avgBuyPrice": sum(p * u for p, u in buys) / tot}


def _mid_at(hist, t, after=True):
    if not hist:
        return None
    if after:
        for ts, p in hist:
            if ts >= t:
                return p
        return hist[-1][1] if hist[-1][0] >= t - 300 else None
    best = None
    for ts, p in hist:
        if ts <= t:
            best = p
    return best


def simulate_copy(trades, delay=DELAY, half_spread=HALF_SPREAD, n=SIM_TRADES):
    """Custo, por dólar, de repetir as maiores operações `delay` segundos depois, cruzando o spread."""
    picked, keys = [], set()
    for a in sorted(trades, key=lambda a: -float(a.get("usdcSize") or 0)):
        k = (a["asset"], a["timestamp"] // 600)
        if k in keys:
            continue
        keys.add(k)
        picked.append(a)
        if len(picked) >= n:
            break

    def one(a):
        t = a["timestamp"]
        try:
            h = api.price_history(a["asset"], t - 600, t + delay + 900)
        except Exception:
            return None
        after = _mid_at(h, t + delay)
        if after is None:
            return None
        px, sz = float(a["price"]), float(a["size"])
        if a["side"] == "BUY":
            cost = min(after + half_spread, 0.999) - px
        else:
            cost = px - max(after - half_spread, 0.001)
        return sz * cost, sz * px, after - px if a["side"] == "BUY" else px - after

    with ThreadPoolExecutor(8) as ex:
        res = [r for r in ex.map(one, picked) if r]
    if not res:
        return {"simTrades": 0, "slipPerDollar": None, "avgAdverseMoveCents": None}
    notional = sum(r[1] for r in res) or 1
    return {"simTrades": len(res),
            "slipPerDollar": sum(r[0] for r in res) / notional,
            # Movimento do midpoint contra o copiador entre a execução do trader e a cópia (sem spread).
            "avgAdverseMoveCents": 100 * sum(r[2] * r[1] for r in res) / notional}


def style(m):
    if (m.get(f"tradesPerDay{WINDOW}d") or 0) > 200 or (m.get("incomeShare") or 0) > 0.5:
        return "mm"  # market maker, bot de alta frequência ou caçador de recompensas
    h = m.get("holdMedianHoursWeighted") or m.get("holdMedianHours")
    if h is None:
        return None
    if h < 1:
        return "scalp"
    if h < 24:
        return "intraday"
    if h < 24 * 14:
        return "swing"
    return "position"


def metrics(user, now=None, delay=DELAY):
    now = now or int(time.time())
    since = now - WINDOW * DAY
    out = {"address": user, "computedAt": now}
    out.update(series_metrics(api.user_pnl(user), now))

    # Carteiras de altíssima frequência são descartadas pelo score; não vale baixar toda a atividade.
    heavy = (out.get(f"tradesPerDay{WINDOW}d") or 0) > 200
    acts, truncated = api.activity(user, since, max_records=2000 if heavy else MAX_ACTIVITY)
    trades = [a for a in acts if a["type"] == "TRADE" and a.get("asset")]
    out["activityRecords"], out["activityTruncated"] = len(acts), truncated
    out["lastTradeAt"] = trades[-1]["timestamp"] if trades else None
    out["daysSinceLastTrade"] = (now - trades[-1]["timestamp"]) / DAY if trades else None
    out.update(holding_metrics(acts, since if not truncated else acts[0]["timestamp"], now))
    out.update(price_profile(trades))

    res = resolved_positions(api.closed_positions(user, since), api.positions(user), since, now)
    out.update(outcome_metrics(res))

    out.update(simulate_copy(trades, delay=delay))
    m, slip = out.get("margin"), out.get("slipPerDollar")
    out["copyMargin"] = m - slip if m is not None and slip is not None else None
    out["copyCapture"] = (m - slip) / m if m and m > 0 and slip is not None else None
    out["portfolioValue"] = api.portfolio_value(user)
    out["tag"] = style(out)
    return out


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(json.dumps(metrics(a.lower()), indent=1))
