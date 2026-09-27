# app.py
import os
import uuid
import traceback
from pathlib import Path
from flask import Flask, request, jsonify, send_file, session, render_template
from dotenv import load_dotenv

from etl.processar_upload import processar_planilha_upload
from scoring.calcular_tco import calcular_ranking
from modelo.previsao_preco import listar_produtos_cadastrados

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

BASE_DIR = Path(__file__).resolve().parent
PLANILHA_MODELO = BASE_DIR / "static" / "planilha_modelo.xlsx"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/planilha-modelo")
def planilha_modelo():
    return send_file(PLANILHA_MODELO, as_attachment=True, download_name="planilha_modelo.xlsx")


@app.route("/upload", methods=["POST"])
def upload():
    arquivo = request.files.get("arquivo")
    if arquivo is None or arquivo.filename == "":
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    nova_sessao = str(uuid.uuid4())
    try:
        sessao_id, total_validos, total_rejeitados, _ = processar_planilha_upload(arquivo, nova_sessao)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"erro": f"Erro ao processar a planilha: {e}"}), 500

    session["sessao_id"] = sessao_id

    return jsonify({
        "sessao_id": sessao_id,
        "total_validos": total_validos,
        "total_rejeitados": total_rejeitados,
    })


@app.route("/produtos")
def produtos():
    sessao_id = session.get("sessao_id")
    if not sessao_id:
        return jsonify({"erro": "Nenhuma planilha enviada ainda nesta sessão."}), 400

    return jsonify({"produtos": listar_produtos_cadastrados(sessao_id)})


@app.route("/ranking", methods=["POST"])
def ranking():
    sessao_id = session.get("sessao_id")
    if not sessao_id:
        return jsonify({"erro": "Nenhuma planilha enviada ainda nesta sessão."}), 400

    dados = request.get_json(silent=True) or {}
    produto = dados.get("produto")
    if not produto:
        return jsonify({"erro": "Informe o produto."}), 400

    try:
        top3, modelo_confiavel, r2 = calcular_ranking(produto, sessao_id)
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"erro": f"Erro ao calcular ranking: {e}"}), 500

    return jsonify({
        "produto": produto,
        "modelo_confiavel": modelo_confiavel,
        "r2": r2,
        "ranking": top3,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)