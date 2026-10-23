"""Grafo de conocimiento: la memoria operativa del copiloto.

El modelo no cambia por dentro; lo que "aprende" queda aquí, en un grafo de
nodos (servicios, despliegues, CVEs, equipos, tickets, notas) unidos por relaciones:

    web-frontend --DEPENDS_ON--> checkout-api --DEPENDS_ON--> inventory-svc
    deploy:482 --DEPLOYED_TO--> checkout-api
    ticket:4711 --ABOUT--> checkout-api ;  ticket:4711 --CAUSED_BY--> deploy:482

Se guarda en un JSON (KNOWLEDGE_FILE) para que sobreviva a reinicios y se pueda leer a mano.
Ahorra tokens porque una consulta devuelve unos pocos hechos ya resumidos, en vez de que el
modelo relea historial, logs o tableros.
"""

import json
import os
import threading
import time
from pathlib import Path

DEFAULT_FILE = Path(__file__).parent / "knowledge" / "graph.json"

# Lo que el copiloto sabe desde el primer día. Lo demás lo aprende trabajando.
SEED_NODES = {
    "checkout-api": {"type": "service", "team": "payments-team"},
    "inventory-svc": {"type": "service", "team": "catalog-team"},
    "web-frontend": {"type": "service", "team": "web-team"},
    "legacy-reporting": {"type": "service", "team": "data-team"},
    "build-agent-image": {"type": "service", "team": "platform-team"},
    "deploy:482": {"type": "deployment", "reason": "health check failed after release"},
    "deploy:1290": {"type": "deployment", "reason": "3 unit tests failed"},
    "CVE-2021-44228": {"type": "cve", "cvss": 10.0, "component": "log4j-core 2.14.1"},
    "CVE-2024-3094": {"type": "cve", "cvss": 10.0, "component": "xz-utils 5.6.0"},
}
SEED_EDGES = [
    ("web-frontend", "DEPENDS_ON", "checkout-api"),
    ("web-frontend", "DEPENDS_ON", "inventory-svc"),
    ("checkout-api", "DEPENDS_ON", "inventory-svc"),
    ("deploy:482", "DEPLOYED_TO", "checkout-api"),
    ("deploy:1290", "DEPLOYED_TO", "inventory-svc"),
    ("CVE-2021-44228", "AFFECTS", "legacy-reporting"),
    ("CVE-2024-3094", "AFFECTS", "build-agent-image"),
]


class KnowledgeGraph:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("KNOWLEDGE_FILE") or DEFAULT_FILE)
        self.lock = threading.Lock()
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.nodes, self.edges = data["nodes"], [tuple(e[:3]) + (e[3] if len(e) > 3 else {},) for e in data["edges"]]
        else:
            self.nodes = {k: dict(v) for k, v in SEED_NODES.items()}
            self.edges = [(s, r, d, {}) for s, r, d in SEED_EDGES]
            self.save()

    # ------------------------------------------------------------ escritura
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"nodes": self.nodes, "edges": [list(e) for e in self.edges]}, indent=1))
        tmp.replace(self.path)                      # escritura atómica: nunca queda un archivo a medias

    def add_node(self, node_id: str, **props) -> None:
        with self.lock:
            self.nodes.setdefault(node_id, {}).update(props)
            self.save()

    def add_edge(self, src: str, rel: str, dst: str, **props) -> None:
        with self.lock:
            for node in (src, dst):
                self.nodes.setdefault(node, {"type": "thing"})
            if not any(e[:3] == (src, rel, dst) for e in self.edges):
                self.edges.append((src, rel, dst, props))
            self.save()

    # ------------------------------------------------------------ lectura
    def out(self, node: str, rel: str) -> list[str]:
        return [d for s, r, d, _ in self.edges if s == node and r == rel]

    def into(self, node: str, rel: str) -> list[str]:
        return [s for s, r, d, _ in self.edges if d == node and r == rel]

    def dependents(self, service: str) -> list[str]:
        """Todo lo que se cae si este servicio se cae (dependencia transitiva, sin ciclos)."""
        seen, todo = [], [service]
        while todo:
            for parent in self.into(todo.pop(), "DEPENDS_ON"):
                if parent not in seen and parent != service:
                    seen.append(parent)
                    todo.append(parent)
        return seen

    def find(self, text: str) -> str | None:
        """Encuentra el nodo que la gente menciona: "checkout" -> "checkout-api", "4711" -> "ticket:4711"."""
        text = text.strip().lower()
        for node in self.nodes:
            if node.lower() == text or node.lower().split(":")[-1] == text:
                return node
        for node in self.nodes:
            if text and (node.lower().startswith(text) or text in node.lower()):
                return node
        return None

    def mentioned_services(self, text: str) -> list[str]:
        text = text.lower()
        return [n for n, p in self.nodes.items() if p.get("type") == "service" and n.lower() in text]


def ago(timestamp: float) -> str:
    minutes = int((time.time() - timestamp) / 60)
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    return f"{hours} hour{'s' if hours > 1 else ''} ago" if hours < 48 else f"{hours // 24} days ago"
