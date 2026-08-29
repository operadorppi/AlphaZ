# -*- coding: utf-8 -*-
"""
replay_engine.py - Motor de Replay e Validacao de Edge (v11.17).

Uso:
  python replay_engine.py --modo paper --modelo D:/MarketData/mimo/26/modelo_lgbm_v5_otimizado.pkl --dia 2026-08-28
"""

import json, os, sys, time, logging, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("Replay")


class TradeMetrics:
    def __init__(self, custo_execucao=5.0):
        self.trades = []
        self.custo_execucao = custo_execucao

    def registrar(self, lado, preco_entrada, preco_saida, motivo=""):
        pnl_bruto = (preco_saida - preco_entrada) if lado == "C" else (preco_entrada - preco_saida)
        pnl_liq = pnl_bruto - self.custo_execucao
        self.trades.append({"lado": lado, "entrada": preco_entrada, "saida": preco_saida,
                            "pnl_bruto": round(pnl_bruto, 2), "pnl_liquido": round(pnl_liq, 2),
                            "motivo": motivo, "acertou": pnl_liq > 0})

    def calcular(self):
        if not self.trades:
            return {"n_trades": 0, "win_rate": 0, "profit_factor": 0, "expectancy_pts": 0,
                    "total_pnl": 0, "max_drawdown": 0, "sharpe": 0, "motivos": {}, "trades": []}
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
        motivos = defaultdict(int)
        for t in self.trades:
            motivos[t["motivo"]] += 1
        return {"n_trades": n, "n_wins": len(wins), "n_losses": len(losses),
                "win_rate": round(wr, 4), "profit_factor": round(pf, 2),
                "expectancy_pts": round(sum(pnls) / n, 2), "total_pnl": round(sum(pnls), 2),
                "max_drawdown": round(mdd, 2), "sharpe": round(sharpe, 3),
                "melhor_trade": round(max(pnls), 2), "pior_trade": round(min(pnls), 2),
                "motivos": dict(motivos), "trades": self.trades}

    def gate(self, pf_min=1.2, wr_min=0.45):
        m = self.calcular()
        pf_ok = m["profit_factor"] >= pf_min
        wr_ok = m["win_rate"] >= wr_min
        return {"aprovado": pf_ok and wr_ok, "pf_ok": pf_ok, "wr_ok": wr_ok,
                "pf_atual": m["profit_factor"], "wr_atual": m["win_rate"],
                "motivo": f"PF={m['profit_factor']:.2f}{'OK' if pf_ok else 'FAIL'} WR={m['win_rate']:.1%}{'OK' if wr_ok else 'FAIL'}"}


class ReplayEngine:
    SLIPPAGE = {"WINV26": 2.0, "WDOU26": 0.5, "INDV26": 2.0, "DOLU26": 0.5}

    def __init__(self, config=None, modelo_path=None, instrumentos=None):
        self.config = config or {}
        self.modelo_path = modelo_path
        self.instrumentos = instrumentos or ["WINV26", "WDOU26"]
        self.state = None
        self.signal_engine = None
        self.scorer = None
        self.metrics = TradeMetrics(self.config.get("custo_execucao_win", 5.0))
        self._posicao = None
        self._cooldown_until = 0.0
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
        if self.modelo_path and os.path.exists(self.modelo_path):
            try:
                from ml.scorer import ScorerML
                self.scorer = ScorerML(self.modelo_path, self.instrumentos)
                self.signal_engine.scorer = self.scorer
                log.info(f"[REPLAY] Scorer carregado: {self.modelo_path}")
            except Exception as e:
                log.warning(f"[REPLAY] Scorer nao carregou: {e}")

    def replay_dia(self, pasta_neg, dia_str=None):
        self._init_camadas()
        neg_files = sorted(Path(pasta_neg).glob(f"raw_negocios_ms_*{dia_str}*.jsonl")) if dia_str else \
                    sorted(Path(pasta_neg).glob("raw_negocios_ms_*.jsonl"))
        if not neg_files:
            log.error(f"Nenhum arquivo em {pasta_neg}")
            return
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
        log.info(f"[REPLAY] {len(eventos)} eventos")
        t0 = time.time()
        for ev in eventos:
            try:
                if ev["_tipo"] == "NEG":
                    self._process_neg(ev)
                self._events += 1
            except Exception:
                pass
            if self._events % 50000 == 0:
                log.info(f"[REPLAY] {self._events:,} ev, {len(self.metrics.trades)} trades, {time.time()-t0:.1f}s")
        self._print_resultados(time.time() - t0)

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

    def _checar_entrada(self, sym, sig, preco, ts):
        if time.time() < self._cooldown_until or sig.score < 0.5:
            return
        slip = self.SLIPPAGE.get(sym, 2.0)
        pe = preco + slip if sig.lado == "C" else preco - slip
        self._posicao = {"lado": sig.lado, "entrada": pe, "tp": getattr(sig, "tp", 100),
                         "sl": getattr(sig, "sl", 50), "aberta_em": ts}

    def _checar_saida(self, sym, preco, ts):
        pos = self._posicao
        if not pos:
            return
        pnl = (preco - pos["entrada"]) if pos["lado"] == "C" else (pos["entrada"] - preco)
        motivo = None
        if pnl >= pos["tp"]:
            motivo = "TP"
        elif pnl <= -pos["sl"]:
            motivo = "SL"
        elif (ts - pos["aberta_em"]) > 300000:
            motivo = "TIMEOUT"
        if motivo:
            slip = self.SLIPPAGE.get(sym, 2.0)
            ps = preco - slip if pos["lado"] == "C" else preco + slip
            self.metrics.registrar(pos["lado"], pos["entrada"], ps, motivo)
            self._cooldown_until = time.time() + self.config.get("cooldown_entre_trades_ms", 5000) / 1000
            self._posicao = None

    def _print_resultados(self, elapsed):
        m = self.metrics.calcular()
        g = self.metrics.gate()
        log.info("=" * 50)
        log.info(f"Trades: {m['n_trades']} | WR: {m['win_rate']:.1%} | PF: {m['profit_factor']:.2f}")
        log.info(f"PnL: {m['total_pnl']:+.1f} | E(s): {m['expectancy_pts']:+.1f} | Sharpe: {m['sharpe']:.3f}")
        log.info(f"MaxDD: {m['max_drawdown']:.1f} | Motivos: {m['motivos']}")
        log.info(f"GATE: {g['motivo']} | APROVADO: {'SIM' if g['aprovado'] else 'NAO'}")
        log.info("=" * 50)
        out = {"timestamp": datetime.now().isoformat(), "metrics": m, "gate": g}
        out_path = Path(self.config.get("save_dir", ".")) / "replay_resultado.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, default=str)
        log.info(f"Salvo: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay Engine v11.17")
    parser.add_argument("--modo", choices=["paper", "validacao"], default="paper")
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--dia", default=None)
    parser.add_argument("--pasta", default="D:/MarketData/mimo")
    parser.add_argument("--ativo", default="WINV26")
    args = parser.parse_args()
    dia_str = args.dia.replace("-", "") if args.dia else None
    engine = ReplayEngine(config={"save_dir": args.pasta, "custo_execucao_win": 5.0, "cooldown_entre_trades_ms": 5000},
                          modelo_path=args.modelo, instrumentos=[args.ativo, "WDOU26"])
    engine.replay_dia(pasta_neg=args.pasta, dia_str=dia_str)
