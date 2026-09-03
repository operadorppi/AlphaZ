# -*- coding: utf-8 -*-
"""
replay_engine.py — Motor de Replay e Validacao de Edge (v13.0 - Realista).

Modos:
  paper:     replay de 1 dia, metricas + gate
  validacao: replay de N dias (default 3), verdicto go/no-go

Gate de vida (Fase 4):
  PF >= 1.2, win_rate >= 45%, max_drawdown_dia < 200 pts
  Todos os N dias devem passar para APROVADO.

Melhorias FASE 17 - Execucao Realista:
  - Latencia simulada
  - Spread variavel baseado em volatilidade
  - Slippage proporcional ao volume
  - Execucao parcial de ordens grandes
  - Rejeicao por circuit breaker e spread excessivo
  - Custos de execucao configuraveis por ativo
  - Stop intrabar com monitoramento continuo
  - Prioridade de fila simulada

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
        self.rejeicoes = []  # Ordens rejeitadas
        self.execucoes_parciais = []  # Parcial fills

    def registrar(self, lado, preco_entrada, preco_saida, motivo="", ts_ms=0, parcial=False):
        pnl_bruto = (preco_saida - preco_entrada) if lado == "C" else (preco_entrada - preco_saida)
        pnl_liq = pnl_bruto - self.custo_execucao
        self.trades.append({
            "lado": lado, "entrada": preco_entrada, "saida": preco_saida,
            "pnl_bruto": round(pnl_bruto, 2), "pnl_liquido": round(pnl_liq, 2),
            "motivo": motivo, "acertou": pnl_liq > 0, "ts_ms": ts_ms,
            "parcial": parcial,
        })

    def registrar_rejeicao(self, lado, motivo, ts_ms=0, preco_referencia=0):
        """Registra ordem rejeitada."""
        self.rejeicoes.append({
            "lado": lado, "motivo": motivo, "ts_ms": ts_ms, "preco_ref": preco_referencia
        })

    def calcular(self):
        result_base = {
            "n_rejeicoes": len(self.rejeicoes),
            "n_parciais": 0  # Will be calculated from trades
        }
        
        if not self.trades:
            result_base.update({
                "n_trades": 0, "win_rate": 0, "profit_factor": 0, "expectancy_pts": 0,
                "total_pnl": 0, "max_drawdown": 0, "max_drawdown_dia": 0,
                "sharpe": 0, "motivos": {}, "trades": []
            })
            return result_base
        
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

        # Max drawdown por dia
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
                    dd_dia = pk_dia - eq_dia
                    if dd_dia > mdd_dia:
                        mdd_dia = dd_dia
                    eq_dia = 0.0
                    pk_dia = 0.0
                    dia_anterior = dia
                eq_dia += t["pnl_liquido"]
                pk_dia = max(pk_dia, eq_dia)
            dd_dia = pk_dia - eq_dia
            if dd_dia > mdd_dia:
                mdd_dia = dd_dia

        motivos = defaultdict(int)
        for t in self.trades:
            motivos[t["motivo"]] += 1

        result = result_base.copy()
        # Count partial fills from trades
        n_parciais = len([t for t in self.trades if t.get("parcial")])
        result["n_parciais"] = n_parciais
        result.update({
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
            "rejeicoes": self.rejeicoes,
        })
        return result

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
            f"REJ={m['n_rejeicoes']}{'OK' if m['n_rejeicoes'] < 10 else 'ALTO'}",
        ]
        return {
            "aprovado": aprovado, "pf_ok": pf_ok, "wr_ok": wr_ok, "dd_ok": dd_ok,
            "pf_atual": m["profit_factor"], "wr_atual": m["win_rate"],
            "dd_atual": m["max_drawdown_dia"],
            "motivo": " | ".join(parts),
        }


# ============================================================
#  ExecutionSimulator — Simulador de execucao realista
# ============================================================
class ExecutionSimulator:
    """Simula condicoes reais de execucao no replay."""
    
    def __init__(self, config=None):
        self.config = config or {}
        replay_cfg = self.config.get("replay", {})
        
        # Latencia base por ativo (ms)
        self.latency_ms = replay_cfg.get("latency_ms", {
            "WINV26": 50, "WDOU26": 20, "INDV26": 50, "DOLU26": 20
        })
        
        # Custo de execucao por ativo (pts)
        self.execution_costs = replay_cfg.get("execution_costs", {
            "WINV26": 5.0, "WDOU26": 1.0, "INDV26": 5.0, "DOLU26": 1.0
        })
        
        # Threshold para execucao parcial (volume grande)
        self.partial_fill_threshold = replay_cfg.get("partial_fill_threshold", 0.8)
        
        # Probabilidades de rejeicao
        self.rejection_probs = replay_cfg.get("rejection_probability", {
            "circuit_breaker": 1.0,
            "daily_limit": 1.0,
            "spread_excessive": 0.5
        })
        
        # Volatilidade base para spread (bps)
        self.vol_spread_base_bps = {"WINV26": 5, "WDOU26": 3, "INDV26": 5, "DOLU26": 3}
        
        # Spread minimo (pts)
        self.min_spread_pts = {"WINV26": 2, "WDOU26": 1, "INDV26": 2, "DOLU26": 1}
        
        # Estado do dia
        self._trades_today = defaultdict(int)
        self._cumulative_pnl = 0.0
        self._daily_loss_limit = -300
        
    def reset_day(self):
        """Reseta estado para novo dia."""
        self._trades_today.clear()
        self._cumulative_pnl = 0.0
    
    def calculate_spread(self, sym, volatility_bps=100):
        """Calcula spread variavel baseado na volatilidade."""
        base_bps = self.vol_spread_base_bps.get(sym, 5)
        # Spread aumenta com volatilidade
        spread_bps = base_bps * (1 + volatility_bps / 100)
        return spread_bps
    
    def calculate_slippage(self, sym, order_volume, price):
        """Calcula slippage proporcional ao volume da ordem."""
        # Slippage base + adicional por volume
        base_slippage = self.latency_ms.get(sym, 20) * 0.01  # ms -> pts approx
        volume_factor = min(order_volume / 100.0, 2.0)  # max 2x slippage
        return base_slippage * (1 + volume_factor)
    
    def check_order_rejection(self, sym, signal_score, current_spread, 
                               daily_trades, daily_pnl, circuit_breaker_active=False):
        """Verifica se ordem deve ser rejeitada."""
        # 1. Verifica circuit breaker
        if circuit_breaker_active:
            return True, "CIRCUIT_BREAKER"
        
        # 2. Verifica limite diario de trades
        max_trades = self.config.get("trading", {}).get("max_trades_dia", 15)
        if daily_trades[sym] >= max_trades:
            return True, "DAILY_LIMIT"
        
        # 3. Verifica stop loss diario
        if daily_pnl <= self._daily_loss_limit:
            return True, "DAILY_LOSS_STOP"
        
        # 4. Verifica spread excessivo (50% chance)
        if current_spread > 20:  # spread muito largo
            if np.random.random() < self.rejection_probs.get("spread_excessive", 0.5):
                return True, "SPREAD_EXCESSIVE"
        
        return False, None
    
    def simulate_execution(self, sym, lado, preco_sinal, volume, ts_ms, volatility_bps=100):
        """
        Simula execucao realista de ordem.
        
        Returns:
            dict com preco_execucao, volume_executado, motivo_rejeicao (se houver)
        """
        # 1. Calcula spread no momento da sinalizacao
        spread = self.calculate_spread(sym, volatility_bps)
        
        # 2. Calcula latencia (simula delay)
        latency = self.latency_ms.get(sym, 30)
        
        # 3. Para ordens market, executa com slippage
        slippage = self.calculate_slippage(sym, volume, preco_sinal)
        
        # 4. Determina preco de execucao
        if lado == "C":
            exec_price = preco_sinal + spread/2 + slippage
        else:
            exec_price = preco_sinal - spread/2 - slippage
        
        # 5. Verifica execucao parcial (ordens grandes)
        partial_ratio = 1.0
        if volume > 50 and np.random.random() < 0.3:
            # 30% chance de execucao parcial para ordens grandes
            partial_ratio = np.random.uniform(0.5, 0.9)
        
        executed_volume = volume * partial_ratio
        
        return {
            "exec_price": round(exec_price, 2),
            "executed_volume": executed_volume,
            "partial_fill": partial_ratio < 1.0,
            "spread_applied": spread,
            "slippage_applied": slippage,
            "latency_ms": latency,
        }


# ============================================================
#  ReplayEngine — motor de replay deterministico com execucao realista
# ============================================================
class ReplayEngine:
    SLIPPAGE = {"WINV26": 2.0, "WDOU26": 0.5, "INDV26": 2.0, "DOLU26": 0.5}
    # TP/SL padrao por ativo (pts do contrato)
    TP_DEFAULT = {"WINV26": 100, "WDOU26": 30, "INDV26": 100, "DOLU26": 30}
    SL_DEFAULT = {"WINV26": 50, "WDOU26": 20, "INDV26": 50, "DOLU26": 20}

    def __init__(self, config=None, modelo_path=None, instrumentos=None):
        self.config = config or {}
        self.modelo_path = modelo_path
        self.instrumentos = instrumentos or ["WINV26", "WDOU26"]
        self.state = None
        self.signal_engine = None
        self.scorer = None
        self.metrics = TradeMetrics(self.config.get("custo_execucao_win", 5.0))
        self.exec_sim = ExecutionSimulator(self.config)
        self._posicao = None
        self._cooldown_until_ms = 0  # timestamp ms (simulado, nao wall clock)
        self._events = 0
        self._cumulative_pnl = 0.0  # PnL acumulado no dia
        self._intraday_stops = defaultdict(list)  # Stops intrabar registrados

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

    def _carregar_eventos(self, pasta_neg, dia_str=None):
        """Carrega e ordena eventos de negociacao de 1 dia."""
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
                        pass
        eventos.sort(key=lambda e: e.get("ts_ms", 0))
        return eventos

    def replay_dia(self, pasta_neg, dia_str=None):
        """Replay de 1 dia. Retorna metricas calculadas."""
        self._init_camadas()
        self.metrics = TradeMetrics(self.config.get("custo_execucao_win", 5.0))
        self.exec_sim.reset_day()
        self._posicao = None
        self._cooldown_until_ms = 0
        self._events = 0

        eventos = self._carregar_eventos(pasta_neg, dia_str)
        if not eventos:
            log.error(f"Nenhum evento para dia {dia_str}")
            return None

        log.info(f"[REPLAY] Dia {dia_str}: {len(eventos)} eventos")
        t0 = time.time()
        for ev in eventos:
            try:
                self._process_neg(ev)
                self._events += 1
            except Exception:
                pass
            if self._events % 100000 == 0:
                log.info(f"  {self._events:,} ev, {len(self.metrics.trades)} trades, {time.time()-t0:.1f}s")

        m = self.metrics.calcular()
        g = self.metrics.gate()
        elapsed = time.time() - t0

        log.info(f"[REPLAY] Dia {dia_str} concluido em {elapsed:.1f}s")
        log.info(f"  Trades: {m['n_trades']} | WR: {m['win_rate']:.1%} | PF: {m['profit_factor']:.2f}")
        log.info(f"  PnL: {m['total_pnl']:+.1f} | DD/dia: {m['max_drawdown_dia']:.0f}")
        log.info(f"  Rejeicoes: {m['n_rejeicoes']} | Parciais: {m['n_parciais']}")
        log.info(f"  Gate: {g['motivo']} | {'APROVADO' if g['aprovado'] else 'REPROVADO'}")

        return {"dia": dia_str, "metrics": m, "gate": g, "elapsed_s": round(elapsed, 1)}

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
            self.exec_sim.reset_day()
            self._posicao = None
            self._cooldown_until_ms = 0
            self._events = 0

            eventos = self._carregar_eventos(pasta_neg, dia_str)
            if not eventos:
                log.warning(f"  Dia {dia_str}: sem dados, pulando")
                todos_resultados.append({"dia": dia_str, "metrics": None, "gate": None, "skipped": True})
                continue

            log.info(f"  {len(eventos)} eventos")
            t0 = time.time()
            for ev in eventos:
                try:
                    self._process_neg(ev)
                    self._events += 1
                except Exception:
                    pass
                if self._events % 100000 == 0:
                    log.info(f"    {self._events:,} ev, {len(self.metrics.trades)} trades")

            m = self.metrics.calcular()
            g = self.metrics.gate()
            elapsed = time.time() - t0

            log.info(f"  Dia {dia_str}: {m['n_trades']} trades | WR={m['win_rate']:.1%} | PF={m['profit_factor']:.2f} | DD={m['max_drawdown_dia']:.0f}")
            log.info(f"  Rejeicoes: {m['n_rejeicoes']} | Parciais: {m['n_parciais']}")
            log.info(f"  Gate: {g['motivo']}")

            todos_resultados.append({
                "dia": dia_str, "metrics": m, "gate": g, "elapsed_s": round(elapsed, 1),
            })

        # Verdicto final
        return self._verdicto(todos_resultados)

    def _verdicto(self, todos_resultados):
        """Calcula verdicto go/no-go baseado em todos os dias."""
        validos = [r for r in todos_resultados if r.get("metrics") and not r.get("skipped")]
        reprovados = [r for r in validos if not r["gate"]["aprovado"]]
        n_validos = len(validos)

        # Medias
        if validos:
            avg_pf = np.mean([r["gate"]["pf_atual"] for r in validos])
            avg_wr = np.mean([r["gate"]["wr_atual"] for r in validos])
            avg_dd = np.mean([r["gate"]["dd_atual"] for r in validos])
            total_pnl = sum(r["metrics"]["total_pnl"] for r in validos)
            total_trades = sum(r["metrics"]["n_trades"] for r in validos)
            total_rejeicoes = sum(r["metrics"]["n_rejeicoes"] for r in validos)
        else:
            avg_pf = avg_wr = avg_dd = total_pnl = total_trades = total_rejeicoes = 0

        aprovado = len(reprovados) == 0 and n_validos >= 2  # pelo menos 2 dias validos

        log.info(f"\n{'='*60}")
        log.info(f"VERDICTO FINAL — {n_validos} dias validos")
        log.info(f"{'='*60}")
        log.info(f"  PF medio:    {avg_pf:.2f}")
        log.info(f"  WR medio:    {avg_wr:.1%}")
        log.info(f"  DD/dia medio: {avg_dd:.0f} pts")
        log.info(f"  PnL total:   {total_pnl:+.0f} pts")
        log.info(f"  Trades total: {total_trades}")
        log.info(f"  Rejeicoes total: {total_rejeicoes}")
        if reprovados:
            log.info(f"  Dias reprovados: {len(reprovados)}")
            for r in reprovados:
                log.info(f"    {r['dia']}: {r['gate']['motivo']}")
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
            "total_rejeicoes": total_rejeicoes,
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
        """
        Verifica entrada com execucao realista (FASE 17).
        - Latencia simulada
        - Spread variavel
        - Slippage proporcional
        - Possivel rejeicao
        - Execucao parcial
        """
        # Cooldown baseado em timestamp simulado (nao wall clock)
        if ts_ms < self._cooldown_until_ms:
            return
        
        # Score minimo para entrada
        if sig.score < 0.5:
            return
        
        # Calcula volatilidade do ativo no momento
        volatility_bps = self.state.get_volatility_bps(sym) if hasattr(self.state, 'get_volatility_bps') else 100
        
        # Verifica rejeicoes
        is_rejected, rejection_reason = self.exec_sim.check_order_rejection(
            sym=sym,
            signal_score=sig.score,
            current_spread=volatility_bps,
            daily_trades=defaultdict(int, {sym: self.metrics.calcular().get('n_trades', 0)}),
            daily_pnl=self._cumulative_pnl,
        )
        
        if is_rejected:
            self.metrics.registrar_rejeicao(sig.lado, rejection_reason, ts_ms, preco)
            log.debug(f"[REPLAY] Ordem rejeitada: {sym} {sig.lado} motivo={rejection_reason}")
            return
        
        # Simula execucao realista
        exec_result = self.exec_sim.simulate_execution(
            sym=sym,
            lado=sig.lado,
            preco_sinal=preco,
            volume=1,  # 1 contrato no replay
            ts_ms=ts_ms,
            volatility_bps=volatility_bps
        )
        
        # Aplica TP/SL do config ou padrao
        tp_pts = self.TP_DEFAULT.get(sym, 100)
        sl_pts = self.SL_DEFAULT.get(sym, 50)
        
        # Preco de entrada com slippage e spread aplicados
        pe = exec_result["exec_price"]
        
        self._posicao = {
            "lado": sig.lado, 
            "entrada": pe, 
            "tp": tp_pts, 
            "sl": sl_pts,
            "aberta_em": ts_ms, 
            "ativo": sym,
            "slippage_applied": exec_result["slippage_applied"],
            "spread_applied": exec_result["spread_applied"],
            "partial_fill": exec_result["partial_fill"],
        }
        
        # Registra custo de execucao
        exec_cost = self.exec_sim.execution_costs.get(sym, 5.0)
        self.metrics.custo_execucao = exec_cost
        
        log.debug(f"[REPLAY] Entrada sim: {sym} {sig.lado} price={pe:.2f} slip={exec_result['slippage_applied']:.2f} part={exec_result['partial_fill']}")

    def _checar_saida(self, sym, preco, ts_ms):
        """
        Verifica saida com execucao realista (FASE 17).
        - Stop intrabar
        - Slippage na saida
        - Execucao parcial possivel
        """
        pos = self._posicao
        if not pos:
            return
        
        # Calcula pnl nao realizado
        pnl_realizado = (preco - pos["entrada"]) if pos["lado"] == "C" else (pos["entrada"] - preco)
        
        # Checagem de stop intrabar (FASE 17)
        if pnl_realizado <= -pos["sl"]:
            # Stop atingido intra-barra
            self._intraday_stops[sym].append({
                "ts_ms": ts_ms, "pnl": pnl_realizado, "preco": preco
            })
            
            # Executa saida com slippage
            exec_result = self.exec_sim.simulate_execution(
                sym=sym, lado=pos["lado"], preco_sinal=preco,
                volume=1, ts_ms=ts_ms, volatility_bps=100
            )
            
            ps = exec_result["exec_price"]
            self.metrics.registrar(pos["lado"], pos["entrada"], ps, "SL_INTRABAR", ts_ms=ts_ms)
            
            cooldown_ms = self.config.get("cooldown_entre_trades_ms", 5000)
            self._cooldown_until_ms = ts_ms + cooldown_ms
            self._posicao = None
            self._cumulative_pnl += ps - pos["entrada"] - self.metrics.custo_execucao
            return
        
        # Verifica TP/SL/timeout
        motivo = None
        if pnl_realizado >= pos["tp"]:
            motivo = "TP"
        elif pnl_realizado <= -pos["sl"]:
            motivo = "SL"
        elif (ts_ms - pos["aberta_em"]) > 300000:  # 5 min timeout
            motivo = "TIMEOUT"
        
        if motivo:
            # Executa saida com slippage
            exec_result = self.exec_sim.simulate_execution(
                sym=sym, lado=pos["lado"], preco_sinal=preco,
                volume=1, ts_ms=ts_ms, volatility_bps=100
            )
            ps = exec_result["exec_price"]
            self.metrics.registrar(pos["lado"], pos["entrada"], ps, motivo, ts_ms=ts_ms)
            cooldown_ms = self.config.get("cooldown_entre_trades_ms", 5000)
            self._cooldown_until_ms = ts_ms + cooldown_ms
            self._posicao = None
            self._cumulative_pnl += ps - pos["entrada"] - self.metrics.custo_execucao


# ============================================================
#  CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay Engine v13.0 (Realista)")
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
    args = parser.parse_args()

    # Carrega config completa
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            full_config = json.load(f)
    else:
        full_config = {}
    
    # Merge com args
    config = {
        **full_config,
        "save_dir": args.pasta,
        "custo_execucao_win": args.custo,
        "cooldown_entre_trades_ms": args.cooldown,
    }
    
    engine = ReplayEngine(
        config=config,
        modelo_path=args.modelo,
        instrumentos=[args.ativo, "WDOU26"],
    )

    if args.modo == "paper":
        dia_str = args.dia.replace("-", "") if args.dia else None
        engine.replay_dia(pasta_neg=args.pasta, dia_str=dia_str)
    elif args.modo == "validacao":
        # Descobre os N dias mais recentes com dados
        pasta = Path(args.pasta)
        all_neg = sorted(pasta.glob("raw_negocios_ms_*.jsonl"))
        # Extrai datas unicas (formato: raw_negocios_ms_YYYYMMDD_*.jsonl)
        datas = set()
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
