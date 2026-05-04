"""Storage layer: Postgres, Qdrant, Redis."""

import json
import psycopg2
import redis
import qdrant_client
from qdrant_client.models import Distance, VectorParams, PointStruct
from llama_index.embeddings.fastembed import FastEmbedEmbedding

from src.config import settings

# --- Postgres ---

from contextlib import contextmanager

_pg_kwargs = None

def _pg_connect():
    global _pg_kwargs
    if _pg_kwargs is None:
        _pg_kwargs = dict(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            dbname=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
        )
    return psycopg2.connect(**_pg_kwargs)

@contextmanager
def pg_cursor():
    conn = _pg_connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def buscar_cliente(numero_whatsapp: str) -> dict | None:
    with pg_cursor() as cur:
        cur.execute(
            "SELECT numero_whatsapp, nome, tipo, membro_grupo FROM clientes WHERE numero_whatsapp = %s",
            (numero_whatsapp,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "nome": row[1], "tipo": row[2], "membro_grupo": row[3]}


def salvar_conversa(dados: dict) -> int:
    with pg_cursor() as cur:
        cur.execute(
            """INSERT INTO conversas
               (cliente_id, numero_whatsapp, tipo_cliente, cliente_recorrente,
                origem, status, aceitou_grupo, escalou_humano, encerrada_em, duracao_segundos)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s) RETURNING id""",
            (
                dados.get("cliente_id"),
                dados["numero_whatsapp"],
                dados.get("tipo_cliente", "atacado"),
                dados.get("cliente_recorrente", False),
                dados.get("origem", "organico"),
                dados.get("status", "resolvido"),
                dados.get("aceitou_grupo"),
                dados.get("escalou_humano", False),
                dados.get("duracao_segundos"),
            ),
        )
        return cur.fetchone()[0]


def buscar_conversa_do_dia(numero_whatsapp: str) -> int | None:
    """Retorna o id da conversa já aberta hoje para este número, ou None."""
    with pg_cursor() as cur:
        cur.execute(
            "SELECT id FROM conversas WHERE numero_whatsapp = %s AND DATE(iniciada_em) = CURRENT_DATE ORDER BY id DESC LIMIT 1",
            (numero_whatsapp,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def upsert_avaliacao_do_dia(dados: dict) -> None:
    """Insere ou atualiza a avaliação da conversa. Negativo prevalece sobre neutro/positivo."""
    with pg_cursor() as cur:
        cur.execute("SELECT id, sentimento FROM avaliacoes WHERE conversa_id = %s", (dados["conversa_id"],))
        existing = cur.fetchone()
        if existing:
            # Negativo prevalece; só atualiza se o novo for mais grave
            ordem = {"positivo": 0, "neutro": 1, "negativo": 2}
            novo = dados.get("sentimento", "neutro")
            atual = existing[1] or "neutro"
            sentimento_final = novo if ordem.get(novo, 1) > ordem.get(atual, 1) else atual
            cur.execute(
                """UPDATE avaliacoes SET
                     sentimento = %s,
                     score_atendimento = LEAST(score_atendimento, %s),
                     tema_principal = %s,
                     duvida_resolvida = %s,
                     interesse_compra = COALESCE(interesse_compra, %s) OR %s,
                     demanda_varejo = COALESCE(demanda_varejo, FALSE) OR %s,
                     observacoes = %s
                   WHERE id = %s""",
                (
                    sentimento_final,
                    dados.get("score_atendimento"),
                    dados.get("tema_principal"),
                    dados.get("duvida_resolvida"),
                    dados.get("interesse_compra"), dados.get("interesse_compra"),
                    dados.get("demanda_varejo", False),
                    dados.get("observacoes"),
                    existing[0],
                ),
            )
        else:
            cur.execute(
                """INSERT INTO avaliacoes
                   (conversa_id, sentimento, score_atendimento, tema_principal,
                    duvida_resolvida, interesse_compra, demanda_varejo, observacoes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    dados["conversa_id"],
                    dados.get("sentimento"),
                    dados.get("score_atendimento"),
                    dados.get("tema_principal"),
                    dados.get("duvida_resolvida"),
                    dados.get("interesse_compra"),
                    dados.get("demanda_varejo", False),
                    dados.get("observacoes"),
                ),
            )


def salvar_avaliacao(dados: dict) -> None:
    with pg_cursor() as cur:
        cur.execute(
            """INSERT INTO avaliacoes
               (conversa_id, sentimento, score_atendimento, tema_principal,
                duvida_resolvida, interesse_compra, demanda_varejo, observacoes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                dados["conversa_id"],
                dados.get("sentimento"),
                dados.get("score_atendimento"),
                dados.get("tema_principal"),
                dados.get("duvida_resolvida"),
                dados.get("interesse_compra"),
                dados.get("demanda_varejo", False),
                dados.get("observacoes"),
            ),
        )


def salvar_mensagem(numero: str, role: str, text: str | None = None,
                    type: str = "text", media_id: str | None = None,
                    conversa_id: int | None = None) -> None:
    """Persiste uma mensagem individual no Postgres (timeline contínua por contato)."""
    with pg_cursor() as cur:
        cur.execute(
            """INSERT INTO mensagens (numero_whatsapp, conversa_id, role, type, text, media_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (numero, conversa_id, role, type, text, media_id),
        )


def buscar_mensagens(numero: str, limite: int = 500) -> list[dict]:
    """Retorna o histórico ordenado cronologicamente de um número."""
    with pg_cursor() as cur:
        cur.execute(
            """SELECT id, role, type, text, media_id, criado_em, conversa_id
               FROM mensagens
               WHERE numero_whatsapp = %s
               ORDER BY criado_em ASC, id ASC
               LIMIT %s""",
            (numero, limite),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def listar_contatos(dias: int = 30) -> list[dict]:
    """Lista contatos com atividade recente, com a última mensagem como preview."""
    with pg_cursor() as cur:
        cur.execute(
            """
            SELECT
                m.numero_whatsapp,
                cl.nome,
                MAX(m.criado_em)                                       AS ultima_atividade,
                (ARRAY_AGG(m.text       ORDER BY m.criado_em DESC))[1] AS ultima_mensagem,
                (ARRAY_AGG(m.type       ORDER BY m.criado_em DESC))[1] AS ultimo_tipo,
                MAX(c.tipo_cliente)                                    AS tipo_cliente
            FROM mensagens m
            LEFT JOIN clientes  cl ON cl.numero_whatsapp = m.numero_whatsapp
            LEFT JOIN conversas c  ON c.id              = m.conversa_id
            WHERE m.criado_em >= NOW() - (INTERVAL '1 day' * %s)
            GROUP BY m.numero_whatsapp, cl.nome
            ORDER BY ultima_atividade DESC
            """,
            (dias,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def buscar_avaliacoes_dia(data: str) -> list[dict]:
    with pg_cursor() as cur:
        cur.execute(
            """SELECT a.*, c.tipo_cliente, c.origem, c.aceitou_grupo
               FROM avaliacoes a JOIN conversas c ON a.conversa_id = c.id
               WHERE DATE(a.criado_em) = %s""",
            (data,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# --- Qdrant ---

_embed_model = None

def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = FastEmbedEmbedding(model_name="BAAI/bge-base-en-v1.5")
    return _embed_model


def _qdrant_client():
    return qdrant_client.QdrantClient(url=settings.QDRANT_URL)


def garantir_collection():
    client = _qdrant_client()
    if not client.collection_exists(settings.QDRANT_COLLECTION):
        client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


def indexar_conversa(conversa_id: int, texto: str, metadata: dict) -> None:
    garantir_collection()
    embed = _get_embed_model()
    vector = embed.get_text_embedding(texto)
    client = _qdrant_client()
    client.upsert(
        collection_name=settings.QDRANT_COLLECTION,
        points=[PointStruct(id=conversa_id, vector=vector, payload=metadata)],
    )


def buscar_conversas_similares(query: str, limit: int = 5) -> list[dict]:
    embed = _get_embed_model()
    vector = embed.get_query_embedding(query)
    client = _qdrant_client()
    results = client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=vector,
        limit=limit,
        with_payload=True,
    )
    return [{"id": p.id, "score": p.score, **p.payload} for p in results.points]


# --- Redis (sessões) ---

_redis_client = None

def redis_conn():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def salvar_sessao(numero: str, dados: dict) -> None:
    ttl = settings.SESSION_TIMEOUT_MINUTES * 60
    redis_conn().setex(f"session:{numero}", ttl, json.dumps(dados, ensure_ascii=False))


def buscar_sessao(numero: str) -> dict | None:
    raw = redis_conn().get(f"session:{numero}")
    return json.loads(raw) if raw else None


def encerrar_sessao(numero: str) -> None:
    redis_conn().delete(f"session:{numero}")


def listar_sessoes_ativas() -> list[dict]:
    """Retorna todas as sessões Redis ativas (prefixo session:*)."""
    r = redis_conn()
    resultado = []
    for key in r.keys("session:*"):
        raw = r.get(key)
        if raw:
            dados = json.loads(raw)
            numero = key.removeprefix("session:")
            resultado.append({"numero": numero, **dados})
    return resultado


def metricas_dia(data: str) -> dict:
    """Retorna métricas agregadas de conversas e avaliações do dia."""
    with pg_cursor() as cur:
        # Conversas iniciadas no dia
        cur.execute(
            """SELECT
                 COUNT(*)                                                   AS total,
                 COUNT(*) FILTER (WHERE escalou_humano = TRUE)              AS escaladas,
                 COUNT(*) FILTER (WHERE aceitou_grupo  = TRUE)              AS aderiram_grupo,
                 ROUND(AVG(NULLIF(duracao_segundos, 0))::numeric, 0)        AS duracao_media_seg
               FROM conversas
               WHERE DATE(iniciada_em) = %s""",
            (data,),
        )
        row = cur.fetchone()
        total          = row[0] or 0
        escaladas      = row[1] or 0
        aderiram_grupo = row[2] or 0
        duracao_media  = int(row[3]) if row[3] is not None else 0

        # Avaliações do dia
        cur.execute(
            """SELECT
                 ROUND(AVG(score_atendimento)::numeric, 2) AS score_medio,
                 COUNT(*) FILTER (WHERE sentimento = 'positivo') AS positivo,
                 COUNT(*) FILTER (WHERE sentimento = 'neutro')   AS neutro,
                 COUNT(*) FILTER (WHERE sentimento = 'negativo') AS negativo,
                 COUNT(*) FILTER (WHERE duvida_resolvida = TRUE) AS resolvidas,
                 COUNT(*) AS avaliadas
               FROM avaliacoes
               WHERE DATE(criado_em) = %s""",
            (data,),
        )
        a = cur.fetchone()
        score_medio = float(a[0]) if a[0] is not None else None

    return {
        "total_conversas":   total,
        "escaladas_humano":  escaladas,
        "aderiram_grupo":    aderiram_grupo,
        "duracao_media_seg": duracao_media,
        "score_medio":       score_medio,
        "positivo":          a[1] or 0,
        "neutro":            a[2] or 0,
        "negativo":          a[3] or 0,
        "duvida_resolvida":  a[4] or 0,
        "avaliadas":         a[5] or 0,
    }


def buscar_conversas_recentes(dias: int = 7) -> list[dict]:
    """Retorna conversas encerradas dos últimos N dias com nome do cliente."""
    with pg_cursor() as cur:
        cur.execute(
            """
            SELECT c.numero_whatsapp, cl.nome, c.tipo_cliente,
                   c.iniciada_em, c.encerrada_em, c.status, c.origem
            FROM conversas c
            LEFT JOIN clientes cl ON cl.numero_whatsapp = c.numero_whatsapp
            WHERE c.iniciada_em >= NOW() - (INTERVAL '1 day' * %s)
            ORDER BY c.iniciada_em DESC
            """,
            (dias,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
