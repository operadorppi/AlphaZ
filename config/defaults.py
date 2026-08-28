# -*- coding: utf-8 -*-
"""
config/defaults.py — ConfigCompleto e funções de config flat/aninhado.

Extraído de config.py para respeitar a arquitetura em camadas (v10.1).
O config.py raiz e config/__init__.py re-exportam daqui.
"""

# Mapeamento: chave_aninhada.secao -> chave_flat
NESTED_TO_FLAT = {
    'horarios.abertura_fim': 'horario_abertura_fim',
    'horarios.almoco_inicio': 'horario_almoco_inicio',
    'horarios.almoco_fim': 'horario_almoco_fim',
    'horarios.fechamento': 'horario_fechamento',
    'aprendizado.delta': 'aprendizado_delta',
    'aprendizado.decay': 'aprendizado_decay',
    'aprendizado.min_amostras': 'aprendizado_min_amostras',
    'circuit_breaker.nivel1_perdas': 'cb_nivel1_perdas',
    'circuit_breaker.nivel1_pnl': 'cb_nivel1_pnl',
    'circuit_breaker.nivel2_perdas': 'cb_nivel2_perdas',
    'circuit_breaker.nivel2_pnl': 'cb_nivel2_pnl',
    'circuit_breaker.nivel3_perdas': 'cb_nivel3_perdas',
    'circuit_breaker.nivel3_pnl': 'cb_nivel3_pnl',
    'trading.tp_pts': 'tp_pts',
    'trading.sl_pts': 'sl_pts',
    'trading.max_holding_s': 'max_holding_s',
    'trading.max_trades_dia': 'max_trades_dia',
    'trading.max_drawdown_dia': 'max_drawdown_dia_pontos',
    'trading.custo_execucao': 'custos_execucao_pontos',
    'web.host': 'web_host',
    'web.port': 'web_port',
    'rtd.book_linhas': 'book_linhas',
    'rtd.tt_linhas': 'tt_linhas',
    'rtd.poll_s': 'poll_s',
    'rtd.max_janelas': 'max_janelas',
    'position_sizing.target_risk_per_trade': 'target_risk_per_trade',
    'position_sizing.max_position_size': 'max_position_size',
}

# Alias para compatibilidade (testes usam nome com underscore)
_NESTED_TO_FLAT = NESTED_TO_FLAT


class ConfigCompleto:
    """Objeto com atributos flat para todas as chaves do config.json.

    Valores default vêm do motor original (motor_rt_alphaz_v9).
    Chaves adicionadas para paridade total com config.json + CONFIG.
    """
    def __init__(self):
        # Dados / paths
        self.base_dir = r'D:\MarketData\Profit'
        self.save_dir = r'D:\MarketData\mimo'
        self.ml_modelo = ''
        self.ml_threshold = 0.6
        # Web
        self.web_host = '127.0.0.1'
        self.web_port = 5001
        # Ativos
        self.ativos = ['WINV26', 'WDOU26']
        self.ativo_principal = 'WINV26'
        self.ativo_contexto = 'WDOU26'
        # RTD
        self.book_linhas = 500
        self.tt_linhas = 500
        self.poll_s = 0.02
        self.max_janelas = 12
        # Trading
        self.tp_pts = 100
        self.sl_pts = 50
        self.max_holding_s = 30
        self.max_trades_dia = 15
        self.tempo_max_posicao_s = 30
        self.custos_execucao_pontos = {'WIN': 5.0, 'WDO': 1.0}
        self.max_drawdown_dia_pontos = -500.0
        # Cooldown
        self.cooldown_entre_trades_s = 45
        self.max_perdas_consecutivas = 3
        # Horários
        self.horario_abertura_fim = (10, 0)
        self.horario_almoco_inicio = (12, 0)
        self.horario_almoco_fim = (13, 30)
        self.horario_fechamento = (16, 30)
        # Faixas de preço
        self.faixas_preco = {}
        # Book split (profundidade do book processada)
        self.book_split = 30
        # Circuit breaker (flat)
        self.cb_nivel1_perdas = 3
        self.cb_nivel1_pnl = -100
        self.cb_nivel2_perdas = 5
        self.cb_nivel2_pnl = -300
        self.cb_nivel3_perdas = 7
        self.cb_nivel3_pnl = -500
        # Aprendizado
        self.aprendizado_delta = 0.02
        self.aprendizado_decay = 0.998
        self.aprendizado_min_amostras = 5
        # Flags
        self.desligar_horarios_ruins = False
        self.normalizar_score = False
        # Position sizing
        self.target_risk_per_trade = 60
        self.max_position_size = 10


def _aplicar_valor_config(atual, novo):
    """Converte 'novo' para o tipo de 'atual' e retorna.

    - int -> int(novo)
    - float -> float(novo)
    - tuple -> tuple(novo) se for list/seq, senão mantém
    - bool -> bool(novo)
    - dict -> dict(novo)
    """
    if isinstance(atual, bool):
        return bool(novo)
    if isinstance(atual, int) and not isinstance(atual, bool):
        return int(novo)
    if isinstance(atual, float):
        return float(novo)
    if isinstance(atual, tuple):
        if isinstance(novo, (list, tuple)):
            return tuple(novo)
        return novo
    if isinstance(atual, dict):
        if isinstance(novo, dict):
            return dict(novo)
        return novo
    return novo


def _aplicar_chaves_flat(dados, cfg_obj):
    """Aplica chaves flat do dict 'dados' ao objeto cfg_obj.

    Chaves inexistentes como atributo são ignoradas.
    Chaves com override aninhado (ex: max_drawdown_dia_pontos quando
    trading.max_drawdown_dia também existe) são aplicadas normalmente —
    o aninhado prevalece depois porque é aplicado em segundo lugar.
    """
    for chave, valor in dados.items():
        if not hasattr(cfg_obj, chave):
            continue
        atual = getattr(cfg_obj, chave)
        setattr(cfg_obj, chave, _aplicar_valor_config(atual, valor))


def _aplicar_config_externa(ext, cfg_obj):
    """Aplica config.json completo ao ConfigCompleto.

    Ordem:
    1. Chaves flat diretas (ext['cooldown_entre_trades_s'] = 99)
    2. Seções aninhadas mapeadas para flat (ext['horarios']['abertura_fim'] -> horario_abertura_fim)
    3. Mappings aninhados diretos (ext['trading']['max_drawdown_dia'] -> max_drawdown_dia_pontos)

    Resultado: aninhado prevalece sobre flat (por ordem de aplicação).
    """
    # 1. Chaves flat diretas
    _aplicar_chaves_flat(ext, cfg_obj)

    # 2. Seções aninhadas mapeadas para flat
    for secao_key, secao_val in ext.items():
        if not isinstance(secao_val, dict):
            continue
        for sub_key, sub_val in secao_val.items():
            caminho = f"{secao_key}.{sub_key}"
            flat_key = NESTED_TO_FLAT.get(caminho)
            if flat_key and hasattr(cfg_obj, flat_key):
                atual = getattr(cfg_obj, flat_key)
                setattr(cfg_obj, flat_key, _aplicar_valor_config(atual, sub_val))
