# -*- coding: utf-8 -*-
"""
testes/test_lograte_v1536.py — Console limpo: agregador de avisos repetidos.

Cenários cobertos:
  1. 1ª ocorrência loga imediatamente com o detalhe real;
  2. repetições na mesma janela são silenciadas e contadas;
  3. virada de janela emite UMA linha-resumo com o total da janela;
  4. chaves diferentes têm janelas independentes;
  5. thread-safety básica (N threads, mesmo agregador, sem perda de contagem).
"""

import logging
import threading

import core.lograte as lr


def _recs(caplog):
    return [r for r in caplog.records if r.name == "LogRate"]


def test_primeira_ocorrencia_loga_imediatamente(caplog):
    rl = lr.LogRateLimit(janela_s=5.0)
    with caplog.at_level(logging.WARNING, logger="LogRate"):
        logou = rl.aviso(("ts_rej", "DOLV26", "timestamp_passado"),
                         "[RTD] DOLV26: timestamp rejeitado: timestamp_passado",
                         "(DAT=09:53:00.858)")
    assert logou is True
    recs = _recs(caplog)
    assert len(recs) == 1
    assert "(DAT=09:53:00.858)" in recs[0].getMessage()


def test_repeticao_silenciada_e_resumo_na_virada(caplog, monkeypatch):
    relogio = [1000.0]
    monkeypatch.setattr(lr.time, "monotonic", lambda: relogio[0])
    rl = lr.LogRateLimit(janela_s=5.0)
    with caplog.at_level(logging.WARNING, logger="LogRate"):
        rl.aviso(("k",), "MSG", "(d1)")
        # 300 repetições dentro da MESMA janela — nenhuma linha extra
        for i in range(300):
            rl.aviso(("k",), "MSG", f"(d{i})")
        assert len(_recs(caplog)) == 1
        assert rl.contagem(("k",)) == 300

        # Virada de janela: 1 linha-resumo com o total
        relogio[0] += 6.0
        logou = rl.aviso(("k",), "MSG", "(d301)")
        assert logou is True
        recs = _recs(caplog)
        assert len(recs) == 2
        msg = recs[1].getMessage()
        assert "300 ocorrencias na janela" in msg
        assert "(ultima (d301))" in msg
        assert rl.contagem(("k",)) == 0


def test_chaves_diferentes_independentes(caplog, monkeypatch):
    relogio = [2000.0]
    monkeypatch.setattr(lr.time, "monotonic", lambda: relogio[0])
    rl = lr.LogRateLimit(janela_s=5.0)
    with caplog.at_level(logging.WARNING, logger="LogRate"):
        rl.aviso(("a", "WIN"), "W-A", "")
        rl.aviso(("b", "DOL"), "W-B", "")
        assert len(_recs(caplog)) == 2  # cada chave loga a 1ª na hora
        # Só a chave A se repete
        for _ in range(50):
            rl.aviso(("a", "WIN"), "W-A", "")
        assert len(_recs(caplog)) == 2  # repetições de A não logam
        relogio[0] += 6.0
        rl.aviso(("b", "DOL"), "W-B", "")  # B não repetiu → linha normal
        # A só resume quando ocorre de novo após a virada
        rl.aviso(("a", "WIN"), "W-A", "")
        recs = _recs(caplog)
        assert len(recs) == 4
        assert "50 ocorrencias" in recs[3].getMessage()  # resumo só de A


def test_sem_repeticao_nao_emite_resumo(caplog, monkeypatch):
    relogio = [3000.0]
    monkeypatch.setattr(lr.time, "monotonic", lambda: relogio[0])
    rl = lr.LogRateLimit(janela_s=5.0)
    with caplog.at_level(logging.WARNING, logger="LogRate"):
        rl.aviso(("k",), "MSG", "")
        relogio[0] += 6.0
        rl.aviso(("k",), "MSG", "")
    recs = _recs(caplog)
    # 1ª linha + 2ª linha (virada) — sem "ocorrencias" porque não houve repetição
    assert len(recs) == 2
    assert all("ocorrencias" not in r.getMessage() for r in recs)


def test_thread_safety_nao_perde_contagem(caplog, monkeypatch):
    relogio = [4000.0]
    monkeypatch.setattr(lr.time, "monotonic", lambda: relogio[0])
    rl = lr.LogRateLimit(janela_s=5.0)
    N = 4
    POR_THREAD = 100  # total 400 < FORCA_EMISSAO (1000) — sem emissão forçada

    def dispara():
        for _ in range(POR_THREAD):
            rl.aviso(("t",), "MSG", "")

    threads = [threading.Thread(target=dispara) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 1ª linha imediata + N*POR_THREAD - 1 repetições contadas
    assert rl.contagem(("t",)) == N * POR_THREAD - 1
