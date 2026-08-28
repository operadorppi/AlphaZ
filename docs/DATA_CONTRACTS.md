# Contratos de Dados

## motor_web -> motor_rt_alphaz
Callback COM: {ativo: str, tipo: str, dados: dict}

## motor_rt_alphaz -> features_lib
Trade: {tms: int, preco: float, qtd: int, lado: str, corretora: str}
Saida: {aggr_imb, cvd_total, delta_preco, vol_total, n, price_eff, ...}

## features_lib -> motor_rt_alphaz (BookLevel)
Entrada: bid_levels, ask_levels [(preco, vol), ...]
Saida: spread, mid, microprice, imbalance, ofi, kyle_lambda, vpin, ...

## scorer -> motor_rt_alphaz
Saida: probabilidade (0-1), sinal (-1/0/+1), confianca (0-1)

## motor_rt_alphaz -> Dashboard
GET /api/features -> {features, score, confianca, sinal, posicao}
