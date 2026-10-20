"""Alexa simulada: un agente de Amazon Bedrock (Strands) que usa tu servidor MCP.

    python alexa_sim/agent.py                       # servidor en http://127.0.0.1:8000/mcp
    MCP_URL=https://.../mcp python alexa_sim/agent.py

Escribe preguntas como "Why is checkout degraded?". Escribe "exit" para salir.
"""

import os

from strands import Agent
from strands.tools.mcp import MCPClient

MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "groq")          # "bedrock" u "ollama"


def build_model():
    """El 'cerebro' de la Alexa: Amazon Bedrock en la nube u Ollama en tu computadora."""
    if MODEL_PROVIDER == "groq":
        from strands.models.openai import OpenAIModel        # Groq habla el mismo "idioma" que la API de OpenAI
        return OpenAIModel(
            client_args={"api_key": os.environ["GROQ_API_KEY"], "base_url": "https://api.groq.com/openai/v1"},
            model_id=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            params={"temperature": 0.2},
        )
    from strands.models import BedrockModel
    return BedrockModel(
        model_id=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )

SYSTEM_PROMPT = """You are Alexa, a voice assistant helping an on-call cloud engineer.
Use the CloudOps tools to answer. When a tool returns a 'speech' field, say it almost word
for word: short, plain English, no lists, no markdown.
Creating a ticket is a write action. First call create_incident_ticket WITHOUT a
confirmation_code, read the preview to the user and ask for confirmation. Only call it again
with the confirmation_code if the user's latest message clearly says yes. If they say no,
do not create anything. Never invent a confirmation code."""


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
