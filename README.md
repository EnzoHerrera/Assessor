# Assessor.AI

Assistente pessoal (chatbot) de **finanças** e **agenda**, construído como um sistema
**multi-agentes** com LangChain (criação dos agentes) e LangGraph (orquestração do fluxo),
exposto via **FastAPI** e consumido por um front-end estático simples.

O assistente recebe uma mensagem do usuário, valida a entrada (guardrail), decide para
qual especialista encaminhar (roteador), executa a tarefa (financeiro / agenda / FAQ),
consolida a resposta (orquestrador), valida a saída (guardrail) e devolve o texto final
ao usuário. A memória de longo prazo (resumos de sessões encerradas) fica no MongoDB,
os dados financeiros ficam no PostgreSQL, e a memória de curto prazo do fluxo fica no
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
Roteador ──► Financeiro ──┐
  │      ──► Agenda ──────┼─► Orquestrador ──► guardrail_saida ──► Usuário
  │      ──► FAQ ─────────┘
  │
  └──(saudação / fora de escopo)──► resposta direta ──► FIM
```

### Componentes

| Componente             | Papel                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------- |
| **Roteador**           | Classifica a intenção e emite o protocolo de encaminhamento (`ROUTE=...`). Responde direto a saudações e mensagens fora de escopo. Também consulta o histórico de conversas anteriores quando necessário. |
| **Financeiro**         | Interpreta pedidos de finanças e opera as tools de `transactions` no PostgreSQL. Saída em JSON. |
| **Agenda**             | Interpreta pedidos de compromissos/eventos. Saída em JSON.                             |
| **FAQ**                | Responde dúvidas sobre o próprio Assessor.AI via RAG sobre o PDF oficial.               |
| **Orquestrador**       | Recebe o JSON do especialista e monta a resposta final para o usuário.                 |
| **Guardrail entrada**  | Anonimiza PII, bloqueia prompt injection / acesso a dados internos e classifica a mensagem (via LLM). |
| **Guardrail saída**    | Remove PII residual, resolve tokens anonimizados e revisa compliance (via LLM).         |

### Modelos (LLMs)

- **Especialista** (Financeiro / Agenda): `gemini-2.5-flash` (Google), com **fallback**
  para `openai/gpt-oss-120b` (Groq).
- **Rápido** (Roteador / Orquestrador / FAQ / classificação do guardrail / resumo de sessão):
  `openai/gpt-oss-120b` (Groq).

### Memória

- **Longo prazo:** MongoDB (`app/memory.py`) — um documento por sessão, com o array de
  mensagens e um resumo gerado por LLM quando a sessão é explicitamente encerrada
  (`POST /sessions/{session_id}/encerrar`). Só sessões com resumo entram na busca de
  histórico (`recuperar_historico`).
- **Fluxo (curto prazo):** `MemorySaver` do LangGraph — persiste o estado do grafo entre
  os turnos de uma mesma sessão (`thread_id` = `session_id`).

### RAG (FAQ)

O agente FAQ usa a tool `faq_retriever` (`app/tools/faq.py`): carrega o PDF a cada chamada,
divide em chunks (`RecursiveCharacterTextSplitter`), gera embeddings
(`GoogleGenerativeAIEmbeddings`) e busca os trechos mais relevantes num índice FAISS
montado na hora.

---

## Estrutura do projeto

```
migracao_fastAPI/
├── README.md
├── LICENSE
├── .env                          # chaves de API e URLs de conexão (não versionar)
├── data/
│   └── FAQ_assessor_v1.1.pdf     # base de conhecimento do agente FAQ
├── frontend/                     # console web estático (HTML/CSS/JS puro)
│   ├── index.html
│   ├── app.js                    # sessão (localStorage), chamadas a /chat e /sessions
│   └── style.css
├── app/                          # código da API
│   ├── main.py                   # cria o FastAPI app, monta rotas, CORS e o front-end estático
│   ├── config.py                 # variáveis de ambiente, caminhos e validação de config
│   ├── schemas.py                # modelos Pydantic de request/response
│   ├── llms.py                   # instâncias dos LLMs (Gemini + Groq, com fallback)
│   ├── agents.py                 # cria os agentes (create_agent) com prompts e tools
│   ├── prompts.py                # prompts de sistema e few-shots de cada agente
│   ├── guardrail.py              # guardrails de entrada e saída + anonimização de PII
│   ├── graph.py                  # define o Estado e monta o grafo (LangGraph)
│   ├── memory.py                 # memória de longo prazo (sessões e resumos no MongoDB)
│   ├── routes/
│   │   ├── chat.py                # POST /chat
│   │   └── sessions.py            # POST /sessions/{id}/iniciar e /encerrar
│   └── tools/
│       ├── db.py                  # conexão com o PostgreSQL (psycopg2)
│       ├── financeiro.py          # tools de transações no PostgreSQL
│       ├── faq.py                 # tool de RAG sobre o PDF do FAQ
│       └── mongo.py               # tool para consultar conversas anteriores (MongoDB)
└── aulas/                        # estudos e protótipos (aula01–aula06), sem relação com a API
```

---

## Rotas da API

| Método | Rota                              | Descrição                                                                 |
| ------ | ---------------------------------- | -------------------------------------------------------------------------- |
| `GET`  | `/health`                          | Health check; reporta problemas de configuração (`.env`, PDF ausente, etc.). |
| `POST` | `/chat`                            | Envia uma mensagem do usuário e recebe a resposta do assistente.           |
| `POST` | `/sessions/{session_id}/iniciar`   | Abre uma sessão explicitamente (opcional — `/chat` já abre sozinho).       |
| `POST` | `/sessions/{session_id}/encerrar`  | Encerra a sessão: gera o resumo via LLM e grava no MongoDB.                |
| `GET`  | `/`                                | Serve o front-end estático (`frontend/index.html`), se presente.          |

### `POST /chat`

```json
// request
{ "session_id": "uuid-do-navegador", "pergunta": "gastei 50 reais no mercado" }

