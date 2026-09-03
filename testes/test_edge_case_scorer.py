"""
test_edge_case_scorer.py — Testes de casos extremos para o scorer ML
Rode: python -m pytest test_edge_case_scorer.py -v
"""
import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import json
import numpy as np

from core.app import App
from core.signal_engine import SignalEngine
from core.learning import Learning
from core.market_state import MarketState
import config


class TestScorerEdgeCases(unittest.TestCase):
    """Testa comportamento do sistema com falhas ou estados extremos do scorer ML"""

    def setUp(self):
        """Configuração básica para cada teste"""
        # Criar um config.json temporário para testes
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, 'config.json')
        
        # Config básica mínima
        self.base_config = {
            "book_split": 30,
            "web": {"host": "127.0.0.1", "port": 5001},
            "ativos": ["WINV26", "WDOU26"],
            "ml_modelo": "",  # por padrão, sem modelo
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
        
        # Recarregar config para usar nosso arquivo temporário
        import importlib
        importlib.reload(config)
        
        # Criar componentes de teste
        self.market_state = MarketState()
        self.learning = Learning(config=config.CONFIG)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sinal_engine_com_scorer_none_usa_heuristica_pura(self):
        """Quando scorer=None, o sinal_engine deve usar apenas heurística"""
        signal_engine = SignalEngine(
            self.market_state, 
            learning=self.learning,
            scorer=None  # explícitamente None
        )
        
        # Verificar que o scorer está realmente definido como None
        self.assertIsNone(signal_engine.scorer)
        
        # Preparar dados de teste
        seg = 1000
        ativo = "WINV26"
        
        # Adicionar alguns negócios para gerar features
        self.market_state.buffer[ativo] = [{
            'preco': 100.0,
            'qtd': 10,
            'agressor': 'Comprador',
            'compradora': 'XP',
            'vendedora': 'ITAU'
        } for _ in range(5)]  # 5 negócios
        
        # Executar cálculo - não deve levantar exceção mesmo sem scorer
        signal_engine.calcular(seg, skip_avaliar=False)
        
        # Verificar que features foram geradas
        self.assertIn(ativo, signal_engine.features)
        features = signal_engine.features[ativo]
        self.assertGreater(features['n'], 0)
        
        # Executar avaliação - deve produzir sinal baseado apenas em heurística
        result = signal_engine.avaliar(ativo, features)
        
        # Verificar que o resultado contém os campos esperados
        self.assertIn('sinal', result)
        self.assertIn('confianca', result)
        self.assertIn('score', result)
        self.assertIn('motivos', result)
        
        # Como não há scorer, ml_prob deve ser 0.5 (valor padrão)
        self.assertEqual(result['ml_prob'], 0.5)

    def test_sinal_engine_com_scorer_mas_sem_prob_para_ativo(self):
        """Quando scorer existe mas não tem prob para o ativo, deve usar heurística"""
        # Criar um scorer mock que não tem o atributo 'prob' ou que está vazio
        mock_scorer = MagicMock()
        mock_scorer.prob = {}  # dicionário vazio
        
        signal_engine = SignalEngine(
            self.market_state, 
            learning=self.learning,
            scorer=mock_scorer
        )
        
        # Preparar dados de teste
        seg = 1000
        ativo = "WINV26"  # ativo que não está em mock_scorer.prob
        
        # Adicionar alguns negócios
        self.market_state.buffer[ativo] = [{
            'preco': 100.0,
            'qtd': 5,
            'agressor': 'Vendedor',
            'compradora': 'ITAU',
            'vendedora': 'XP'
        } for _ in range(3)]
        
        # Executar cálculo e avaliação
        signal_engine.calcular(seg, skip_avaliar=False)
        result = signal_engine.avaliar(ativo, signal_engine.features[ativo])
        
        # Deve ter usado heurística pura (ml_prob = 0.5)
        self.assertEqual(result['ml_prob'], 0.5)
        # Os outros campos devem estar presentes
        self.assertIsInstance(result['score'], (int, float))
        self.assertIsInstance(result['confianca'], (int, float))
        self.assertIsInstance(result['motivos'], list)

    def test_sinal_engine_com_scorer_que_levanta_exceção_no_prob(self):
        """Quando acesso a scorer.prob levanta exceção, deve tratar graciosamente"""
        # Criar um scorer mock que levanta exceção ao acessar .prob
        mock_scorer = MagicMock()
        type(mock_scorer).prob = MagicMock(side_effect=Exception("Erro ao acessar prob"))
        
        signal_engine = SignalEngine(
            self.market_state, 
            learning=self.learning,
            scorer=mock_scorer
        )
        
        # Preparar dados de teste
        seg = 1000
        ativo = "WINV26"
        
        # Adicionar alguns negócios
        self.market_state.buffer[ativo] = [{
            'preco': 100.0,
            'qtd': 5,
            'agressor': 'Comprador',
            'compradora': 'XP',
            'vendedora': 'ITAU'
        }]
        
        # Isso não deve levantar exceção - deve tratar o erro e usar heurística
        signal_engine.calcular(seg, skip_avaliar=False)
        result = signal_engine.avaliar(ativo, signal_engine.features[ativo])
        
        # Deve ter usado heurística pura devido ao erro
        self.assertEqual(result['ml_prob'], 0.5)

    def test_sinal_engine_com_scorer_retornando_prob_inválida(self):
        """Quando scorer.prob[ativo] retorna valor não numérico, deve tratar graciosamente"""
        mock_scorer = MagicMock()
        mock_scorer.prob = {"WINV26": "invalid"}  # string em vez de float
        
        signal_engine = SignalEngine(
            self.market_state, 
            learning=self.learning,
            scorer=mock_scorer
        )
        
        # Preparar dados de teste
        seg = 1000
        ativo = "WINV26"
        
        # Adicionar alguns negócios
        self.market_state.buffer[ativo] = [{
            'preco': 100.0,
            'qtd': 5,
            'agressor': 'Comprador',
            'compradora': 'XP',
            'vendedora': 'ITAU'
        }]
        
        # Executar - não deve levantar exceção de tipo
        signal_engine.calcular(seg, skip_avaliar=False)
        
        # A avaliação deve lidar com o valor inválido
        # Dependendo de como o sinal_engine lida com isso, pode:
        # 1. Usar valor padrão (0.5)
        # 2. Tentar converter e falhar, então usar padrão
        # 3. Levantar exceção (menos ideal)
        # Vamos verificar que pelo menos não travou e produziu algum resultado
        result = signal_engine.avaliar(ativo, signal_engine.features[ativo])
        
        # Verificar que temos um resultado válido
        self.assertIn('sinal', result)
        self.assertIn('confianca', result)
        self.assertIn('score', result)
        # ml_prob pode ser o valor inválido convertido ou o padrão
        # Pelo menos deve ser um número
        self.assertIsInstance(result['ml_prob'], (int, float))

    def test_app_inicializa_com_scorer_none_quando_modelo_ausente(self):
        """App deve inicializar com scorer=None quando ml_modelo não aponta para arquivo válido"""
        # Configurar para apontar para arquivo que não existe
        self.base_config['ml_modelo'] = "/path/que/nao/existe/modelo.pkl"
        
        with open(self.config_path, 'w') as f:
            json.dump(self.base_config, f)
        
        import importlib
        importlib.reload(config)
        
        # Criar o app - ele deve lidar com o modelo ausente graciosamente
        app = App()
        
        # Verificar que o scorer é None (não levantou exceção durante init)
        self.assertIsNone(app.scorer)
        # Também verificar que o signal_engine tem scorer=None
        self.assertIsNone(app.signal.scorer)

    def test_app_carrega_scorer_com_sucesso_quando_modelo_válido_mock(self):
        """App deve carregar scorer quando dado um mock válido"""
        # Para este teste, vamos mockar o scorer.py inteiro
        with patch('core.app.importlib.util') as mock_util:
            # Configurar o mock para retornar um módulo falso com ScorerML
            mock_spec = MagicMock()
            mock_util.spec_from_file_location.return_value = mock_spec
            
            mock_scorer_mod = MagicMock()
            mock_util.module_from_spec.return_value = mock_scorer_mod
            
            mock_scorer_class = MagicMock()
            mock_scorer_mod.ScorerML = mock_scorer_class
            
            mock_scorer_instance = MagicMock()
            mock_scorer_class.return_value = mock_scorer_instance
            
            # Configurar caminho do modelo existente
            # v14.8: App(config=...) injeta o config diretamente — o App()
            # sem argumentos lê o config.json da raiz (ml_modelo='') e o
            # arquivo temporário do teste nunca é lido.
            app = App(config={'ml_modelo': '/fake/path/modelo.pkl'})
            
            # Verificar que tentou carregar o scorer
            mock_util.spec_from_file_location.assert_called()
            # Verificar que criou uma instância do ScorerML
            mock_scorer_class.assert_called_once()
            # Verificar que o app e o signal_engine têm o scorer definido
            self.assertIsNotNone(app.scorer)
            self.assertIsNotNone(app.signal.scorer)
            # Eles devem ser a mesma instância (app.signal.scorer é definido a partir de app.scorer)
            self.assertEqual(app.scorer, app.signal.scorer)


if __name__ == '__main__':
    unittest.main()