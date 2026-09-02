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
    args = parser.parse_args()

    config = {
        "save_dir": args.pasta,
        "custo_execucao_win": args.custo,
        "cooldown_entre_trades_ms": args.cooldown,
    }
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
