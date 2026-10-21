"""Mode d'authentification `cookie` : connexion wp-login.php + jeton REST (nonce)."""
from __future__ import annotations

import dataclasses
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from wordpress_mcp.client import WordPressClient, WordPressError
from wordpress_mcp.config import SETTINGS

from ._helpers import BASE, ME, make_client

PASSWORD = "S3cret pass!"
LOGGED_IN = {"Set-Cookie": "wordpress_logged_in_abc=val123; Path=/; HttpOnly", "Location": BASE + "/wp-admin/"}


def cookie_client(**overrides) -> WordPressClient:
    settings = dataclasses.replace(SETTINGS, base_url=BASE, user="ogec-test", auth_mode="cookie",
                                   password=PASSWORD, app_password=None, **overrides)
    return WordPressClient(settings, http=httpx.AsyncClient(base_url=BASE, timeout=5))


def mock_login(status=302, headers=None):
    return respx.post(f"{BASE}/wp-login.php").mock(return_value=httpx.Response(status, headers=headers or LOGGED_IN))


def mock_nonce(*values):
    seq = [httpx.Response(200, text=v) for v in values]
    return respx.get(f"{BASE}/wp-admin/admin-ajax.php").mock(side_effect=seq)


def test_authenticated_depends_on_mode():
    base = dataclasses.replace(SETTINGS, user="u", app_password=None, password=None)
    assert not dataclasses.replace(base, auth_mode="cookie", password=None).authenticated
    assert dataclasses.replace(base, auth_mode="cookie", password="p").authenticated
    # le mot de passe complet ne suffit pas en mode basic, ni l'inverse
    assert not dataclasses.replace(base, auth_mode="basic", password="p").authenticated
    assert not dataclasses.replace(base, auth_mode="cookie", app_password="abcd").authenticated
    assert dataclasses.replace(base, auth_mode="basic", app_password="abcd").authenticated


def test_secrets_hidden_from_repr():
    s = dataclasses.replace(SETTINGS, password=PASSWORD, app_password="APPSECRET")
    assert PASSWORD not in repr(s) and "APPSECRET" not in repr(s)


def test_unknown_mode_refused():
    settings = dataclasses.replace(SETTINGS, base_url=BASE, auth_mode="jwt")
    with pytest.raises(WordPressError) as e:
        WordPressClient(settings, http=httpx.AsyncClient(base_url=BASE))
    assert e.value.code == "config"


@respx.mock
async def test_login_flow_then_session_reused():
    login = mock_login()
    nonce = mock_nonce("abc123def4")
    me = respx.get(f"{BASE}/wp-json/wp/v2/users/me").mock(return_value=httpx.Response(200, json=ME))
    client = cookie_client()
    assert client.context == "edit"
    assert (await client.me())["id"] == 1

    form = parse_qs(login.calls.last.request.content.decode())
    assert form["log"] == ["ogec-test"] and form["pwd"] == [PASSWORD]
    assert nonce.calls.last.request.url.params["action"] == "rest-nonce"
    req = me.calls.last.request
    assert req.headers["X-WP-Nonce"] == "abc123def4"
    assert "wordpress_logged_in_abc=val123" in req.headers["cookie"]
    assert "Authorization" not in req.headers

    await client.me()  # session réutilisée : pas de nouvelle connexion
    assert login.call_count == 1 and nonce.call_count == 1


@respx.mock
async def test_relogin_once_when_nonce_expired():
    login = mock_login()
    nonce = mock_nonce("nonce0000001", "nonce0000002")
    expired = httpx.Response(403, json={"code": "rest_cookie_invalid_nonce", "message": "Jeton invalide"})
    route = respx.get(f"{BASE}/wp-json/wp/v2/pages").mock(
        side_effect=[expired, httpx.Response(200, json=[{"id": 1}], headers={"X-WP-TotalPages": "1"})])
    out = await cookie_client().get_all("/wp/v2/pages")
    assert out == [{"id": 1}]
    assert login.call_count == 2 and nonce.call_count == 2
    assert [c.request.headers["X-WP-Nonce"] for c in route.calls] == ["nonce0000001", "nonce0000002"]


