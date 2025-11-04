from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Any
import json
import re
from dotenv import load_dotenv
import os
from fastapi.middleware.cors import CORSMiddleware
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

app = FastAPI(title="Simple Gemini Interview API", version="1.0")


origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8080",   
    "http://127.0.0.1:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,      
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class GenerateQuestionRequest(BaseModel):
    context: Optional[str] = None


class GenerateQuestionResponse(BaseModel):
    question: str


class EvaluateAnswerRequest(BaseModel):
    question: str
    answer: str


class EvaluateAnswerResponse(BaseModel):
    score: int
    improvements: List[str]
    improvedAnswer: str


def get_llm(temperature: float = 0.6, max_tokens: int = 300):
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY no está configurada")
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        temperature=temperature,
        max_output_tokens=max_tokens,
        google_api_key=api_key,
    )


def parse_json_from_text(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = text[first : last + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass
    return None


def extract_text_from_llm_response(resp: Any) -> str:
    """
    Extrae el texto de respuesta del objeto que devuelva el LLM.
    Maneja varios casos comunes: str, objeto con .content, .message, .generations.
    Devuelve string vacío si no puede extraer texto.
    """
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    # objeto con atributo 'content'
    if hasattr(resp, "content"):
        try:
            return str(resp.content)
        except Exception:
            pass
    # algunos bindings devuelven .message.content
    if hasattr(resp, "message") and hasattr(resp.message, "content"):
        try:
            return str(resp.message.content)
        except Exception:
            pass
    # LangChain style: .generations -> list(list(Generation)) with .text
    if hasattr(resp, "generations"):
        try:
            gens = resp.generations
            if (
                isinstance(gens, list)
                and gens
                and isinstance(gens[0], list)
                and gens[0]
            ):
                gen0 = gens[0][0]
                if hasattr(gen0, "text"):
                    return str(gen0.text)
            # fallback: try first element string
            if isinstance(gens[0][0], str):
                return str(gens[0][0])
        except Exception:
            pass
    # fallback: str()
    try:
        return str(resp)
    except Exception:
        return ""


@app.post("/question/generate", response_model=GenerateQuestionResponse)
def generate_question(
    req: GenerateQuestionRequest,
    temperature: Optional[float] = Query(0.6),
    max_tokens: Optional[int] = Query(150),
):
    system = SystemMessage(
        content=(
            "Eres un generador de preguntas de entrevista en español. "
            "Genera UNA pregunta simple, clara y directa sobre el tema proporcionado. "
            "Devuelve SOLO un JSON con la clave 'question'."
        )
    )
    human = HumanMessage(
        content=f"Tema: {req.context or 'general'}\n\nResponde con: {{\"question\": \"...\"}}"
    )

    try:
        llm = get_llm(temperature=temperature, max_tokens=max_tokens)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        resp = llm.invoke([system, human])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error llamando al LLM: {e}")

    text = extract_text_from_llm_response(resp)
    parsed = parse_json_from_text(text)
    if not parsed or "question" not in parsed:
        raise HTTPException(
            status_code=502, detail="LLM no devolvió JSON válido para la pregunta."
        )
    question = str(parsed["question"]).strip()
    return {"question": question}


@app.post("/answer/evaluate", response_model=EvaluateAnswerResponse)
def evaluate_answer(
    req: EvaluateAnswerRequest,
    temperature: Optional[float] = Query(0.2),
    max_tokens: Optional[int] = Query(400),
):
    if not req.question or not req.answer:
        raise HTTPException(status_code=400, detail="question y answer son requeridos.")

    system = SystemMessage(
        content=(
            "Eres un evaluador de respuestas de entrevistas en español. "
            "Recibirás una pregunta y una respuesta. Devuelve SOLO un JSON con las claves: "
            "'score' (entero 0-100), 'improvements' (lista de strings), 'improvedAnswer' (string)."
        )
    )
    human = HumanMessage(
        content=f"Pregunta: {req.question}\n\nRespuesta: {req.answer}\n\nResponde con JSON."
    )

    try:
        llm = get_llm(temperature=temperature, max_tokens=max_tokens)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        resp = llm.invoke([system, human])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error llamando al LLM: {e}")

    text = extract_text_from_llm_response(resp)
    parsed = parse_json_from_text(text)
    if not parsed or not {"score", "improvements", "improvedAnswer"}.issubset(
        set(parsed.keys())
    ):
        raise HTTPException(
            status_code=502, detail="LLM no devolvió JSON válido para la evaluación."
        )

    try:
        score = int(parsed["score"])
    except Exception:
        m = re.search(r"\d{1,3}", str(parsed.get("score", "")))
        score = int(m.group(0)) if m else 0

    improvements = (
        parsed["improvements"]
        if isinstance(parsed["improvements"], list)
        else [str(parsed.get("improvements", ""))]
    )
    improved = str(parsed["improvedAnswer"])
    score = max(0, min(100, score))
    return {"score": score, "improvements": improvements, "improvedAnswer": improved}


@app.get("/health")
def health():
    return {"status": "ok"}
