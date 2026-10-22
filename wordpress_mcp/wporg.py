"""Dernières versions publiées sur wordpress.org (cœur, extensions, thèmes) — LECTURE SEULE.

L'API REST de WordPress ne dit pas si des mises à jour sont disponibles. On compare donc les
versions installées avec l'API publique de wordpress.org :
  - hôte fixe `api.wordpress.org`, trois chemins fixes, GET seulement ;
  - aucun identifiant ni en-tête du site n'est envoyé ; seul le nom public (slug) de chaque
    extension ou thème part, validé par expression régulière ;
  - une extension commerciale (absente de wordpress.org) est signalée « hors wordpress.org ».
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

WPORG = "https://api.wordpress.org"
CORE_PATH = "/core/version-check/1.7/"
PLUGIN_PATH = "/plugins/info/1.2/"
THEME_PATH = "/themes/info/1.2/"
USER_AGENT = "wordpress-mcp/0.8"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_FEED_VERSION_RE = re.compile(r"<generator>\s*https?://wordpress\.org/\?v=([0-9][0-9.]*)\s*</generator>")


def plugin_slug(plugin_id: str | None) -> str | None:
    """`elementor/elementor` → `elementor` ; `hello` → `hello` ; slug invalide → None."""
    slug = (plugin_id or "").split("/")[0].strip().lower()
    return slug if _SLUG_RE.fullmatch(slug) else None


def theme_slug(stylesheet: str | None) -> str | None:
    slug = (stylesheet or "").strip().lower()
    return slug if _SLUG_RE.fullmatch(slug) else None


def version_from_feed(xml: str) -> str | None:
    """Version du cœur annoncée par le flux RSS (`<generator>https://wordpress.org/?v=7.1.3`)."""
    m = _FEED_VERSION_RE.search(xml or "")
    return m.group(1) if m else None


def _parts(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def is_outdated(installed: str | None, latest: str | None) -> bool | None:
    """True si `latest` est plus récente que `installed` ; None si l'une manque."""
    if not installed or not latest:
        return None
    a, b = _parts(installed), _parts(latest)
    if not a or not b:
        return None
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) < b + (0,) * (width - len(b))


class WordPressOrg:
    """Client minimal de api.wordpress.org (GET, chemins fixes, sans identifiants)."""

    def __init__(self, timeout: float = 20.0, concurrency: int = 5):
        self._timeout = timeout
        self._sem = asyncio.Semaphore(concurrency)

    async def _get(self, http: httpx.AsyncClient, path: str, params: dict[str, Any]) -> Any:
        async with self._sem:
            resp = await http.get(WPORG + path, params=params)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def latest_core(self, http: httpx.AsyncClient) -> str | None:
        data = await self._get(http, CORE_PATH, {})
        offers = (data or {}).get("offers") or []
        return offers[0].get("current") if offers else None

    async def latest_plugin(self, http: httpx.AsyncClient, slug: str) -> str | None:
        data = await self._get(http, PLUGIN_PATH, {"action": "plugin_information", "request[slug]": slug,
                                                   "request[fields][sections]": 0})
        return data.get("version") if isinstance(data, dict) and not data.get("error") else None

    async def latest_theme(self, http: httpx.AsyncClient, slug: str) -> str | None:
        data = await self._get(http, THEME_PATH, {"action": "theme_information", "request[slug]": slug})
        return data.get("version") if isinstance(data, dict) and not data.get("error") else None

    async def latest_versions(self, plugin_slugs: list[str], theme_slugs: list[str]) -> dict[str, Any]:
        """{"core": str|None, "plugins": {slug: str|None}, "themes": {slug: str|None}}.
        Lève httpx.HTTPError si wordpress.org est injoignable."""
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False,
                                     headers={"User-Agent": USER_AGENT}) as http:
            plugins = sorted({s for s in plugin_slugs if _SLUG_RE.fullmatch(s or "")})
            themes = sorted({s for s in theme_slugs if _SLUG_RE.fullmatch(s or "")})
            core, *rest = await asyncio.gather(
                self.latest_core(http),
                *(self.latest_plugin(http, s) for s in plugins),
                *(self.latest_theme(http, s) for s in themes),
            )
        return {"core": core,
                "plugins": dict(zip(plugins, rest[:len(plugins)])),
                "themes": dict(zip(themes, rest[len(plugins):]))}