@respx.mock
async def test_no_infinite_relogin_loop():
    login = mock_login()
    mock_nonce("nonce0000001", "nonce0000002", "nonce0000003")
    respx.get(f"{BASE}/wp-json/wp/v2/users/me").mock(
        return_value=httpx.Response(401, json={"code": "rest_not_logged_in", "message": "Pas connecté."}))
    with pytest.raises(WordPressError) as e:
        await cookie_client().me()
    assert e.value.status == 401 and "WP_PASSWORD" in str(e.value)
    assert login.call_count == 2  # connexion initiale + une seule reconnexion


@respx.mock
async def test_login_refused_without_logged_in_cookie():
    mock_login(status=200, headers={"Set-Cookie": "wordpress_test_cookie=WP+Cookie+check; Path=/"})
    mock_nonce("unused00000")
    with pytest.raises(WordPressError) as e:
        await cookie_client().me()
    assert e.value.code == "login_refuse"
    assert PASSWORD not in str(e.value) and "ogec-test" not in str(e.value)


@respx.mock
async def test_nonce_not_found():
    mock_login()
    respx.get(f"{BASE}/wp-admin/admin-ajax.php").mock(return_value=httpx.Response(200, text="0"))
    with pytest.raises(WordPressError) as e:
        await cookie_client().me()
    assert e.value.code == "nonce_introuvable"


@respx.mock
async def test_only_write_request_is_the_login_post():
    mock_login()
    mock_nonce("abc123def4")
    respx.get(f"{BASE}/wp-json/wp/v2/pages").mock(
        return_value=httpx.Response(200, json=[], headers={"X-WP-TotalPages": "1"}))
    client = cookie_client()
    await client.get_all("/wp/v2/pages")
    with pytest.raises(WordPressError):
        await client.request("POST", "/wp/v2/pages")
    posts = [c.request.url.path for c in respx.calls if c.request.method != "GET"]
    assert posts == ["/wp-login.php"]


@respx.mock
async def test_basic_mode_never_logs_in():
    login = mock_login()
    respx.get(f"{BASE}/wp-json/wp/v2/users/me").mock(return_value=httpx.Response(200, json=ME))
    client = make_client()
    await client.me()
    assert not login.called


# --- en-tête de remplacement (WP_AUTH_HEADER) ---------------------------------------------------
import base64


def header_client(header="X-Claude-Auth", **overrides) -> WordPressClient:
    settings = dataclasses.replace(SETTINGS, base_url=BASE, user="ogec-test", auth_mode="basic",
                                   app_password="abcd efgh ijkl", password=None, auth_header=header, **overrides)
    return WordPressClient(settings, http=httpx.AsyncClient(base_url=BASE, timeout=5))


@respx.mock
async def test_alt_header_replaces_authorization():
    route = respx.get(f"{BASE}/wp-json/wp/v2/users/me").mock(return_value=httpx.Response(200, json=ME))
    await header_client().me()
    req = route.calls.last.request
    assert "Authorization" not in req.headers
    scheme, token = req.headers["X-Claude-Auth"].split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(token).decode() == "ogec-test:abcdefghijkl"


@pytest.mark.parametrize("name", ["Authorization", "cookie", "Host", "bad header", "x", "X_Under;score"])
def test_alt_header_name_validated(name):
    with pytest.raises(WordPressError) as e:
        header_client(header=name)
    assert e.value.code == "config"


@respx.mock
async def test_alt_header_ignored_without_credentials():
    route = respx.get(f"{BASE}/wp-json/").mock(return_value=httpx.Response(200, json={}))
    settings = dataclasses.replace(SETTINGS, base_url=BASE, user=None, app_password=None, auth_header="X-Claude-Auth")
    await WordPressClient(settings, http=httpx.AsyncClient(base_url=BASE)).site_info()
    assert "X-Claude-Auth" not in route.calls.last.request.headers
