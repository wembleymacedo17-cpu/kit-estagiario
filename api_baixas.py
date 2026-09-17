"""
API do sistema Kit Escolar.

Responsável pela parte do fluxo que acontece DEPOIS que o colaborador já
finalizou o cadastro no Streamlit (kit-colaborador.py) e recebeu o QR Code.

Rotas:
- GET  /api/kits/consultar/{codigo_retirada}   -> usada quando a câmera lê o QR Code
- POST /api/kits/dar-baixa/{codigo_retirada}   -> usada quando o funcionário aperta "ENTREGAR KIT"

Para rodar:
    uvicorn api_baixas:app --reload
"""
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from datetime import datetime
import uvicorn
from conector_Postgre import SupabaseConnector 

app = FastAPI(title="API de Baixas - Kit Escolar")
db_connector = SupabaseConnector()

class PayloadQRCode(BaseModel):
    codigo_retirada: str

# 1. NOVA ROTA: Busca as informações antes de dar a baixa
@app.get("/api/v1/retiradas/info/{codigo_retirada}", status_code=status.HTTP_200_OK)
def buscar_informacoes_qrcode(codigo_retirada: str):
    # Query faz o cruzamento (JOIN) entre as 3 tabelas
    query_info = """
        SELECT 
            r.id_colaborador, 
            r.status, 
            r.qtd_kits,
            d.nome_filho, 
            e.kit_escolhido
        FROM public.retiradas r
        LEFT JOIN public.escolhas_kits e ON r.id_colaborador = e.id_colaborador
        LEFT JOIN public.dependentes d ON e.id_dependente = d.id_dependente
        WHERE r.codigo_retirada = :codigo_qr
    """
    
    try:
        linhas = db_connector.consultar_dados(query_info, {"codigo_qr": codigo_retirada})
        
        if not linhas:
            raise HTTPException(status_code=404, detail="QR Code não encontrado.")
            
        # Como o select retorna uma linha por filho, vamos agrupar tudo
        dados_formatados = {
            "codigo_retirada": codigo_retirada,
            "status": linhas[0]["status"],
            "id_colaborador": linhas[0]["id_colaborador"],
            "qtd_kits": linhas[0]["qtd_kits"],
            "dependentes": []
        }
        
        for linha in linhas:
            if linha["nome_filho"]: # Garante que só adiciona se houver dependente
                dados_formatados["dependentes"].append({
                    "nome_filho": linha["nome_filho"],
                    "kit_escolhido": linha["kit_escolhido"]
                })
                
        return dados_formatados
        
    except HTTPException:
        raise
    except Exception as erro:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(erro)}")

# 2. ROTA DE BAIXA: Agora só é acionada quando o botão for clicado no Streamlit
@app.put("/api/v1/retiradas/baixa", status_code=status.HTTP_200_OK)
def realizar_baixa(payload: PayloadQRCode):
    query_update = """
        UPDATE public.retiradas
        SET 
            status = 'ENTREGUE', 
            data_entrega = :data_atual
        WHERE 
            codigo_retirada = :codigo_qr 
            AND status = 'PENDENTE'
        RETURNING id_retirada, id_colaborador, qtd_kits;
    """
    agora = datetime.now()
    parametros = {"data_atual": agora, "codigo_qr": payload.codigo_retirada}
    
    try:
        resultado = db_connector.executar_baixa(query_update, parametros)
        if not resultado:
            raise HTTPException(status_code=404, detail="Kit já entregue ou QR Code inválido.")
            
        return {"status": "sucesso", "mensagem": "Baixa realizada com sucesso!"}
    except HTTPException:
        raise
    except Exception as erro:
        raise HTTPException(status_code=500, detail=str(erro))

if __name__ == "__main__":
    uvicorn.run("api_baixas:app", host="0.0.0.0", port=8000, reload=True)