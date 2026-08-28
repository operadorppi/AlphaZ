"""
test_edge_case_book_split.py — Testes de casos extremos para book_split
Rode: python -m pytest test_edge_case_book_split.py -v
"""
import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import json

from core.app import App
from core.market_state import MarketState, EstadoAtivo
from core.signal_engine import SignalEngine
from core.learning import Learning
import config


class TestBookSplitEdgeCases(unittest.TestCase):
    """Testa comportamento do sistema com valores extremos de book_split"""

    def setUp(self):
        """Configuração básica para cada teste"""
        # Criar um config.json temporário para testes
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, 'config.json')
        
        # Config básica mínima
        self.base_config = {
            "book_split": 30,  # valor padrão
            "web": {"host": "127.0.0.1", "port": 5001},
            "ativos": ["WINV26", "WDOU26"],
            "trading": {
                "tp_pts": 100,
                "sl_pts": 50,
                "max_holding_s": 30,
                "max_trades_dia": 15,
                "custo_execucao": {"WIN": 5.0, "WDO": 1.0}
            }
        }
        
        with open(self.config_path, 'w') as f:
            json.dump(self.base_config, f)
        
        # Patch para usar nosso config temporário
        self.config_patcher = patch('config.CONFIG', config.CONFIG)
        self.mock_config = self.config_patcher.start()
        
        # Recarregar o módulo de config para usar nosso arquivo
        import importlib
        importlib.reload(config)
        
        self.config_patcher.stop()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_book_split_zero_cria_listas_vazias(self):
        """book_split=0 deve criar listas vazias para book_bid/book_ask"""
        # Temporariamente modificar o config
        original_book_split = config.CONFIG.get('book_split')
        config.CONFIG['book_split'] = 0
        
        try:
            state = MarketState()
            # Quando book_split=0, as listas devem estar vazias
            self.assertEqual(len(state.book_bid), 0)
            self.assertEqual(len(state.book_ask), 0)
            self.assertEqual(state.book_bid, [])
            self.assertEqual(state.book_ask, [])
        finally:
            config.CONFIG['book_split'] = original_book_split

    def test_book_split_um_cria_listas_com_um_elemento(self):
        """book_split=1 deve criar listas com um elemento cada"""
        original_book_split = config.CONFIG.get('book_split')
        config.CONFIG['book_split'] = 1
        
        try:
            state = MarketState()
            self.assertEqual(len(state.book_bid), 1)
            self.assertEqual(len(state.book_ask), 1)
            self.assertIsInstance(state.book_bid[0], dict)
            self.assertIsInstance(state.book_ask[0], dict)
        finally:
            config.CONFIG['book_split'] = original_book_split

    def test_book_split_negativo_levanta_ValueError_no_range(self):
        """book_split negativo deve causar ValueError ao criar listas via range"""
        original_book_split = config.CONFIG.get('book_split')
        config.CONFIG['book_split'] = -5
        
        try:
            with self.assertRaises(ValueError):
                MarketState()  # Isso vai tentar criar range(-5) que levanta ValueError
        finally:
            config.CONFIG['book_split'] = original_book_split

    def test_extrair_book_snapshot_com_book_split_zero_retorna_listas_vazias(self):
        """extrair_book_snapshot deve funcionar com book_split=0"""
        original_book_split = config.CONFIG.get('book_split')
        config.CONFIG['book_split'] = 0
        
        try:
            state = MarketState()
            estado = EstadoAtivo()
            
            # Inicializar alguns campos necessários para não causar outros erros
            estado.book_bid = [{} for _ in range(config.CONFIG['book_split'])]
            estado.book_ask = [{} for _ in range(config.CONFIG['book_split'])]
            
            # Chave que seria chamada pelo market_state
            from core.market_state import extrair_book_snapshot
            result = extrair_book_snapshot(estado)
            
            # Com book_split=0, todas as listas de resultado devem estar vazias
            self.assertEqual(result['bid_vol'], [])
            self.assertEqual(result['bid_preco'], [])
            self.assertEqual(result['ask_vol'], [])
            self.assertEqual(result['ask_preco'], [])
        finally:
            config.CONFIG['book_split'] = original_book_split

    def test_alimentar_book_com_book_split_zero_não_cai_em_erro(self):
        """alimentar_book deve lidar gracciosamente com book_split=0"""
        original_book_split = config.CONFIG.get('book_split')
        config.CONFIG['book_split'] = 0
        
        try:
            state = MarketState()
            
            # Dados de teste mínimos
            snap = {}  # book vazio
            bid_vol = 0
            ask_vol = 0
            ofi_data = {'ofi_total': 0.0, 'ofi_ewma': 0.0}
            estado = EstadoAtivo()
            
            # Inicializar estruturas necessárias
            estado.book_bid = [{} for _ in range(config.CONFIG['book_split'])]
            estado.book_ask = [{} for _ in range(config.CONFIG['book_split'])]
            estado.book_ultimo_snap = None
            estado.book_ultimo_t = 0
            estado.ultimo_book_tempo = 0
            
            # Isso não deve levantar exceção
            state.alimentar_book("TEST", snap, bid_vol, ask_vol, ofi_data, estado=estado)
            
            # Verificar que o estado foi atualizado mesmo com listas vazias
            self.assertEqual(estado.book_ultimo_snap, (bid_vol, ask_vol, ()))
            self.assertEqual(state.market_state.book_snap_ant.get("TEST"), snap)
        finally:
            config.CONFIG['book_split'] = original_book_split

    def test_signal_engine_calcular_com_book_split_zero(self):
        """SignalEngine.calcular deve funcionar com book_split=0"""
        original_book_split = config.CONFIG.get('book_split')
        config.CONFIG['book_split'] = 0
        
        try:
            market_state = MarketState()
            learning = Learning()
            signal_engine = SignalEngine(market_state, learning=learning)
            
            # Preparar dados mínimos
            seg = 1000
            ativo = "TEST"
            
            # Criar um negócio mínimo para processar
            market_state.buffer[ativo] = [{
                'preco': 100.0,
                'qtd': 1,
                'agressor': 'Comprador',
                'compradora': 'XP',
                'vendedora': 'ITAU'
            }]
            
            # Isso não deve levantar exceção
            signal_engine.calcular(seg, skip_avaliar=False)
            
            # Verificar que features foram criadas (mesmo que vazias ou com valores padrão)
            self.assertIn(ativo, signal_engine.features)
            features = signal_engine.features[ativo]
            self.assertEqual(features['ativo'], ativo)
            self.assertEqual(features['n'], 1)
        finally:
            config.CONFIG['book_split'] = original_book_split


if __name__ == '__main__':
    unittest.main()