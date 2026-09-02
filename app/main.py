from fastapi import FastAPI
from app.routes.chat import router as router_chat
from fastapi.middleware.cors import CORSMiddleware
from app.config import FRONTEND_DIR
from fastapi.staticfiles import StaticFiles
from app.config import validar_config          # junto dos outros imports
from app.routes import chat, sessions

for _problema in validar_config():                 # logo antes de app = FastAPI(...)
    print(f"[config] ATENÇÃO: {_problema}")

app = FastAPI(
    title="Assessor IA",
    description="Assesor financeiro e de agenda com LangChain e LangGraph",
    version="0.1.0",
)

@app.get("/health")
def health() -> dict:
    """Responde 'ok' se o servidor subiu"""
    problemas = validar_config()
    return {
        "status": "ok" if not problemas else "atencao",
        "problemas_de_configuracao": problemas,
    }

app.include_router(router_chat)
app.include_router(chat.router)
app.include_router(sessions.router)    

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    def raiz() -> dict:
        return {
            "status": "ok",
            "mensagem": "Acessor IA está rodando, mas o frontend não foi encontrado. Por favor, verifique a instalação do frontend.",
        }
