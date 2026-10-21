"""Garde-fou lecture seule, authentification, pagination, erreurs."""
from __future__ import annotations

import base64

import httpx
import pytest
import respx

from wordpress_mcp.client import ALLOWED_NAMESPACES, WordPressError, check_read_only, _namespace_of

from ._helpers import BASE, SITE_INFO, make_client


def test_namespace_of():
    assert _namespace_of("/") == ""
    assert _namespace_of("/wp/v2/pages") == "wp/v2"
    assert _namespace_of("wp/v2/pages/12/revisions") == "wp/v2"
    assert _namespace_of("/elementor/v1/form-submissions") == "elementor/v1"
    assert _namespace_of("/batch/v1") == "batch/v1"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_write_methods_refused(method):
    with pytest.raises(WordPressError) as e:
        check_read_only(method, "/wp/v2/pages")
    assert e.value.code == "lecture_seule"


@pytest.mark.parametrize("path", ["/batch/v1", "/ai1wm/v1/exports", "/elementor-one/v1/plugins",
                                  "/image-optimizer/v1/backups", "/hub-connector/v1/sync"])
def test_dangerous_namespaces_refused(path):
    with pytest.raises(WordPressError) as e:
        check_read_only("GET", path)
    assert e.value.code == "namespace_refuse"


def test_allowed_namespaces_are_read_only_targets():
    # Aucun espace de noms d'export/import/installation ne doit figurer dans la liste blanche.
    for forbidden in ("batch", "ai1wm", "elementor-one", "image-optimizer", "hub-connector", "wpmudev"):
        assert not any(forbidden in ns for ns in ALLOWED_NAMESPACES)


@respx.mock
async def test_request_refuses_write_before_any_http():
    route = respx.post(f"{BASE}/wp-json/wp/v2/pages").mock(return_value=httpx.Response(201, json={}))
    client = make_client()
    with pytest.raises(WordPressError):
        await client.request("POST", "/wp/v2/pages")
    assert not route.called


@respx.mock
async def test_basic_auth_header_sent():
    route = respx.get(f"{BASE}/wp-json/").mock(return_value=httpx.Response(200, json=SITE_INFO))
    client = make_client()
    await client.site_info()
    auth = route.calls.last.request.headers["Authorization"]
    assert auth.startswith("Basic ")
    user, pw = base64.b64decode(auth[6:]).decode().split(":", 1)
    assert user == "claude-test"
    assert pw == "abcdefghijkl"  # espaces retirés


@respx.mock
async def test_anonymous_has_no_auth_header():
    route = respx.get(f"{BASE}/wp-json/").mock(return_value=httpx.Response(200, json=SITE_INFO))
    client = make_client(authenticated=False)
    await client.site_info()
    assert "Authorization" not in route.calls.last.request.headers
    assert client.context == "view"


@respx.mock
async def test_get_all_follows_pagination_and_limit():
    def handler(request):
        page = int(request.url.params.get("page", "1"))
        per_page = int(request.url.params["per_page"])
        assert per_page == 100
        start = (page - 1) * per_page
        items = [{"id": i} for i in range(start, min(start + per_page, 250))]
        return httpx.Response(200, json=items, headers={"X-WP-TotalPages": "3", "X-WP-Total": "250"})

    respx.get(f"{BASE}/wp-json/wp/v2/pages").mock(side_effect=handler)
    client = make_client(per_page_max=500)
    items = await client.get_all("/wp/v2/pages", {"status": "publish"})
    assert [i["id"] for i in items] == list(range(250))
    items = await client.get_all("/wp/v2/pages", {}, limit=120)
    assert len(items) == 120


@respx.mock
async def test_get_all_capped_by_settings():
    respx.get(f"{BASE}/wp-json/wp/v2/posts").mock(
        return_value=httpx.Response(200, json=[{"id": i} for i in range(100)], headers={"X-WP-TotalPages": "9"}))
    client = make_client(per_page_max=150)
    items = await client.get_all("/wp/v2/posts", {}, limit=10_000)
    assert len(items) == 150


@respx.mock
async def test_params_cleaned():
    route = respx.get(f"{BASE}/wp-json/wp/v2/posts").mock(return_value=httpx.Response(200, json=[]))
    await make_client().get("/wp/v2/posts", {"search": None, "status": "", "categories": [], "page": 2})
    assert dict(route.calls.last.request.url.params) == {"page": "2"}


@respx.mock
async def test_error_401_message():
    respx.get(f"{BASE}/wp-json/wp/v2/users/me").mock(
        return_value=httpx.Response(401, json={"code": "rest_not_logged_in", "message": "Vous n'êtes pas connecté."}))
    with pytest.raises(WordPressError) as e:
        await make_client().me()
    assert e.value.status == 401 and e.value.code == "rest_not_logged_in"
    assert "WP_APP_PASSWORD" in str(e.value)


@respx.mock
async def test_error_non_json():
    respx.get(f"{BASE}/wp-json/wp/v2/pages/1").mock(return_value=httpx.Response(503, text="<html>maintenance</html>"))
    with pytest.raises(WordPressError) as e:
        await make_client().get("/wp/v2/pages/1")
    assert e.value.status == 503 and "503" in str(e.value)


def test_missing_base_url_refused():
    from wordpress_mcp.client import WordPressClient
    from ._helpers import make_settings
    import dataclasses
    with pytest.raises(WordPressError) as e:
        WordPressClient(dataclasses.replace(make_settings(), base_url=""))
    assert e.value.code == "config"
