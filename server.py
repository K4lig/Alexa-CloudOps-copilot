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
from knowledge import KnowledgeGraph, ago

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
GRAPH = KnowledgeGraph()
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

ERROR_SLO = 5.0          # objetivo de servicio: más de 5 % de errores es inaceptable, sea "normal" o no


def resolve_service(name: str) -> str:
    """Convierte lo que dice la gente ("checkout") en el nombre real ("checkout-api")."""
    name = name.strip().lower()
    if name in SERVICES:
        return name
    for real in SERVICES:
        if real.startswith(name) or name in real:
            return real
    return name


def assess(service: str) -> dict | None:
    """Revisa un servicio y decide: sano o degradado, con la frase para decir."""
    data = cloudwatch_health(service) if DATA_SOURCE == "cloudwatch" else SERVICES.get(service)
    if data is None:
        return None
    jumped = data["error_rate"] > max(2.0, data["baseline"] * 3)     # cambió mucho vs. lo normal
    over_slo = data["error_rate"] >= ERROR_SLO                         # supera el objetivo, aunque sea "lo normal"
    if jumped:
        speech = (f"{service} is degraded: error rate is about {data['error_rate']:.0f} percent "
                  f"versus {data['baseline']} normally, right after deployment {data['last_deploy']}.")
    elif over_slo:
        speech = (f"{service} is degraded: error rate is about {data['error_rate']:.0f} percent, "
                  f"above the {ERROR_SLO:.0f} percent objective. It has been this bad for over an hour, "
                  f"starting around deployment {data['last_deploy']}.")
    else:
        return {"status": "healthy", "speech": f"{service} looks healthy.", **data}
    return {"status": "degraded", "speech": speech, **data}


def spoken_list(items: list[str]) -> str:
    """["a", "b", "c"] -> "a, b, and c": así se enumera en voz alta."""
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth"]


def spoken_steps(items: list[str]) -> str:
    """Elementos largos, uno por frase: "First, ... Second, ..." se entiende mejor al oído."""
    if len(items) == 1:
        return items[0][0].upper() + items[0][1:] + "."
    return " ".join(f"{ORDINALS[i] if i < len(ORDINALS) else 'Also'}, {item}." for i, item in enumerate(items))


@mcp.tool(
    description=(
        "Explain whether ONE cloud service is healthy or degraded and why. "
        "Known services: checkout-api, inventory-svc, web-frontend."
    ),
    annotations=READ_ONLY,
)
def diagnose_service(service: str) -> Answer:
    service = resolve_service(service)
    result = assess(service)
    if result is None:
        return Answer(speech=f"I couldn't find recent data for a service called {service}.",
                      details={"known_services": list(SERVICES)})
    speech = result.pop("speech")
    if result["status"] == "degraded":
        context, facts = graph_context(service)
        speech = f"{speech} {context}".strip()
        result["knowledge"] = facts
    return Answer(speech=speech, details=result)

def graph_context(service: str) -> tuple[str, dict]:
    """Lo que el grafo agrega a un diagnóstico: ¿la causa viene de una dependencia? ¿a quién afecta?"""
    deps = [d for d in GRAPH.out(service, "DEPENDS_ON") if d in SERVICES]
    bad_deps = [d for d in deps if (assess(d) or {}).get("status") == "degraded"]
    impacted = GRAPH.dependents(service)
    past = [t for t in GRAPH.into(service, "ABOUT") if t.startswith("ticket:")]
    parts = []
    if bad_deps:
        parts.append(f"Its dependency {spoken_list(bad_deps)} is also degraded and may be the root cause.")
    elif deps:
        parts.append(f"Its dependency {spoken_list(deps)} is healthy, so the cause is likely in {service} itself.")
    if impacted:
        parts.append(f"This also affects {spoken_list(impacted)}.")
    if past:
        last = GRAPH.nodes[past[-1]]
        parts.append(f"A similar incident was filed before: bug {past[-1].split(':')[1]}, {ago(last.get('created', time.time()))}.")
    notes = [GRAPH.nodes[n]["text"] for n in GRAPH.into(service, "ABOUT") if n.startswith("note:")]
    if notes:
        parts.append(f"Team note: {notes[-1]}")
    facts = {"depends_on": deps, "degraded_dependencies": bad_deps, "impacted": impacted,
             "past_tickets": past, "notes": notes}
    return " ".join(parts), facts

