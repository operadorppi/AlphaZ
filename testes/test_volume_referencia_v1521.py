# -*- coding: utf-8 -*-
"""
testes/test_volume_referencia_v1521.py — Baseline de volume relativo (P1-A26).

O VolumeRelativoTracker ANTES:
  - reset_diario() limpava `_historico`; o scorer chamava esse reset na 1a
    linha de cada novo dia (ml/scorer._atualizar_ajuste_para_dia), logo o
    historico arquivado no rollover do update() morria na mesma linha — em
    live a referencia entre dias NUNCA acumulava e volume_relativo ficava
    preso no fallback 1.0;
  - o snapshot usava 1.0 tanto como fallback "sem referencia" (cold start)
    quanto como valor real — o ML nao conseguia distinguir.

Agora:
  - o tracker faz o proprio rollover no update() (arquiva o dia anterior);
  - reset_diario() preserva o historico (zera so o dia corrente);
  - snapshot expoe referencia_disponivel + referencia_dias;
  - volume_relativo compara ACUMULADO de hoje vs ACUMULADO tipico dos dias
    anteriores ate o mesmo minuto (~1.0 = ritmo normal quando ha referencia).

Cobertura:
  1. Cold start -> 1.0 fallback com referencia_disponivel=False
  2. Mesmo dia (sem dias anteriores) -> ainda sem referencia
  3. Virada de dia arquiva o dia anterior e passa a haver referencia
  4. 1.0 COM referencia (ritmo normal) != 1.0 de cold start (caso do A26)
  5. reset_diario() preserva o historico entre dias
  6. Acumulado de multiplos dias entra na media (referencia_dias cresce)
  7. Cap de 20 dias no historico
  8. Guard estrutural: scorer nao chama mais vrels.reset_diario()
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _epoch_brt(ano, mes, dia, hora, minu=0):
    """Epoch ms de um horario de Brasilia (UTC-3)."""
    utc = datetime(ano, mes, dia, hora, minu, tzinfo=timezone.utc) + timedelta(hours=3)
    return int(utc.timestamp() * 1000)


def _ts_dia_minuto(dia_offset, minuto_do_dia):
    """ts de 09:00 + minuto_do_dia, no dia (2026,9,1+dia_offset)."""
    base = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc) + timedelta(hours=3)
    base = base + timedelta(days=dia_offset, minutes=minuto_do_dia)
    return int(base.timestamp() * 1000)


class TestVolumeReferencia:
    def _novo(self):
        from features.volume_relativo import VolumeRelativoTracker
        return VolumeRelativoTracker()

    # ------------------------------------------------------------------
    def test_cold_start_1_0_sem_referencia(self):
        tr = self._novo()
        s = tr.snapshot()
        assert s["volume_relativo"] == 1.0
        assert s["referencia_disponivel"] is False
        assert s["referencia_dias"] == 0
        assert s["volume_acumulado_dia"] == 0.0

    def test_mesmo_dia_ainda_sem_referencia(self):
        """Volume do dia 1 sozinho nao cria referencia (so dias anteriores)."""
        tr = self._novo()
        tr.update(100, _ts_dia_minuto(0, 10))
        tr.update(150, _ts_dia_minuto(0, 20))
        s = tr.snapshot()
        assert s["volume_relativo"] == 1.0  # fallback
        assert s["referencia_disponivel"] is False
        assert s["referencia_dias"] == 0

    def test_virada_de_dia_cria_referencia(self):
        """Dia 2 acumula 100 no minuto 10; dia 1 arquivado tinha 100 ate o
        minuto 10 -> relativo = 100/100 = 1.0 COM referencia (caso A26)."""
        tr = self._novo()
        # Dia 1: volume ate o minuto 10 = 100 (min 5 -> 40, min 10 -> 60)
        tr.update(40, _ts_dia_minuto(0, 5))
        tr.update(60, _ts_dia_minuto(0, 10))
        # Dia 2: primeiro evento no minuto 10 com 100 (gatilho do rollover)
        tr.update(100, _ts_dia_minuto(1, 10))
        s = tr.snapshot()
        assert s["referencia_disponivel"] is True
        assert s["referencia_dias"] == 1
        # 1.0 real (ritmo identico ao dia anterior) — NAO e fallback
        assert s["volume_relativo"] == pytest.approx(1.0, abs=1e-3)
        assert s["volume_acumulado_dia"] == pytest.approx(100.0, abs=1e-3)

    def test_relativo_maior_que_1_quando_volume_acima_da_media(self):
        """Dia 2 acumula 150 ate o minuto 10; dia 1 tinha 100 -> 1.5."""
        tr = self._novo()
        tr.update(100, _ts_dia_minuto(0, 10))
        tr.update(150, _ts_dia_minuto(1, 10))
        s = tr.snapshot()
        assert s["referencia_disponivel"] is True
        assert s["volume_relativo"] == pytest.approx(1.5, abs=1e-3)

    def test_reset_diario_preserva_historico(self):
        """reset_diario() zera o dia corrente mas mantem a referencia."""
        tr = self._novo()
        tr.update(100, _ts_dia_minuto(0, 10))
        # dia 2 inicia (arquiva dia 1)
        tr.update(100, _ts_dia_minuto(1, 10))
        assert len(tr._historico) == 1
        # reset que o scorer fazia na virada de dia (agora removido)
        tr.reset_diario()
        assert len(tr._historico) == 1  # historico sobrevive
        # mais volume no dia 2 -> referencia ainda valida
        tr.update(50, _ts_dia_minuto(1, 10))
        s = tr.snapshot()
        assert s["referencia_disponivel"] is True
        assert s["referencia_dias"] == 1
        assert s["volume_relativo"] == pytest.approx(0.5, abs=1e-3)  # 50/100

    def test_multiplos_dias_entram_na_media(self):
        """3 dias: no dia 3 a media usa os 2 dias anteriores."""
        tr = self._novo()
        tr.update(100, _ts_dia_minuto(0, 10))   # dia 1
        tr.update(300, _ts_dia_minuto(1, 10))   # dia 2 (arquiva dia 1)
        # dia 3: acumulado 200; esperado = (100+300)/2 = 200 -> relativo 1.0
        tr.update(200, _ts_dia_minuto(2, 10))
        s = tr.snapshot()
        assert s["referencia_disponivel"] is True
        assert s["referencia_dias"] == 2
        assert s["volume_relativo"] == pytest.approx(1.0, abs=1e-3)

    def test_cap_20_dias_no_historico(self):
        tr = self._novo()
        for d in range(1, 26):  # 25 dias
            tr.update(10, _ts_dia_minuto(d, 10))
            # cada update novo dia arquiva o anterior no rollover interno
        assert len(tr._historico) == 20
        s = tr.snapshot()
        assert s["referencia_dias"] == 20

    def test_fora_do_minuto_do_pregao_nao_suja_referencia(self):
        """Volume antes das 09:00 nao cria minuto; nao vira referencia."""
        tr = self._novo()
        # 08:00 BRT = minuto -60 -> fora da janela [0, 570)
        ts_pre = _ts_dia_minuto(0, -60)
        tr.update(9999, ts_pre)
        s = tr.snapshot()
        assert s["volume_acumulado_dia"] == pytest.approx(9999.0, abs=1e-3)
        assert s["referencia_disponivel"] is False  # nada gravado em minuto valido
        assert s["volume_por_minuto"] == 0.0


class TestGuardScorer:
    def test_scorer_nao_reseta_vrels_na_virada(self):
        """ml/scorer._atualizar_ajuste_para_dia nao pode resetar vrels:
        reset externo apagava o historico recem-arquivado (P1-A26)."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "ml", "scorer.py"),
                   encoding="utf-8").read()
        assert "self.vrels[ativo].reset_diario()" not in src

    def test_alias_continua_apontando_para_implementacao_unica(self):
        from features.volume_relativo import VolumeRelativoTracker as V1
        from features.volume_relativo_tracker import VolumeRelativoTracker as V2
        assert V1 is V2
