"""Alexa simulada en la terminal: un agente de Strands que usa tu servidor MCP.

    python alexa_sim/agent.py                       # servidor en http://127.0.0.1:8000/mcp
    MCP_URL=https://.../mcp python alexa_sim/agent.py

Escribe preguntas como "Why is checkout degraded?". Escribe "exit" para salir.
"""

import logging
import os
from pathlib import Path

from strands import Agent
from strands.tools.mcp import MCPClient

# Los modelos de razonamiento (gpt-oss en Groq) hacen que Strands avise en cada turno; es inofensivo.
logging.getLogger("strands.models.openai").setLevel(logging.ERROR)

MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "groq")          # "groq", "bedrock" u "ollama"


def build_model():
    """El 'cerebro' de la Alexa: Amazon Bedrock en la nube u Ollama en tu computadora."""
    if MODEL_PROVIDER == "groq":
        from strands.models.openai import OpenAIModel        # Groq habla el mismo "idioma" que la API de OpenAI
        return OpenAIModel(
            client_args={"api_key": os.environ["GROQ_API_KEY"], "base_url": "https://api.groq.com/openai/v1"},
            model_id=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            params={"temperature": 0.2},
            stream=False,                        # en streaming, gpt-oss en Groq a veces repite la respuesta
        )
    from strands.models import BedrockModel
    return BedrockModel(
        model_id=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )

# El mismo prompt lo usa voice-mcp-sim (SYSTEM_PROMPT_FILE=alexa_sim/prompt.txt).
SYSTEM_PROMPT = (Path(__file__).parent / "prompt.txt").read_text()


def main() -> None:
    cloudops = MCPClient(url=MCP_URL)          # cliente MCP por Streamable HTTP, como Alexa+
    with cloudops:
        tools = cloudops.list_tools_sync()
        print(f"Conectado a {MCP_URL}: {', '.join(t.tool_name for t in tools)}")
        print(f"Modelo: {MODEL_PROVIDER}\n")
        alexa = Agent(
            model=build_model(),
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            callback_handler=None,             # sin imprimir pasos internos; solo la respuesta
        )
        while True:
            question = input("Tú: ").strip()
            if question.lower() in {"exit", "salir", "quit"}:
                break
            if not question:
                continue
            reply = alexa(question)
            print(f"Alexa: {reply}\n")


if __name__ == "__main__":
    main()
