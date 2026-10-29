# Score de copiabilidade (Polymarket)

Nota de 0 a 100 que diz o quanto vale a pena copiar uma carteira do Polymarket. Segue a mesma ideia do
Hyperliquid_CopyTrade (risco/retorno, drawdown, acerto, estilo, frequência e histórico), com duas mudanças:

1. No Hyperliquid o score imitava o copyScore da Hyperdash com um modelo treinado. O Polymarket não tem um score
   público para servir de alvo, então aqui a nota é uma soma ponderada e transparente. Os pesos são um ponto de
   partida; o próximo passo é calibrá-los pelo resultado real de copiar (ver "Próximos passos").
2. Entram métricas próprias de mercados de previsão: quanto da margem sobra para quem copia com atraso e quanto do
   lucro depende de poucas apostas.

## Dados (APIs públicas, sem autenticação)

| Endpoint | Uso |
|---|---|
| `data-api /v2/user-pnl?interval=all&fidelity=1d` | Série diária acumulada desde o início da carteira: `position_pnl` (realizado + não realizado, já sem taxas), `wallet_income` (rebates de maker, recompensas, indicação), `volume_usdc`, `trade_count`. |
| `data-api /activity` | Compras, vendas, resgates e merges da janela. O offset vai até 5000, então a paginação move o `end`. |
| `data-api /closed-positions` e `/positions` | Resultado por posição. As perdas resolvidas e não resgatadas ficam em `/positions` com `redeemable = true`, por isso as duas listas são somadas. |
| `clob /prices-history?fidelity=1` | Midpoint por minuto de cada outcome, para simular a cópia atrasada. |
| `data-api /v1/leaderboard` | Lista de candidatas (50 por página). |
| `data-api /value` | Valor atual das posições. |

A série do `v2/user-pnl` bate com o PnL do leaderboard e começa no primeiro dia de atividade da carteira.

## Métricas (janela principal de 90 dias)

- **PnL diário** = variação do `position_pnl`. Depósitos e saques não contam, e a renda que não se copia
  (rebates, recompensas) fica de fora.
- **Sharpe e Sortino** sobre o PnL diário em dólares, anualizados por √365. O Polymarket não expõe o valor da conta
  ao longo do tempo; com capital estável, é o mesmo que usar o retorno. O Sortino usa o desvio só dos dias negativos,
  como no `hl_metrics.py`.
- **Fator de recuperação** = PnL da janela / maior queda do PnL acumulado (substitui o drawdown em %).
- **Acerto e profit factor** sobre as posições encerradas ou resolvidas na janela.
- **Concentração (`top3Share`)** = lucro das 3 melhores posições / lucro líquido. Acima de 1, sem elas o saldo seria negativo.
- **Cópia atrasada**: nas 40 maiores operações da janela, o copiador executa 120 s depois (`--delay`) ao midpoint
  daquele minuto mais meio spread (0,5 centavo). `slipPerDollar` é o custo por dólar negociado; `margin` é o PnL do
  trader por dólar negociado; `copyCapture = (margin - slipPerDollar) / margin` é a fração da margem que sobra para quem copia.
- **Permanência**: mediana, ponderada pelo valor investido, do tempo entre a primeira compra e o zeramento da posição
  (venda, merge ou resgate). Posições ainda abertas entram com o tempo até agora.
- **Frequência**: `trade_count` por dia.
- **incomeShare**: renda que não se copia / (PnL das posições + essa renda).

## Estilo (`tag`)

| tag | regra |
|---|---|
| `mm` | mais de 200 execuções por dia ou mais da metade do ganho vindo de rebates e recompensas (market maker, bot ou caçador de recompensas) |
| `scalp` | permanência mediana < 1 h |
| `intraday` | < 24 h |
| `swing` | < 14 dias |
| `position` | 14 dias ou mais |

## Composição da nota

| Componente | Peso | 0 pontos | Nota cheia |
|---|---|---|---|
| Sharpe 90d | 20 | ≤ 0 | ≥ 4 |
| Sortino 90d | 5 | ≤ 0 | ≥ 6 |
| Fator de recuperação 90d | 10 | ≤ 0 | ≥ 5 |
| Acerto (posições) | 5 | ≤ 40% | ≥ 75% |
| Profit factor | 5 | ≤ 1 | ≥ 3 |
| Diversificação (`top3Share`) | 10 | ≥ 0,9 | ≤ 0,3 |
| Cópia atrasada (`copyCapture`) | 20 | ≤ 0 | ≥ 0,8 |
| Permanência (escala log) | 10 | ≤ 30 min | ≥ 48 h |
| Frequência | 5 | ≥ 200/dia | ≤ 20/dia |
| Histórico (idade e nº de posições) | 5 | < 30 dias ou < 10 posições | ≥ 180 dias e ≥ 60 posições |
| Recência | 5 | sem operar há 30 dias | operou nos últimos 3 dias |

## Travas (a nota fica no menor valor aplicável)

| Limite | Quando |
|---|---|
| 0 | `tag = mm` (equivale ao `algo` do Hyperliquid, que zerava o score) |
| 20 | PnL de 90 dias ≤ 0 |
| 25 | a cópia atrasada perde toda a margem (`copyCapture ≤ 0`) |
| 30 | sem operar há mais de 30 dias |
| 40 | sem as 3 maiores apostas o resultado seria negativo |
| 50 | menos de 10 posições resolvidas em 90 dias (amostra pequena demais) |

## Limitações

- O midpoint por minuto não mostra a profundidade do livro: copiar uma ordem grande custa mais que meio spread.
- A simulação usa as maiores operações; numa carteira com muitas ordens pequenas a amostra é menos representativa.
- Carteiras com mais de 200 execuções por dia só têm 2000 registros de atividade lidos (já saem com nota 0).
- Conversões de mercados neg-risk e splits não entram no cálculo da permanência.

## Próximos passos

- Guardar as métricas diariamente (como o `score-samples.js` do Hyperliquid) e medir o resultado real de copiar as
  carteiras com nota alta; com isso, calibrar os pesos por regressão em vez de fixá-los à mão.
- Simular a cópia com o livro de ofertas (`clob /book`) para carteiras que operam tamanhos grandes.
