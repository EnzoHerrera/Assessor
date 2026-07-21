import os 
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings

load_dotenv()
PDF_PATH = os.getenv("FAQ_PDF_PATH", "app/FAQ_assessor_v1.1.pdf")

@tool("faq_retriever")
def faq_retriever(question: str) -> str:
    """Ferramenta para recuperar informações da FAQ a partir de um PDF. Recebe uma pergunta e retorna os trechos mais relevantes do documento."""
    loader = PyPDFLoader(PDF_PATH)
    docs = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=150)
    chunks = splitter.split_documents(docs)
    embeddings = GoogleGenerativeAIEmbeddings(
        model = "gemini-embedding-2-preview",
        google_api_key = os.getenv("GEMINI_API_KEY"),
    )
    db = FAISS.from_documents(chunks, embeddings)
    results = db.similarity_search(question, k=6)
    return "\n\n".join(trecho.page_content for trecho in results)