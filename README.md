# Assessor.AI

Assistente pessoal (chatbot) de **finanças** e **agenda**, construído como um sistema
**multi-agentes** com LangChain (criação dos agentes) e LangGraph (orquestração do fluxo),
exposto via **FastAPI** e consumido por um front-end estático simples.

O assistente recebe uma mensagem do usuário, valida a entrada (guardrail), decide para
qual especialista encaminhar (roteador), executa a tarefa (financeiro / agenda / FAQ),
consolida a resposta (orquestrador), valida a saída (guardrail) e devolve o texto final
ao usuário. Um **juiz de fidelidade** (Jev) confere a resposta contra as evidências das tools e só
então as escritas do turno são gravadas. A conversa completa (texto bruto) e os resumos de sessão ficam no MongoDB,
os dados financeiros ficam no PostgreSQL, a busca semântica (FAQ e memória de longo
prazo) roda sobre embeddings no Qdrant, e a memória de curto prazo do fluxo fica no
checkpointer do LangGraph.

---

## Arquitetura

```
Usuário
  │
  ▼
guardrail_entrada ──(bloqueado)──► FIM
  │
  ▼
Roteador ──► Financeiro ──┐   (tools de escrita só PREPARAM a operação)
  │      ──► Agenda ──────┼─► Orquestrador ──┐
  │      ──► FAQ ─────────┴──────────────────┴─► Juiz (Jev) ─► confirmar_escritas ─► guardrail_saida ─► Usuário
  │                                                 │                 │
  │                                          recusa / erro        aprovado: grava no Postgres
  │                                          => frase segura      (1 transação) e só depois
  │                                                               chama o Google Calendar
  └──(saudação / fora de escopo)──► resposta direta ──► FIM
```

### Componentes

| Componente             | Papel                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------- |
| **Roteador**           | Classifica a intenção e emite o protocolo de encaminhamento (`ROUTE=...`). Responde direto a saudações e mensagens fora de escopo. Também consulta o histórico de conversas anteriores quando necessário. |
| **Financeiro**         | Interpreta pedidos de finanças e opera as tools de `transactions` no PostgreSQL. Saída em JSON. |
| **Agenda**             | Interpreta pedidos de compromissos/eventos. Saída em JSON.                             |
| **FAQ**                | Responde dúvidas sobre o próprio Assessor.AI via RAG sobre o PDF oficial.               |
| **Orquestrador**       | Recebe o JSON do especialista e monta a resposta final para o usuário. Também confere se a saída é coerente com a escrita pendente. |
| **Juiz (Jev)**         | Confere a resposta contra as evidências das tools (`app/juiz.py`). Se recusar, errar ou não responder, a resposta vira a frase segura e nada é gravado. |
| **confirmar_escritas** | Nó sem LLM: só grava a fila do turno se o juiz aprovou (`app/graph.py`).               |
| **Guardrail entrada**  | Anonimiza PII, bloqueia prompt injection / acesso a dados internos e classifica a mensagem (via LLM). |
| **Guardrail saída**    | Remove PII residual, resolve tokens anonimizados e revisa compliance (via LLM).         |

### Modelos (LLMs)

- **Especialista** (Financeiro / Agenda): `gemini-2.5-flash` (Google), com **fallback**
  para `openai/gpt-oss-120b` (Groq).
- **Rápido** (Roteador / Orquestrador / FAQ / classificação do guardrail / resumo de sessão):
  `openai/gpt-oss-120b` (Groq).

### Memória

- **Longo prazo (bruta):** MongoDB (`app/memory.py`) — um documento por sessão, com o
  array de mensagens e um resumo gerado por LLM quando a sessão é explicitamente
  encerrada (`POST /sessions/{session_id}/encerrar`).
- **Longo prazo (semântica):** Qdrant, coleção `memoria_resumos` — ao encerrar a sessão,
  o resumo também é convertido em embedding e indexado lá, com `user_id` no payload para
  isolar os resumos por usuário. A tool `buscar_historico` faz busca por similaridade
  nessa coleção (com *fallback* para o Mongo, filtrando por `resumo` não vazio, quando não
  há termo de busca).
- **Fluxo (curto prazo):** `MemorySaver` do LangGraph — persiste o estado do grafo entre
  os turnos de uma mesma sessão (`thread_id` = `session_id`).

`user_id` é o identificador **estável** do usuário entre sessões (o `session_id` muda a
cada "nova sessão" no front); é ele que amarra os resumos de conversas diferentes à mesma
pessoa. Hoje o `ChatRequest` aceita `user_id` com padrão `"usuario_teste"`.

### Perfil financeiro

