# -*- coding: utf-8 -*-
"""
testes/test_poc_migration_temporal_v1523.py — PocMigrationTracker temporal
(P0-A28).

ANTES (v9.40): update(preco, poc) sem ts — poc_velocity = delta entre duas
atualizacoes consecutivas. Um POC que andou 5 pontos em 10ms ou em 5s
produzia a MESMA "velocidade" 5.0, congelada ate a proxima mudanca de POC.

AGORA: velocidade no grid temporal do master clock (100ms):
  - corte fecha com o POC do ultimo trade com ts ESTRITAMENTE menor que o
    corte (o trade que dispara o avanco entra no corte SEGUINTE);
  - delta por linha de 100ms -> EWMA alpha=0.1 (paridade com o batch
    diff().ewm(alpha=0.1).mean() do features_expansao);
  - cortes intermediarios forward-filled (POC constante -> delta 0 -> a
    EWMA decai com o tempo real decorrido);
  - rollover interno por dia de Brasilia (padrao P0-A27).
"""

import math
import time

import pytest

from features.poc_migration import PocMigrationTracker

DIA1 = 1_770_000_000_000          # dia BRT N
DIA2 = DIA1 + 86_400_000_000      # dia BRT N+1
GRID = 100
ALPHA = 0.1


def _esperado_apos_spike(delta_spike, n_zeros):
    """EWMA alpha=0.1: spike concentrado em 1 linha + n linhas de delta 0."""
    v = ALPHA * delta_spike
    for _ in range(n_zeros):
        v *= (1 - ALPHA)
    return v


class TestSemanticaTemporal:
    def test_cold_start_zeros(self):
        """Sem nenhum evento: snapshot tudo zero (fallback documentado)."""
        t = PocMigrationTracker()
        s = t.snapshot()
        assert s['poc_delta'] == 0.0
        assert s['poc_velocity'] == 0.0
        assert s['poc_direction'] == 0.0
        assert s['dist_preco_poc'] == 0.0
        assert s['preco_acima_poc'] == 0.0

    def test_velocidade_decai_com_tempo_real(self):
        """A28: pulo de +5 em 5s NAO sustenta velocity=5 — a EWMA decai nas
        linhas de 100ms sem mudanca de POC (tempo real decorrido conta)."""
        t = PocMigrationTracker()
        # t1: POC parado em 100000
        t.update(DIA1 + 0, 100000, 100000)
        # t2: 5s depois o POC anda +5 (a mudanca cai na 1a linha apos t2)
        t.update(DIA1 + 5_000, 100005, 100005)
        # t3: 2s depois, POC parado — avanca os cortes intermediarios
        t.update(DIA1 + 7_000, 100005, 100005)

        # Linha do spike em t2+100; zeros de t2+200 ate t2+2000 -> 19 zeros
        # (snapshot arredonda para 4 casas, como o restante do tracker)
        esperado = round(_esperado_apos_spike(5.0, n_zeros=19), 4)
        s = t.snapshot()
        assert abs(s['poc_velocity'] - esperado) < 1e-9
        assert s['poc_velocity'] < 1.0   # ANTES seria 5.0 congelado
        # direcao da ultima linha fechada (zero): POC parado
        assert s['poc_direction'] == 0.0

    def test_mesmo_pulo_rapido_velocidade_maior(self):
        """Mesmo pulo +5 acontecendo em 100ms decai menos -> velocity maior
        que no cenario de 5s. ANTES: identico (independia do tempo)."""
        lento = PocMigrationTracker()
        lento.update(DIA1 + 0, 100000, 100000)
        lento.update(DIA1 + 5_000, 100005, 100005)
        lento.update(DIA1 + 7_000, 100005, 100005)

        rapido = PocMigrationTracker()
        rapido.update(DIA1 + 0, 100000, 100000)
        rapido.update(DIA1 + 100, 100005, 100005)  # move na linha seguinte
        rapido.update(DIA1 + 200, 100005, 100005)

        s_r = rapido.snapshot()
        s_l = lento.snapshot()
        # rapido: spike na 2a linha fechada, sem linha de zero -> 0.5;
        # lento: 19 zeros apos o spike -> ~0.0675
        assert abs(s_r['poc_velocity'] - round(_esperado_apos_spike(5.0, 0), 4)) < 1e-9
        assert s_r['poc_velocity'] > s_l['poc_velocity']

    def test_ewma_exata_spike(self):
        """Pulo de +2 na 2a linha (sem zeros depois): vel = alpha*2 = 0.2."""
        t = PocMigrationTracker()
        t.update(DIA1 + 0, 100000, 100000)
        t.update(DIA1 + 100, 100002, 100002)  # move entra na linha t+200
        t.update(DIA1 + 200, 100002, 100002)
        s = t.snapshot()
        assert abs(s['poc_velocity'] - 0.2) < 1e-9
        assert s['poc_delta'] == 2.0
        assert s['poc_direction'] == 1.0


