# Backlog — NinoAgent

## Em aberto

### ~~[P1] Agrupamento de interações em conversa única por dia~~ ✅ Concluído

**Solução implementada:**
- `store.py`: `buscar_conversa_do_dia()` reutiliza conversa existente do dia; `upsert_avaliacao_do_dia()` atualiza avaliação com regra "negativo prevalece"
- `webhook.py`: `_garantir_conversa_registrada` consulta conversa do dia antes de criar nova
- `sentiment.py`: usa `upsert_avaliacao_do_dia` em vez de `salvar_avaliacao`
- Queries do monitor e relatório não precisaram de alteração — já contam por `conversas.id`

---

### [P2] Verificar envio do catálogo
Após cópia das credenciais Google Drive via scp, confirmar que a ferramenta `enviar_catalogo` funciona corretamente em produção.

---

### [P3] Associar `conversa_id` às mensagens
Passar `conversa_id` da sessão Redis para `salvar_mensagem` nos pontos de envio, para rastreabilidade completa.

---

### [P4] Badge de sentimento na sidebar do monitor
CSS e estrutura HTML já existem (`conv-badges`), mas o badge 😊/😟 não aparece nos itens da lista — falta fazer o join entre `listar_contatos` e a última avaliação de sentimento no endpoint `/conversas` e passar o campo `sentimento` para o template JS.

---

### [P5] Testar Llama 3.3 70B como modelo do WholesaleAgent
Variável: `WHOLESALE_MODEL=openrouter/meta-llama/llama-3.3-70b-instruct`
Comparar qualidade de resposta e latência com o Gemini 2.5 Flash atual.

---

~~### [P6] Token permanente da Meta~~ ✅ **Implementado** — `WHATSAPP_TOKEN` configurado via `.env`, usado em todos os pontos de envio. Documentado em `producao.md` como token de sistema permanente via Business Manager.