A tela `frontend/perfil.html` envia o perfil para `POST /perfil` (`app/routes/perfil.py`). O
dado estruturado (renda, gasto fixo, horizonte, perfil de investidor) vai para o MongoDB
(collection `perfis`) e **cada restrição** vira um ponto próprio no Qdrant, collection
`perfil_restricoes`, com `user_id` no payload. Salvar de novo substitui o cadastro inteiro
nos dois bancos. O especialista financeiro consulta isso pela tool `consultar_perfil_financeiro`
(`app/tools/perfil.py`), que lê o `user_id` **só do contexto da requisição** (nunca do modelo e
sem fallback para `thread_id`) e faz busca semântica nas 3 restrições mais próximas da pergunta.
O chat só lê o perfil: quem escreve é a tela.

### Escrita e juiz

As tools que alteram dados (`add_transaction`, `update_transaction`, `add_event`,
`add_google_event`) **não gravam**. Elas registram a intenção numa fila do turno
(`app/tools/write_staging.py`) e devolvem `pending_approval`. Depois do veredito do juiz, o nó
`confirmar_escritas` decide: aprovado grava todas as escritas do Postgres numa única
transação (e só então chama o Google Calendar); recusado, sem resposta, com erro ou com
saída incoerente descarta a fila sem abrir conexão de escrita. Detalhes e alternativas
descartadas em [decisao_tecnica.md](decisao_tecnica.md).

### RAG (FAQ)

O conteúdo do PDF é **pré-indexado** no Qdrant, coleção `faq_chunks`, pelo script
`app/ingest_faq.py` — ele carrega o PDF, divide em chunks
(`RecursiveCharacterTextSplitter`), gera embeddings (`GoogleGenerativeAIEmbeddings`,
`gemini-embedding-2-preview`, 768d) e faz upsert em lote no Qdrant. Deve ser rodado uma
vez (ou sempre que o PDF mudar):

```bash
python -m app.ingest_faq
```

Em tempo de execução, a tool `faq_retriever` (`app/tools/faq.py`) só gera o embedding da
pergunta e consulta os 6 pontos mais próximos na coleção já indexada — nada de PDF é lido
nem re-indexado a cada chamada. O cliente Qdrant e a função de embedding ficam
centralizados em `app/qdrant.py` (mesmo modelo de embedding usado pela memória semântica).

---

## Estrutura do projeto

```
migracao_fastAPI/
├── README.md
├── LICENSE
├── decisao_tecnica.md            # decisão da Etapa 8 (juiz e escrita), com evidências
├── JUSTIFICATIVA.md              # justificativas da prática do Perfil
├── requirements.txt              # dependências
├── .env.example                  # modelo do .env (sem valores)
├── .env                          # chaves de API e URLs de conexão (não versionar)
├── gcp-oauth.keys.json           # credenciais OAuth do Google (não versionar)
├── google-calendar.token.json    # token gerado por autenticar() (não versionar)
├── images/                       # prints usados no decisao_tecnica.md
├── data/
│   └── FAQ_assessor_v1.1.pdf     # base de conhecimento do agente FAQ
├── frontend/                     # console web estático (HTML/CSS/JS puro)
│   ├── index.html
│   ├── app.js                    # sessão (localStorage), chamadas a /chat e /sessions
│   ├── style.css
│   ├── perfil.html / .css / .js  # tela Perfil (POST /perfil)
│   └── monitor.html              # monitor de latência, erro, tokens e custo
├── app/                          # código da API
│   ├── main.py                   # cria o FastAPI app, monta rotas, CORS e o front-end estático
│   ├── config.py                 # variáveis de ambiente, caminhos e validação de config
│   ├── schemas.py                # modelos Pydantic de request/response
│   ├── llms.py                   # instâncias dos LLMs (Gemini + Groq, com fallback)
│   ├── agents.py                 # cria os agentes (create_agent) com prompts e tools
│   ├── prompts.py                # prompts de sistema e few-shots de cada agente
│   ├── guardrail.py              # guardrails de entrada e saída + anonimização de PII
│   ├── graph.py                  # define o Estado e monta o grafo (LangGraph), incl. confirmar_escritas
│   ├── juiz.py                   # juiz de fidelidade (Jev): veredito que autoriza a escrita
│   ├── perfil.py                 # persistência do perfil (MongoDB + Qdrant)
│   ├── observabilidade.py        # registro de latência, erro, tokens e custo por turno
│   ├── mcp_server.py             # servidor MCP com as tools financeiras
│   ├── memory.py                 # memória de longo prazo (sessões no MongoDB + resumos no Qdrant)
│   ├── qdrant.py                 # cliente Qdrant e geração de embeddings (compartilhado)
│   ├── ingest_faq.py             # script de indexação do PDF do FAQ no Qdrant (rodar 1x)
│   ├── routes/
│   │   ├── chat.py                # POST /chat
│   │   ├── perfil.py              # POST /perfil
│   │   ├── monitor.py             # GET /monitor
│   │   └── sessions.py            # POST /sessions/{id}/iniciar e /encerrar
│   └── tools/
│       ├── db.py                  # conexão com o PostgreSQL (psycopg2)
│       ├── write_staging.py       # fila de escritas do turno (prepara / descarta / confirma)
│       ├── financeiro.py          # tools de transações no PostgreSQL
│       ├── agenda.py              # tools de agenda (events no PostgreSQL)
│       ├── calendario_google.py   # Google Calendar (MCP oficial + Calendar API) e autenticar()
│       ├── perfil.py              # consultar_perfil_financeiro (só leitura)
│       ├── faq.py                 # tool de RAG sobre o PDF do FAQ (consulta o Qdrant já indexado)
│       └── mongo.py               # tool para consultar conversas anteriores (busca semântica)
```