class TestRolloverDiario:
    def test_dia_novo_sem_contaminacao_do_dia_anterior(self):
        """O estado do dia anterior nao vaza: 1a linha do dia novo com delta
        +2 -> vel = 0.1*2 = 0.2 (se o dia 1 vazasse, 0.9*0.5 + 0.2 = 0.65)."""
        t = PocMigrationTracker()
        # Dia 1 termina com vel = 0.5 (spike +5, 1 linha de zero)
        t.update(DIA1 + 0, 100000, 100000)
        t.update(DIA1 + 100, 100005, 100005)
        t.update(DIA1 + 200, 100005, 100005)
        assert abs(t.snapshot()['poc_velocity'] - 0.5) < 1e-9

        # Dia 2: nivel de POC totalmente diferente (90000) — 1o trade entra
        # no perfil novo, sem POC/velocidade do dia anterior
        t.update(DIA2 + 0, 90000, 90000)
        t.update(DIA2 + 100, 90002, 90002)
        t.update(DIA2 + 200, 90002, 90002)
        s = t.snapshot()
        assert abs(s['poc_velocity'] - 0.2) < 1e-9
        assert s['poc_delta'] == 2.0
        assert s['poc_direction'] == 1.0
        assert s['dist_preco_poc'] == 0.0  # preco 90002 == poc 90002

    def test_reset_diario_limpa_identidade_e_estado(self):
        """reset_diario zera tudo; o proximo update recomeca (contrato dos
        auditores: o metodo existe e limpa)."""
        t = PocMigrationTracker()
        t.update(DIA1 + 0, 100000, 100000)
        t.update(DIA1 + 100, 100005, 100005)
        t.update(DIA1 + 200, 100005, 100005)
        assert t.snapshot()['poc_velocity'] > 0
        t.reset_diario()
        s = t.snapshot()
        assert s['poc_velocity'] == 0.0
        assert s['poc_delta'] == 0.0
        assert s['poc_direction'] == 0.0
        # identidade de dia recomeca
        t.update(DIA2 + 0, 50000, 50000)
        t.update(DIA2 + 100, 50002, 50002)
        t.update(DIA2 + 200, 50002, 50002)
        assert abs(t.snapshot()['poc_velocity'] - 0.2) < 1e-9


class TestContrato:
    def test_update_exige_ts_ms(self):
        """Assinatura antiga update(preco, poc) quebra (TypeError) — garante
        que nenhum caller volta a medir velocidade sem tempo."""
        t = PocMigrationTracker()
        with pytest.raises(TypeError):
            t.update(100000, 100000)  # faltou ts_ms (1o argumento posicional)

    def test_dist_preco_poc_e_acima(self):
        """Comportamento dist_preco_poc / preco_acima_poc preservado."""
        t = PocMigrationTracker()
        t.update(DIA1 + 0, 100000, 100000)
        t.update(DIA1 + 100, 100010, 100002)  # preco acima do POC
        t.update(DIA1 + 200, 100010, 100002)
        s = t.snapshot()
        assert s['dist_preco_poc'] == 8.0    # 100010 - 100002
        assert s['preco_acima_poc'] == 1.0

        t2 = PocMigrationTracker()
        t2.update(DIA1 + 0, 100000, 100000)
        t2.update(DIA1 + 100, 99990, 100002)  # preco abaixo do POC
        t2.update(DIA1 + 200, 99990, 100002)
        s2 = t2.snapshot()
        assert s2['dist_preco_poc'] == -12.0
        assert s2['preco_acima_poc'] == 0.0

    def test_fora_de_ordem_nao_corrompe(self):
        """Evento atrasado nao corrompe: buffer ordenado + cortes monotonicos."""
        t = PocMigrationTracker()
        t.update(DIA1 + 5_000, 100005, 100005)
        t.update(DIA1 + 0, 100000, 100000)    # atrasado, entra no lugar
        t.update(DIA1 + 10_000, 100007, 100007)
        t.update(DIA1 + 10_100, 100007, 100007)
        s = t.snapshot()
        assert math.isfinite(s['poc_velocity'])
        assert isinstance(s['poc_direction'], float)

    def test_sem_wall_clock_na_fonte(self):
        """Guard estrutural: o modulo nao usa relogio da maquina."""
        import inspect
        import features.poc_migration as mod
        src = inspect.getsource(mod)
        assert 'time.time' not in src
        assert 'datetime.now' not in src

    def test_reset_diario_existe(self):
        """Contrato dos auditores de integridade temporal."""
        assert hasattr(PocMigrationTracker, 'reset_diario')
