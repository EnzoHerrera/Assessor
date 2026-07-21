from dotenv import load_dotenv
from google import genai
import os

load_dotenv()

client = genai.Client(api_key = os.getenv("GEMINI_API_KEY"))

try:
    response = client.models.generate_content(
        model = "gemini-3-flash-preview",
        contents = "Se cinco roupas levam 5 horas para secar, quanto tempo leva para 50 roupas secarem?",
        config = genai.types.GenerateContentConfig(
            system_instruction = "Você é um assessor que responde analisando os dados fornecidos e encontre padrões não explícitos, e de forma clara e objetiva.",
            temperature = 0.7,
            top_p = 0.95,
        )
    )
    print(response.text)

except Exception as e:
    print("Erro ao consumir a API: ", e)