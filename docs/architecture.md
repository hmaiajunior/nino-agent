# NinoAgent — Arquitetura Técnica

## Visão Geral

Sistema multi-agente para automação de atendimento e campanhas de uma loja de moda infantil
focada em atacado. Construído com CrewAI, Claude, Evolution API e infraestrutura Docker/AWS.

```
[CampaignAgent] ←→ [PerformanceAgent]
       ↓
  Facebook Ads
       ↓
  Lead chega no WhatsApp (Evolution API)
       ↓
[QualificationAgent]
  - varejo ou atacado?
  - já é cliente? (consulta Postgres)
  - origem da campanha?
       ↓                        ↓
  é varejo?              é atacado?
       ↓                        ↓
 responde padrão         [WholesaleAgent]
 e encerra                 - atende dúvidas
                           - convida para grupo
                                ↓
                        [SentimentAgent]
                          - analisa conversa
                          - classifica satisfação
                                ↓
                       ┌────────┴────────┐
                       ↓                 ↓
                   Postgres           Qdrant
                (metadados)      (embeddings das
                                   conversas)
                       └────────┬────────┘
                                ↓
                        [InsightAgent] ← roda 23h (cron)
                          - relatório diário completo
```

---

## Agentes

### QualificationAgent
- **LLM:** Claude claude-3-5-haiku (rápido, barato — tarefa simples)
- **Tools:** `consultar_cliente_postgres(numero_whatsapp)`
- **Trigger:** webhook Evolution API (nova mensagem)
- **Output:** JSON de contexto → passa para WholesaleAgent ou encerra

### WholesaleAgent
- **LLM:** Claude claude-3-5-sonnet (qualidade máxima — atendimento ao cliente)
- **Tools:**
  - `consultar_produtos(categoria, faixa_etaria)`
  - `consultar_historico_cliente(cliente_id)`
  - `registrar_interesse(cliente_id, dados)`
  - `enviar_mensagem_whatsapp(numero, texto)`
- **Trigger:** contexto do QualificationAgent
- **Output:** conversa completa salva no Postgres + Qdrant

### SentimentAgent
- **LLM:** Claude claude-3-5-haiku (análise estruturada, output JSON)
- **Tools:**
  - `salvar_avaliacao_postgres(dados)`
  - `indexar_conversa_qdrant(conversa_id, texto, metadata)`
- **Trigger:** fim de cada conversa (timeout 30min sem resposta = conversa encerrada)
- **Output:** JSON de avaliação persistido

### InsightAgent
- **LLM:** Claude claude-3-5-sonnet (relatório executivo)
- **Tools:**
  - `buscar_avaliacoes_dia(data)`
  - `buscar_historico_7dias()`
  - `buscar_temas_qdrant(periodo)`
  - `enviar_relatorio(destino, conteudo)`
- **Trigger:** cron job 23h00 via n8n
- **Output:** relatório enviado via WhatsApp/email para o dono da loja

---

## Storage

### Postgres — The Ledger

```sql
-- Clientes
CREATE TABLE clientes (
    id SERIAL PRIMARY KEY,
    numero_whatsapp VARCHAR(20) UNIQUE NOT NULL,
    nome VARCHAR(100),
    cnpj VARCHAR(18),
    cidade VARCHAR(100),
    estado CHAR(2),
    email VARCHAR(100),
    tipo VARCHAR(10) DEFAULT 'atacado',  -- atacado | varejo
    membro_grupo BOOLEAN DEFAULT FALSE,
    criado_em TIMESTAMP DEFAULT NOW()
);

-- Conversas
CREATE TABLE conversas (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER REFERENCES clientes(id),
    numero_whatsapp VARCHAR(20),
    tipo_cliente VARCHAR(10),          -- atacado | varejo
    cliente_recorrente BOOLEAN,
    origem VARCHAR(50),                -- campanha_[nome] | organico
    status VARCHAR(20),                -- resolvido | pendente | convertido | descartado
    aceitou_grupo BOOLEAN,
    escalou_humano BOOLEAN DEFAULT FALSE,
    iniciada_em TIMESTAMP DEFAULT NOW(),
    encerrada_em TIMESTAMP,
    duracao_segundos INTEGER
);

-- Avaliações (output do SentimentAgent)
CREATE TABLE avaliacoes (
    id SERIAL PRIMARY KEY,
    conversa_id INTEGER REFERENCES conversas(id),
    sentimento VARCHAR(10),            -- positivo | neutro | negativo
    score_atendimento SMALLINT,        -- 1-5
    tema_principal VARCHAR(50),
    duvida_resolvida BOOLEAN,
    interesse_compra BOOLEAN,
    demanda_varejo BOOLEAN DEFAULT FALSE,
    observacoes TEXT,
    criado_em TIMESTAMP DEFAULT NOW()
);
```

