"""Router de monitoramento — lista conversas e expõe ações de controle humano."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.config import settings
from src.storage import store
from src.whatsapp import enviar_whatsapp

# ---------------------------------------------------------------------------
# HTML da interface (servido em GET /monitor/)
# Token é injetado no lugar de {{TOKEN}} antes de servir.
# ---------------------------------------------------------------------------
_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NinoAgent · Monitor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; height: 100vh; display: flex; flex-direction: column; background: #f0f2f5; }

  /* Layout principal */
  #app { display: flex; height: 100vh; overflow: hidden; }

  /* Sidebar */
  #sidebar { width: 320px; min-width: 320px; background: white; border-right: 1px solid #e0e0e0; display: flex; flex-direction: column; }
  #sidebar-header { padding: 14px 16px; background: #075e54; color: white; font-size: 16px; font-weight: 600; letter-spacing: .3px; }
  #conversa-lista { flex: 1; overflow-y: auto; }
  .conv-item { padding: 12px 16px; border-bottom: 1px solid #f0f0f0; cursor: pointer; transition: background .15s; }
  .conv-item:hover { background: #f5f5f5; }
  .conv-item.selected { background: #ebebeb; }
  .conv-meta { display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px; }
  .conv-nome { font-size: 14px; font-weight: 600; color: #111; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 180px; }
  .conv-preview { font-size: 12px; color: #667; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .conv-badges { display: flex; gap: 4px; align-items: center; flex-shrink: 0; }

  /* Badges */
  .b-ativa    { font-size: 11px; color: #25d366; }
  .b-encerrada { font-size: 11px; color: #aaa; }
  .b-humano   { font-size: 10px; background: #ff9800; color: white; padding: 1px 6px; border-radius: 10px; font-weight: 600; }

  /* Painel de chat */
  #chat { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  #empty-state { flex: 1; display: flex; align-items: center; justify-content: center; color: #aaa; font-size: 15px; }
  #chat-content { flex: 1; display: none; flex-direction: column; }

  /* Cabeçalho do chat */
  #chat-header { padding: 12px 16px; background: #f0f2f5; border-bottom: 1px solid #ddd; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  #chat-info { min-width: 0; }
  #chat-nome   { font-size: 15px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #chat-status { font-size: 12px; color: #667; margin-top: 1px; }
  #chat-acoes  { display: flex; gap: 8px; flex-shrink: 0; }

  /* Botões */
  button { padding: 7px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; transition: opacity .15s; }
  button:hover { opacity: .85; }
  button:disabled { opacity: .4; cursor: default; }
  #btn-assumir  { background: #ff9800; color: white; }
  #btn-devolver { background: #4caf50; color: white; }

  /* Área de mensagens */
  #mensagens { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 6px; background: #e5ddd5; }
  .msg { max-width: 68%; padding: 8px 12px; border-radius: 8px; font-size: 14px; line-height: 1.45; word-wrap: break-word; }
  .msg-cliente { align-self: flex-end; background: white; border-radius: 8px 8px 0 8px; }
  .msg-agente  { align-self: flex-start; background: #dcf8c6; border-radius: 8px 8px 8px 0; }
  .msg-humano  { align-self: flex-start; background: #fff3cd; border-radius: 8px 8px 8px 0; }
  .msg-label   { font-size: 10px; color: #888; margin-top: 3px; }

  /* Input humano */
  #input-area { display: none; padding: 10px 16px; background: #f0f2f5; border-top: 1px solid #ddd; gap: 8px; align-items: center; }
  #input-texto { flex: 1; padding: 9px 14px; border: 1px solid #ddd; border-radius: 20px; font-size: 14px; outline: none; background: white; }
  #input-texto:focus { border-color: #075e54; }
  #btn-enviar { background: #075e54; color: white; border-radius: 20px; }
</style>
</head>
<body>
<div id="app">

  <!-- Sidebar -->
  <div id="sidebar">
    <div id="sidebar-header">💬 NinoAgent Monitor</div>
    <div id="conversa-lista"><div style="padding:16px;color:#aaa;font-size:13px">Carregando...</div></div>
  </div>

  <!-- Chat -->
  <div id="chat">
    <div id="empty-state">← Selecione uma conversa</div>
    <div id="chat-content">
      <div id="chat-header">
        <div id="chat-info">
          <div id="chat-nome"></div>
          <div id="chat-status"></div>
        </div>
        <div id="chat-acoes">
          <button id="btn-assumir"  onclick="assumir()">Assumir conversa</button>
          <button id="btn-devolver" onclick="devolver()" style="display:none">Devolver ao agente</button>
        </div>
      </div>
      <div id="mensagens"></div>
      <div id="input-area">
        <input id="input-texto" type="text" placeholder="Digite sua resposta..."
               onkeydown="if(event.key==='Enter')enviar()">
        <button id="btn-enviar" onclick="enviar()">Enviar</button>
      </div>
    </div>
  </div>

</div>
<script>
const TOKEN = '{{TOKEN}}';
let sel = null;   // número da conversa selecionada

async function api(path, opts = {}) {
  const sep = path.includes('?') ? '&' : '?';
  const r = await fetch('/monitor' + path + sep + 'token=' + TOKEN, {
    headers: {'Content-Type': 'application/json'},
    ...opts,
  });
  if (!r.ok) { console.error(path, r.status, await r.text()); return null; }
  return r.json();
}

async function carregarLista() {
  const lista = await api('/conversas');
  if (!lista) return;
  const el = document.getElementById('conversa-lista');
  if (!lista.length) { el.innerHTML = '<div style="padding:16px;color:#aaa;font-size:13px">Nenhuma conversa.</div>'; return; }
  el.innerHTML = lista.map(c => {
    const nome = esc(c.nome || c.numero);
    const ativo = c.status === 'ativa';
    const humano = c.modo === 'humano';
    return `<div class="conv-item${c.numero === sel ? ' selected' : ''}" onclick="selecionar('${esc(c.numero)}')">
      <div class="conv-meta">
        <span class="conv-nome">${nome}</span>
        <span class="conv-badges">
          ${humano ? '<span class="b-humano">HUMANO</span>' : ''}
          <span class="${ativo ? 'b-ativa' : 'b-encerrada'}">${ativo ? '🟢' : '⚪'}</span>
        </span>
      </div>
      <div class="conv-preview">${esc(c.ultima_mensagem || '—')}</div>
    </div>`;
  }).join('');
}

async function carregarConversa(numero) {
  const conv = await api('/conversas/' + numero);
  if (!conv) return;

  document.getElementById('chat-nome').textContent =
    (conv.nome || conv.numero) + (conv.tipo_cliente ? ' · ' + conv.tipo_cliente : '');
  document.getElementById('chat-status').textContent =
    conv.status === 'ativa' ? '🟢 ativa' : '⚪ encerrada';

  const humano = conv.modo === 'humano';
  document.getElementById('btn-assumir').style.display  = humano ? 'none' : '';
  document.getElementById('btn-devolver').style.display = humano ? '' : 'none';
  document.getElementById('input-area').style.display   = humano ? 'flex' : 'none';

  const msgs = document.getElementById('mensagens');
  msgs.innerHTML = conv.historico.map(m => {
    const cls   = m.role === 'cliente' ? 'msg-cliente' : m.role === 'humano' ? 'msg-humano' : 'msg-agente';
    const label = m.role === 'agente' ? '[AGENTE]' : m.role === 'humano' ? '[HUMANO]' : '';
    return `<div class="msg ${cls}">${esc(m.text)}${label ? '<div class="msg-label">' + label + '</div>' : ''}</div>`;
  }).join('');
  msgs.scrollTop = msgs.scrollHeight;

  document.getElementById('empty-state').style.display  = 'none';
  document.getElementById('chat-content').style.display = 'flex';
}

async function selecionar(numero) {
  sel = numero;
  await Promise.all([carregarLista(), carregarConversa(numero)]);
}

async function assumir() {
  if (!sel) return;
  await api('/conversas/' + sel + '/assumir', {method: 'POST'});
  await Promise.all([carregarLista(), carregarConversa(sel)]);
}

async function devolver() {
  if (!sel) return;
  await api('/conversas/' + sel + '/devolver', {method: 'POST'});
  await Promise.all([carregarLista(), carregarConversa(sel)]);
}

async function enviar() {
  if (!sel) return;
  const inp = document.getElementById('input-texto');
  const texto = inp.value.trim();
  if (!texto) return;
  inp.value = '';
  await api('/conversas/' + sel + '/enviar', {method: 'POST', body: JSON.stringify({texto})});
  await carregarConversa(sel);
}

// Previne XSS nas strings injetadas no HTML
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Boot + polling 5s
carregarLista();
setInterval(async () => {
  await carregarLista();
  if (sel) await carregarConversa(sel);
}, 5000);
</script>
</body>
</html>"""

router = APIRouter(tags=["monitor"])


class _EnvioBody(BaseModel):
    texto: str


# --- UI ---

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def monitor_ui(token: str = Query(...)):
    if not settings.MONITOR_TOKEN or token != settings.MONITOR_TOKEN:
        raise HTTPException(status_code=403, detail="Token inválido")
    return HTMLResponse(_HTML.replace("{{TOKEN}}", token))


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
