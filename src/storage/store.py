"""Storage layer: Postgres, Qdrant, Redis."""

import json
import psycopg2
import redis
import qdrant_client
from qdrant_client.models import Distance, VectorParams, PointStruct
from llama_index.embeddings.fastembed import FastEmbedEmbedding

from src.config import settings

# --- Postgres ---

from psycopg2 import pool as pg_pool

_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=2, maxconn=20,
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            dbname=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
        )
    return _pool

def pg_conn():
    return _get_pool().getconn()

def pg_release(conn):
    _get_pool().putconn(conn)

from contextlib import contextmanager

@contextmanager
def pg_cursor():
    conn = _get_pool().getconn()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _get_pool().putconn(conn)


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
