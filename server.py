"""Alexa CloudOps Copilor: Servidor MCP."""

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import boto3

from pydantic import BaseModel, Field
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

# 1. Crear el servidor - "instruccions" es lo primero que va a leer el cliente en este caso Alexa.

mcp = MCPServer (
    name="Alexa-CloudOps-copilot",
    instructions="Answers questions about the health of cloud services.",
)

class Answer(BaseModel):
    """Lo que devuelve cada tool: una frase para decir y los datos de respaldo."""
    speech: str
    details: dict = Field(default_factory=dict)


READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)

# 2. Datos ficticios por el momento.
SERVICES = {
    "checkout-api": {"error_rate": 12.3, "baseline": 0.4, "last_deploy": "482"},
    "inventory-svc": {"error_rate": 0.3, "baseline": 0.5, "last_deploy": "1290"},
    "web-frontend": {"error_rate": 0.4, "baseline": 0.2, "last_deploy": "160"},
}

FAILED_DEPLOYMENTS = [
    {"id": "482", "service": "checkout-api", "hours_ago": 2, "reason": "health check failed after release"},
    {"id": "1290", "service": "inventory-svc", "hours_ago": 9, "reason": "3 unit tests failed"},
]

CRITICAL_CVES = [
    {"id": "CVE-2021-44228", "cvss": 10.0, "component": "log4j-core 2.14.1", "service": "legacy-reporting"},
    {"id": "CVE-2024-3094", "cvss": 10.0, "component": "xz-utils 5.6.0", "service": "build-agent-image"},
]

# 3. Tool - La descripcion es lo que Alexa lee para decidir si la usa.

# ---------------------------------------------------------------- datos reales (CloudWatch)
DATA_SOURCE = os.getenv("DATA_SOURCE", "demo")      # "demo" o "cloudwatch"
NAMESPACE = "CloudOpsCopilot/Demo"
DEFAULT_BASELINE = 0.5                               # si todavía no hay historial


def _sum_metric(cloudwatch, service: str, metric: str, stat: str, start: datetime, end: datetime) -> list[float]:
    """Pide a CloudWatch una métrica de un servicio en una ventana de tiempo."""
    period = int((end - start).total_seconds())
    response = cloudwatch.get_metric_data(
        MetricDataQueries=[{
            "Id": "m",
            "MetricStat": {
                "Metric": {"Namespace": NAMESPACE, "MetricName": metric,
                           "Dimensions": [{"Name": "Service", "Value": service}]},
                "Period": period,
                "Stat": stat,
            },
        }],
        StartTime=start,
        EndTime=end,
    )
    return response["MetricDataResults"][0]["Values"]


def cloudwatch_health(service: str) -> dict | None:
    """Errores y lentitud de los últimos 10 minutos, comparados con la hora anterior."""
    cloudwatch = boto3.client("cloudwatch")
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    recent_start, baseline_start = now - timedelta(minutes=10), now - timedelta(minutes=70)

    requests = sum(_sum_metric(cloudwatch, service, "Requests", "Sum", recent_start, now))
    if requests == 0:
        return None                                  # sin tráfico: no sabemos nada de ese servicio
    errors = sum(_sum_metric(cloudwatch, service, "Errors", "Sum", recent_start, now))
    latency = _sum_metric(cloudwatch, service, "LatencyMs", "Average", recent_start, now)

    old_requests = sum(_sum_metric(cloudwatch, service, "Requests", "Sum", baseline_start, recent_start))
    old_errors = sum(_sum_metric(cloudwatch, service, "Errors", "Sum", baseline_start, recent_start))
    baseline = round(100 * old_errors / old_requests, 1) if old_requests else DEFAULT_BASELINE

    last_deploy = next((d["id"] for d in FAILED_DEPLOYMENTS if d["service"] == service), "unknown")
    return {
        "error_rate": round(100 * errors / requests, 1),
        "baseline": max(baseline, 0.1),
        "latency_ms": round(latency[0]) if latency else None,
        "requests_10min": int(requests),
        "last_deploy": last_deploy,
        "source": "cloudwatch",
    }


