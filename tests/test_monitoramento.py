"""Testes do envio de métricas ao New Relic: só age com a licença configurada
e nunca derruba a aplicação."""
import pytest

from config import monitoramento


class AgenteFalso:
    def __init__(self):
        self.eventos = []

    def record_custom_event(self, tipo, atributos):
        self.eventos.append((tipo, atributos))


@pytest.fixture
def agente(monkeypatch):
    falso = AgenteFalso()
    monkeypatch.setattr(monitoramento, "_nr", falso)
    monkeypatch.setenv("NEW_RELIC_LICENSE_KEY", "chave-de-teste")
    return falso


TENDENCIA = {
    "confiavel": True,
    "motivo": None,
    "modelo": "Random Forest",
    "r2": 0.52,
    "variacao_percentual": 103.3,
    "total_pedidos": 108,
    "preco_referencia": 86.0,
    "media_historica": 42.0,
    "data_referencia": "2026-07-27",
    "serie_mensal": [{"mes": "2026-07", "preco_medio": 80.0, "pedidos": 4}],
}


def test_sem_licenca_o_evento_nao_e_enviado(agente, monkeypatch):
    monkeypatch.delenv("NEW_RELIC_LICENSE_KEY")

    monitoramento.registrar_evento("Teste", {"a": 1})

    assert agente.eventos == []


def test_com_licenca_o_evento_e_enviado(agente):
    monitoramento.registrar_evento("Teste", {"a": 1})

    assert agente.eventos == [("Teste", {"a": 1})]


def test_falha_do_agente_nao_derruba_a_aplicacao(monkeypatch):
    class AgenteQuebrado:
        def record_custom_event(self, tipo, atributos):
            raise RuntimeError("agente fora do ar")

    monkeypatch.setattr(monitoramento, "_nr", AgenteQuebrado())
    monkeypatch.setenv("NEW_RELIC_LICENSE_KEY", "chave-de-teste")

    assert monitoramento.registrar_evento("Teste", {"a": 1}) is None


def test_sem_agente_instalado_nada_acontece(monkeypatch):
    monkeypatch.setattr(monitoramento, "_nr", None)
    monkeypatch.setenv("NEW_RELIC_LICENSE_KEY", "chave-de-teste")

    assert monitoramento.registrar_evento("Teste", {"a": 1}) is None


def test_evento_de_tendencia_leva_so_o_que_importa(agente):
    """Só resultados do modelo: nada de produto, fornecedor ou série de preços."""
    monitoramento.registrar_tendencia(TENDENCIA, 1.23456)

    tipo, atributos = agente.eventos[0]
    assert tipo == "TendenciaCalculada"
    assert atributos == {
        "confiavel": True,
        "motivo": "nenhum",
        "modelo": "Random Forest",
        "r2": 0.52,
        "variacao_percentual": 103.3,
        "total_pedidos": 108,
        "tempo_calculo_segundos": 1.235,
    }


def test_evento_de_tendencia_sem_modelo_omite_o_r2(agente):
    sem_tendencia = {
        **TENDENCIA, "confiavel": False, "motivo": "poucos_pedidos",
        "modelo": None, "r2": None, "variacao_percentual": 0.0, "total_pedidos": 40,
    }

    monitoramento.registrar_tendencia(sem_tendencia, 0.5)

    atributos = agente.eventos[0][1]
    assert atributos["confiavel"] is False
    assert atributos["motivo"] == "poucos_pedidos"
    assert atributos["modelo"] == "nenhum"
    assert "r2" not in atributos


def test_evento_de_upload_calcula_o_percentual_rejeitado(agente):
    monitoramento.registrar_upload(306, 6, 15.04321)

    assert agente.eventos == [("UploadProcessado", {
        "pedidos_validos": 306,
        "pedidos_rejeitados": 6,
        "percentual_rejeitado": 1.9,
        "duracao_segundos": 15.043,
    })]


def test_evento_de_upload_sem_linhas_nao_divide_por_zero(agente):
    monitoramento.registrar_upload(0, 0, 0.2)

    assert agente.eventos[0][1]["percentual_rejeitado"] == 0.0