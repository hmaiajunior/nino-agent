"""Tools dos agentes NinoAgent — formato CrewAI BaseTool."""

import json
import re
from typing import Type
from pydantic import BaseModel, Field
from crewai.tools import BaseTool

import httpx
from src.config import settings
from src.storage import store


def _dominio_site() -> str:
    """Retorna o domínio do site sem protocolo/www/barra final."""
    site = (settings.SITE_URL or "").lower()
    for prefix in ("https://", "http://", "www."):
        if site.startswith(prefix):
            site = site[len(prefix):]
    return site.rstrip("/")


def _texto_redireciona_ao_site(texto: str) -> bool:
    """True se o texto enviado pela Bia menciona o domínio do site.

    Normaliza http/https/www/barra final para captar variações ("www.x.com.br",
    "x.com.br", "https://www.x.com.br/").
    """
    dominio = _dominio_site()
    if not dominio or not texto:
        return False
    return dominio in texto.lower()


def _remover_url_site(texto: str) -> str:
    """Remove o URL do site (e o conectivo imediatamente anterior) do texto.

    Defesa em código de último caso — em uso normal, a Bia recebe instrução no
    prompt para não repetir o link em 30 min. Quando ela escapa, a frase aqui
    pode ficar levemente truncada, mas o cliente não vê o URL duplicado.
    """
    dominio = _dominio_site()
    if not dominio:
        return texto
    padrao = re.compile(
        r"(?:\b(?:em|no|l[áa]|aqui|pelo)\s+)?"          # conectivo opcional
        r"(?:link\s+(?:do\s+)?site|site|l[áa])?"        # noun-phrase opcional
        r"\s*[:\-—]?\s*"                                # separador opcional
        r"(?:https?://)?(?:www\.)?" + re.escape(dominio) + r"(?:/\S*)?",
        re.IGNORECASE,
    )
    texto_limpo = padrao.sub("", texto)
    texto_limpo = re.sub(r"\s+([.!?,;:])", r"\1", texto_limpo)
    texto_limpo = re.sub(r"\s{2,}", " ", texto_limpo)
    return texto_limpo.strip()


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

        # Cooldown do link do site: 30 min por número. Se a Bia tentar reenviar
        # o URL dentro da janela, removemos o link antes do envio (texto continua,
        # só o URL some). Evita repetição sem perder o teor da mensagem.
        site_mencionado_originalmente = _texto_redireciona_ao_site(texto)
        if site_mencionado_originalmente and store.site_em_cooldown(numero):
            texto = _remover_url_site(texto)

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
            # B9: detecta envio do link do grupo e seta flag persistente.
            # Mais robusto que escanear o histórico em cada turno (que falhava
            # se o link no .env mudasse).
            if settings.GRUPO_LINK and settings.GRUPO_LINK in texto:
                store.merge_sessao(numero, grupo_convidado=True)
            # Conta redirecionamento ao site. Considera apenas envio efetivo
            # do URL (o cooldown pode ter removido o link entre a chamada e o
            # POST). Após 2 envios nesta conversa, a 3ª iteração da Bia é
            # escalada silenciosamente para humano.
            if _texto_redireciona_ao_site(texto):
                store.contar_redirect_site(numero)
                store.marcar_site_enviado(numero)  # arma cooldown de 30 min
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
    description: str = (
        "Envia o catálogo de atacado (PDF) ao cliente via WhatsApp. "
        "EXCLUSIVO para clientes atacado (lojistas) — tem preços de atacado. "
        "Para varejo, recusa com instrução de cadastro de revendedor."
    )

    def _run(self, numero_whatsapp: str) -> str:
        # O3: defesa em código contra envio para varejo. O catálogo tem preços
        # de atacado e cadastro de revendedor é o caminho para varejo virar atacado.
        sessao = store.buscar_sessao(numero_whatsapp) or {}
        if sessao.get("tipo_cliente") == "varejo":
            return (
                "recusado_varejo: cliente é VAREJO. NÃO envie o catálogo. "
                "Em vez disso, chame enviar_mensagem explicando que o catálogo é "
                "exclusivo para lojistas (tem preços de atacado), que ele pode ver "
                f"todos os produtos no site {settings.SITE_URL}, e que se quiser "
                "comprar com preço de atacado precisa fazer o cadastro de revendedor "
                "no site. A análise leva até 48h, mas geralmente sai bem antes."
            )

        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_SA_CREDENTIALS_PATH,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)

        # Inclui modifiedTime para invalidar cache quando o PDF é atualizado.
        results = drive.files().list(
            q=f"'{settings.GOOGLE_DRIVE_CATALOG_FOLDER_ID}' in parents and mimeType='application/pdf' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=1,
            fields="files(id, name, modifiedTime)",
        ).execute()

        files = results.get("files", [])
        if not files:
            return "erro: nenhum PDF encontrado na pasta do catálogo"

        file_id = files[0]["id"]
        file_name = files[0]["name"]
        modified_time = files[0].get("modifiedTime", "")

        # Defesa B6: limite de 1 catálogo por execução. Texto via enviar_mensagem
        # continua permitido em paralelo — o agente pode mandar catálogo + 1 texto.
        sessao = store.buscar_sessao(numero_whatsapp) or {}
        exec_id = sessao.get("current_exec_id")
        if exec_id:
            r_conn = store.redis_conn()
            chave_exec = f"sent_catalog:{numero_whatsapp}:{exec_id}"
            n = r_conn.incr(chave_exec)
            r_conn.expire(chave_exec, 120)
            if n > 1:
                return "erro: enviar_catalogo ja foi chamado nesta execucao"

        # Cache do media_id: o PDF é o mesmo para todos os clientes e o media_id
        # da Meta vale ~30 dias. Hash da chave inclui modifiedTime — se o PDF for
        # atualizado no Drive, cache fica órfão automaticamente. TTL de 24h para
        # margem confortável.
        cache_key = f"catalog:media_id:{file_id}:{modified_time}"
        r_conn = store.redis_conn()
        media_id = r_conn.get(cache_key)

        headers_auth = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}

        if not media_id:
            # Cache miss: baixa do Drive + upload pra Meta + cacheia
            from io import BytesIO
            from googleapiclient.http import MediaIoBaseDownload

            buf = BytesIO()
            request = drive.files().get_media(fileId=file_id)
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buf.seek(0)
            pdf_bytes = buf.getvalue()

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

            r_conn.setex(cache_key, 86400, media_id)  # 24h

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
