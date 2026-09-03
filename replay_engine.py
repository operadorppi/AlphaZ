# -*- coding: utf-8 -*-
"""
replay_engine.py — Motor de Replay e Validacao de Edge (v12.0).

Modos:
  paper:     replay de 1 dia, metricas + gate
  validacao: replay de N dias (default 3), verdicto go/no-go

Gate de vida (Fase 4):
  PF >= 1.2, win_rate >= 45%, max_drawdown_dia < 200 pts
  Todos os N dias devem passar para APROVADO.

Uso:
  # 1 dia
  python replay_engine.py --modo paper --dia 2026-08-28

  # 3 dias consecutivos (validacao)
  python replay_engine.py --modo validacao --dias 3

  # Specifies modelo
  python replay_engine.py --modo validacao --dias 3 --modelo D:/MarketData/mimo/26/modelo_lgbm_v5_otimizado.pkl
"""

import json, os, sys, time, logging, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("Replay")


# ============================================================
#  TradeMetrics — metricas por dia e acumuladas
# ============================================================
class TradeMetrics:
    def __init__(self, custo_execucao=5.0):
        self.trades = []
        self.custo_execucao = custo_execucao

    def registrar(self, lado, preco_entrada, preco_saida, motivo="", ts_ms=0):
        pnl_bruto = (preco_saida - preco_entrada) if lado == "C" else (preco_entrada - preco_saida)
        pnl_liq = pnl_bruto - self.custo_execucao
        self.trades.append({
            "lado": lado, "entrada": preco_entrada, "saida": preco_saida,
            "pnl_bruto": round(pnl_bruto, 2), "pnl_liquido": round(pnl_liq, 2),
            "motivo": motivo, "acertou": pnl_liq > 0, "ts_ms": ts_ms,
        })

    def calcular(self):
        if not self.trades:
            return {"n_trades": 0, "win_rate": 0, "profit_factor": 0, "expectancy_pts": 0,
                    "total_pnl": 0, "max_drawdown": 0, "max_drawdown_dia": 0,
                    "sharpe": 0, "motivos": {}, "trades": []}
        pnls = [t["pnl_liquido"] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        n = len(pnls)
        wr = len(wins) / n if n else 0
        ganhos = sum(wins) if wins else 0
        perdas = abs(sum(losses)) if losses else 0.01
        pf = ganhos / perdas if perdas > 0 else float("inf")
        eq = np.cumsum(pnls)
        pk = np.maximum.accumulate(eq)
        mdd = float((pk - eq).max()) if len(eq) else 0
        sharpe = float(np.mean(pnls) / np.std(pnls)) if n > 1 and np.std(pnls) > 0 else 0

        # Max drawdown por dia (usando ts_ms)
        mdd_dia = 0.0
        if self.trades and self.trades[0].get("ts_ms", 0) > 0:
            from datetime import timezone, timedelta
            tz_br = timezone(timedelta(hours=-3))
            dia_anterior = None
            eq_dia = 0.0
            pk_dia = 0.0
            for t in self.trades:
                ts = t.get("ts_ms", 0)
                if ts > 0:
                    dt = datetime.fromtimestamp(ts / 1000, tz=tz_br)
                    dia = dt.date()
                else:
                    dia = None
                if dia != dia_anterior:
                    # Novo dia: registra mdd do dia anterior
                    dd_dia = pk_dia - eq_dia
                    if dd_dia > mdd_dia:
                        mdd_dia = dd_dia
                    eq_dia = 0.0
                    pk_dia = 0.0
                    dia_anterior = dia
                eq_dia += t["pnl_liquido"]
                pk_dia = max(pk_dia, eq_dia)
            # Ultimo dia
            dd_dia = pk_dia - eq_dia
            if dd_dia > mdd_dia:
                mdd_dia = dd_dia

        motivos = defaultdict(int)
        for t in self.trades:
            motivos[t["motivo"]] += 1
        return {
            "n_trades": n, "n_wins": len(wins), "n_losses": len(losses),
            "win_rate": round(wr, 4), "profit_factor": round(pf, 2),
            "expectancy_pts": round(sum(pnls) / n, 2) if n else 0,
            "total_pnl": round(sum(pnls), 2),
            "max_drawdown": round(mdd, 2),
            "max_drawdown_dia": round(mdd_dia, 2),
            "sharpe": round(sharpe, 3),
            "melhor_trade": round(max(pnls), 2) if n else 0,
            "pior_trade": round(min(pnls), 2) if n else 0,
            "motivos": dict(motivos), "trades": self.trades,
        }

    def gate(self, pf_min=1.2, wr_min=0.45, mdd_max=200.0):
        """Gate de vida: PF, WR e max DD por dia."""
        m = self.calcular()
        pf_ok = m["profit_factor"] >= pf_min
        wr_ok = m["win_rate"] >= wr_min
        dd_ok = m["max_drawdown_dia"] <= mdd_max
        aprovado = pf_ok and wr_ok and dd_ok
        parts = [
            f"PF={m['profit_factor']:.2f}{'OK' if pf_ok else 'FAIL'}",
            f"WR={m['win_rate']:.1%}{'OK' if wr_ok else 'FAIL'}",
            f"DD={m['max_drawdown_dia']:.0f}{'OK' if dd_ok else 'FAIL(max ' + str(mdd_max) + ')'}",
        ]
        return {
            "aprovado": aprovado, "pf_ok": pf_ok, "wr_ok": wr_ok, "dd_ok": dd_ok,
            "pf_atual": m["profit_factor"], "wr_atual": m["win_rate"],
            "dd_atual": m["max_drawdown_dia"],
            "motivo": " | ".join(parts),
        }


# ============================================================
#  ReplayEngine — motor de replay deterministico
# ============================================================
class ReplayEngine:
    SLIPPAGE = {"WINV26": 2.0, "WDOV26": 0.5, "INDV26": 2.0, "DOLV26": 0.5}
    # TP/SL padrao por ativo (pts do contrato)
    TP_DEFAULT = {"WINV26": 100, "WDOV26": 30, "INDV26": 100, "DOLV26": 30}
    SL_DEFAULT = {"WINV26": 50, "WDOV26": 20, "INDV26": 50, "DOLV26": 20}

    def __init__(self, config=None, modelo_path=None, instrumentos=None):
        self.config = config or {}
        self.modelo_path = modelo_path
        self.instrumentos = instrumentos or ["WINV26", "WDOV26"]
        self.state = None
        self.signal_engine = None
        self.scorer = None
        self.metrics = TradeMetrics(self.config.get("custo_execucao_win", 5.0))
        self._posicao = None
        self._cooldown_until_ms = 0  # timestamp ms (simulado, nao wall clock)
        self._events = 0
        # P0-A14: book do Hive por ativo p/ paridade live/replay.
        # ativo -> [(ts_ms, bid_p, bid_v, ask_p, ask_v), ...] ordenado por ts_ms
        self._books = {}
        self._book_idx = defaultdict(int)
        # P0-A15: replay NUNCA engole erro silenciosamente.
        # strict=True aborta no 1o erro inesperado; strict=False (default,
        # diagnostico) conta e loga TODOS os erros e o gate e reprovado quando
        # houver perda — resultados comprometidos nunca passam como validos.
        self.strict = bool(self.config.get("replay_strict", False))
        self._max_erros_log = int(self.config.get("replay_max_erros_log", 5))
        self._erros = 0
        self._erros_por_tipo = defaultdict(int)
        self._primeiros_erros = []
        self._linhas_ignoradas = 0
        self._arquivos_pulados = 0

    def _init_camadas(self):
        from core.market_state import MarketState
        from core.learning import Learning
        from core.regime_detector import RegimeDetector
        from core.risk_manager import RiskManager
        from core.signal_engine import SignalEngine
        from features.feature_engine import FeatureEngine
        self.state = MarketState(config=self.config)
        self.learning = Learning(config=self.config)
        self.regime = RegimeDetector(config=self.config)
        self.risk = RiskManager(config=self.config)
        self.feature_engine = FeatureEngine(self.state, config=self.config)
        self.signal_engine = SignalEngine(self.state, self.learning, self.regime,
                                          self.feature_engine, risk=self.risk, config=self.config)
        # v12.0: Batch mode — so recalcula features quando o segundo muda
        self.signal_engine._batch_mode = True
        if self.modelo_path and os.path.exists(self.modelo_path):
            try:
                from ml.scorer import ScorerML
                self.scorer = ScorerML(self.modelo_path, self.instrumentos)
                self.signal_engine.scorer = self.scorer
                log.info(f"[REPLAY] Scorer carregado: {self.modelo_path}")
            except Exception as e:
                log.warning(f"[REPLAY] Scorer nao carregou: {e}")

    def _registrar_erro(self, ev):
        """P0-A15: registra um erro de processamento com detalhe e amostra.

        Nunca silencioso: o 1o erro loga a excecao completa (com traceback);
        os demais sao contados por tipo e amostrados (ate _max_erros_log) para
        nao inundar o log com 999k linhas identicas. No fim do dia, se houve
        qualquer erro, o gate e FORCADO a reprovar (resultado comprometido).
        """
        import traceback
        self._erros += 1
        tipo = "desconhecido"
        try:
            exc = sys.exc_info()[1]
            tipo = type(exc).__name__ if exc is not None else "desconhecido"
            self._erros_por_tipo[tipo] += 1
            if len(self._primeiros_erros) < self._max_erros_log:
                self._primeiros_erros.append({
                    "tipo": tipo,
                    "erro": str(exc),
                    "evento": {
                        "ativo": ev.get("ativo") if isinstance(ev, dict) else None,
                        "ts_ms": ev.get("ts_ms") if isinstance(ev, dict) else None,
                    },
                    "traceback": traceback.format_exc()[-2000:],
                })
                log.error(
                    f"[REPLAY] ERRO #{self._erros} [{tipo}] no evento "
                    f"{ev if isinstance(ev, dict) else ev}: {exc}")
                log.error(f"[REPLAY]   {traceback.format_exc().splitlines()[-3:]}")
        except Exception:
            self._erros_por_tipo["desconhecido"] += 1

    def _reset_erros(self):
        """P0-A15: zera os contadores de erro (1x por dia)."""
        self._erros = 0
        self._erros_por_tipo = defaultdict(int)
        self._primeiros_erros = []
        self._linhas_ignoradas = 0
        self._arquivos_pulados = 0

    def _processar_evento(self, ev, t0):
        """P0-A15: processa 1 evento com politica explicita de erro.

        Retorna True se processou, False se o erro foi em modo permissivo.
        Em modo STRICT, qualquer excecao inesperada interrompe o replay
        (re-raise) — uma perda silenciosa invalidaria a auditoria.
        """
        try:
            self._process_neg(ev)
            return True
        except Exception:
            self._registrar_erro(ev)
            if self.strict:
                log.error("[REPLAY] STRICT: abortando replay por erro inesperado "
                          "(perda nao pode ser silenciosa em auditoria)")
                raise
            return False

    def _processar_lote_eventos(self, eventos, t0):
        """P0-A15: loop comum de replay_dia/replay_multi_dia."""
        for ev in eventos:
            self._events += 1  # tentado (para o progresso)
            try:
                ok = self._processar_evento(ev, t0)
            except Exception:
                # Modo strict abortou — propaga com contexto
                raise
            if self._erros and self._erros % 100000 == 0:
                log.warning(f"  ... {self._erros:,} erros acumulados em {self._events:,} eventos")
            if self._events % 100000 == 0:
                log.info(f"  {self._events:,} ev, {len(self.metrics.trades)} trades, "
                         f"{time.time()-t0:.1f}s")

    def _dia_com_erros(self, dia_str):
        """True se o dia teve qualquer perda (erros, linhas/arquivos pulados)."""
        return (self._erros > 0 or self._linhas_ignoradas > 0
                or self._arquivos_pulados > 0)

    def _resumo_erros(self, dia_str):
        """Loga o resumo de erros do dia + retorna dict p/ o resultado."""
        resumo = {
            "erros": self._erros,
            "erros_por_tipo": dict(self._erros_por_tipo),
            "linhas_ignoradas": self._linhas_ignoradas,
            "arquivos_pulados": self._arquivos_pulados,
            "amostras": self._primeiros_erros[:3],
        }
        if self._erros:
            log.error(f"[REPLAY] Dia {dia_str}: {self._erros} eventos falharam "
                      f"de {self._events} ({dict(self._erros_por_tipo)})")
            log.error(f"[REPLAY] Dia {dia_str}: resultado COMPROMETIDO — gate reprovado")
        if self._linhas_ignoradas:
            log.warning(f"[REPLAY] Dia {dia_str}: {self._linhas_ignoradas} linhas JSONL "
                        "ilegiveis ignoradas na carga")
        if self._arquivos_pulados:
            log.warning(f"[REPLAY] Dia {dia_str}: {self._arquivos_pulados} arquivos "
                        "Parquet ilegiveis pulados na carga")
        return resumo

    def _ler_book_hive(self, pasta_neg, dia_str=None):
        """P0-A14: Le snapshots de BOOK do Parquet Hive e monta snapshots
        compactos por ativo, ordenados por ts_ms.

        O RAW de BOOK e denormalizado (1 row por nivel de preco) e as linhas
        de um mesmo snapshot sao consecutivas no arquivo (o writer grava o
        loop de niveis sob lock). Detecta os blocos pela mudanca de
        (ts_ns, janela_id) com numpy e monta: [(ts_ms, bid_p, bid_v,
        ask_p, ask_v), ...] com bids ordenados desc (melhor primeiro) e
        asks asc — mesma semantica de profundidade que o live usa.

        Retorno: dict ativo -> lista de snapshots compactos ordenada por ts.
        Vazio se nao ha BOOK Hive para o dia (replay segue sem book, com
        features de OFI zeradas — comportamento legado, agora logado).
        """
        try:
            from adapters.file_storage import find_hive_files
        except Exception:
            return {}
        files = find_hive_files(str(pasta_neg), dia_str=dia_str, data_type="BOOK")
        if not files:
            return {}
        try:
            import pyarrow.parquet as pq
        except Exception as e:
            log.warning(f"[REPLAY] pyarrow indisponivel p/ ler book: {e}")
            return {}
        cols = ["ts_ns", "ativo", "janela_id", "bid", "ask", "bid_volume", "ask_volume"]
        por_ativo = defaultdict(list)
        n_snaps = 0
        for f in files:
            try:
                tbl = pq.read_table(str(f), columns=cols)
            except Exception as e:
                log.warning(f"[REPLAY] Falha ao ler book {f}: {e}")
                continue
            if tbl.num_rows == 0:
                continue
            ts_ns = tbl.column("ts_ns").to_numpy(zero_copy_only=False)
            jan = tbl.column("janela_id").to_numpy(zero_copy_only=False)
            ativos = tbl.column("ativo").to_pylist()
            bid_p = tbl.column("bid").to_numpy(zero_copy_only=False)
            ask_p = tbl.column("ask").to_numpy(zero_copy_only=False)
            bid_v = tbl.column("bid_volume").to_numpy(zero_copy_only=False)
            ask_v = tbl.column("ask_volume").to_numpy(zero_copy_only=False)

            ts_i = ts_ns.astype(np.int64)
            ja_i = jan.astype(np.int64)
            # Limites dos blocos: mudanca de (ts_ns, janela_id)
            mudanca = np.empty(ts_i.size, dtype=bool)
            mudanca[0] = True
            if ts_i.size > 1:
                mudanca[1:] = (ts_i[1:] != ts_i[:-1]) | (ja_i[1:] != ja_i[:-1])
            bounds = np.append(np.flatnonzero(mudanca), ts_i.size)
            for k in range(len(bounds) - 1):
                a, b = int(bounds[k]), int(bounds[k + 1])
                ts_ms = int(ts_i[a] // 1_000_000)
                if ts_ms <= 0:
                    continue
                bps, bvs = bid_p[a:b], bid_v[a:b]
                aps, avs = ask_p[a:b], ask_v[a:b]
                mb = (bps > 0) & (bvs > 0)
                ma = (aps > 0) & (avs > 0)
                if not mb.any() and not ma.any():
                    continue
                # bids desc (melhor primeiro), asks asc
                bps_b, bvs_b = bps[mb], bvs[mb]
                aps_a, avs_a = aps[ma], avs[ma]
                ob = np.argsort(-bps_b)
                oa = np.argsort(aps_a)
                por_ativo[ativos[a]].append((
                    ts_ms,
                    bps_b[ob].tolist(), bvs_b[ob].tolist(),
                    aps_a[oa].tolist(), avs_a[oa].tolist(),
                ))
                n_snaps += 1
        for ativo in por_ativo:
            por_ativo[ativo].sort(key=lambda s: s[0])
        if n_snaps:
            log.info(f"[REPLAY] Book Hive: {n_snaps:,} snapshots em {len(por_ativo)} ativos")
        return dict(por_ativo)

    def _alimentar_book_ate(self, sym, ts_ms):
        """P0-A14: Alimenta os snapshots de book do ativo com ts <= ts_ms,
        na ordem temporal (merge TT+BOOK igual ao caminho do live).

        Sem isso, o replay rodava com OFI/features de book ZERADAS enquanto
        o live alimentava o book a cada poll — sinal do replay divergia do
        sinal real (pesos heuristicos de ofi/book_* em learning.py).
        """
        snaps = self._books.get(sym)
        if not snaps:
            return
        i = self._book_idx.get(sym, 0)
        if i >= len(snaps) or snaps[i][0] > ts_ms:
            return
        try:
            from core.contracts import BookSnapshot, BookLevel
        except Exception:
            return
        while i < len(snaps) and snaps[i][0] <= ts_ms:
            ts_s, bp, bv, ap, av = snaps[i]
            bids = [BookLevel(float(p), int(v)) for p, v in zip(bp, bv)]
            asks = [BookLevel(float(p), int(v)) for p, v in zip(ap, av)]
            try:
                self.state.alimentar_book(BookSnapshot(
                    symbol=sym, timestamp_ms=ts_s, bids=bids, asks=asks))
            except Exception as e:
                log.warning(f"[REPLAY] alimentar_book falhou ({sym} t={ts_s}): {e}")
            i += 1
        self._book_idx[sym] = i

    def _ler_tt_hive(self, pasta_neg, dia_str=None):
        """v15.1: Le negocios do Parquet Hive (RAW/data_type=TT/date=.../asset=...).

        Mapeia o schema RAW (ts_ns, ativo, preco, quantidade, agressor,
        compradora, vendedora, is_rlp) para o formato de evento do replay.
        Linhas RLP sao ignoradas (fluxo duplicado de T&T — evitaria contagem dupla).
        """
        try:
            from adapters.file_storage import find_hive_files
        except Exception:
            return []
        files = find_hive_files(str(pasta_neg), dia_str=dia_str, data_type="TT")
        if not files:
            return []
        import pyarrow.parquet as pq
        cols = ["ts_ns", "ativo", "preco", "quantidade", "agressor",
                "compradora", "vendedora", "is_rlp"]
        eventos = []
        for f in files:
            try:
                tbl = pq.read_table(f, columns=cols)
            except Exception as e:
                # P0-A15: arquivo pulado e contado (nunca silencioso)
                self._arquivos_pulados += 1
                log.warning(f"[REPLAY] Falha ao ler {f}: {e} "
                            f"(total pulados: {self._arquivos_pulados})")
                continue
            for row in tbl.to_pylist():
                if row.get("is_rlp"):
                    continue
                ts_ns = row.get("ts_ns") or 0
                if not ts_ns:
                    continue
                eventos.append({
                    "_tipo": "NEG",
                    "ativo": row.get("ativo") or "",
                    "ts_ms": int(ts_ns // 1_000_000),
                    "preco": float(row.get("preco") or 0),
                    "qtd": int(row.get("quantidade") or 0),
                    "agressor": row.get("agressor") or "",
                    "compradora": row.get("compradora") or "",
                    "vendedora": row.get("vendedora") or "",
                })
        return eventos

    def _carregar_eventos(self, pasta_neg, dia_str=None):
        """Carrega e ordena eventos de negociacao de 1 dia.

        v15.1: tenta Parquet Hive primeiro; fallback para JSONL legado.
        """
        eventos = self._ler_tt_hive(pasta_neg, dia_str)
        if eventos:
            eventos.sort(key=lambda e: e.get("ts_ms", 0))
            return eventos
        if dia_str:
            neg_files = sorted(Path(pasta_neg).glob(f"raw_negocios_ms_*{dia_str}*.jsonl"))
        else:
            neg_files = sorted(Path(pasta_neg).glob("raw_negocios_ms_*.jsonl"))
        if not neg_files:
            return []
        eventos = []
        for nf in neg_files:
            with open(nf, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        ev["_tipo"] = "NEG"
                        eventos.append(ev)
                    except Exception:
                        # P0-A15: linha ilegivel e contada (nunca silenciosa)
                        self._linhas_ignoradas += 1
                        if self._linhas_ignoradas <= 3:
                            log.warning(f"[REPLAY] Linha JSONL ilegivel em {nf.name} "
                                        f"(total: {self._linhas_ignoradas}): "
                                        f"{line[:120]}")
        eventos.sort(key=lambda e: e.get("ts_ms", 0))
        return eventos

    def replay_dia(self, pasta_neg, dia_str=None):
        """Replay de 1 dia. Retorna metricas calculadas."""
        self._init_camadas()
        self._books = self._ler_book_hive(pasta_neg, dia_str)
        self._book_idx = defaultdict(int)
        self.metrics = TradeMetrics(self.config.get("custo_execucao_win", 5.0))
        self._posicao = None
        self._cooldown_until_ms = 0
        self._events = 0
        self._reset_erros()

        eventos = self._carregar_eventos(pasta_neg, dia_str)
        if not eventos:
            log.error(f"Nenhum evento para dia {dia_str}")
            return None

        log.info(f"[REPLAY] Dia {dia_str}: {len(eventos)} eventos")
        t0 = time.time()
        try:
            self._processar_lote_eventos(eventos, t0)
        except Exception:
            log.error(f"[REPLAY] Dia {dia_str} ABORTADO em modo STRICT")
            return {
                "dia": dia_str, "metrics": None, "gate": None,
                "abortado": True, "erros": self._erros,
                "erros_por_tipo": dict(self._erros_por_tipo),
                "amostras": self._primeiros_erros[:3],
                "elapsed_s": round(time.time() - t0, 1),
            }

        m = self.metrics.calcular()
        g = self.metrics.gate()
        resumo_erros = self._resumo_erros(dia_str)
        if self._dia_com_erros(dia_str):
            # P0-A15: resultados comprometidos nunca aprovam o gate
            g = {
                "aprovado": False, "pf_ok": False, "wr_ok": False, "dd_ok": False,
                "pf_atual": m.get("profit_factor", 0), "wr_atual": m.get("win_rate", 0),
                "dd_atual": m.get("max_drawdown_dia", 0),
                "motivo": f"REPLAY COMPROMETIDO: {self._erros} erros + "
                          f"{self._linhas_ignoradas} linhas + "
                          f"{self._arquivos_pulados} arquivos (ver resumo_erros)",
            }
        elapsed = time.time() - t0

        log.info(f"[REPLAY] Dia {dia_str} concluido em {elapsed:.1f}s")
        log.info(f"  Trades: {m['n_trades']} | WR: {m['win_rate']:.1%} | PF: {m['profit_factor']:.2f}")
        log.info(f"  PnL: {m['total_pnl']:+.1f} | DD/dia: {m['max_drawdown_dia']:.0f}")
        log.info(f"  Gate: {g['motivo']} | {'APROVADO' if g['aprovado'] else 'REPROVADO'}")

        return {"dia": dia_str, "metrics": m, "gate": g,
                "resumo_erros": resumo_erros, "elapsed_s": round(elapsed, 1)}

    def replay_multi_dia(self, pasta_neg, dias):
        """Replay de N dias consecutivos. Retorna lista de resultados + verdicto."""
        self._init_camadas()
        todos_resultados = []

        for dia_str in dias:
            log.info(f"\n{'='*60}")
            log.info(f"REPLAY DIA: {dia_str}")
            log.info(f"{'='*60}")

            # Reset para cada dia (posicao, cooldown, metricas)
            self.metrics = TradeMetrics(self.config.get("custo_execucao_win", 5.0))
            self._posicao = None
            self._cooldown_until_ms = 0
            self._events = 0
            self._reset_erros()
            self._books = self._ler_book_hive(pasta_neg, dia_str)
            self._book_idx = defaultdict(int)

            eventos = self._carregar_eventos(pasta_neg, dia_str)
            if not eventos:
                log.warning(f"  Dia {dia_str}: sem dados, pulando")
                todos_resultados.append({"dia": dia_str, "metrics": None, "gate": None, "skipped": True})
                continue

            log.info(f"  {len(eventos)} eventos")
            t0 = time.time()
            try:
                self._processar_lote_eventos(eventos, t0)
            except Exception:
                log.error(f"  Dia {dia_str} ABORTADO em modo STRICT")
                todos_resultados.append({
                    "dia": dia_str, "metrics": None, "gate": None,
                    "abortado": True, "erros": self._erros,
                    "erros_por_tipo": dict(self._erros_por_tipo),
                    "amostras": self._primeiros_erros[:3],
                })
                continue

            m = self.metrics.calcular()
            g = self.metrics.gate()
            resumo_erros = self._resumo_erros(dia_str)
            if self._dia_com_erros(dia_str):
                # P0-A15: resultados comprometidos nunca aprovam o gate
                g = {
                    "aprovado": False, "pf_ok": False, "wr_ok": False, "dd_ok": False,
                    "pf_atual": m.get("profit_factor", 0),
                    "wr_atual": m.get("win_rate", 0),
                    "dd_atual": m.get("max_drawdown_dia", 0),
                    "motivo": f"REPLAY COMPROMETIDO: {self._erros} erros + "
                              f"{self._linhas_ignoradas} linhas + "
                              f"{self._arquivos_pulados} arquivos",
                }
            elapsed = time.time() - t0

            log.info(f"  Dia {dia_str}: {m['n_trades']} trades | WR={m['win_rate']:.1%} | PF={m['profit_factor']:.2f} | DD={m['max_drawdown_dia']:.0f}")
            log.info(f"  Gate: {g['motivo']}")

            todos_resultados.append({
                "dia": dia_str, "metrics": m, "gate": g, "elapsed_s": round(elapsed, 1),
                "resumo_erros": resumo_erros,
            })

        # Verdicto final
        return self._verdicto(todos_resultados)

    def _verdicto(self, todos_resultados):
        """Calcula verdicto go/no-go baseado em todos os dias."""
        # P0-A15: um dia so e "valido" se tem gate; dias abortados ou sem gate
        # (strict, sem dados) contam como reprovados — um replay com perda de
        # eventos NAO pode aprovar o gate.
        validos = [r for r in todos_resultados
                   if r.get("metrics") and r.get("gate") and not r.get("skipped")]
        reprovados = [r for r in todos_resultados
                      if r.get("abortado") or r.get("gate") is None
                      or (r.get("gate") and not r["gate"]["aprovado"])]
        n_validos = len(validos)

        # Medias
        if validos:
            avg_pf = np.mean([r["gate"]["pf_atual"] for r in validos])
            avg_wr = np.mean([r["gate"]["wr_atual"] for r in validos])
            avg_dd = np.mean([r["gate"]["dd_atual"] for r in validos])
            total_pnl = sum(r["metrics"]["total_pnl"] for r in validos)
            total_trades = sum(r["metrics"]["n_trades"] for r in validos)
        else:
            avg_pf = avg_wr = avg_dd = total_pnl = total_trades = 0

        aprovado = len(reprovados) == 0 and n_validos >= 2  # pelo menos 2 dias validos

        log.info(f"\n{'='*60}")
        log.info(f"VERDICTO FINAL — {n_validos} dias validos")
        log.info(f"{'='*60}")
        log.info(f"  PF medio:    {avg_pf:.2f}")
        log.info(f"  WR medio:    {avg_wr:.1%}")
        log.info(f"  DD/dia medio: {avg_dd:.0f} pts")
        log.info(f"  PnL total:   {total_pnl:+.0f} pts")
        log.info(f"  Trades total: {total_trades}")
        if reprovados:
            log.info(f"  Dias reprovados: {len(reprovados)}")
            for r in reprovados:
                if r.get("gate") is not None:
                    log.info(f"    {r['dia']}: {r['gate']['motivo']}")
                elif r.get("abortado"):
                    log.info(f"    {r['dia']}: ABORTADO (strict) com {r.get('erros', 0)} erros")
                else:
                    log.info(f"    {r['dia']}: sem dados validos / gate nulo")
        log.info(f"\n  >>> VERDICTO: {'GO (aprovado)' if aprovado else 'NO-GO (reprovado)'}")
        log.info(f"{'='*60}")

        verdicto = {
            "timestamp": datetime.now().isoformat(),
            "n_dias": n_validos,
            "aprovado": aprovado,
            "pf_medio": round(avg_pf, 2),
            "wr_medio": round(avg_wr, 4),
            "dd_dia_medio": round(avg_dd, 2),
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "dias_reprovados": len(reprovados),
            "por_dia": todos_resultados,
        }

        # Salvar resultado
        out_path = Path(self.config.get("save_dir", ".")) / "replay_resultado.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(verdicto, f, indent=2, default=str)
        log.info(f"Salvo: {out_path}")

        return verdicto

    def _process_neg(self, ev):
        sym = ev.get("ativo", "")
        ts = ev.get("ts_ms", 0)
        p = ev.get("preco", 0)
        q = ev.get("qtd", 0)
        agr = ev.get("agressor", "")
        comp = ev.get("compradora", "")
        vend = ev.get("vendedora", "")
        if p <= 0 or q <= 0:
            return
        # P0-A14: alimentar o book deste ativo ate o ts do trade (merge
        # temporal). Sem isto, ofi_total/ofi_ewma e book_* ficam zerados no
        # replay enquanto sao reais no live.
        if self._books:
            self._alimentar_book_ate(sym, ts)
        self.state.alimentar_negocio(sym, ts, p, q, agr, comp, vend)
        if self.scorer:
            self.scorer.evento(sym, ts, p, q, agr, comp, vend)
        sig = self.signal_engine.calcular(ts // 1000)
        if sym == self.instrumentos[0] and sig:
            if self._posicao:
                self._checar_saida(sym, p, ts)
            if not self._posicao and sig.lado:
                self._checar_entrada(sym, sig, p, ts)

    def _checar_entrada(self, sym, sig, preco, ts_ms):
        # Cooldown baseado em timestamp simulado (nao wall clock)
        if ts_ms < self._cooldown_until_ms:
            return
        if sig.score < 0.5:
            return
        slip = self.SLIPPAGE.get(sym, 2.0)
        pe = preco + slip if sig.lado == "C" else preco - slip
        tp_pts = self.TP_DEFAULT.get(sym, 100)
        sl_pts = self.SL_DEFAULT.get(sym, 50)
        self._posicao = {
            "lado": sig.lado, "entrada": pe, "tp": tp_pts, "sl": sl_pts,
            "aberta_em": ts_ms, "ativo": sym,
        }

    def _checar_saida(self, sym, preco, ts_ms):
        pos = self._posicao
        if not pos:
            return
        pnl = (preco - pos["entrada"]) if pos["lado"] == "C" else (pos["entrada"] - preco)
        motivo = None
        if pnl >= pos["tp"]:
            motivo = "TP"
        elif pnl <= -pos["sl"]:
            motivo = "SL"
        elif (ts_ms - pos["aberta_em"]) > 300000:  # 5 min timeout
            motivo = "TIMEOUT"
        if motivo:
            slip = self.SLIPPAGE.get(sym, 2.0)
            ps = preco - slip if pos["lado"] == "C" else preco + slip
            self.metrics.registrar(pos["lado"], pos["entrada"], ps, motivo, ts_ms=ts_ms)
            cooldown_ms = self.config.get("cooldown_entre_trades_ms", 5000)
            self._cooldown_until_ms = ts_ms + cooldown_ms
            self._posicao = None


# ============================================================
#  CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay Engine v12.0")
    parser.add_argument("--modo", choices=["paper", "validacao"], default="paper",
                        help="paper=1 dia, validacao=N dias consecutivos")
    parser.add_argument("--modelo", default=None,
                        help="Path do .pkl do modelo")
    parser.add_argument("--dia", default=None,
                        help="Data especifica (ex: 2026-08-28)")
    parser.add_argument("--dias", type=int, default=3,
                        help="Numero de dias para validacao (default: 3)")
    parser.add_argument("--pasta", default="D:/MarketData/mimo",
                        help="Pasta com raw_negocios_ms_*.jsonl")
    parser.add_argument("--ativo", default="WINV26")
    parser.add_argument("--custo", type=float, default=5.0,
                        help="Custo de execucao por trade (pts)")
    parser.add_argument("--cooldown", type=int, default=5000,
                        help="Cooldown entre trades (ms)")
    parser.add_argument("--strict", action="store_true",
                        help="P0-A15: aborta no 1o erro inesperado (default: permissivo "
                             "com contagem explicita + gate reprovado quando ha perda)")
    args = parser.parse_args()

    # P0-A14: o replay deve rodar com a MESMA config operacional do live
    # (config.json), nao um config minimalista de 3 chaves com defaults de
    # codigo divergentes. Args do CLI sobrescrevem apenas o especifico do
    # replay (save_dir, custo, cooldown).
    config = {}
    try:
        from config import get_config_dict
        config = dict(get_config_dict())
        log.info("[REPLAY] config.json operacional carregado (paridade com o live)")
    except Exception as e:
        log.warning(f"[REPLAY] config.json nao carregou ({e}); usando defaults do CLI")
    config.update({
        "save_dir": args.pasta,
        "custo_execucao_win": args.custo,
        "cooldown_entre_trades_ms": args.cooldown,
        "replay_strict": args.strict,
    })
    engine = ReplayEngine(
        config=config,
        modelo_path=args.modelo,
        instrumentos=[args.ativo, "WDOV26"],
    )

    if args.modo == "paper":
        dia_str = args.dia.replace("-", "") if args.dia else None
        engine.replay_dia(pasta_neg=args.pasta, dia_str=dia_str)
    elif args.modo == "validacao":
        # Descobre os N dias mais recentes com dados
        pasta = Path(args.pasta)
        datas = set()
        # v15.1: dias do Parquet Hive (RAW/data_type=TT/date=YYYYMMDD/)
        hive_tt = pasta / "RAW" / "data_type=TT"
        if hive_tt.exists():
            for d in hive_tt.iterdir():
                if d.is_dir() and d.name.startswith("date="):
                    dd = d.name[len("date="):]
                    if len(dd) == 8 and dd.isdigit() and dd.startswith("20"):
                        datas.add(dd)
        # Fallback: JSONL legado (formato: raw_negocios_ms_YYYYMMDD_*.jsonl)
        all_neg = sorted(pasta.glob("raw_negocios_ms_*.jsonl"))
        for f in all_neg:
            parts = f.stem.split("_")
            for p in parts:
                if len(p) == 8 and p.isdigit() and p.startswith("20"):
                    datas.add(p)
                    break
        dias_sorted = sorted(datas)[-args.dias:]
        if not dias_sorted:
            log.error("Nenhum dia com dados encontrado")
            sys.exit(1)
        log.info(f"Dias para validacao: {dias_sorted}")
        engine.replay_multi_dia(pasta_neg=args.pasta, dias=dias_sorted)
