"""Alexa CloudOps Copilor: Servidor MCP."""

from mcp.server.mcpserver import MCPServer

# 1. Crear el servidor - "instruccions" es lo primero que va a leer el cliente en este caso Alexa.

mcp = MCPServer (
    name="Alexa-CloudOps-copilot",
    instructions="Answers questions about the health of cloud services.",
)

# 2. Datos ficticios por el momento.
SERVICES = {
    "checkout-api": {"error_rate": 12.3, "baseline": 0.4, "last_deploy": "482"},
    "inventory-svc": {"error_rate": 0.3, "baseline": 0.5, "last_deploy": "1290"},
    "web-frontend": {"error_rate": 0.4, "baseline": 0.2, "last_deploy": "160"},
}

# 3. Tool - La descripcion es lo que Alexa lee para decidir si la usa.
@mcp.tool(description="Explain whether a cloud service is healthy or degraded and why.")
def diagnose_service(service: str) -> str:
    data = SERVICES.get(service)
    if data is None:
        return f"I couldn't find a service called {service}."
    if data["error_rate"] > data["baseline"] * 3:
        return (
	    f"{service} is degraded: error rate is about {data['error_rate']:.0f} percent "
	    f"versus {data['baseline']} normally, right after deployment {data['last_deploy']}."
)
    return f"{service} looks healthy."

# 4. Arranca en modo Streamable HTTP

if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8000, stateless_http=True)
