# etl/processar_upload.py
import uuid
import pandas as pd
from sqlalchemy import text
from config.database import get_engine
from config.constantes import SESSAO_BASE_LOCAL

UNIDADE_ALIASES = {
    "KG": "KG", "QUILO": "KG", "QUILOS": "KG", "KILO": "KG", "KILOS": "KG",
    "L": "LITROS", "LT": "LITROS", "LITRO": "LITROS", "LITROS": "LITROS",
}

COLUNAS_ESPERADAS = {
    "Nome_Fornecedor", "Produto", "Unidade_Medida", "Data_Compra",
    "Quantidade", "Preco_Unitario", "Custo_Frete",
    "Prazo_Entrega_Prometido_Dias_Uteis", "Tempo_Entrega_Real_Dias_Uteis",
    "Pedido_Sofreu_Atraso", "Pedido_Sofreu_Avaria",
}

HORAS_RETENCAO_SESSAO = 24


def limpar_sessoes_antigas(engine, horas=HORAS_RETENCAO_SESSAO):
    """Apaga pedidos de uploads com mais de `horas` de idade, exceto a base
    local da empresa (SESSAO_BASE_LOCAL), que nunca deve ser limpa por aqui."""
    with engine.begin() as conn:
        resultado = conn.execute(
            text("""
                DELETE FROM pedido
                WHERE sessao_id != :sessao_base_local
                  AND criado_em < NOW() - (:horas || ' hours')::interval
            """),
            {"sessao_base_local": SESSAO_BASE_LOCAL, "horas": horas},
        )
        if resultado.rowcount:
            print(f"Limpeza automática: {resultado.rowcount} pedidos de sessões antigas removidos.")


def parse_data(valor):
    try:
        data = pd.to_datetime(valor, dayfirst=True, errors="raise")
        if data.year < 2000 or data.year > 2100:
            return None
        return data.date()
    except Exception:
        return None


def normalizar_unidade(valor):
    if pd.isnull(valor):
        return None
    texto = str(valor).strip().upper()
    return UNIDADE_ALIASES.get(texto)