@mcp.tool(
    description=(
        "Check ALL cloud services at once. Use for 'list the services', 'which services are degraded?', "
        "'how is everything?' or any overview question."
    ),
    annotations=READ_ONLY,
)
def service_overview() -> Answer:
    results = {name: assess(name) for name in SERVICES}
    degraded = [n for n, r in results.items() if r and r["status"] == "degraded"]
    healthy = [n for n, r in results.items() if r and r["status"] == "healthy"]
    no_data = [n for n, r in results.items() if r is None]

    if degraded:
        parts = [f"{n} at about {results[n]['error_rate']:.0f} percent errors" for n in degraded]
        speech = f"{len(degraded)} of {len(SERVICES)} services {'is' if len(degraded) == 1 else 'are'} degraded: {spoken_list(parts)}."
        if healthy:
            speech += f" {spoken_list(healthy)} {'looks' if len(healthy) == 1 else 'look'} healthy."
    elif healthy:
        speech = f"All {len(healthy)} services with data look healthy: {spoken_list(healthy)}."
    else:
        speech = "I have no recent data for any service."
    if no_data and (degraded or healthy):
        speech += f" No recent data for {spoken_list(no_data)}."
    details = {n: (r["status"] if r else "no_data") for n, r in results.items()}
    return Answer(speech=speech, details={"services": details})


@mcp.tool(
    description="List deployments that failed recently. Use for questions like 'what failed today?'.",
    annotations=READ_ONLY,
)
def list_failed_deployments(since_hours: int = 24) -> Answer:
    failed = [d for d in FAILED_DEPLOYMENTS if d["hours_ago"] <= since_hours]
    if not failed:
        return Answer(speech=f"No deployments failed in the last {since_hours} hours.")
    items = [f"{d['service']} deployment {d['id']}, {d['hours_ago']} hours ago: {d['reason']}" for d in failed]
    noun = "deployment" if len(failed) == 1 else "deployments"
    speech = f"{len(failed)} {noun} failed in the last {since_hours} hours. {spoken_steps(items)}"
    return Answer(speech=speech, details={"deployments": failed})


@mcp.tool(
    description="Check whether critical vulnerabilities (CVSS 9 or higher) affect production.",
    annotations=READ_ONLY,
)
def check_critical_cves(min_cvss: float = 9.0) -> Answer:
    found = [c for c in CRITICAL_CVES if c["cvss"] >= min_cvss]
    if not found:
        return Answer(speech="Good news: no critical vulnerabilities affect production.")
    items = [f"{c['id'].replace('-', ' ')} in {c['component']}, used by {c['service']}" for c in found]
    noun = "vulnerability affects" if len(found) == 1 else "vulnerabilities affect"
    speech = f"Yes, {len(found)} critical {noun} production. {spoken_steps(items)}"
    return Answer(speech=speech, details={"vulnerabilities": found})

PENDING: dict[str, tuple[str, float]] = {}   # código -> (huella del ticket, hora de vencimiento)
CODE_TTL_SECONDS = 300
_next_ticket = max([4710] + [int(n.split(":")[1]) for n in GRAPH.nodes if n.startswith("ticket:")]) + 1


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
    learned = learn_from_ticket(ticket_id, title, description, severity)
    return Answer(speech=f"Done. Bug {ticket_id} is created with severity {severity}.",
                  details={"status": "created", "ticket_id": ticket_id, "learned": learned})


def learn_from_ticket(ticket_id: int, title: str, description: str, severity: int) -> list[str]:
    """Cada ticket queda en el grafo, unido al servicio y al despliegue que lo causó."""
    node = f"ticket:{ticket_id}"
    GRAPH.add_node(node, type="ticket", title=title, severity=severity, created=time.time())
    text = f"{title} {description}"
    learned = []
    for service in GRAPH.mentioned_services(text) or [resolve_service(w) for w in text.split() if resolve_service(w) in SERVICES][:1]:
        GRAPH.add_edge(node, "ABOUT", service)
        learned.append(f"{node} ABOUT {service}")
        for deploy in GRAPH.into(service, "DEPLOYED_TO"):
            if deploy.split(":")[1] in text or (assess(service) or {}).get("last_deploy") == deploy.split(":")[1]:
                GRAPH.add_edge(node, "CAUSED_BY", deploy)
                learned.append(f"{node} CAUSED_BY {deploy}")
    return learned

