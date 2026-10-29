"""Acesso às APIs públicas do Polymarket (sem autenticação).

- data-api: atividade, posições, PnL diário (v2/user-pnl) e leaderboard da carteira.
- clob: histórico de preços (midpoint) de cada outcome, usado para simular a cópia com atraso.
"""
import threading
import time

import requests

DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DAY = 86_400

_local = threading.local()


def _session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def get(url, params=None, attempts=5):
    for i in range(attempts):
        try:
            r = _session().get(url, params=params, timeout=30)
        except requests.RequestException:
            if i == attempts - 1:
                raise
            time.sleep(2 * 2 ** i)
            continue
        if (r.status_code == 429 or r.status_code >= 500) and i < attempts - 1:
            time.sleep(2 * 2 ** i)
            continue
        r.raise_for_status()
        return r.json()


def user_pnl(user):
    """Série diária acumulada desde o início da carteira: position_pnl, wallet_income, volume, trade_count..."""
    return get(f"{DATA}/v2/user-pnl", {"user": user, "interval": "all", "fidelity": "1d"})["data"]["points"]


def activity(user, since, max_records=20_000):
    """Atividade (TRADE, REDEEM, MERGE, SPLIT, CONVERSION...) desde `since` (segundos), da mais nova para a mais antiga.

    A API limita o offset a 5000, então pagina movendo o `end` para o registro mais antigo já lido.
    Devolve (registros em ordem cronológica, truncado?).
    """
    out, seen, end = [], set(), None
    while len(out) < max_records:
        offset, oldest, got = 0, None, 0
        while offset < 5000 and len(out) < max_records:
            p = {"user": user, "limit": 500, "offset": offset, "start": since,
                 "sortBy": "TIMESTAMP", "sortDirection": "DESC"}
            if end:
                p["end"] = end
            batch = get(f"{DATA}/activity", p)
            for a in batch:
                k = (a["transactionHash"], a["asset"], a["type"], a["side"], a["size"], a["timestamp"])
                if k not in seen:
                    seen.add(k)
                    out.append(a)
                    got += 1
            if batch:
                oldest = batch[-1]["timestamp"]
            if len(batch) < 500:
                return sorted(out, key=lambda a: a["timestamp"]), False
            offset += 500
        if not got or oldest is None:
            break
        end = oldest  # o próximo bloco começa no segundo mais antigo lido (repetidos são descartados)
    return sorted(out, key=lambda a: a["timestamp"]), True


def _paged(path, user, limit, cap, stop=None, **extra):
    out, offset = [], 0
    while offset < cap:
        batch = get(f"{DATA}/{path}", {"user": user, "limit": limit, "offset": offset, **extra})
        out += batch
        if len(batch) < limit or (stop and stop(batch)):
            break
        offset += limit
    return out


def positions(user, cap=5000):
    """Posições abertas, incluindo as já resolvidas e ainda não resgatadas (onde ficam as perdas)."""
    return _paged("positions", user, 500, cap, sizeThreshold=0)


def closed_positions(user, since=0, cap=3000):
    """Posições encerradas (vendidas ou resgatadas), da mais nova para a mais antiga, até `since`."""
    out = _paged("closed-positions", user, 50, cap, stop=lambda b: b[-1]["timestamp"] < since,
                 sortBy="TIMESTAMP", sortDirection="DESC")
    return [c for c in out if c["timestamp"] >= since]


def portfolio_value(user):
    v = get(f"{DATA}/value", {"user": user})
    return float(v[0]["value"]) if v else 0.0


def leaderboard(period="MONTH", order="PNL", n=100, category=None):
    out = []
    while len(out) < n:
        p = {"timePeriod": period, "orderBy": order, "limit": 50, "offset": len(out)}
        if category:
            p["category"] = category
        batch = get(f"{DATA}/v1/leaderboard", p)
        out += batch
        if len(batch) < 50:
            break
    return out[:n]


def price_history(asset, start, end):
    """Midpoint por minuto entre start e end (segundos). Lista de (t, p)."""
    h = get(f"{CLOB}/prices-history", {"market": asset, "startTs": int(start), "endTs": int(end), "fidelity": 1})
    return [(x["t"], float(x["p"])) for x in h.get("history", [])]
