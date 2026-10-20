"""Revisa cada pieza de la Alexa por separado, con tiempos y errores visibles.

    python alexa_sim/check.py                    # usa MODEL_PROVIDER (bedrock por defecto)
    MODEL_PROVIDER=ollama python alexa_sim/check.py
"""

import threading
import time
import traceback

import agent  # reutiliza la configuración de agent.py
from strands import Agent
from strands.tools.mcp import MCPClient

TIMEOUT = 120  # segundos máximos por prueba


def run_with_timeout(label, fn):
    result, error = {}, {}

    def target():
        try:
            result["value"] = fn()
        except Exception as exc:  # mostramos cualquier error en lugar de tragarlo
            error["exc"] = exc
            error["tb"] = traceback.format_exc(limit=3)

    start = time.time()
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(TIMEOUT)
    seconds = time.time() - start
    if worker.is_alive():
        print(f"[TIEMPO AGOTADO] {label}: sin respuesta tras {TIMEOUT}s")
        return None
    if error:
        print(f"[ERROR] {label} ({seconds:.1f}s): {type(error['exc']).__name__}: {error['exc']}")
        return None
    print(f"[OK] {label} ({seconds:.1f}s): {str(result['value'])[:200]}")
    return result["value"]


def main():
    print(f"Servidor MCP: {agent.MCP_URL} | Modelo: {agent.MODEL_PROVIDER}\n")

    # 1. ¿Responde tu servidor MCP?
    client = MCPClient(url=agent.MCP_URL)
    with client:
        tools = run_with_timeout("1. Listar herramientas", client.list_tools_sync)
        run_with_timeout("2. Llamar diagnose_service directo",
                         lambda: client.call_tool_sync("t1", "diagnose_service", {"service": "checkout-api"})["content"])

        # 3. ¿Responde el modelo, sin herramientas?
        run_with_timeout("3. Modelo solo (sin herramientas)",
                         lambda: Agent(model=agent.build_model(), callback_handler=None)("Say hello in five words."))

        # 4. ¿Modelo + herramientas juntos?
        if tools:
            run_with_timeout("4. Agente completo",
                             lambda: Agent(model=agent.build_model(), tools=tools,
                                           system_prompt=agent.SYSTEM_PROMPT,
                                           callback_handler=None)("Why is checkout degraded?"))


if __name__ == "__main__":
    main()