### Qdrant — The Memory

**Collection:** `ninoagent_conversas`
- **Embedding model:** FastEmbed BAAI/bge-base-en-v1.5 (local, sem custo por token)
- **Payload por ponto:**
```json
{
  "conversa_id": 123,
  "cliente_id": 456,
  "data": "2026-04-16",
  "sentimento": "positivo",
  "tema": "duvida_produto",
  "tipo_cliente": "atacado"
}
```

### Redis — Session Store
- Chave: `session:{numero_whatsapp}`
- TTL: 30 minutos (timeout de conversa)
- Valor: histórico da conversa em andamento + contexto do QualificationAgent

---

## Infraestrutura

### Docker Compose (desenvolvimento local)
```yaml
services:
  postgres:    # porta 5432
  qdrant:      # porta 6333
  redis:       # porta 6379
  evolution:   # porta 8080 (Evolution API)
  n8n:         # porta 5678
  langfuse:    # porta 3000
```

### AWS (produção)
| Serviço | AWS |
|---------|-----|
| Agentes CrewAI | ECS Fargate |
| Postgres | RDS t3.small |
| Qdrant | EC2 t3.medium |
| Redis | ElastiCache t3.micro |
| Evolution API | ECS Fargate |
| n8n | ECS Fargate |
| LangFuse | ECS Fargate + RDS |
| Secrets | AWS Secrets Manager |

---

## Fluxo de dados completo

```
1. Lead clica no anúncio do Facebook
2. Abre WhatsApp → mensagem recebida pela Evolution API
3. Evolution API dispara webhook → n8n
4. n8n aciona QualificationAgent via CrewAI
5. QualificationAgent consulta Postgres (cliente existente?)
6. QualificationAgent classifica e passa contexto
7a. Varejo → resposta padrão → SentimentAgent → encerra
7b. Atacado → WholesaleAgent inicia atendimento
8. WholesaleAgent mantém conversa (contexto no Redis)
9. Timeout 30min → conversa encerrada → SentimentAgent acionado
10. SentimentAgent salva avaliação no Postgres + embedding no Qdrant
11. 23h00 → n8n aciona InsightAgent
12. InsightAgent consulta Postgres + Qdrant → gera relatório → envia
```

---

## Observabilidade

**LangFuse** rastreia por conversa:
- Tokens consumidos por agente
- Latência de resposta
- Score de qualidade (do SentimentAgent)
- Custo estimado

**Alertas (via n8n):**
- Score médio do dia < 3.0 → alerta imediato para o dono
- Mais de 5 conversas negativas seguidas → alerta
- InsightAgent falhou ao rodar → alerta

---

## Variáveis de ambiente

```env
# LLM
ANTHROPIC_API_KEY=

# WhatsApp
EVOLUTION_API_URL=
EVOLUTION_API_KEY=
EVOLUTION_INSTANCE=

# Storage
POSTGRES_HOST=
POSTGRES_DB=ninoagent
POSTGRES_USER=
POSTGRES_PASSWORD=
QDRANT_URL=
QDRANT_COLLECTION=ninoagent_conversas
REDIS_URL=

# Observabilidade
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=

# Negócio
LOJA_NOME=
LOJA_WHATSAPP=
GRUPO_LINK=
HORARIO_ATENDIMENTO=
```
