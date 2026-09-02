from langchain.tools import tool
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from app.config import FAQ_PDF_PATH, GEMINI_API_KEY

@tool("faq_retriever")
def faq_retriever(question: str) -> str:
    """Ferramenta para recuperar informações da FAQ a partir de um PDF. Recebe uma pergunta e retorna os trechos mais relevantes do documento."""
    loader = PyPDFLoader(FAQ_PDF_PATH)
    docs = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=150)
    chunks = splitter.split_documents(docs)
    embeddings = GoogleGenerativeAIEmbeddings(
        model = "gemini-embedding-2-preview",
        google_api_key = GEMINI_API_KEY,
    )
    db = FAISS.from_documents(chunks, embeddings)
    results = db.similarity_search(question, k=6)
    return "\n\n".join(trecho.page_content for trecho in results)