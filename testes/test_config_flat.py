import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_config_flat.py — Testes R3: config.json é aplicado de verdade (flat + aninhado)

ANTES: `_carregar_config_externa` lia apenas um subconjunto de seções aninhadas
(web, ativos, rtd, trading, circuit_breaker, save_dir). Dezenas de chaves flat
(cooldown_entre_trades_s, percentil_*, faixas_preco, estrategias, horario_*,
cb_*, aprendizado_*...) eram IGNORADAS silenciosamente — editar config.json não
tinha efeito. Seções 'horarios' e 'aprendizado' também eram ignoradas.

DEPOIS: todas as chaves flat com atributo correspondente são aplicadas
(com conversão de tipo), e os mappings aninhados continuam prevalecendo em
conflito — preservando o comportamento efetivo atual.
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg_mod

# chaves flat que têm override aninhado (mappings existentes) — o aninhado
# prevalece por design; excluídas do teste de paridade direta
CHAVES_COM_OVERRIDE_ANINHADO = {
    'web_host', 'web_port', 'ativos', 'book_linhas', 'tt_linhas',
    'max_trades_dia', 'max_drawdown_dia_pontos', 'tempo_max_posicao_s',
    'custos_execucao_pontos',
    'cb_nivel1_perdas', 'cb_nivel1_pnl', 'cb_nivel2_perdas', 'cb_nivel2_pnl',
    'cb_nivel3_perdas', 'cb_nivel3_pnl',
    'save_dir',
    'horario_abertura_fim', 'horario_almoco_inicio', 'horario_almoco_fim',
    'horario_fechamento',
    'aprendizado_delta', 'aprendizado_decay', 'aprendizado_min_amostras',
}


class TestAplicarValorConfig:
    """Conversão de tipo do valor JSON para o tipo do atributo."""

    def test_converte_tipos(self):
        assert cfg_mod._aplicar_valor_config(45, 99) == 99          # int
        assert cfg_mod._aplicar_valor_config(0.75, 0.9) == 0.9      # float
        assert cfg_mod._aplicar_valor_config((10, 0), [9, 30]) == (9, 30)  # list -> tuple
        assert cfg_mod._aplicar_valor_config(True, False) is False  # bool
        assert cfg_mod._aplicar_valor_config(True, 'x') is True     # bool nunca vira True por string
        assert cfg_mod._aplicar_valor_config({}, {'WIN': [1, 2]}) == {'WIN': [1, 2]}  # dict


class TestAplicarChavesFlat:
    """R3: chaves flat são aplicadas; inexistentes são ignoradas."""

    def test_aplica_apenas_atributos_existentes(self):
        cfg_obj = cfg_mod.ConfigCompleto()
        cfg_mod._aplicar_chaves_flat({
            'cooldown_entre_trades_s': 99,
            'max_perdas_consecutivas': 7,
            'horario_abertura_fim': [9, 0],
            'faixas_preco': {'WIN': [1, 2]},
            'chave_inexistente': 123,
        }, cfg_obj)

        assert cfg_obj.cooldown_entre_trades_s == 99
        assert cfg_obj.max_perdas_consecutivas == 7
        assert cfg_obj.horario_abertura_fim == (9, 0)
        assert cfg_obj.faixas_preco == {'WIN': [1, 2]}
        assert not hasattr(cfg_obj, 'chave_inexistente')

    def test_secoes_aninhadas_horarios_e_aprendizado(self):
        """R3: seções 'horarios' e 'aprendizado' antes ignoradas agora valem."""
        cfg_obj = cfg_mod.ConfigCompleto()
        cfg_mod._aplicar_config_externa({
            'horarios': {'abertura_fim': [9, 30], 'fechamento': [17, 45]},
            'aprendizado': {'delta': 0.05, 'min_amostras': 10},
        }, cfg_obj)

        assert cfg_obj.horario_abertura_fim == (9, 30)
        assert cfg_obj.horario_fechamento == (17, 45)
        assert cfg_obj.aprendizado_delta == 0.05
        assert cfg_obj.aprendizado_min_amostras == 10

    def test_aninhado_prevalece_sobre_flat(self):
        """Comportamento efetivo atual preservado: trading.max_drawdown_dia=-300
        prevalece sobre a chave flat -500 (ordem: flat primeiro, aninhado depois)."""
        cfg_obj = cfg_mod.ConfigCompleto()
        cfg_mod._aplicar_config_externa({
            'max_drawdown_dia_pontos': -500.0,
            'trading': {'max_drawdown_dia': -300},
        }, cfg_obj)
        assert cfg_obj.max_drawdown_dia_pontos == -300

    def test_paridade_config_json_real(self):
        """Para cada chave flat escalar do config.json (sem override aninhado),
        o ConfigCompleto efetivo reflete o valor do JSON."""
        cfg_path = os.path.join(os.path.dirname(cfg_mod.__file__), 'config.json')
        with open(cfg_path, 'r', encoding='utf-8') as f:
            ext = json.load(f)

        cfg_obj = cfg_mod.ConfigCompleto()
        cfg_mod._aplicar_config_externa(ext, cfg_obj)

        verificadas = 0
        for chave, valor in ext.items():
            if chave in CHAVES_COM_OVERRIDE_ANINHADO:
                continue
            if not hasattr(cfg_obj, chave):
                continue
            atual = getattr(cfg_obj, chave)
            esperado = cfg_mod._aplicar_valor_config(atual, valor)
            assert atual == esperado, (
                f"chave flat '{chave}' ignorada pelo loader (R3): "
                f"efetivo={atual!r} config.json={valor!r}"
            )
            verificadas += 1

        assert verificadas >= 5, (
            f"Poucas chaves flat verificadas ({verificadas}) — config.json mudou?"
        )