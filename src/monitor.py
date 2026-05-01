"""Router de monitoramento — lista conversas e expõe ações de controle humano."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.config import settings
from src.storage import store
from src.whatsapp import enviar_whatsapp

router = APIRouter(tags=["monitor"])


class _EnvioBody(BaseModel):
    texto: str


# --- Auth ---

def _token(token: str = Query(..., description="Token de acesso ao monitor")):
    if not settings.MONITOR_TOKEN or token != settings.MONITOR_TOKEN:
        raise HTTPException(status_code=403, detail="Token inválido")


# --- Helpers ---

def _ultima_atividade(historico: list[dict]) -> str | None:
    """Retorna timestamp ISO da última mensagem ou None se histórico vazio."""
    # O histórico não armazena timestamp individualmente; usamos now() como proxy
    # para sessões ativas — o TTL do Redis já garante que só existem as recentes.
    return datetime.now(timezone.utc).isoformat() if historico else None


def _preview(historico: list[dict]) -> str:
    """Última mensagem do cliente truncada em 60 chars."""
    msgs_cliente = [m["text"] for m in historico if m["role"] == "cliente"]
    if not msgs_cliente:
        return ""
    return msgs_cliente[-1][:60]


# --- Endpoints ---

@router.get("/conversas")
def listar_conversas(_=Depends(_token)):
    """Lista conversas ativas (Redis) + encerradas (Postgres), sem duplicatas."""
    ativas = store.listar_sessoes_ativas()
    encerradas = store.buscar_conversas_recentes(dias=7)

    numeros_ativos = {s["numero"] for s in ativas}

    resultado: list[dict] = []

    for sessao in ativas:
        historico = sessao.get("historico", [])
        resultado.append({
            "numero": sessao["numero"],
            "nome": None,  # Redis não armazena nome; será enriquecido pelo frontend se necessário
            "status": "ativa",
            "modo": sessao.get("modo", "agente"),
            "tipo_cliente": sessao.get("tipo_cliente"),
            "ultima_mensagem": _preview(historico),
            "ultima_atividade": _ultima_atividade(historico),
        })

    for conv in encerradas:
        numero = conv["numero_whatsapp"]
        if numero in numeros_ativos:
            continue  # já incluída como ativa
        encerrada_em = conv.get("encerrada_em")
        resultado.append({
            "numero": numero,
            "nome": conv.get("nome"),
            "status": "encerrada",
            "modo": "agente",
            "tipo_cliente": conv.get("tipo_cliente"),
            "ultima_mensagem": "",
            "ultima_atividade": encerrada_em.isoformat() if encerrada_em else None,
        })

    # Ordena por ultima_atividade decrescente (None vai para o final)
    resultado.sort(key=lambda c: c["ultima_atividade"] or "", reverse=True)
    return resultado


@router.get("/conversas/{numero}")
def detalhar_conversa(numero: str, _=Depends(_token)):
    """Retorna histórico completo de uma conversa (Redis se ativa, Postgres se encerrada)."""
    sessao = store.buscar_sessao(numero)

    if sessao:
        cliente = store.buscar_cliente(numero)
        return {
            "numero": numero,
            "nome": cliente["nome"] if cliente else None,
            "status": "ativa",
            "modo": sessao.get("modo", "agente"),
            "tipo_cliente": sessao.get("tipo_cliente"),
            "historico": sessao.get("historico", []),
        }

    # Sem sessão ativa — busca a conversa mais recente no Postgres
    encerradas = store.buscar_conversas_recentes(dias=30)
    conv = next((c for c in encerradas if c["numero_whatsapp"] == numero), None)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")

    return {
        "numero": numero,
        "nome": conv.get("nome"),
        "status": "encerrada",
        "modo": "agente",
        "tipo_cliente": conv.get("tipo_cliente"),
        # Histórico não é persistido no Postgres por enquanto; retorna vazio
        "historico": [],
    }


@router.post("/conversas/{numero}/assumir")
def assumir_conversa(numero: str, _=Depends(_token)):
    """Ativa modo humano: pausa o agente para esta conversa."""
    sessao = store.buscar_sessao(numero)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada (conversa já encerrada)")
    sessao["modo"] = "humano"
    store.salvar_sessao(numero, sessao)
    return {"status": "assumido", "numero": numero}


@router.post("/conversas/{numero}/devolver")
def devolver_conversa(numero: str, _=Depends(_token)):
    """Remove modo humano: agente volta a responder na próxima mensagem do cliente."""
    sessao = store.buscar_sessao(numero)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada (conversa já encerrada)")
    sessao.pop("modo", None)
    store.salvar_sessao(numero, sessao)
    return {"status": "devolvido", "numero": numero}


@router.post("/conversas/{numero}/enviar")
def enviar_mensagem_humano(numero: str, body: _EnvioBody, _=Depends(_token)):
    """Envia mensagem manual via Meta API e registra no histórico Redis."""
    sessao = store.buscar_sessao(numero)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada (conversa já encerrada)")
    if sessao.get("modo") != "humano":
        raise HTTPException(status_code=409, detail="Conversa não está no modo humano")

    enviar_whatsapp(numero, body.texto)

    historico = sessao.get("historico", [])
    historico.append({"role": "humano", "text": body.texto})
    sessao["historico"] = historico
    store.salvar_sessao(numero, sessao)

    return {"status": "enviado"}
