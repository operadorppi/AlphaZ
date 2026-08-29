# -*- coding: utf-8 -*-
"""
replay_engine.py — Motor de Replay Determinístico (v10.1).

Responsável por ler arquivos JSONL de captura bruta e processar cada evento
através das camadas desacopladas do sistema. Garante que a lógica de sinais
e execução seja idêntica ao ambiente de produção.

Uso:
  python replay_engine.py --pasta MarketData/Profit --sessao 20260828_120000
"""

import json
import os
import sys
import logging
import argparse
from pathlib import Path

# Configuração de Logs seguindo padrão do projeto
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("Replay")

from core.contracts import Signal
from core.market_state import MarketState
from core.learning import Learning
from core.regime_detector import RegimeDetector
from core.risk_manager import RiskManager
from core.position_manager import PositionManager
from core.signal_engine import SignalEngine
from features.feature_engine import FeatureEngine
from ml.scorer import ScorerML

class ReplayEngine:
    """Orquestra o replay de dados brutos através das camadas do motor."""
    
    def __init__(self, base_dir, session_ts):
        self.base_dir = Path(base_dir)
        self.session_ts = session_ts
        
        # Inicializa camadas desacopladas
        self.state = MarketState(base_dir=str(self.base_dir))
        self.learning = Learning()
        self.regime = RegimeDetector()
        self.risk = RiskManager()
        self.pos_manager = PositionManager(self.risk, learning=self.learning)
        self.feature_engine = FeatureEngine(self.state)
        
        # Localiza modelo para o Scorer
        modelo_path = self.base_dir / "modelos" / "modelo_final.pkl"
        # v10.1: O Scorer agora é injetado no SignalEngine se disponível
        self.scorer = ScorerML(str(modelo_path), ["WINV26", "WDOU26"]) if modelo_path.exists() else None
        
        self.signal_engine = SignalEngine(self.state, self.learning, self.regime, 
                                          self.feature_engine, risk=self.risk)
        if self.scorer:
            self.signal_engine.scorer = self.scorer

    def run(self):
        neg_file = self.base_dir / f"raw_negocios_ms_{self.session_ts}.jsonl"

        if not neg_file.exists():
            log.error(f"Arquivo de negócios não encontrado: {neg_file}")
            return

        log.info(f"Iniciando Replay Determinístico: {self.session_ts}")
        
        # Processamento linha a linha para simular fluxo de tempo real
        with open(neg_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    self._process_event(data)
                except Exception as e:
                    log.error(f"Erro ao processar linha: {e}")
                    continue

        log.info("Replay finalizado.")
        self._print_stats()

    def _process_event(self, event):
        sym = event['ativo']
        ts = event['ts_ms']
        p = event['preco']
        q = event['qtd']
        agr = event['agressor']
        comp = event.get('compradora', '')
        vend = event.get('vendedora', '')

        # 1. Alimenta MarketState (Cálculo de Features de Microestrutura)
        self.state.alimentar_negocio(sym, ts, p, q, agr, comp, vend)

        # 2. Alimenta Scorer (ML Inference)
        if self.scorer:
            self.scorer.evento(sym, ts, p, q, agr, comp, vend)

        # 3. Signal Engine produz sinal (Lógica de decisão baseada em regras + ML)
        seg = ts // 1000
        sig: Signal = self.signal_engine.calcular(seg)

        # 4. Position Manager avalia abertura/fechamento (Filtro de Risco)
        if sym == "WINV26" and sig:
            # v10.21: Obtenção de decisão de risco para paridade com loop Live
            res_recentes = self.learning.resultados if self.learning else []
            decision = self.risk.pode_abrir(sig, res_recentes)

            res = self.pos_manager.gerenciar(
                sym, sig, p, decision=decision,
                regime=self.signal_engine.features.get(sym, {}).get('regime')
            )
            if res and res.tipo in ('ABRIR', 'FECHAR'):
                log.info(f"[{ts}] EVENTO OPERACIONAL: {res.tipo} | PNL: {res.pnl}")

    def _print_stats(self):
        from core.metrics import Metrics
        m = Metrics(resultados=self.pos_manager.learning.resultados if self.pos_manager.learning else [])
        stats = m.calcular()
        log.info(f"Estatísticas Consolidadas do Replay: {stats}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pasta", default="MarketData/Profit")
    parser.add_argument("--sessao", required=True, help="Timestamp da sessão (ex: 20260808_090000)")
    args = parser.parse_args()

    engine = ReplayEngine(args.pasta, args.sessao)
    engine.run()