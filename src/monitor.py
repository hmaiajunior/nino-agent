"""Router de monitoramento — lista conversas e expõe ações de controle humano."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
import httpx

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
  .msg-hora    { font-size: 10px; color: #999; margin-top: 2px; text-align: right; }
  .data-sep    { align-self: center; margin: 12px 0 4px; }
  .data-sep span { display: inline-block; background: rgba(255,255,255,.85); color: #555; font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 10px; box-shadow: 0 1px 1px rgba(0,0,0,.05); }

  /* Input humano */
  #input-area { display: none; padding: 10px 16px; background: #f0f2f5; border-top: 1px solid #ddd; gap: 8px; align-items: center; }
  #input-texto { flex: 1; padding: 9px 14px; border: 1px solid #ddd; border-radius: 20px; font-size: 14px; outline: none; background: white; }
  #input-texto:focus { border-color: #075e54; }
  #btn-enviar { background: #075e54; color: white; border-radius: 20px; }
  #btn-anexo { background: #e8e8e8; color: #555; border-radius: 50%; width: 36px; height: 36px; padding: 0; font-size: 17px; flex-shrink: 0; }
  #btn-anexo:disabled { opacity: .5; }

  /* Mídia nas bolhas */
  .msg audio { width: 100%; max-width: 260px; margin-top: 4px; display: block; }
  .msg video { max-width: 260px; border-radius: 6px; margin-top: 4px; display: block; }
  .msg img   { max-width: 260px; border-radius: 6px; margin-top: 4px; display: block; }

  /* Mobile — padrão lista → detalhe */
  @media (max-width: 600px) {
    #sidebar { width: 100%; min-width: 0; border-right: none; }
    #chat { display: none; position: fixed; inset: 0; z-index: 10; background: #e5ddd5; }
    #chat.mobile-aberto { display: flex; }
    #btn-voltar { display: flex; }
    .msg { max-width: 85%; }
    .msg audio, .msg video, .msg img { max-width: 100%; }
  }
  #btn-voltar { display: none; background: none; color: #075e54; font-size: 20px; padding: 0 8px 0 0; }

  /* Métricas */
  #metricas { display: flex; gap: 8px; padding: 10px 12px; background: white; border-bottom: 1px solid #e0e0e0; flex-wrap: wrap; }
  .metric { flex: 1; min-width: 70px; background: #f7f8fa; border-radius: 8px; padding: 8px 10px; text-align: center; }
  .metric-valor { font-size: 18px; font-weight: 700; color: #075e54; line-height: 1.1; }
  .metric-label { font-size: 10px; color: #667; margin-top: 3px; text-transform: uppercase; letter-spacing: .3px; }
  .metric-pos { color: #25d366; }
  .metric-neg { color: #e53935; }
  .metric-warn { color: #ff9800; }
</style>
</head>
<body>
<div id="app">

  <!-- Sidebar -->
  <div id="sidebar">
    <div id="sidebar-header">💬 NinoAgent Monitor</div>
    <div id="metricas"><div style="padding:6px;color:#aaa;font-size:11px">…</div></div>
    <div id="conversa-lista"><div style="padding:16px;color:#aaa;font-size:13px">Carregando...</div></div>
  </div>

  <!-- Chat -->
  <div id="chat">
    <div id="empty-state">← Selecione uma conversa</div>
    <div id="chat-content">
      <div id="chat-header">
        <button id="btn-voltar" onclick="voltarLista()" title="Voltar">‹</button>
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
        <input id="file-input" type="file" accept="audio/*,video/*,image/*,application/pdf"
               style="display:none" onchange="enviarMidia()">
        <button id="btn-anexo" title="Enviar arquivo" onclick="document.getElementById('file-input').click()">📎</button>
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
  // Mantém posição do scroll se o usuário não está no fim (evita "pular" durante leitura)
  const noFim = msgs.scrollTop + msgs.clientHeight >= msgs.scrollHeight - 50;

  let ultimaData = null;
  msgs.innerHTML = conv.historico.map(m => {
    const cls   = m.role === 'cliente' ? 'msg-cliente' : m.role === 'humano' ? 'msg-humano' : 'msg-agente';
    const label = m.role === 'agente' ? '[AGENTE]' : m.role === 'humano' ? '[HUMANO]' : '';
    const tipo  = m.type || 'text';

    // Separador de data entre conversas distintas
    let separador = '';
    if (m.timestamp) {
      const d = new Date(m.timestamp);
      const dataStr = d.toLocaleDateString('pt-BR', {day: '2-digit', month: 'short', year: 'numeric'});
      if (dataStr !== ultimaData) {
        separador = `<div class="data-sep"><span>${dataStr}</span></div>`;
        ultimaData = dataStr;
      }
    }

    let conteudo;
    if (tipo === 'audio') {
      conteudo = `<audio controls src="/monitor/media/${esc(m.media_id)}?token=${TOKEN}"></audio><div class="msg-label">${esc(m.text)}</div>`;
    } else if (tipo === 'video') {
      conteudo = `<video controls src="/monitor/media/${esc(m.media_id)}?token=${TOKEN}"></video>`;
    } else if (tipo === 'image') {
      conteudo = `<img src="/monitor/media/${esc(m.media_id)}?token=${TOKEN}" alt="imagem">`;
    } else {
      conteudo = esc(m.text);
    }

    const hora = m.timestamp
      ? `<div class="msg-hora">${new Date(m.timestamp).toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'})}</div>`
      : '';

    return `${separador}<div class="msg ${cls}">${conteudo}${(label && tipo === 'text') ? '<div class="msg-label">' + label + '</div>' : ''}${hora}</div>`;
  }).join('');

  if (noFim) msgs.scrollTop = msgs.scrollHeight;

  document.getElementById('empty-state').style.display  = 'none';
  document.getElementById('chat-content').style.display = 'flex';
}

async function selecionar(numero) {
  sel = numero;
  await Promise.all([carregarLista(), carregarConversa(numero)]);
  document.getElementById('chat').classList.add('mobile-aberto');
}

function voltarLista() {
  document.getElementById('chat').classList.remove('mobile-aberto');
  sel = null;
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

async function enviarMidia() {
  if (!sel) return;
  const input = document.getElementById('file-input');
  const file = input.files[0];
  if (!file) return;
  const btn = document.getElementById('btn-anexo');
  btn.disabled = true;
  btn.textContent = '⏳';
  try {
    const form = new FormData();
    form.append('file', file);
    const r = await fetch('/monitor/conversas/' + sel + '/enviar-midia?token=' + TOKEN, {
      method: 'POST',
      body: form,
    });
    if (r.ok) {
      await carregarConversa(sel);
    } else {
      const err = await r.text();
      console.error('Erro mídia:', err);
      alert('Erro ao enviar arquivo.');
    }
  } finally {
    btn.disabled = false;
    btn.textContent = '📎';
    input.value = '';
  }
}

async function carregarMetricas() {
  const m = await api('/metricas');
  if (!m) return;
  const h = m.hoje, a = m.agora;
  const score = h.score_medio !== null ? h.score_medio.toFixed(1) : '—';
  const dur   = h.duracao_media_seg ? Math.round(h.duracao_media_seg / 60) + 'min' : '—';
  const escPct = h.total_conversas ? Math.round(100 * h.escaladas_humano / h.total_conversas) + '%' : '—';
  const scoreCls = h.score_medio === null ? '' : h.score_medio >= 4 ? 'metric-pos' : h.score_medio < 3 ? 'metric-neg' : 'metric-warn';

  document.getElementById('metricas').innerHTML = `
    <div class="metric"><div class="metric-valor">${h.total_conversas}</div><div class="metric-label">Hoje</div></div>
    <div class="metric"><div class="metric-valor ${scoreCls}">${score}</div><div class="metric-label">Score</div></div>
    <div class="metric"><div class="metric-valor metric-pos">${h.positivo}</div><div class="metric-label">😊</div></div>
    <div class="metric"><div class="metric-valor metric-neg">${h.negativo}</div><div class="metric-label">😟</div></div>
    <div class="metric"><div class="metric-valor metric-warn">${escPct}</div><div class="metric-label">Humano</div></div>
    <div class="metric"><div class="metric-valor">${dur}</div><div class="metric-label">Duração</div></div>
    <div class="metric"><div class="metric-valor">${a.ativas}</div><div class="metric-label">Em curso</div></div>
  `;
}

// Previne XSS nas strings injetadas no HTML
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Boot + polling 5s
carregarLista();
carregarMetricas();
setInterval(async () => {
  await Promise.all([carregarLista(), carregarMetricas()]);
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

@router.get("/metricas")
def metricas(_=Depends(_token)):
    """Métricas agregadas: conversas/avaliações de hoje + estado atual em Redis."""
    from datetime import date
    hoje = str(date.today())
    dia = store.metricas_dia(hoje)

    ativas = store.listar_sessoes_ativas()
    em_humano = sum(1 for s in ativas if s.get("modo") == "humano")

    return {
        "hoje": dia,
        "agora": {
            "ativas": len(ativas),
            "em_humano": em_humano,
            "com_agente": len(ativas) - em_humano,
        },
    }


@router.get("/conversas")
def listar_conversas(_=Depends(_token)):
    """Lista contatos com atividade nos últimos 30 dias (uma entrada por número)."""
    ativas = {s["numero"]: s for s in store.listar_sessoes_ativas()}
    contatos = store.listar_contatos(dias=30)

    resultado: list[dict] = []
    vistos: set[str] = set()

    for c in contatos:
        numero = c["numero_whatsapp"]
        vistos.add(numero)
        sessao = ativas.get(numero)
        ultima = c.get("ultima_atividade")
        resultado.append({
            "numero": numero,
            "nome": c.get("nome"),
            "status": "ativa" if sessao else "encerrada",
            "modo": (sessao or {}).get("modo", "agente"),
            "tipo_cliente": c.get("tipo_cliente") or (sessao or {}).get("tipo_cliente"),
            "ultima_mensagem": (c.get("ultima_mensagem") or "")[:60],
            "ultima_atividade": ultima.isoformat() if ultima else None,
        })

    # Sessões Redis sem nenhuma mensagem persistida (raro — janela curta entre acumular e persistir)
    for numero, sessao in ativas.items():
        if numero in vistos:
            continue
        hist = sessao.get("historico", [])
        resultado.append({
            "numero": numero,
            "nome": None,
            "status": "ativa",
            "modo": sessao.get("modo", "agente"),
            "tipo_cliente": sessao.get("tipo_cliente"),
            "ultima_mensagem": _preview(hist),
            "ultima_atividade": _ultima_atividade(hist),
        })

    resultado.sort(key=lambda c: c["ultima_atividade"] or "", reverse=True)
    return resultado


@router.get("/conversas/{numero}")
def detalhar_conversa(numero: str, _=Depends(_token)):
    """Timeline completa de um contato: Postgres (persistido) + Redis (mensagens recém-recebidas)."""
    sessao = store.buscar_sessao(numero)
    cliente = store.buscar_cliente(numero)

    # 1. Histórico persistido (timeline contínua, todas as conversas anteriores)
    persistidas = store.buscar_mensagens(numero, limite=500)
    historico = [
        {
            "role": m["role"],
            "type": m.get("type") or "text",
            "text": m.get("text") or "",
            "media_id": m.get("media_id"),
            "timestamp": m["criado_em"].isoformat() if m.get("criado_em") else None,
        }
        for m in persistidas
    ]

    # 2. Mensagens da sessão ativa que ainda não foram persistidas (defesa contra race)
    if sessao:
        textos_persistidos = {(m["role"], m["text"]) for m in persistidas[-20:]}
        for m in sessao.get("historico", []):
            chave = (m["role"], m.get("text", ""))
            if chave in textos_persistidos:
                continue
            historico.append({
                "role": m["role"],
                "type": m.get("type") or "text",
                "text": m.get("text") or "",
                "media_id": m.get("media_id"),
                "timestamp": None,
            })

    if not historico and not sessao:
        raise HTTPException(status_code=404, detail="Contato sem histórico")

    return {
        "numero": numero,
        "nome": cliente["nome"] if cliente else None,
        "status": "ativa" if sessao else "encerrada",
        "modo": (sessao or {}).get("modo", "agente"),
        "tipo_cliente": (sessao or {}).get("tipo_cliente") or (cliente or {}).get("tipo"),
        "historico": historico,
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


@router.get("/media/{media_id}")
def proxy_media(media_id: str, request: Request, _=Depends(_token)):
    """Proxy autenticado de mídia da Meta API com suporte a Range (necessário para <audio>/<video>)."""
    headers_auth = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}

    try:
        meta = httpx.get(
            f"https://graph.facebook.com/v19.0/{media_id}",
            headers=headers_auth, timeout=10,
        )
        meta.raise_for_status()
        data = meta.json()
        url = data["url"]
        mime_type = data.get("mime_type", "application/octet-stream")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao obter URL da mídia: {e}")

    try:
        file_resp = httpx.get(url, headers=headers_auth, timeout=60)
        file_resp.raise_for_status()
        content = file_resp.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao baixar mídia: {e}")

    total = len(content)
    range_header = request.headers.get("range")
    if range_header:
        try:
            range_val = range_header.strip().replace("bytes=", "")
            start_str, _, end_str = range_val.partition("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else total - 1
            end = min(end, total - 1)
        except ValueError:
            start, end = 0, total - 1
        chunk = content[start:end + 1]
        return Response(
            content=chunk, status_code=206, media_type=mime_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(chunk)),
            },
        )

    return Response(
        content=content, media_type=mime_type,
        headers={"Accept-Ranges": "bytes", "Content-Length": str(total)},
    )


@router.post("/conversas/{numero}/enviar-midia")
def enviar_midia_humano(numero: str, file: UploadFile = File(...), _=Depends(_token)):
    """Faz upload de arquivo para a Meta API e envia ao cliente via WhatsApp."""
    sessao = store.buscar_sessao(numero)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada (conversa já encerrada)")
    if sessao.get("modo") != "humano":
        raise HTTPException(status_code=409, detail="Conversa não está no modo humano")

    headers_auth = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
    to = numero if numero.startswith("+") else f"+{numero}"
    mime = file.content_type or "application/octet-stream"

    # 1. Upload do arquivo para a Meta API
    try:
        upload_resp = httpx.post(
            f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/media",
            headers=headers_auth,
            files={"file": (file.filename, file.file, mime)},
            data={"messaging_product": "whatsapp"},
            timeout=60,
        )
        upload_resp.raise_for_status()
        media_id = upload_resp.json()["id"]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha no upload para a Meta API: {e}")

    # 2. Determina tipo e label para o histórico
    if mime.startswith("audio/"):
        msg_type, label = "audio", "[áudio enviado 🎵]"
    elif mime.startswith("video/"):
        msg_type, label = "video", "[vídeo enviado 🎬]"
    elif mime.startswith("image/"):
        msg_type, label = "image", "[imagem enviada 🖼️]"
    else:
        msg_type, label = "document", f"[arquivo: {file.filename or 'documento'}]"

    media_obj = {"id": media_id}
    if msg_type == "document":
        media_obj["filename"] = file.filename or "arquivo"

    # 3. Envia a mensagem WhatsApp
    try:
        send_resp = httpx.post(
            f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers={**headers_auth, "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": msg_type,
                msg_type: media_obj,
            },
            timeout=30,
        )
        send_resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao enviar mídia: {e}")

    # 4. Registra no histórico Redis e na timeline persistente
    historico = sessao.get("historico", [])
    historico.append({"role": "humano", "type": msg_type, "media_id": media_id, "text": label})
    sessao["historico"] = historico
    store.salvar_sessao(numero, sessao)
    store.salvar_mensagem(numero, "humano", text=label, type=msg_type, media_id=media_id)

    return {"status": "enviado", "type": msg_type, "media_id": media_id}


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
    store.salvar_mensagem(numero, "humano", text=body.texto, type="text")

    return {"status": "enviado"}
