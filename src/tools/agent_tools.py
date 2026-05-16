"""Tools dos agentes NinoAgent — formato CrewAI BaseTool."""

import json
from typing import Type
from pydantic import BaseModel, Field
from crewai.tools import BaseTool

import httpx
from src.config import settings
from src.storage import store


# --- Schemas ---

class NumeroSchema(BaseModel):
    numero_whatsapp: str = Field(description="Número WhatsApp do cliente")

class MensagemSchema(BaseModel):
    numero: str = Field(description="Número WhatsApp destino")
    texto: str = Field(description="Texto da mensagem")

class SessaoSchema(BaseModel):
    numero: str = Field(description="Número WhatsApp")
    contexto_json: str = Field(description="Contexto em JSON string")

class NumeroSimplesSchema(BaseModel):
    numero: str = Field(description="Número WhatsApp")

class ConversaSchema(BaseModel):
    numero_whatsapp: str = Field(description="Número WhatsApp do cliente")
    tipo_cliente: str = Field(default="atacado", description="Tipo: atacado, varejo ou desconhecido")
    origem: str = Field(default="organico", description="Origem do contato")
    status: str = Field(default="resolvido", description="Status: resolvido, pendente ou convertido")
    aceitou_grupo: bool | None = Field(default=None, description="Se o cliente aceitou o convite para o grupo")
    cliente_recorrente: bool = Field(default=False, description="Se é cliente recorrente")

class AvaliacaoSchema(BaseModel):
    dados_json: str = Field(description=(
        "Dicionário Python com: conversa_id (int), sentimento (str), score (int 1-5), "
        "tema_principal (str max 50 chars), duvida_resolvida (bool), "
        "interesse_compra (bool), demanda_varejo (bool). "
        "Exemplo: {\"conversa_id\": 121, \"sentimento\": \"positivo\", \"score\": 4, "
        "\"tema_principal\": \"Pedido minimo\", \"duvida_resolvida\": True, "
        "\"interesse_compra\": True, \"demanda_varejo\": False}"
    ))

class DataSchema(BaseModel):
    data: str = Field(description="Data no formato YYYY-MM-DD")

class QuerySchema(BaseModel):
    query: str = Field(description="Texto para busca semântica")


# --- Tools ---

class ConsultarClienteTool(BaseTool):
    name: str = "consultar_cliente"
    description: str = "Consulta se o número já é cliente cadastrado. Retorna perfil ou 'novo_cliente'."

    def _run(self, numero_whatsapp: str) -> str:
        cliente = store.buscar_cliente(numero_whatsapp)
        if not cliente:
            return "novo_cliente"
        return f"cliente_id={cliente['id']} | nome={cliente['nome']} | tipo={cliente['tipo']} | membro_grupo={cliente['membro_grupo']}"


class EnviarMensagemTool(BaseTool):
    name: str = "enviar_mensagem"
    description: str = "Envia mensagem de texto via WhatsApp (Meta API oficial)."

    def _run(self, numero: str, texto: str) -> str:
        # Defesa em código contra LLM chamando enviar_mensagem 2x no mesmo turno
        # (bug histórico de 02/05). crew.py grava `current_exec_id` na sessão a
        # cada turno; aqui usamos INCR atômico no Redis com TTL curto.
        sessao = store.buscar_sessao(numero) or {}
        exec_id = sessao.get("current_exec_id")
        if exec_id:
            r_conn = store.redis_conn()
            chave = f"sent:{numero}:{exec_id}"
            n = r_conn.incr(chave)
            r_conn.expire(chave, 120)
            if n > 1:
                return "erro: enviar_mensagem ja foi chamado nesta execucao; envie tudo em uma unica chamada"

        to = numero if numero.startswith("+") else f"+{numero}"
        texto = texto.encode("utf-8", errors="ignore").decode("utf-8")
        url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": texto},
        }
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=10)
            r.raise_for_status()
            store.append_historico(numero, {"role": "agente", "text": texto})
            store.salvar_mensagem_sessao(numero, "agente", text=texto, type="text")
            return "mensagem_enviada"
        except httpx.HTTPStatusError as e:
            return f"erro_envio: {e} | body: {e.response.text}"
        except Exception as e:
            return f"erro_envio: {e}"


