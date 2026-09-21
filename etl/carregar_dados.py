# etl/carregar_dados.py
from pathlib import Path
import pandas as pd
from sqlalchemy import text
from config.database import get_engine

CAMINHO_PLANILHA = Path(__file__).resolve().parent.parent / "data" / "Dados_TCO_Teste.xlsx"

# Mapeia cada produto para sua unidade de medida fixa
UNIDADE_POR_PRODUTO = {
    "ACIDO SULFURICO PA": "LITROS",
    "ACIDO CLORIDRICO PA": "LITROS",
    "CLORETO DE SODIO PA": "KG",
}


def parse_data(valor):
    """Tenta converter a data; retorna None se não for possível."""
    try:
        data = pd.to_datetime(valor, dayfirst=True, errors="raise")
        if data.year < 2015 or data.year > 2026:
            return None
        return data.date()
    except Exception:
        return None


def carregar_dimensoes(engine, df):
    """Insere fornecedores e produtos únicos, evitando duplicar se rodar de novo."""
    fornecedores = df["Nome_Fornecedor"].dropna().unique()
    produtos = df["Produto"].dropna().unique()

    with engine.begin() as conn:
        for nome in fornecedores:
            conn.execute(
                text("""
                    INSERT INTO fornecedor (nome) VALUES (:nome)
                    ON CONFLICT (nome) DO NOTHING
                """),
                {"nome": nome},
            )
        for nome in produtos:
            unidade = UNIDADE_POR_PRODUTO.get(nome, "KG")
            conn.execute(
                text("""
                    INSERT INTO produto (nome, unidade_medida) VALUES (:nome, :unidade)
                    ON CONFLICT (nome) DO NOTHING
                """),
                {"nome": nome, "unidade": unidade},
            )


def buscar_mapa_ids(engine):
    """Busca os IDs já gravados no banco para fornecedor e produto."""
    with engine.connect() as conn:
        fornecedores = conn.execute(text("SELECT id, nome FROM fornecedor")).fetchall()
        produtos = conn.execute(text("SELECT id, nome, unidade_medida FROM produto")).fetchall()

    mapa_fornecedor = {nome: id_ for id_, nome in fornecedores}
    mapa_produto = {nome: (id_, unidade) for id_, nome, unidade in produtos}
    return mapa_fornecedor, mapa_produto


def validar_e_transformar(df, mapa_fornecedor, mapa_produto):
    """Separa linhas válidas das inválidas, aplicando as regras de negócio.

    ID_Pedido nulo NÃO é motivo de rejeição — é só um campo de rastreabilidade
    até a planilha original. A duplicidade só é verificável quando o ID existe.
    """
    validos = []
    rejeitados = []
    ids_ja_vistos = set()

    for idx, row in df.iterrows():
        motivo = None
        id_pedido = row["ID_Pedido"]
        tem_id = not pd.isnull(id_pedido)

        if tem_id and id_pedido in ids_ja_vistos:
            motivo = "ID_Pedido duplicado"
        else:
            data_convertida = parse_data(row["Data_Compra"])
            if data_convertida is None:
                motivo = f"Data inválida: {row['Data_Compra']}"

        if motivo:
            rejeitados.append({"linha_planilha": idx, "ID_Pedido": id_pedido, "motivo": motivo})
            continue

        if tem_id:
            ids_ja_vistos.add(id_pedido)

        produto_id, unidade = mapa_produto[row["Produto"]]

        if unidade == "KG":
            quantidade = row["Quantidade_Adquirida_KG"]
            preco_unitario = row["Preco_Unitario_KG"]
        else:
            quantidade = row["Quantidade_Adquirida_Litros"]
            preco_unitario = row["Preco_Unitario_Litros"]

        validos.append({
            "id_pedido_origem": int(id_pedido) if tem_id else None,
            "fornecedor_id": mapa_fornecedor[row["Nome_Fornecedor"]],
            "produto_id": produto_id,
            "data_compra": parse_data(row["Data_Compra"]),
            "quantidade": quantidade,
            "preco_unitario": preco_unitario,
            "custo_frete": row["Custo_Frete"],
            "prazo_entrega_prometido_dias": row["Prazo_Entrega_Prometido_Dias_Uteis"],
            "tempo_entrega_real_dias": row["Tempo_Entrega_Real_Dias_Uteis"],
            "sofreu_atraso": row["Pedido_Sofreu_Atraso"].strip().upper() == "SIM",
            "sofreu_avaria": row["Pedido_Sofreu_Avaria"].strip().upper() == "SIM",
        })

    return pd.DataFrame(validos), pd.DataFrame(rejeitados)


def inserir_pedidos(engine, df_validos):
    with engine.begin() as conn:
        for _, row in df_validos.iterrows():
            dados = row.to_dict()

            # O Pandas converte a coluna inteira para float64 quando mistura
            # None com números, e o None vira NaN — que não é um NULL válido
            # para o Postgres. Corrigimos isso explicitamente aqui.
            if pd.isna(dados["id_pedido_origem"]):
                dados["id_pedido_origem"] = None
            else:
                dados["id_pedido_origem"] = int(dados["id_pedido_origem"])

            conn.execute(
                text("""
                    INSERT INTO pedido (
                        id_pedido_origem, fornecedor_id, produto_id, data_compra,
                        quantidade, preco_unitario, custo_frete,
                        prazo_entrega_prometido_dias, tempo_entrega_real_dias,
                        sofreu_atraso, sofreu_avaria
                    ) VALUES (
                        :id_pedido_origem, :fornecedor_id, :produto_id, :data_compra,
                        :quantidade, :preco_unitario, :custo_frete,
                        :prazo_entrega_prometido_dias, :tempo_entrega_real_dias,
                        :sofreu_atraso, :sofreu_avaria
                    )
                """),
                dados,
            )


def main():
    engine = get_engine()
    df = pd.read_excel(CAMINHO_PLANILHA)

    print(f"Planilha lida: {len(df)} linhas.")

    # Recarga completa: limpa os pedidos antes de inserir de novo,
    # evitando duplicar registros já carregados em execuções anteriores.
    # A planilha é tratada como a fonte única da verdade.
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE pedido RESTART IDENTITY"))

    carregar_dimensoes(engine, df)
    mapa_fornecedor, mapa_produto = buscar_mapa_ids(engine)

    df_validos, df_rejeitados = validar_e_transformar(df, mapa_fornecedor, mapa_produto)

    print(f"Linhas válidas: {len(df_validos)}")
    print(f"Linhas rejeitadas: {len(df_rejeitados)}")

    if not df_rejeitados.empty:
        df_rejeitados.to_csv(CAMINHO_PLANILHA.parent / "linhas_rejeitadas.csv", index=False)
        print(f"Detalhe das rejeições salvo em {CAMINHO_PLANILHA.parent / 'linhas_rejeitadas.csv'}")

    inserir_pedidos(engine, df_validos)
    print("Pedidos inseridos no banco com sucesso.")


if __name__ == "__main__":
    main()