---

## Rotas da API

| Método | Rota                              | Descrição                                                                 |
| ------ | ---------------------------------- | -------------------------------------------------------------------------- |
| `GET`  | `/health`                          | Health check; reporta problemas de configuração (`.env`, PDF ausente, etc.). |
| `POST` | `/chat`                            | Envia uma mensagem do usuário e recebe a resposta do assistente.           |
| `POST` | `/sessions/{session_id}/iniciar`   | Abre uma sessão explicitamente (opcional — `/chat` já abre sozinho).       |
| `POST` | `/sessions/{session_id}/encerrar`  | Encerra a sessão: gera o resumo via LLM e grava no MongoDB.                |
| `POST` | `/perfil`                          | Salva o perfil financeiro (Mongo + Qdrant) e devolve o perfil gravado.     |
| `GET`  | `/monitor`                         | Métricas dos turnos (latência, p95, erro, tokens, custo). Tela: `/monitor.html`. |
| `GET`  | `/`                                | Serve o front-end estático (`frontend/index.html`), se presente.          |

### `POST /chat`

```json
// request
{
  "session_id": "uuid-do-navegador",
  "user_id": "usuario_teste",
  "pergunta": "gastei 50 reais no mercado"
}

// response
{ "resposta": "- Lancei R$ 50,00 em 'comida' hoje.\n- *Recomendação*: ..." }
```

`user_id` é opcional (padrão `"usuario_teste"`) e identifica o usuário de forma estável
entre sessões — é o que a busca semântica de histórico usa para isolar os resumos de cada
pessoa no Qdrant.

### `POST /sessions/{session_id}/encerrar`

```json
// response
{ "session_id": "uuid-do-navegador", "resumo": "Usuário registrou um gasto de R$ 50 em comida." }
```

O front-end chama essa rota automaticamente ao clicar em "nova sessão", antes de gerar um
novo `session_id` — é o gatilho que gera o resumo usado depois pela tool `buscar_historico`.

---

## Tools

### `app/tools/financeiro.py` — PostgreSQL (transações)

Exportadas em `TOOLS`, usadas pelo agente **Financeiro**:

| Tool                 | Função                                                                        |
| -------------------- | ------------------------------------------------------------------------------ |
| `add_transaction`    | **Prepara** uma transação (valor, tipo, categoria, forma de pagamento, data); grava só após o juiz aprovar. |
| `search_transaction` | Consulta transações por texto e/ou intervalo de datas (America/Sao_Paulo).     |
| `saldo_total`        | Saldo (INCOME − EXPENSES) de todo o histórico.                                 |
| `saldo_diario`       | Saldo de um dia local informado (YYYY-MM-DD).                                  |
| `update_transaction` | **Prepara** a atualização de uma transação por `id` ou por (texto + data); grava só após o juiz aprovar. |

Categorias suportadas: comida, besteira, estudo, férias, transporte, moradia, saúde, lazer,
contas, investimento, presente, outros.
Tipos: `1=INCOME`, `2=EXPENSES`, `3=TRANSFER`.

### `app/tools/agenda.py` — PostgreSQL (eventos)

Exportadas em `TOOLS_AGENDA`, usadas pelo agente **Agenda**:

| Tool           | Função                                                                            |
| -------------- | ---------------------------------------------------------------------------------- |
| `add_event`    | **Prepara** um evento (título, início, fim, local, notas); grava só após o juiz aprovar. |
| `query_events` | Consulta eventos por texto e/ou datas locais (America/Sao_Paulo).                  |

### `app/tools/calendario_google.py` — Google Calendar

- `add_google_event` (em `TOOLS_GOOGLE`) — **prepara** o evento no Google Calendar; ele só é criado
  depois do commit do Postgres, e nunca se o turno foi recusado.
- `autenticar()` — fluxo OAuth que gera o `google-calendar.token.json` (veja "Como rodar").

### `app/tools/perfil.py` — perfil