class SalvarContextoSessaoTool(BaseTool):
    name: str = "salvar_contexto_sessao"
    description: str = "Salva o contexto da conversa em andamento no Redis."

    def _run(self, numero: str, contexto_json: str) -> str:
        # Merge atômico: preserva historico e demais campos não enviados pelo LLM.
        store.merge_sessao(numero, **json.loads(contexto_json))
        return "contexto_salvo"


class BuscarContextoSessaoTool(BaseTool):
    name: str = "buscar_contexto_sessao"
    description: str = "Recupera o contexto da conversa em andamento do Redis."

    def _run(self, numero: str) -> str:
        dados = store.buscar_sessao(numero)
        return json.dumps(dados, ensure_ascii=False) if dados else "sem_sessao_ativa"


class RegistrarConversaTool(BaseTool):
    name: str = "registrar_conversa"
    description: str = "Persiste os dados finais da conversa no Postgres e indexa no Qdrant."

    def _run(self, numero_whatsapp: str, tipo_cliente: str = "atacado", origem: str = "organico",
             status: str = "resolvido", aceitou_grupo: bool | None = None,
             cliente_recorrente: bool = False) -> str:
        dados = {
            "numero_whatsapp": numero_whatsapp,
            "tipo_cliente": tipo_cliente,
            "origem": origem,
            "status": status,
            "aceitou_grupo": aceitou_grupo,
            "cliente_recorrente": cliente_recorrente,
        }
        conversa_id = store.salvar_conversa(dados)

        # Monta texto para indexação — busca histórico da sessão
        numero = numero_whatsapp
        sessao = store.buscar_sessao(numero) or {}
        historico = sessao.get("historico", [])
        texto = " | ".join([f"{m['role']}: {m['text']}" for m in historico])

        if texto:
            store.indexar_conversa(
                conversa_id=conversa_id,
                texto=texto,
                metadata={
                    "conversa_id": conversa_id,
                    "tipo_cliente": tipo_cliente,
                    "origem": origem,
                },
            )

        # Salva conversa_id na sessão para o SentimentAgent usar (merge atômico)
        if numero:
            store.merge_sessao(numero, conversa_id=conversa_id)

        return f"conversa_id={conversa_id}"


class RegistrarAvaliacaoTool(BaseTool):
    name: str = "registrar_avaliacao"
    description: str = (
        "Persiste a avaliação no Postgres. "
        "Passe dados_json com: conversa_id (int), sentimento (positivo/neutro/negativo), "
        "score (int 1-5), tema_principal (str max 50 chars), duvida_resolvida (bool), "
        "interesse_compra (bool), demanda_varejo (bool)."
    )

    def _run(self, dados_json: str) -> str:
        import ast
        try:
            # Normaliza: remove escapes extras se vier como string JSON dentro de string
            raw = dados_json.strip()
            if raw.startswith('"') and raw.endswith('"'):
                raw = raw[1:-1].replace('\\"', '"')
            # Tenta JSON (true/false minúsculo), depois ast (True/False Python)
            try:
                dados = json.loads(raw)
            except json.JSONDecodeError:
                dados = ast.literal_eval(raw)

            conversa_id = int(dados.get("conversa_id", 0))
            if conversa_id <= 0 or conversa_id > 2_147_483_647:
                return "erro: conversa_id inválido"

            store.salvar_avaliacao({
                "conversa_id": conversa_id,
                "sentimento": dados.get("sentimento"),
                "score_atendimento": dados.get("score"),
                "tema_principal": str(dados.get("tema_principal", ""))[:50],
                "duvida_resolvida": dados.get("duvida_resolvida"),
                "interesse_compra": dados.get("interesse_compra"),
                "demanda_varejo": dados.get("demanda_varejo", False),
            })
            return "avaliacao_salva"
        except Exception as e:
            return f"erro: {e}"


