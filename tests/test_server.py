"""Pruebas: llaman a cada tool como lo haría Alexa, pero en memoria."""

import pytest
from mcp.client.client import Client

from server import mcp

pytestmark = pytest.mark.anyio

TICKET = {"title": "checkout-api degraded", "description": "Error rate 12 percent", "severity": 2}


@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture(autouse=True)
def fresh_graph(tmp_path, monkeypatch):
    """Cada prueba arranca con un grafo nuevo en una carpeta temporal: no toca knowledge/graph.json."""
    import server
    from knowledge import KnowledgeGraph
    monkeypatch.setattr(server, "GRAPH", KnowledgeGraph(tmp_path / "graph.json"))

@pytest.fixture
async def alexa():
    async with Client(mcp) as client:
        yield client


async def ask(client, tool, args=None):
    result = await client.call_tool(tool, args or {})
    return result.structured_content


async def test_hay_siete_tools(alexa):
    tools = (await alexa.list_tools()).tools
    assert {t.name for t in tools} == {"diagnose_service", "service_overview", "list_failed_deployments",
                                       "check_critical_cves", "create_incident_ticket", "recall", "remember"}


async def test_servicio_degradado(alexa):
    answer = await ask(alexa, "diagnose_service", {"service": "checkout-api"})
    assert "degraded" in answer["speech"] and "482" in answer["speech"]


async def test_servicio_sano(alexa):
    answer = await ask(alexa, "diagnose_service", {"service": "web-frontend"})
    assert "healthy" in answer["speech"]


async def test_servicio_que_no_existe(alexa):
    answer = await ask(alexa, "diagnose_service", {"service": "billing"})
    assert "couldn't find" in answer["speech"]


async def test_despliegues_fallidos(alexa):
    answer = await ask(alexa, "list_failed_deployments")
    assert answer["speech"].startswith("2 deployments failed")
    assert "checkout-api deployment 482" in answer["speech"] and "inventory-svc deployment 1290" in answer["speech"]


async def test_cves_criticas(alexa):
    answer = await ask(alexa, "check_critical_cves")
    assert "CVE 2021 44228" in answer["speech"] and "CVE 2024 3094" in answer["speech"]


async def test_ticket_pide_permiso_y_luego_crea(alexa):
    preview = await ask(alexa, "create_incident_ticket", TICKET)
    assert preview["details"]["status"] == "awaiting_confirmation"
    code = preview["details"]["confirmation_code"]

    done = await ask(alexa, "create_incident_ticket", {**TICKET, "confirmation_code": code})
    assert done["details"]["status"] == "created"

    again = await ask(alexa, "create_incident_ticket", {**TICKET, "confirmation_code": code})
    assert again["details"]["status"] == "rejected" # el código es de un solo uso


async def test_ticket_rechaza_codigo_inventado(alexa):
    answer = await ask(alexa, "create_incident_ticket", {**TICKET, "confirmation_code": "abc123"})
    assert answer["details"]["status"] == "rejected"


async def test_ticket_rechaza_cambios_despues_del_si(alexa):
    code = (await ask(alexa, "create_incident_ticket", TICKET))["details"]["confirmation_code"]
    tampered = {**TICKET, "severity": 1, "confirmation_code": code}
    assert (await ask(alexa, "create_incident_ticket", tampered))["details"]["status"] == "rejected"

# ---------------------------------------------------------------- CloudWatch, sin tocar AWS
from datetime import datetime, timedelta, timezone

import boto3
from moto import mock_aws

import server


@pytest.fixture
def fake_aws(monkeypatch):
    """Un CloudWatch de mentira en memoria: nada sale a internet ni cuesta dinero."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setattr(server, "DATA_SOURCE", "cloudwatch")
    with mock_aws():
        yield boto3.client("cloudwatch")


def put(cw, service, minutes_ago, requests, errors):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    dims = [{"Name": "Service", "Value": service}]
    cw.put_metric_data(Namespace=server.NAMESPACE, MetricData=[
        {"MetricName": "Requests", "Dimensions": dims, "Value": requests, "Timestamp": when},
        {"MetricName": "Errors", "Dimensions": dims, "Value": errors, "Timestamp": when},
        {"MetricName": "LatencyMs", "Dimensions": dims, "Value": 2000, "Timestamp": when},
    ])


async def test_cloudwatch_detecta_degradacion(fake_aws, alexa):
    put(fake_aws, "checkout-api", 30, requests=500, errors=2)     # la hora anterior: sana
    put(fake_aws, "checkout-api", 3, requests=500, errors=60)     # ahora: 12 % de errores
    answer = await ask(alexa, "diagnose_service", {"service": "checkout-api"})
    assert "degraded" in answer["speech"]
    assert answer["details"]["source"] == "cloudwatch"


async def test_cloudwatch_sin_trafico(fake_aws, alexa):
    answer = await ask(alexa, "diagnose_service", {"service": "checkout-api"})
    assert "couldn't find recent data" in answer["speech"]

async def test_entiende_nombres_cortos(alexa):
    answer = await ask(alexa, "diagnose_service", {"service": "checkout"})
    assert "checkout-api is degraded" in answer["speech"]


async def test_cloudwatch_falla_larga_supera_slo(fake_aws, alexa):
    put(fake_aws, "checkout-api", 30, requests=500, errors=60)    # la hora anterior también iba mal
    put(fake_aws, "checkout-api", 3, requests=500, errors=60)
    answer = await ask(alexa, "diagnose_service", {"service": "checkout-api"})
    assert "above the 5 percent objective" in answer["speech"]


async def test_resumen_nombra_los_degradados(alexa):
    answer = await ask(alexa, "service_overview")
    assert answer["speech"].startswith("1 of 3 services is degraded: checkout-api at about 12 percent errors.")
    assert "inventory-svc and web-frontend look healthy" in answer["speech"]
    assert answer["details"]["services"]["checkout-api"] == "degraded"


async def test_resumen_con_cloudwatch_sin_datos(fake_aws, alexa):
    put(fake_aws, "checkout-api", 3, requests=500, errors=60)
    answer = await ask(alexa, "service_overview")
    assert "checkout-api" in answer["speech"]
    assert "No recent data for inventory-svc and web-frontend" in answer["speech"]

async def test_diagnostico_separa_causa_e_impacto(alexa):
    answer = await ask(alexa, "diagnose_service", {"service": "checkout-api"})
    assert "inventory-svc is healthy, so the cause is likely in checkout-api itself" in answer["speech"]
    assert "This also affects web-frontend" in answer["speech"]


async def test_aprende_de_cada_ticket(alexa):
    first = await ask(alexa, "create_incident_ticket", TICKET)
    done = await ask(alexa, "create_incident_ticket", {**TICKET, "confirmation_code": first["details"]["confirmation_code"]})
    bug = done["details"]["ticket_id"]
    assert f"ticket:{bug} CAUSED_BY deploy:482" in done["details"]["learned"]
    again = await ask(alexa, "diagnose_service", {"service": "checkout-api"})
    assert f"A similar incident was filed before: bug {bug}" in again["speech"]


async def test_recuerda_y_sobrevive_reinicios(alexa):
    import server
    from knowledge import KnowledgeGraph
    await ask(alexa, "remember", {"subject": "checkout", "fact": "A rollback to deployment 481 fixed it last time"})
    reborn = KnowledgeGraph(server.GRAPH.path)              # como si el servidor se reiniciara
    assert any(p.get("text") == "A rollback to deployment 481 fixed it last time." for p in reborn.nodes.values())
    answer = await ask(alexa, "recall", {"topic": "checkout-api"})
    assert "rollback to deployment 481" in answer["speech"]
