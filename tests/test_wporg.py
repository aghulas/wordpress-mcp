"""wordpress.org : logique pure et client (respx), sans réseau."""
from __future__ import annotations

import httpx
import respx

from wordpress_mcp import wporg as W


def test_slugs():
    assert W.plugin_slug("elementor/elementor") == "elementor"
    assert W.plugin_slug("hide-admin-menu/init") == "hide-admin-menu"
    assert W.plugin_slug("hello") == "hello"
    assert W.plugin_slug("../etc/passwd") is None and W.plugin_slug(None) is None
    assert W.theme_slug("Twentytwentyfive") == "twentytwentyfive"
    assert W.theme_slug("a b") is None


def test_version_from_feed():
    assert W.version_from_feed("<rss><generator>https://wordpress.org/?v=7.1.3</generator></rss>") == "7.1.3"
    assert W.version_from_feed("<rss></rss>") is None


def test_is_outdated():
    assert W.is_outdated("7.1", "7.1.3") is True
    assert W.is_outdated("7.1.3", "7.1.3") is False
    assert W.is_outdated("1.7.1069", "1.7.1070") is True
    assert W.is_outdated("4.10.0", "4.9.9") is False
    assert W.is_outdated("1.0", None) is None and W.is_outdated(None, "1.0") is None


@respx.mock
async def test_latest_versions_no_credentials_and_fixed_host():
    def plugin_info(request):
        slug = request.url.params["request[slug]"]
        if slug == "elementor":
            return httpx.Response(200, json={"version": "4.4.0"})
        return httpx.Response(404, json={"error": "Plugin not found."})

    core = respx.get(f"{W.WPORG}{W.CORE_PATH}").mock(
        return_value=httpx.Response(200, json={"offers": [{"current": "7.1.3"}]}))
    respx.get(f"{W.WPORG}{W.PLUGIN_PATH}").mock(side_effect=plugin_info)
    respx.get(f"{W.WPORG}{W.THEME_PATH}").mock(return_value=httpx.Response(200, json={"version": "1.5"}))
    out = await W.WordPressOrg().latest_versions(["elementor", "elementor-pro", "", "../x"], ["twentytwentyfive"])
    assert out == {"core": "7.1.3", "plugins": {"elementor": "4.4.0", "elementor-pro": None},
                   "themes": {"twentytwentyfive": "1.5"}}
    for call in respx.calls:
        assert call.request.url.host == "api.wordpress.org" and call.request.method == "GET"
        assert "authorization" not in call.request.headers and "x-claude-auth" not in call.request.headers
    assert core.called