class BuscarAvaliacoesDiaTool(BaseTool):
    name: str = "buscar_avaliacoes_do_dia"
    description: str = "Busca todas as avaliações de um dia específico (formato YYYY-MM-DD)."

    def _run(self, data: str) -> str:
        avaliacoes = store.buscar_avaliacoes_dia(data)
        return json.dumps(avaliacoes, ensure_ascii=False, default=str)


class BuscarTemasRecorrentesTool(BaseTool):
    name: str = "buscar_temas_recorrentes"
    description: str = "Busca conversas semanticamente similares no Qdrant para identificar padrões."

    def _run(self, query: str) -> str:
        resultados = store.buscar_conversas_similares(query, limit=10)
        return json.dumps(resultados, ensure_ascii=False)


class EnviarCatalogTool(BaseTool):
    name: str = "enviar_catalogo"
    description: str = "Busca o PDF mais recente na pasta de catálogo do Google Drive e envia ao cliente via WhatsApp."

    def _run(self, numero_whatsapp: str) -> str:
        from io import BytesIO
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload

        creds = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_SA_CREDENTIALS_PATH,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)

        results = drive.files().list(
            q=f"'{settings.GOOGLE_DRIVE_CATALOG_FOLDER_ID}' in parents and mimeType='application/pdf' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=1,
            fields="files(id, name)",
        ).execute()

        files = results.get("files", [])
        if not files:
            return "erro: nenhum PDF encontrado na pasta do catálogo"

        file_id = files[0]["id"]
        file_name = files[0]["name"]

        # Baixa o PDF na memória — evita tornar o arquivo público no Drive.
        buf = BytesIO()
        request = drive.files().get_media(fileId=file_id)
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        pdf_bytes = buf.getvalue()

        # 1. Upload do PDF para a Meta — devolve media_id
        headers_auth = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
        try:
            upload_resp = httpx.post(
                f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/media",
                headers=headers_auth,
                files={"file": (file_name, pdf_bytes, "application/pdf")},
                data={"messaging_product": "whatsapp", "type": "application/pdf"},
                timeout=60,
            )
            upload_resp.raise_for_status()
            media_id = upload_resp.json()["id"]
        except Exception as e:
            return f"erro_upload: {e}"

        # 2. Envia a mensagem usando media_id (não link público)
        to = numero_whatsapp if numero_whatsapp.startswith("+") else f"+{numero_whatsapp}"
        url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": {"id": media_id, "filename": file_name},
        }
        try:
            r = httpx.post(url, json=payload, headers={**headers_auth, "Content-Type": "application/json"}, timeout=30)
            r.raise_for_status()
            # Registra no histórico para aparecer no monitor
            store.append_historico(numero_whatsapp, {
                "role": "agente", "type": "document", "media_id": media_id,
                "text": f"[catálogo: {file_name}]",
            })
            store.salvar_mensagem_sessao(numero_whatsapp, "agente",
                                         text=f"[catálogo: {file_name}]",
                                         type="document", media_id=media_id)
            return "catalogo_enviado"
        except Exception as e:
            return f"erro_envio: {e}"


# Instâncias prontas para uso
consultar_cliente = ConsultarClienteTool()
enviar_mensagem = EnviarMensagemTool()
salvar_contexto_sessao = SalvarContextoSessaoTool()
buscar_contexto_sessao = BuscarContextoSessaoTool()
registrar_conversa = RegistrarConversaTool()
registrar_avaliacao = RegistrarAvaliacaoTool()
buscar_avaliacoes_do_dia = BuscarAvaliacoesDiaTool()
buscar_temas_recorrentes = BuscarTemasRecorrentesTool()
enviar_catalogo = EnviarCatalogTool()
