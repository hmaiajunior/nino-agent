"""
Ingestão dos atendimentos simulados (ShadowTraffic) → Postgres + Qdrant.

Uso:
    cd ninoagent
    python -m src.ingest
"""

import json
import os
import sys
from pathlib import Path

import psycopg2
import qdrant_client
from qdrant_client.models import Distance, VectorParams, PointStruct
from llama_index.embeddings.fastembed import FastEmbedEmbedding
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

DATA_DIR = Path(__file__).parent.parent / "gen" / "data"

PG_CONN = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=int(os.getenv("POSTGRES_PORT", 5432)),
    dbname=os.getenv("POSTGRES_DB", "ninoagent"),
    user=os.getenv("POSTGRES_USER", "ninoagent"),
    password=os.getenv("POSTGRES_PASSWORD", "ninoagent"),
)
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "ninoagent_conversas")
VECTOR_SIZE = 768


def load_records(data_dir: Path) -> list[dict]:
    records = []
    # Suporta .jsonl e arquivos JSON array (ex: atendimentos0.json)
    files = sorted(data_dir.glob("atendimentos*.json*"))
    for path in files:
        with open(path) as f:
            content = f.read().strip()
        if content.startswith("["):
            records.extend(json.loads(content))
        else:
            for line in content.splitlines():
                if line.strip():
                    records.append(json.loads(line))
    print(f"  {len(records)} registros carregados de {len(files)} arquivo(s)")
    return records


def ingest_postgres(records: list[dict]) -> dict[int, int]:
    """Insere conversas e avaliações. Retorna mapa conversa_id_original → id_postgres."""
    id_map = {}
    conn = psycopg2.connect(**PG_CONN)
    cur = conn.cursor()

    for r in records:
        # Insere conversa
        cur.execute(
            """INSERT INTO conversas
               (numero_whatsapp, tipo_cliente, cliente_recorrente, origem,
                status, aceitou_grupo, escalou_humano, duracao_segundos,
                iniciada_em, encerrada_em)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamp,%s::timestamp)
               RETURNING id""",
            (
                r["numero_whatsapp"],
                r["tipo_cliente"],
                r["cliente_recorrente"],
                r["origem"],
                r["status"],
                r.get("aceitou_grupo"),
                r["escalou_humano"],
                r["duracao_segundos"],
                r["iniciada_em"],
                r["iniciada_em"],  # encerrada_em = iniciada_em + duração (simplificado)
            ),
        )
        conversa_id = cur.fetchone()[0]
        id_map[r["conversa_id"]] = conversa_id

        # Insere avaliação
        cur.execute(
            """INSERT INTO avaliacoes
               (conversa_id, sentimento, score_atendimento, tema_principal,
                duvida_resolvida, interesse_compra, demanda_varejo)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                conversa_id,
                r["sentimento"],
                r["score_atendimento"],
                r["tema_principal"],
                r["duvida_resolvida"],
                r["interesse_compra"],
                r["demanda_varejo"],
            ),
        )

    conn.commit()
    cur.close()
    conn.close()
    print(f"  {len(records)} conversas + avaliações inseridas no Postgres")
    return id_map


def ingest_qdrant(records: list[dict], id_map: dict[int, int]) -> None:
    """Gera embeddings do histórico e indexa no Qdrant."""
    client = qdrant_client.QdrantClient(url=QDRANT_URL)

    if client.collection_exists(COLLECTION):
        print(f"  Collection '{COLLECTION}' existente — recriando...")
        client.delete_collection(COLLECTION)

    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )

    embed = FastEmbedEmbedding(model_name="BAAI/bge-base-en-v1.5")

    points = []
    for r in records:
        texto = r.get("historico", "")
        if not texto:
            continue
        vector = embed.get_text_embedding(texto)
        conversa_id = id_map.get(r["conversa_id"], r["conversa_id"])
        points.append(
            PointStruct(
                id=conversa_id,
                vector=vector,
                payload={
                    "conversa_id": conversa_id,
                    "tipo_cliente": r["tipo_cliente"],
                    "origem": r["origem"],
                    "sentimento": r["sentimento"],
                    "tema_principal": r["tema_principal"],
                    "score_atendimento": r["score_atendimento"],
                    "historico": texto,
                },
            )
        )

    client.upsert(collection_name=COLLECTION, points=points)
    count = client.count(COLLECTION).count
    print(f"  {count} vetores indexados no Qdrant (collection: {COLLECTION})")


def main():
    if not DATA_DIR.exists() or not list(DATA_DIR.glob("atendimentos*.json*")):
        print(f"Nenhum arquivo encontrado em {DATA_DIR}")
        print("Execute primeiro: cd gen && docker compose up shadowtraffic")
        sys.exit(1)

    print("=== NinoAgent — Ingestão de Atendimentos Simulados ===\n")

    print("1. Carregando dados gerados pelo ShadowTraffic...")
    records = load_records(DATA_DIR)

    print("2. Inserindo no Postgres...")
    id_map = ingest_postgres(records)

    print("3. Gerando embeddings e indexando no Qdrant...")
    ingest_qdrant(records, id_map)

    print("\nIngestão concluída com sucesso!")
    print(f"  Conversas no Postgres: {len(records)}")
    print(f"  Vetores no Qdrant:     {len(records)}")


if __name__ == "__main__":
    main()