@mcp.tool(
    description="Explain whether a cloud service is healthy or degraded and why.",
    annotations=READ_ONLY,
)
def diagnose_service(service: str) -> Answer:
    data = cloudwatch_health(service) if DATA_SOURCE == "cloudwatch" else SERVICES.get(service)
    if data is None:
        return Answer(speech=f"I couldn't find recent data for a service called {service}.",
                      details={"known_services": list(SERVICES)})
    if data["error_rate"] > data["baseline"] * 3:
        speech = (f"{service} is degraded: error rate is about {data['error_rate']:.0f} percent "
                  f"versus {data['baseline']} normally, right after deployment {data['last_deploy']}.")
        return Answer(speech=speech, details={"status": "degraded", **data})
    return Answer(speech=f"{service} looks healthy.", details={"status": "healthy", **data})

@mcp.tool(
    description="List deployments that failed recently. Use for questions like 'what failed today?'.",
    annotations=READ_ONLY,
)
def list_failed_deployments(since_hours: int = 24) -> Answer:
    failed = [d for d in FAILED_DEPLOYMENTS if d["hours_ago"] <= since_hours]
    if not failed:
        return Answer(speech=f"No deployments failed in the last {since_hours} hours.")
    first = failed[0]
    speech = (f"{len(failed)} deployments failed in the last {since_hours} hours. "
              f"The most recent is {first['service']}, deployment {first['id']}: {first['reason']}.")
    return Answer(speech=speech, details={"deployments": failed})


@mcp.tool(
    description="Check whether critical vulnerabilities (CVSS 9 or higher) affect production.",
    annotations=READ_ONLY,
)
def check_critical_cves(min_cvss: float = 9.0) -> Answer:
    found = [c for c in CRITICAL_CVES if c["cvss"] >= min_cvss]
    if not found:
        return Answer(speech="Good news: no critical vulnerabilities affect production.")
    worst = found[0]
    speech = (f"Yes, {len(found)} critical vulnerabilities affect production. The worst is "
              f"{worst['id'].replace('-', ' ')}, in {worst['component']}, used by {worst['service']}.")
    return Answer(speech=speech, details={"vulnerabilities": found})

PENDING: dict[str, tuple[str, float]] = {}   # código -> (huella del ticket, hora de vencimiento)
CODE_TTL_SECONDS = 300
_next_ticket = 4711


def _fingerprint(title: str, description: str, severity: int) -> str:
    """Huella SHA-256 del contenido exacto del ticket."""
    payload = json.dumps({"t": title, "d": description, "s": severity}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@mcp.tool(
    description=(
        "Create a bug ticket for an incident. TWO STEPS: call without confirmation_code to get "
        "a preview and a code; read the preview to the user and call again with the code only "
        "if they clearly say yes. Never invent a code."
    ),
    annotations=WRITE,
)
def create_incident_ticket(
    title: str,
    description: str,
    severity: int = 2,
    confirmation_code: str | None = None,
) -> Answer:
    global _next_ticket
    now = time.time()
    fingerprint = _fingerprint(title, description, severity)

    # Paso 1: sin código -> no crea nada, solo pide permiso
    if confirmation_code is None:
        code = secrets.token_hex(3)
        PENDING[code] = (fingerprint, now + CODE_TTL_SECONDS)
        return Answer(
            speech=f"I'm about to create a severity {severity} bug titled: {title}. Should I go ahead?",
            details={"status": "awaiting_confirmation", "confirmation_code": code},
        )

    # Paso 2: con código -> se verifica y se gasta (pop lo borra: un solo uso)
    pending = PENDING.pop(confirmation_code, None)
    if pending is None or pending[1] < now:
        return Answer(speech="That confirmation code is unknown or expired. Ask me again to start over.",
                      details={"status": "rejected"})
    if pending[0] != fingerprint:
        return Answer(speech="The ticket changed after you confirmed, so I did not create it.",
                      details={"status": "rejected"})

    ticket_id = _next_ticket
    _next_ticket += 1
    return Answer(speech=f"Done. Bug {ticket_id} is created with severity {severity}.",
                  details={"status": "created", "ticket_id": ticket_id})

# 4. Arranca en modo Streamable HTTP

if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8000, stateless_http=True)