- `consultar_perfil_financeiro` — lê o perfil cadastrado e as restrições mais relevantes. Usada
  pelo agente **Financeiro**; só leitura.

### `app/tools/faq.py` — RAG

- `faq_retriever` — recupera trechos relevantes do PDF do FAQ. Usada pelo agente **FAQ**.

### `app/tools/mongo.py` — histórico

- `buscar_historico` — consulta resumos de conversas anteriores (sessões já encerradas) do
  usuário. Exportada em `TOOLS_MEMORIA`, usada pelo **Roteador** e pelo agente **Agenda**.

---

## Variáveis de ambiente (`.env`)

| Variável         | Descrição                                            |
| ---------------- | ----------------------------------------------------- |
| `GEMINI_API_KEY` | Chave da API do Google Gemini (LLMs e embeddings).     |
| `GROQ_API_KEY`   | Chave da API da Groq.                                 |
| `DATABASE_URL`   | String de conexão do PostgreSQL.                      |
| `MONGODB_URI`    | String de conexão do MongoDB.                         |
| `QDRANT_URL`     | URL da instância Qdrant.                              |
| `QDRANT_API_KEY` | Chave da API do Qdrant.                                |
| `TYPESAFE_API_KEY` | Chave do Jev, o juiz de fidelidade (obrigatória; sem ela toda resposta vira a frase segura). |
| `GOOGLE_OAUTH_CREDENTIALS` | Caminho do JSON de credenciais OAuth (padrão: `gcp-oauth.keys.json` na raiz). |
| `GOOGLE_CALENDAR_MCP_URL` | URL do MCP do Google Calendar (opcional, já tem padrão). |
| `GOOGLE_CALENDAR_ACCESS_TOKEN` | Access token avulso (opcional; senão usa `google-calendar.token.json`). |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Observabilidade no LangSmith (opcional). |

Há um modelo em `.env.example`. O caminho do PDF do FAQ é fixo em `app/config.py` (`data/FAQ_assessor_v1.1.pdf`, relativo à
raiz do projeto) e não depende de variável de ambiente.

`GET /health` reporta qualquer uma dessas variáveis ausente, ou o PDF do FAQ não encontrado.

---

## Como rodar

Pré-requisitos:

- Python 3.14
- PostgreSQL acessível via `DATABASE_URL` (com as tabelas `transactions`, `transaction_types`
  e `categories`)
- MongoDB acessível via `MONGODB_URI`
- Qdrant acessível via `QDRANT_URL` / `QDRANT_API_KEY`, com as coleções `faq_chunks`,
  `memoria_resumos` e `perfil_restricoes` já criadas (768 dimensões, Cosine, mesma dimensão do
  embedding `gemini-embedding-2-preview`) — o código faz `upsert`/`query` nelas, mas não as cria.
  O passo a passo está em [GUIA_QDRANT.md](GUIA_QDRANT.md).
- A tabela `events` no PostgreSQL (agenda), além das de finanças.
- Uma chave do Jev em `TYPESAFE_API_KEY`.
- Um arquivo `.env` na raiz com as variáveis acima preenchidas

Instalando as dependências (use um ambiente virtual):

```bash
pip install -r requirements.txt
```

Depois copie `.env.example` para `.env` e preencha.

Primeiro uso (indexa o PDF do FAQ no Qdrant — repita sempre que o PDF mudar):

```bash
python -m app.ingest_faq
```

Google Calendar (opcional, só para criar eventos no Google): coloque o JSON de credenciais OAuth
("App para computador", do seu projeto no Google Cloud, com a Calendar API ativa e seu Gmail
como usuário de teste) em `gcp-oauth.keys.json` na raiz e rode uma vez:

```bash
python -c "from app.tools.calendario_google import autenticar; print(autenticar())"
```

Isso abre o navegador e gera o `google-calendar.token.json`. Os dois arquivos estão no
`.gitignore`.

Subindo a API:

```bash
uvicorn app.main:app --reload
```

Por padrão a API sobe em `http://localhost:8000`. Com `frontend/index.html` presente, ele já
é servido na raiz (`/`) — basta abrir o navegador nesse endereço. O `app.js` do front aponta
para `http://localhost:8000` via `API_BASE`; ajuste essa constante se a API rodar em outra
porta/host.

---

## Segurança e compliance

- **PII** (CPF, CNPJ, telefone, e-mail, conta, cartão) é anonimizada na entrada e removida/omitida na saída.
- **Prompt injection** e tentativas de **acesso a dados internos** são bloqueadas por padrões determinísticos.
- **Classificação semântica** (ofensivo, perigoso, ilícito, político, indicação de investimento) é feita por LLM na entrada.
- **Compliance financeiro** (CVM/ANBIMA) é revisado por LLM na saída.