// response
{ "resposta": "- Lancei R$ 50,00 em 'comida' hoje.\n- *Recomendação*: ..." }
```

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
| `add_transaction`    | Insere uma transação (valor, tipo, categoria, forma de pagamento, data).       |
| `search_transaction` | Consulta transações por texto e/ou intervalo de datas (America/Sao_Paulo).     |
| `saldo_total`        | Saldo (INCOME − EXPENSES) de todo o histórico.                                 |
| `saldo_diario`       | Saldo de um dia local informado (YYYY-MM-DD).                                  |
| `update_transaction` | Atualiza uma transação por `id` ou por (texto + data).                         |

Categorias suportadas: comida, besteira, estudo, férias, transporte, moradia, saúde, lazer,
contas, investimento, presente, outros.
Tipos: `1=INCOME`, `2=EXPENSES`, `3=TRANSFER`.

### `app/tools/faq.py` — RAG

- `faq_retriever` — recupera trechos relevantes do PDF do FAQ. Usada pelo agente **FAQ**.

### `app/tools/mongo.py` — histórico

- `buscar_historico` — consulta resumos de conversas anteriores (sessões já encerradas) do
  usuário. Exportada em `TOOLS_MEMORIA`, usada pelo **Roteador** e pelo agente **Agenda**.

---

## Variáveis de ambiente (`.env`)

| Variável         | Descrição                                            |
| ---------------- | ----------------------------------------------------- |
| `GEMINI_API_KEY` | Chave da API do Google Gemini.                        |
| `GROQ_API_KEY`   | Chave da API da Groq.                                 |
| `DATABASE_URL`   | String de conexão do PostgreSQL.                      |
| `MONGODB_URI`    | String de conexão do MongoDB.                         |

O caminho do PDF do FAQ é fixo em `app/config.py` (`data/FAQ_assessor_v1.1.pdf`, relativo à
raiz do projeto) e não depende de variável de ambiente.

`GET /health` reporta qualquer uma dessas variáveis ausente, ou o PDF do FAQ não encontrado.

---

## Como rodar

Pré-requisitos:

- Python 3.14
- PostgreSQL acessível via `DATABASE_URL` (com as tabelas `transactions`, `transaction_types`
  e `categories`)
- MongoDB acessível via `MONGODB_URI`
- Um arquivo `.env` na raiz com as variáveis acima preenchidas

Frameworks principais: `fastapi`, `uvicorn`, `langchain`, `langgraph`, `langchain-google-genai`,
`langchain-groq`, `langchain-community`, `psycopg2`, `pymongo`, `faiss`, `python-dotenv`.

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