def para_numero(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        texto = valor.strip().replace(",", ".")
        try:
            return float(texto)
        except ValueError:
            return None
    return None


def para_int_opcional(valor):
    if pd.isnull(valor):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def processar_planilha_upload(arquivo, sessao_id=None):
    """Processa uma planilha genérica enviada por um usuário do site e insere
    no banco marcada com um sessao_id próprio, isolando de outros uploads.

    Retorna: (sessao_id, total_validos, total_rejeitados, df_rejeitados)
    """
    engine = get_engine()
    limpar_sessoes_antigas(engine)

    if sessao_id is None:
        sessao_id = str(uuid.uuid4())

    df = pd.read_excel(arquivo)

    faltando = COLUNAS_ESPERADAS - set(df.columns)
    if faltando:
        raise ValueError(f"Colunas obrigatórias ausentes na planilha: {faltando}")

    unidade_por_produto = {}
    for _, row in df.iterrows():
        produto = row["Produto"]
        unidade = normalizar_unidade(row["Unidade_Medida"])
        if pd.notnull(produto) and unidade is not None and produto not in unidade_por_produto:
            unidade_por_produto[produto] = unidade

    fornecedores = df["Nome_Fornecedor"].dropna().unique()
    produtos = df["Produto"].dropna().unique()

    with engine.begin() as conn:
        for nome in fornecedores:
            conn.execute(
                text("INSERT INTO fornecedor (nome) VALUES (:nome) ON CONFLICT (nome) DO NOTHING"),
                {"nome": nome},
            )
        for nome in produtos:
            unidade = unidade_por_produto.get(nome, "KG")
            conn.execute(
                text("""
                    INSERT INTO produto (nome, unidade_medida) VALUES (:nome, :unidade)
                    ON CONFLICT (nome) DO NOTHING
                """),
                {"nome": nome, "unidade": unidade},
            )

    with engine.connect() as conn:
        mapa_fornecedor = {n: i for i, n in conn.execute(text("SELECT id, nome FROM fornecedor")).fetchall()}
        mapa_produto = {n: i for i, n in conn.execute(text("SELECT id, nome FROM produto")).fetchall()}

    validos = []
    rejeitados = []
    ids_ja_vistos = set()

    for idx, row in df.iterrows():
        motivo = None

        id_pedido = para_int_opcional(row.get("ID_Pedido"))
        tem_id = id_pedido is not None

        if pd.isnull(row["Nome_Fornecedor"]) or row["Nome_Fornecedor"] not in mapa_fornecedor:
            motivo = "Fornecedor inválido ou vazio"
        elif pd.isnull(row["Produto"]) or row["Produto"] not in mapa_produto:
            motivo = "Produto inválido ou vazio"
        elif tem_id and id_pedido in ids_ja_vistos:
            motivo = "ID_Pedido duplicado"
        else:
            data_convertida = parse_data(row["Data_Compra"])
            unidade_linha = normalizar_unidade(row["Unidade_Medida"])
            quantidade = para_numero(row["Quantidade"])
            preco_unitario = para_numero(row["Preco_Unitario"])
            custo_frete = para_numero(row["Custo_Frete"])
            prazo_prometido = para_numero(row["Prazo_Entrega_Prometido_Dias_Uteis"])
            tempo_real = para_numero(row["Tempo_Entrega_Real_Dias_Uteis"])

            if data_convertida is None:
                motivo = f"Data inválida: {row['Data_Compra']}"
            elif unidade_linha is None:
                motivo = f"Unidade de medida não reconhecida: {row['Unidade_Medida']}"
            elif quantidade is None:
                motivo = f"Quantidade inválida (esperado número): {row['Quantidade']}"
            elif preco_unitario is None:
                motivo = f"Preço unitário inválido (esperado número): {row['Preco_Unitario']}"
            elif custo_frete is None:
                motivo = f"Custo de frete inválido (esperado número): {row['Custo_Frete']}"
            elif prazo_prometido is None:
                motivo = f"Prazo prometido inválido (esperado número): {row['Prazo_Entrega_Prometido_Dias_Uteis']}"
            elif tempo_real is None:
                motivo = f"Tempo de entrega real inválido (esperado número): {row['Tempo_Entrega_Real_Dias_Uteis']}"

        if motivo:
            rejeitados.append({"linha_planilha": idx, "ID_Pedido": row.get("ID_Pedido"), "motivo": motivo})
            continue

        if tem_id:
            ids_ja_vistos.add(id_pedido)

        validos.append({
            "id_pedido_origem": id_pedido,
            "fornecedor_id": mapa_fornecedor[row["Nome_Fornecedor"]],
            "produto_id": mapa_produto[row["Produto"]],
            "data_compra": data_convertida,
            "quantidade": quantidade,
            "preco_unitario": preco_unitario,
            "custo_frete": custo_frete,
            "prazo_entrega_prometido_dias": int(prazo_prometido),
            "tempo_entrega_real_dias": int(tempo_real),
            "sofreu_atraso": str(row["Pedido_Sofreu_Atraso"]).strip().upper() == "SIM",
            "sofreu_avaria": str(row["Pedido_Sofreu_Avaria"]).strip().upper() == "SIM",
            "sessao_id": sessao_id,
        })

    df_validos = pd.DataFrame(validos)
    df_rejeitados = pd.DataFrame(rejeitados)

    if not df_validos.empty:
        with engine.begin() as conn:
            for _, row in df_validos.iterrows():
                conn.execute(
                    text("""
                        INSERT INTO pedido (
                            id_pedido_origem, fornecedor_id, produto_id, data_compra,
                            quantidade, preco_unitario, custo_frete,
                            prazo_entrega_prometido_dias, tempo_entrega_real_dias,
                            sofreu_atraso, sofreu_avaria, sessao_id
                        ) VALUES (
                            :id_pedido_origem, :fornecedor_id, :produto_id, :data_compra,
                            :quantidade, :preco_unitario, :custo_frete,
                            :prazo_entrega_prometido_dias, :tempo_entrega_real_dias,
                            :sofreu_atraso, :sofreu_avaria, :sessao_id
                        )
                    """),
                    row.to_dict(),
                )

    return sessao_id, len(df_validos), len(df_rejeitados), df_rejeitados