# Copytrade Polymarket

Ferramentas para encontrar carteiras do Polymarket que valham a pena copiar. Primeira peça: o analisador de carteira
com o **score de copiabilidade** (0-100), adaptado do que foi feito no
[Hyperliquid_CopyTrade](https://github.com/ronaldoaf/Hyperliquid_CopyTrade). Fórmula, dados e limitações em
[`docs/copyscore.md`](docs/copyscore.md).

## Uso

```bash
pip install -r requirements.txt
cd scripts

# Score de uma ou mais carteiras (proxy wallet do Polymarket)
python3 score_wallet.py 0xENDERECO [0xOUTRO ...] [--delay 120] [--full]

# Só as métricas, em JSON
python3 pm_metrics.py 0xENDERECO

# Ranking das carteiras do leaderboard (gera ranking.csv e ranking.json)
python3 scan_leaderboard.py --period MONTH --n 100 --out ranking
```

## Arquivos

- `scripts/pm_api.py`: acesso às APIs públicas (data-api e clob), com repetição em erro 429/5xx.
- `scripts/pm_metrics.py`: métricas de risco/retorno, posições, permanência e a simulação de cópia atrasada.
- `scripts/score_wallet.py`: componentes, pesos e travas do score.
- `scripts/scan_leaderboard.py`: pontua o leaderboard inteiro e ordena pela nota.
