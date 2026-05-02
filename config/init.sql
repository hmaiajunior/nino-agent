CREATE TABLE IF NOT EXISTS clientes (
    id SERIAL PRIMARY KEY,
    numero_whatsapp VARCHAR(20) UNIQUE NOT NULL,
    nome VARCHAR(100),
    cnpj VARCHAR(18),
    cidade VARCHAR(100),
    estado CHAR(2),
    email VARCHAR(100),
    tipo VARCHAR(10) DEFAULT 'atacado',
    membro_grupo BOOLEAN DEFAULT FALSE,
    criado_em TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversas (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER REFERENCES clientes(id),
    numero_whatsapp VARCHAR(20) NOT NULL,
    tipo_cliente VARCHAR(10),
    cliente_recorrente BOOLEAN DEFAULT FALSE,
    origem VARCHAR(50) DEFAULT 'organico',
    status VARCHAR(20) DEFAULT 'pendente',
    aceitou_grupo BOOLEAN,
    escalou_humano BOOLEAN DEFAULT FALSE,
    iniciada_em TIMESTAMP DEFAULT NOW(),
    encerrada_em TIMESTAMP,
    duracao_segundos INTEGER
);

CREATE TABLE IF NOT EXISTS avaliacoes (
    id SERIAL PRIMARY KEY,
    conversa_id INTEGER REFERENCES conversas(id),
    sentimento VARCHAR(10),
    score_atendimento SMALLINT CHECK (score_atendimento BETWEEN 1 AND 5),
    tema_principal VARCHAR(50),
    duvida_resolvida BOOLEAN,
    interesse_compra BOOLEAN,
    demanda_varejo BOOLEAN DEFAULT FALSE,
    observacoes TEXT,
    criado_em TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_conversas_numero ON conversas(numero_whatsapp);
CREATE INDEX idx_conversas_data ON conversas(iniciada_em);
CREATE INDEX idx_avaliacoes_sentimento ON avaliacoes(sentimento);

-- Histórico individual de mensagens (timeline contínua por contato)
CREATE TABLE IF NOT EXISTS mensagens (
    id SERIAL PRIMARY KEY,
    numero_whatsapp VARCHAR(20) NOT NULL,
    conversa_id INTEGER REFERENCES conversas(id),
    role VARCHAR(10) NOT NULL,         -- 'cliente' | 'agente' | 'humano'
    type VARCHAR(15) DEFAULT 'text',   -- 'text' | 'audio' | 'video' | 'image' | 'document'
    text TEXT,
    media_id VARCHAR(64),
    criado_em TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mensagens_numero_data ON mensagens(numero_whatsapp, criado_em);
