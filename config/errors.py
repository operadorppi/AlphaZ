"""Erros de configuração (FASE 10 P1)."""


class ConfigError(Exception):
    """Erro de configuração explícito.

    Nunca se silencia divergência: todo conflito, chave desconhecida ou
    valor inválido levanta esta exceção com a localização exata da chave.
    """
