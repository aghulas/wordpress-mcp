"""Dates MEC : lecture du JSON-LD de la page publique quand l'API REST ne les expose pas."""
from __future__ import annotations

import httpx
import pytest
import respx

from wordpress_mcp import server as S
from wordpress_mcp.mec import parse_event_jsonld

from ._helpers import BASE, make_client

LD = """<html><script type="application/ld+json">
{"@context":"http://schema.org","@type":"Event","startDate":"2099-07-05","endDate":"2099-07-06",
 "location":{"@type":"Place","name":"Cour","address":"1 rue Test"}}</script></html>"""


def _event(i, title, link):
    return {"id": i, "type": "mec-events", "title": {"rendered": title}, "slug": title.lower(), "status": "publish",
            "link": link, "date": "2026-01-01T00:00:00", "modified": "2026-01-01T00:00:00",
            "excerpt": {"rendered": ""}, "meta": None}


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(S, "_client", make_client())


def test_parse_jsonld():
    assert parse_event_jsonld(LD) == {"debut": "2099-07-05", "fin": "2099-07-06", "lieu": "Cour", "adresse": "1 rue Test"}
    assert parse_event_jsonld("<html>rien</html>") == {}
    assert parse_event_jsonld('<script type="application/ld+json">{pas du json</script>') == {}
    assert parse_event_jsonld('<script type="application/ld+json">[{"@type":"Organization"},{"@type":"Event","startDate":"2099-01-01"}]</script>') == {"debut": "2099-01-01"}


@respx.mock
async def test_evenements_dates_from_public_page():
    respx.get(f"{BASE}/wp-json/wp/v2/mec-events").mock(return_value=httpx.Response(
        200, json=[_event(1, "Fete", f"{BASE}/events/fete"), _event(2, "Vieux", f"{BASE}/events/vieux")],
        headers={"X-WP-TotalPages": "1"}))
    page = respx.get(f"{BASE}/events/fete").mock(return_value=httpx.Response(200, text=LD))
    respx.get(f"{BASE}/events/vieux").mock(return_value=httpx.Response(
        200, text=LD.replace("2099-07-05", "2020-01-01").replace("2099-07-06", "2020-01-02")))
    out = await S.wp_evenements()
    assert [e["titre"] for e in out] == ["Fete"] and out[0]["debut"] == "2099-07-05" and out[0]["lieu"] == "Cour"
    assert "X-Claude-Auth" not in page.calls.last.request.headers and "Authorization" not in page.calls.last.request.headers
    assert len(await S.wp_evenements(a_venir=False)) == 2


@respx.mock
async def test_evenements_page_failure_does_not_break_list():
    respx.get(f"{BASE}/wp-json/wp/v2/mec-events").mock(return_value=httpx.Response(
        200, json=[_event(1, "Fete", f"{BASE}/events/fete")], headers={"X-WP-TotalPages": "1"}))
    respx.get(f"{BASE}/events/fete").mock(return_value=httpx.Response(500))
    out = await S.wp_evenements()
    assert len(out) == 1 and out[0]["debut"] is None


@respx.mock
async def test_public_page_other_domain_refused():
    link = "https://evil.example/events/x"
    respx.get(f"{BASE}/wp-json/wp/v2/mec-events").mock(return_value=httpx.Response(
        200, json=[_event(1, "Fete", link)], headers={"X-WP-TotalPages": "1"}))
    evil = respx.get(link).mock(return_value=httpx.Response(200, text=LD))
    out = await S.wp_evenements()
    assert not evil.called and out[0]["debut"] is None


@respx.mock
async def test_public_page_www_variant_accepted():
    c = make_client()
    c.settings = c.settings.__class__(**{**c.settings.__dict__, "base_url": "https://www.wp.test"})
    respx.get("https://wp.test/events/x").mock(return_value=httpx.Response(200, text="ok"))
    assert await c.get_public_page("https://wp.test/events/x") == "ok"
