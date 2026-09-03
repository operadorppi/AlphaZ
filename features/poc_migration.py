# -*- coding: utf-8 -*-
"""
features/poc_migration.py — Migracao do POC TEMPORAL (P0-A28, v15.23).

ANTES (v9.40): update(preco, poc) sem timestamp — velocity = delta entre
duas atualizacoes consecutivas, independente do tempo decorrido. Um POC que
andou 5 pontos em 10ms ou em 5 segundos produzia a MESMA "velocidade" 5.0 —
a feature poc_velocity media o tamanho do pulo, nao a velocidade.

AGORA: mesmo contrato temporal das fases A20/A21/A25 — grid do master clock
de 100ms (mesma borda do GeradorJanelas / features/volatility.py):

    - cada trade amostra (ts_ms, preco, poc_ate_t), com POC causal ate t;
    - cada corte de 100ms fecha com o POC do ULTIMO trade com ts
      ESTRITAMENTE menor que o corte (o trade que dispara o avanco entra no
      corte SEGUINTE);
    - cortes intermediarios (sem trade novo) sao fechados forward-filled:
      POC constante -> delta 0 -> a EWMA decai. Isso e identico ao batch
      (features_expansao roda sobre o dataset_100ms forward-filled), entao
      live e batch medem a mesma grandeza;
    - poc_delta     = delta de POC da ultima linha de 100ms fechada
                      (paridade: diff() do batch por linha);
    - poc_velocity  = EWMA causal alpha=0.1 das deltas por linha
                      (paridade: diff().ewm(alpha=0.1).mean() do batch);
                      unidade: pontos de POC por linha de 100ms;
    - poc_direction = sinal da delta da linha fechada (paridade:
                      np.sign(diff) do batch).

Rollover de sessao interno por dia de Brasilia (padrao P0-A27/v15.22): o
estado do dia anterior e descartado na 1a atualizacao do dia novo ANTES de
acumular — o POC da virada nunca contamina e o 1o trade do dia novo entra no
perfil da sessao nova. reset_diario() preservado (contrato dos auditores).
"""

from bisect import bisect_left

try:
    from core.temporal import dia_de_ts_br as _dia_de_ts_br
except Exception:  # pragma: no cover - fallback defensivo (mesmo contrato)
    def _dia_de_ts_br(ts_ms):
        return (int(ts_ms) - 3 * 3600 * 1000) // 86_400_000

_GRID_MS = 100
_ALPHA_VEL = 0.1          # mesmo alpha do batch (features_expansao)
_MAX_LINHAS = 200_000     # cap defensivo de memoria


class PocMigrationTracker:
    """Rastreia evolucao do POC no grid temporal do master clock (100ms)."""

    def __init__(self):
        self._dia = None            # dia BRT corrente (rollover interno)
        self._amostras_ts = []      # ts_ms de cada amostra (ordenado)
        self._amostras_poc = []     # POC correspondente
        self._amostras_preco = []   # preco correspondente (p/ dist_preco_poc)
        self._cortes_poc = []       # POC de fechamento por linha de 100ms
        self._proximo_corte = None  # proximo corte de grid a processar
        self._vel = 0.0             # EWMA causal das deltas por linha

    # ------------------------------------------------------------------
    def update(self, ts_ms, preco, poc_ate_t):
        """Registra (ts_ms, preco, POC causal ate ts_ms) e avanca os cortes
        de 100ms pendentes (intermediarios inclusos, forward-filled)."""
        if ts_ms is None or preco is None or preco <= 0:
            return
        if poc_ate_t is None or poc_ate_t <= 0:
            return
        ts_ms = int(ts_ms)
        poc = float(poc_ate_t)

        # Rollover interno por dia de Brasilia (padrao P0-A27): descarta o
        # estado do dia anterior ANTES de acumular o 1o evento do dia novo.
        dia = _dia_de_ts_br(ts_ms)
        if self._dia is None:
            self._dia = dia
        elif dia != self._dia:
            self._reset_estado()
            self._dia = dia

        # Buffer ordenado (eventos fora de ordem nao corrompem)
        if self._amostras_ts and ts_ms >= self._amostras_ts[-1]:
            self._amostras_ts.append(ts_ms)
            self._amostras_poc.append(poc)
            self._amostras_preco.append(float(preco))
        else:
            i = bisect_left(self._amostras_ts, ts_ms)
            self._amostras_ts.insert(i, ts_ms)
            self._amostras_poc.insert(i, poc)
            self._amostras_preco.insert(i, float(preco))

        if self._proximo_corte is None:
            # 1o corte: a PRIMEIRA borda de grid APOS o 1o evento (mesmo do
            # GeradorJanelas / VolatilityTracker).
            self._proximo_corte = (ts_ms // _GRID_MS + 1) * _GRID_MS
            return

        while self._proximo_corte <= ts_ms:
            p = self._poc_antes_de(self._proximo_corte)
            if p is not None:
                self._fechar_corte(self._proximo_corte, p)
            self._proximo_corte += _GRID_MS

    def _poc_antes_de(self, corte_ts):
        """POC do ultimo trade com ts ESTRITAMENTE menor que corte_ts."""
        if not self._amostras_ts:
            return None
        i = bisect_left(self._amostras_ts, corte_ts) - 1
        if i < 0:
            return None
        return self._amostras_poc[i]

    def _fechar_corte(self, corte_ts, poc):
        """Fecha 1 linha de 100ms: delta por linha -> EWMA de velocidade."""
        self._cortes_poc.append(float(poc))
        excesso = len(self._cortes_poc) - _MAX_LINHAS
        if excesso > 0:
            del self._cortes_poc[:excesso]

        if len(self._cortes_poc) >= 2:
            delta = self._cortes_poc[-1] - self._cortes_poc[-2]
            self._vel = _ALPHA_VEL * delta + (1 - _ALPHA_VEL) * self._vel

    # ------------------------------------------------------------------
    def snapshot(self):
        """Features de migracao do POC (grid de 100ms, causal)."""
        if self._dia is None or not self._amostras_ts:
            return {
                'poc_delta': 0.0,
                'poc_velocity': 0.0,
                'poc_direction': 0.0,
                'dist_preco_poc': 0.0,
                'preco_acima_poc': 0.0,
            }

        # Delta da ultima linha fechada (0.0 enquanto so ha 1 linha)
        delta = 0.0
        if len(self._cortes_poc) >= 2:
            delta = self._cortes_poc[-1] - self._cortes_poc[-2]

        # Distancia preco-POC com o preco do ultimo trade (comportamento
        # preservado da v9.40)
        preco_ult = self._amostras_preco[-1]
        poc_atual = self._amostras_poc[-1]
        dist = preco_ult - poc_atual
        acima = 1.0 if preco_ult > poc_atual else 0.0

        return {
            'poc_delta': round(delta, 4),
            'poc_velocity': round(self._vel, 4),
            'poc_direction': 1.0 if delta > 0 else (-1.0 if delta < 0 else 0.0),
            'dist_preco_poc': round(dist, 2),
            'preco_acima_poc': acima,
        }

    # ------------------------------------------------------------------
    def reset_diario(self):
        """v12.2: Reset diario (contrato dos auditores). Limpa tudo,
        incluindo a identidade de dia — o proximo update recomeca."""
        self._reset_estado()
        self._dia = None

    def _reset_estado(self):
        self._amostras_ts = []
        self._amostras_poc = []
        self._amostras_preco = []
        self._cortes_poc = []
        self._proximo_corte = None
        self._vel = 0.0