@mcp.tool(
    description=(
        "Recall what the team already knows about a service, deployment, ticket or CVE: owner team, "
        "dependencies, what it affects, past incidents and saved notes. Use for 'what do we know about X?', "
        "'what depends on X?', 'what happened last time?'."
    ),
    annotations=READ_ONLY,
)
def recall(topic: str) -> Answer:
    node = GRAPH.find(topic)
    if node is None:
        return Answer(speech=f"I don't know anything about {topic} yet.", details={"found": False})
    props = GRAPH.nodes[node]
    kind = props.get("type")
    notes = [GRAPH.nodes[n]["text"] for n in GRAPH.into(node, "ABOUT") if n.startswith("note:")]
    tickets = [n for n in GRAPH.into(node, "ABOUT") + GRAPH.into(node, "CAUSED_BY") if n.startswith("ticket:")]
    parts = []
    if kind == "service":
        parts.append(f"{node} is owned by the {props.get('team', 'unknown').replace('-', ' ')}.")
        if deps := GRAPH.out(node, "DEPENDS_ON"):
            parts.append(f"It depends on {spoken_list(deps)}.")
        if users := GRAPH.dependents(node):
            parts.append(f"If it fails, {spoken_list(users)} {'is' if len(users) == 1 else 'are'} affected.")
        if cves := GRAPH.into(node, "AFFECTS"):
            parts.append(f"It is exposed to {spoken_list([c.replace('-', ' ') for c in cves])}.")
    elif kind == "deployment":
        parts.append(f"Deployment {node.split(':')[1]} went to {spoken_list(GRAPH.out(node, 'DEPLOYED_TO'))}: {props.get('reason', 'no details')}.")
    elif kind == "ticket":
        about = GRAPH.out(node, "ABOUT")
        cause = GRAPH.out(node, "CAUSED_BY")
        parts.append(f"Bug {node.split(':')[1]}, {props.get('title')}, was filed {ago(props.get('created', time.time()))}"
                     + (f" about {spoken_list(about)}" if about else "")
                     + (f", caused by deployment {cause[0].split(':')[1]}" if cause else "") + ".")
    elif kind == "cve":
        parts.append(f"{node.replace('-', ' ')} has CVSS {props.get('cvss')} in {props.get('component')} "
                     f"and affects {spoken_list(GRAPH.out(node, 'AFFECTS'))}.")
    if tickets and kind != "ticket":
        parts.append(f"Past incidents: {spoken_list(['bug ' + t.split(':')[1] for t in tickets[-3:]])}.")
    if notes:
        parts.append(f"Team notes: {' '.join(notes[-3:])}")
    if not parts:
        parts.append(f"I only know that {node} exists.")
    return Answer(speech=" ".join(parts), details={"node": node, **props, "notes": notes, "tickets": tickets})


@mcp.tool(
    description=(
        "Save a fact the engineer explicitly asks you to remember, linked to a service, deployment or ticket "
        "(e.g. 'remember that a rollback fixed checkout last time'). Only call it when the user asks to remember something."
    ),
    annotations=WRITE,
)
def remember(subject: str, fact: str) -> Answer:
    node = GRAPH.find(subject) or resolve_service(subject)
    note = f"note:{len([n for n in GRAPH.nodes if n.startswith('note:')]) + 1}"
    text = fact.strip().rstrip(".") + "."
    GRAPH.add_node(note, type="note", text=text, created=time.time())
    GRAPH.add_edge(note, "ABOUT", node)
    return Answer(speech=f"Got it. I'll remember that about {node}.", details={"note": note, "about": node, "text": text})

# 4. Arranca en modo Streamable HTTP

if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8000, stateless_http=True)
