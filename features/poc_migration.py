# poc_migration_tracker.py — Migracao do POC ao vivo (v9.40)
# Rastreia movimento do POC: delta, velocidade, direcao
# Causal: so usa informacao disponivel ate t

class PocMigrationTracker:
    """Rastreia evolucao do POC ao longo do pregao."""
    
    def __init__(self):
        self._poc_anterior = None
        self._poc_atual = None
        self._poc_historico = []  # ultimos POCs (max 100)
        self._preco_anterior = None
    
    def update(self, preco, poc_ate_t):
        """Atualiza com preco atual e POC calculado ate este instante."""
        if preco is None or preco <= 0:
            return
        if poc_ate_t is None or poc_ate_t <= 0:
            return
        
        self._poc_anterior = self._poc_atual
        self._poc_atual = float(poc_ate_t)
        self._preco_anterior = float(preco)
        
        self._poc_historico.append(self._poc_atual)
        if len(self._poc_historico) > 100:
            self._poc_historico = self._poc_historico[-100:]
    
    def snapshot(self):
        """Retorna features de migracao do POC."""
        if self._poc_atual is None or self._poc_anterior is None:
            return {
                'poc_delta': 0.0,
                'poc_velocity': 0.0,
                'poc_direction': 0.0,
                'dist_preco_poc': 0.0,
                'preco_acima_poc': 0.0,
            }
        
        delta = self._poc_atual - self._poc_anterior
        velocity = delta  # delta por update (simplificado)
        
        # Direcao: media movel dos deltas
        if len(self._poc_historico) >= 5:
            recentes = self._poc_historico[-5:]
            dirs = [recentes[i] - recentes[i-1] for i in range(1, len(recentes))]
            direction = sum(dirs) / len(dirs) if dirs else 0.0
        else:
            direction = delta
        
        # Distancia preco-POC
        dist = 0.0
        acima = 0.0
        if self._preco_anterior and self._poc_atual:
            dist = self._preco_anterior - self._poc_atual
            acima = 1.0 if self._preco_anterior > self._poc_atual else 0.0
        
        return {
            'poc_delta': round(delta, 2),
            'poc_velocity': round(velocity, 2),
            'poc_direction': round(direction, 2),
            'dist_preco_poc': round(dist, 2),
            'preco_acima_poc': acima,
        }
