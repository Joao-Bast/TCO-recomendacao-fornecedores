# config/monitoramento.py
"""Métricas de negócio enviadas ao New Relic.

O agente do New Relic é iniciado pelo comando do contêiner (newrelic-admin) e só
fica ativo quando existe a variável NEW_RELIC_LICENSE_KEY. Sem ela (desenvolvimento
local e testes), tudo aqui é ignorado.

Nenhum dado de pedido, fornecedor ou produto é enviado: só contagens, tempos e o
resultado do modelo."""
import os
import traceback

try:
    import newrelic.agent as _nr
except ImportError:  # agente não instalado
    _nr = None


def _ativo():
    return _nr is not None and bool(os.getenv("NEW_RELIC_LICENSE_KEY"))


def registrar_evento(tipo, atributos):
    """Envia um evento personalizado. O monitoramento nunca derruba a aplicação."""
    if not _ativo():
        return
    try:
        _nr.record_custom_event(tipo, atributos)
    except Exception:
        traceback.print_exc()


def registrar_tendencia(tendencia, duracao_segundos):
    """Resultado do cálculo de tendência: se o modelo foi usado, qual venceu e quanto levou.
    O tempo inclui a leitura do histórico e o treino dos três modelos."""
    atributos = {
        "confiavel": bool(tendencia["confiavel"]),
        "motivo": tendencia["motivo"] or "nenhum",
        "modelo": tendencia["modelo"] or "nenhum",
        "r2": tendencia["r2"],
        "variacao_percentual": tendencia["variacao_percentual"],
        "total_pedidos": tendencia["total_pedidos"],
        "tempo_calculo_segundos": round(duracao_segundos, 3),
    }
    registrar_evento("TendenciaCalculada", {k: v for k, v in atributos.items() if v is not None})


def registrar_upload(validos, rejeitados, duracao_segundos):
    """Resultado de um upload: quantas linhas entraram, quantas foram rejeitadas e o tempo."""
    total = validos + rejeitados
    registrar_evento("UploadProcessado", {
        "pedidos_validos": validos,
        "pedidos_rejeitados": rejeitados,
        "percentual_rejeitado": round(rejeitados / total * 100, 1) if total else 0.0,
        "duracao_segundos": round(duracao_segundos, 3),
    })