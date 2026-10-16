"""Pruebas: llaman a cada tool como lo haría Alexa, pero en memoria."""

import pytest
from mcp.client.client import Client

from server import mcp

pytestmark = pytest.mark.anyio

TICKET = {"title": "checkout-api degraded", "description": "Error rate 12 percent", "severity": 2}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def alexa():
    async with Client(mcp) as client:
        yield client


async def ask(client, tool, args=None):
    result = await client.call_tool(tool, args or {})
    return result.structured_content


async def test_hay_cuatro_tools(alexa):
    tools = (await alexa.list_tools()).tools
    assert {t.name for t in tools} == {
        "diagnose_service", "list_failed_deployments", "check_critical_cves", "create_incident_ticket"}


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


async def test_cves_criticas(alexa):
    answer = await ask(alexa, "check_critical_cves")
    assert "CVE 2021 44228" in answer["speech"]


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
