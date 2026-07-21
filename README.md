# Assessor.AI

Assistente pessoal (chatbot) de **finanças** e **agenda**, construído como um sistema
**multi-agentes** com LangChain (criação dos agentes) e LangGraph (orquestração do fluxo).

O assistente recebe uma mensagem do usuário, valida a entrada (guardrail), decide para
qual especialista encaminhar (roteador), executa a tarefa (financeiro / agenda / FAQ),
consolida a resposta (orquestrador), valida a saída (guardrail) e devolve o texto final
ao usuário. A memória de longo prazo fica no MongoDB e a memória do fluxo fica no grafo.

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
  │      ──► FAQ ─────────┼─────────────────────────────────────► Usuário (bypassa o orquestrador)
  │
  └──(saudação / fora de escopo)──► resposta direta ──► FIM
```

### Componentes

| Componente          | Papel                                                                                 |
| ------------------- | ------------------------------------------------------------------------------------- |
| **Roteador**        | Classifica a intenção e emite o protocolo de encaminhamento (`ROUTE=...`). Responde direto a saudações e mensagens fora de escopo. |
| **Financeiro**      | Interpreta pedidos de finanças e opera as tools de `transactions`. Saída em JSON.     |
| **Agenda**          | Interpreta pedidos de compromissos/eventos. Saída em JSON.                            |
| **FAQ**             | Responde dúvidas sobre o próprio Assessor.AI via RAG sobre o PDF oficial.             |
| **Orquestrador**    | Recebe o JSON do especialista e monta a resposta final para o usuário.                |
| **Guardrail entrada** | Anonimiza PII, bloqueia prompt injection / acesso a dados internos e classifica a mensagem (via LLM). |
| **Guardrail saída** | Remove PII residual, resolve tokens anonimizados e revisa compliance (via LLM).       |

### Modelos (LLMs)

- **Especialista** (Financeiro / Agenda): `gemini-2.5-flash` (Google) com **fallback** para
  `openai/gpt-oss-120b` (Groq).
- **Rápido** (Roteador / Orquestrador / FAQ): `llama-3.3-70b-versatile` (Groq).
- **Guardrail** e **resumo de sessão**: `llama-3.3-70b-versatile` (Groq).

### Memória

- **Longo prazo:** MongoDB (`memory_mongodb.py`) — um documento por sessão, com as mensagens
  e um resumo gerado por LLM ao encerrar a sessão.
- **Fluxo:** `MemorySaver` do LangGraph — persiste o estado do grafo entre os turnos.

### RAG (FAQ)

O agente FAQ usa `faq_retriever` (`app/tools/faq_tools.py`): carrega o PDF, divide em chunks
(`RecursiveCharacterTextSplitter`), gera embeddings (`GoogleGenerativeAIEmbeddings`) e busca
os trechos mais relevantes num índice FAISS.

---

## Estrutura do projeto

```
Assessor/
├── README.md
├── .env                         # chaves de API e URLs de conexão (não versionar)
├── .vscode/
│   └── settings.json
├── app/                         # código da aplicação
│   ├── main.py                  # ponto de entrada: monta o grafo e roda o loop de conversa
│   ├── prompts.py               # prompts de sistema e few-shots de cada agente
│   ├── guardrail.py             # guardrails de entrada e saída + anonimização de PII
│   ├── memory_mongodb.py        # memória de longo prazo (sessões e resumos no MongoDB)
│   ├── data/
│   │   └── FAQ_assessor_v1.1.pdf # base de conhecimento do agente FAQ
│   └── tools/
│       ├── pg_tools.py          # tools de transações no PostgreSQL
│       ├── faq_tools.py         # tool de RAG sobre o PDF do FAQ
│       └── memory_tools.py      # tool para consultar conversas anteriores
└── aulas/                       # estudos e protótipos (aula01–aula06)
```

---

## Tools

### `pg_tools.py` — PostgreSQL (transações)

Exportadas em `TOOLS`:

| Tool                 | Função                                                                        |
| -------------------- | ----------------------------------------------------------------------------- |
| `add_transaction`    | Insere uma transação (valor, tipo, categoria, forma de pagamento, data).      |
| `search_transaction` | Consulta transações por texto e/ou intervalo de datas (America/Sao_Paulo).    |
| `saldo_total`        | Saldo (INCOME − EXPENSES) de todo o histórico.                                |
| `saldo_diario`       | Saldo de um dia local informado (YYYY-MM-DD).                                  |
| `update_transaction` | Atualiza uma transação por `id` ou por (texto + data).                        |

Categorias suportadas: comida, besteira, estudo, férias, transporte, moradia, saúde, lazer,
contas, investimento, presente, outros.
Tipos: `1=INCOME`, `2=EXPENSES`, `3=TRANSFER`.

### `faq_tools.py` — RAG

- `faq_retriever` — recupera trechos relevantes do PDF do FAQ.

### `memory_tools.py` — histórico

- `buscar_historico` — consulta resumos de conversas anteriores (sessões já encerradas) do usuário.

---

## Variáveis de ambiente (`.env`)

| Variável         | Descrição                                            |
| ---------------- | ---------------------------------------------------- |
| `GEMINI_API_KEY` | Chave da API do Google Gemini.                       |
| `GROQ_API_KEY`   | Chave da API da Groq.                                |
| `DATABASE_URL`   | String de conexão do PostgreSQL.                     |
| `MONGODB_URI`    | String de conexão do MongoDB.                        |
| `FAQ_PDF_PATH`   | (Opcional) Caminho do PDF do FAQ. Padrão: `app/data/FAQ_assessor_v1.1.pdf`. |

---

## Como rodar

O projeto é executado a partir do **VS Code**, abrindo `app/main.py` e usando
**Iniciar sem depuração** (`Ctrl+F5`). O diretório de trabalho é a **raiz do workspace**
(`Assessor/`), por isso os caminhos relativos (como o do PDF) partem da raiz.

Pré-requisitos:

- Python 3.14
- PostgreSQL acessível via `DATABASE_URL` (com as tabelas `transactions`, `transaction_types`
  e `categories`)
- MongoDB acessível via `MONGODB_URI`
- Um arquivo `.env` na raiz com as variáveis acima preenchidas

Frameworks principais: `langchain`, `langgraph`, `langchain-google-genai`, `langchain-groq`,
`langchain-community`, `psycopg2`, `pymongo`, `faiss`, `python-dotenv`.

Para encerrar a conversa no loop, digite: `sair`, `end`, `fim`, `tchau` ou `bye`.

---

## Segurança e compliance

- **PII** (CPF, CNPJ, telefone, e-mail, conta, cartão) é anonimizada na entrada e removida/omitida na saída.
- **Prompt injection** e tentativas de **acesso a dados internos** são bloqueadas por padrões determinísticos.
- **Classificação semântica** (ofensivo, perigoso, ilícito, político, indicação de investimento) é feita por LLM na entrada.
- **Compliance financeiro** (CVM/ANBIMA) é revisado por LLM na saída.
