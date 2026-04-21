"""Gerador de relatório diário em HTML."""

from datetime import date, datetime
from src.storage.store import pg_conn


def _query(sql: str, params=None) -> list[dict]:
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _md_to_html(text: str) -> str:
    """Converte markdown simples para HTML."""
    import re
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"^### (.+)$", r"<h3>\1</h3>", text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$",  r"<h3>\1</h3>", text, flags=re.MULTILINE)
    text = re.sub(r"^#### (.+)$",r"<h4>\1</h4>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*",   r"<em>\1</em>", text)
    text = re.sub(r"^> (.+)$", r"<blockquote>\1</blockquote>", text, flags=re.MULTILINE)
    # Listas
    lines, out, in_list = text.split("\n"), [], False
    for line in lines:
        if re.match(r"^[-•*] ", line):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{line[2:].strip()}</li>")
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            if line.strip() and not line.startswith("<h"):
                out.append(f"<p>{line}</p>" if line.strip() else "")
            else:
                out.append(line)
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def gerar_html(data: str | None = None, analise: str | None = None) -> str:
    data = data or str(date.today())

    resumo = _query("""
        SELECT
            COUNT(DISTINCT c.id)                                            AS total_conversas,
            SUM(CASE WHEN c.tipo_cliente='atacado'   THEN 1 ELSE 0 END)    AS atacado,
            SUM(CASE WHEN c.tipo_cliente='varejo'    THEN 1 ELSE 0 END)    AS varejo,
            SUM(CASE WHEN c.tipo_cliente='desconhecido' THEN 1 ELSE 0 END) AS desconhecido,
            SUM(CASE WHEN c.aceitou_grupo=true       THEN 1 ELSE 0 END)    AS aceitou_grupo,
            ROUND(AVG(a.score_atendimento)::numeric, 1)                    AS score_medio,
            SUM(CASE WHEN a.sentimento='positivo'    THEN 1 ELSE 0 END)    AS positivos,
            SUM(CASE WHEN a.sentimento='neutro'      THEN 1 ELSE 0 END)    AS neutros,
            SUM(CASE WHEN a.sentimento='negativo'    THEN 1 ELSE 0 END)    AS negativos,
            SUM(CASE WHEN a.interesse_compra=true    THEN 1 ELSE 0 END)    AS interesse_compra,
            SUM(CASE WHEN a.demanda_varejo=true      THEN 1 ELSE 0 END)    AS demanda_varejo
        FROM conversas c
        LEFT JOIN avaliacoes a ON a.conversa_id = c.id
        WHERE DATE(c.iniciada_em) = %s
    """, (data,))

    temas = _query("""
        SELECT a.tema_principal, COUNT(*) AS total, a.sentimento
        FROM avaliacoes a
        JOIN conversas c ON a.conversa_id = c.id
        WHERE DATE(c.iniciada_em) = %s AND a.tema_principal IS NOT NULL
        GROUP BY a.tema_principal, a.sentimento
        ORDER BY total DESC LIMIT 8
    """, (data,))

    conversas = _query("""
        SELECT c.id, c.numero_whatsapp, c.tipo_cliente, c.status,
               c.iniciada_em, a.sentimento, a.score_atendimento, a.tema_principal
        FROM conversas c
        LEFT JOIN avaliacoes a ON a.conversa_id = c.id
        WHERE DATE(c.iniciada_em) = %s
        ORDER BY c.iniciada_em DESC
    """, (data,))

    r = resumo[0] if resumo else {}
    total = r.get("total_conversas") or 0
    positivos = r.get("positivos") or 0
    neutros = r.get("neutros") or 0
    negativos = r.get("negativos") or 0
    score = r.get("score_medio") or "-"
    atacado = r.get("atacado") or 0
    varejo = r.get("varejo") or 0
    interesse = r.get("interesse_compra") or 0
    grupo = r.get("aceitou_grupo") or 0

    sent_pct = lambda n: f"{round(n/total*100)}%" if total else "0%"

    def sent_badge(s):
        cores = {"positivo": "#22c55e", "neutro": "#f59e0b", "negativo": "#ef4444"}
        return f'<span style="background:{cores.get(s,"#94a3b8")};color:#fff;padding:2px 10px;border-radius:20px;font-size:12px">{s or "—"}</span>'

    rows_temas = "".join(f"""
        <tr>
            <td style="padding:10px 16px">{t.get('tema_principal','—')}</td>
            <td style="padding:10px 16px;text-align:center">{t.get('total')}</td>
            <td style="padding:10px 16px;text-align:center">{sent_badge(t.get('sentimento'))}</td>
        </tr>""" for t in temas)

    rows_conversas = "".join(f"""
        <tr>
            <td style="padding:10px 16px">{c.get('id')}</td>
            <td style="padding:10px 16px">{c.get('numero_whatsapp','—')}</td>
            <td style="padding:10px 16px">{c.get('tipo_cliente','—')}</td>
            <td style="padding:10px 16px">{c.get('status','—')}</td>
            <td style="padding:10px 16px;text-align:center">{sent_badge(c.get('sentimento'))}</td>
            <td style="padding:10px 16px;text-align:center">{c.get('score_atendimento') or '—'}</td>
            <td style="padding:10px 16px">{c.get('tema_principal') or '—'}</td>
            <td style="padding:10px 16px;font-size:12px;color:#64748b">{str(c.get('iniciada_em',''))[:16]}</td>
        </tr>""" for c in conversas)

    data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m/%Y")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Relatório PlayBeKids — {data_fmt}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0 }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #f1f5f9; color: #1e293b }}
  .wrap {{ max-width: 960px; margin: 40px auto; padding: 0 20px }}
  header {{ background: linear-gradient(135deg,#6366f1,#8b5cf6); color: #fff; border-radius: 16px; padding: 36px 40px; margin-bottom: 28px }}
  header h1 {{ font-size: 26px; font-weight: 700 }}
  header p {{ opacity: .8; margin-top: 6px; font-size: 14px }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(160px,1fr)); gap: 16px; margin-bottom: 28px }}
  .card {{ background: #fff; border-radius: 14px; padding: 22px 20px; box-shadow: 0 1px 4px rgba(0,0,0,.07) }}
  .card .val {{ font-size: 32px; font-weight: 700; color: #6366f1 }}
  .card .lbl {{ font-size: 12px; color: #64748b; margin-top: 4px }}
  .section {{ background: #fff; border-radius: 14px; padding: 24px; margin-bottom: 24px; box-shadow: 0 1px 4px rgba(0,0,0,.07) }}
  .section h2 {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; color: #334155 }}
  .bar-wrap {{ display: flex; gap: 8px; align-items: center; margin-bottom: 10px }}
  .bar-lbl {{ width: 80px; font-size: 13px; color: #475569 }}
  .bar {{ height: 10px; border-radius: 6px }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px }}
  thead tr {{ background: #f8fafc }}
  thead th {{ padding: 10px 16px; text-align: left; font-weight: 600; color: #475569; border-bottom: 1px solid #e2e8f0 }}
  tbody tr:hover {{ background: #f8fafc }}
  tbody tr {{ border-bottom: 1px solid #f1f5f9 }}
  footer {{ text-align: center; font-size: 12px; color: #94a3b8; margin: 32px 0 }}
  .analise {{ font-size: 14px; line-height: 1.8; color: #334155 }}
  .analise h3 {{ font-size: 15px; font-weight: 700; color: #6366f1; margin: 20px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #e2e8f0 }}
  .analise h4 {{ font-size: 14px; font-weight: 600; color: #475569; margin: 14px 0 6px }}
  .analise ul {{ padding-left: 20px; margin: 6px 0 }}
  .analise li {{ margin-bottom: 6px }}
  .analise strong {{ color: #1e293b }}
  .analise p {{ margin-bottom: 10px }}
  .analise blockquote {{ border-left: 3px solid #6366f1; padding: 8px 16px; background: #f8f7ff; border-radius: 0 8px 8px 0; margin: 12px 0; color: #4f46e5 }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📊 Relatório Diário — PlayBeKids</h1>
    <p>Data: {data_fmt} &nbsp;·&nbsp; Gerado em: {datetime.now().strftime("%d/%m/%Y %H:%M")}</p>
  </header>

  <div class="cards">
    <div class="card"><div class="val">{total}</div><div class="lbl">Conversas</div></div>
    <div class="card"><div class="val">{atacado}</div><div class="lbl">Atacado</div></div>
    <div class="card"><div class="val">{varejo}</div><div class="lbl">Varejo descartado</div></div>
    <div class="card"><div class="val">{interesse}</div><div class="lbl">Interesse em compra</div></div>
    <div class="card"><div class="val">{grupo}</div><div class="lbl">Aceitaram grupo</div></div>
    <div class="card"><div class="val">{score}</div><div class="lbl">Score médio</div></div>
  </div>

  <div class="section">
    <h2>Sentimento</h2>
    <div class="bar-wrap">
      <span class="bar-lbl">Positivo</span>
      <div class="bar" style="width:{sent_pct(positivos)};background:#22c55e;min-width:4px"></div>
      <span style="font-size:13px;color:#475569">{positivos} ({sent_pct(positivos)})</span>
    </div>
    <div class="bar-wrap">
      <span class="bar-lbl">Neutro</span>
      <div class="bar" style="width:{sent_pct(neutros)};background:#f59e0b;min-width:4px"></div>
      <span style="font-size:13px;color:#475569">{neutros} ({sent_pct(neutros)})</span>
    </div>
    <div class="bar-wrap">
      <span class="bar-lbl">Negativo</span>
      <div class="bar" style="width:{sent_pct(negativos)};background:#ef4444;min-width:4px"></div>
      <span style="font-size:13px;color:#475569">{negativos} ({sent_pct(negativos)})</span>
    </div>
  </div>

  <div class="section">
    <h2>Temas mais frequentes</h2>
    {"<p style='color:#94a3b8;font-size:13px'>Nenhum tema registrado hoje.</p>" if not temas else f"""
    <table>
      <thead><tr><th>Tema</th><th>Ocorrências</th><th>Sentimento</th></tr></thead>
      <tbody>{rows_temas}</tbody>
    </table>"""}
  </div>

  <div class="section">
    <h2>Conversas do dia</h2>
    {"<p style='color:#94a3b8;font-size:13px'>Nenhuma conversa registrada hoje.</p>" if not conversas else f"""
    <table>
      <thead><tr><th>ID</th><th>Número</th><th>Tipo</th><th>Status</th><th>Sentimento</th><th>Score</th><th>Tema</th><th>Horário</th></tr></thead>
      <tbody>{rows_conversas}</tbody>
    </table>"""}
  </div>

  {f'''<div class="section">
    <h2>🤖 Análise do InsightAgent</h2>
    <div class="analise">{_md_to_html(analise)}</div>
  </div>''' if analise else ''}

  <footer>NinoAgent · PlayBeKids · {data_fmt}</footer>
</div>
</body>
</html>"""
