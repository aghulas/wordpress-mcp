"""Client HTTP de l'API REST WordPress — LECTURE SEULE.

Garde-fou : seule la méthode GET est autorisée (`request` refuse tout le reste), et seuls les
espaces de noms de `ALLOWED_NAMESPACES` sont joignables. Rien ne peut être créé, modifié ni
supprimé depuis ce module, quels que soient les droits du compte configuré.

Authentification, deux modes (WP_AUTH_MODE) :
  - basic (défaut) : HTTP Basic avec un mot de passe d'application (WP_USER / WP_APP_PASSWORD) ;
  - cookie : connexion par wp-login.php (WP_USER / WP_PASSWORD), puis jeton REST (nonce) lu sur
    admin-ajax.php?action=rest-nonce et envoyé dans X-WP-Nonce. Utile quand l'hébergeur retire
    l'en-tête Authorization. Le POST de connexion est la seule écriture réseau possible ; il vise
    un chemin fixe et ne passe pas par `request`, qui reste en lecture seule.
Sans identifiants, le client interroge l'API en anonyme (contenu publié uniquement, pas de
brouillons, pas de menus, pas de formulaires, pas d'extensions).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any

import httpx

from .config import SETTINGS, Settings

log = logging.getLogger("wordpress_mcp.client")

API_PREFIX = "/wp-json"
# Mode cookie : seules requêtes hors API REST, chemins fixes (jamais construits à partir d'une entrée).
LOGIN_PATH = "/wp-login.php"
NONCE_PATH = "/wp-admin/admin-ajax.php"
AUTH_MODES: frozenset[str] = frozenset({"basic", "cookie"})
_HEADER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{1,63}$")
STALE_SESSION_CODES: frozenset[str] = frozenset({"rest_cookie_invalid_nonce", "rest_not_logged_in"})

# Espaces de noms REST que le connecteur a le droit d'interroger (tous en GET seulement).
ALLOWED_NAMESPACES: frozenset[str] = frozenset({
    "",                     # racine /wp-json/ (description du site)
    "wp/v2",                # cœur WordPress
    "wp-site-health/v1",    # santé du site
    "elementor/v1",         # soumissions de formulaires, globals
    "elementor-pro/v1",     # licence
    "mec/v1",               # Modern Events Calendar
    "redirection/v1",       # extension Redirection
})

READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})


class WordPressError(Exception):
    """Erreur renvoyée par WordPress (code + message) ou refus du garde-fou."""

    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


def _namespace_of(path: str) -> str:
    """Espace de noms REST d'un chemin `/wp/v2/pages` → `wp/v2` ; `/` → ``."""
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    # ex. wp/v2/pages → wp/v2 ; elementor/v1/documents/… → elementor/v1
    return "/".join(parts[:2])


def check_read_only(method: str, path: str) -> None:
    """Lève WordPressError si la requête n'est pas une lecture autorisée."""
    if method.upper() not in READ_METHODS:
        raise WordPressError(
            f"connecteur en lecture seule : méthode {method.upper()} refusée pour {path}",
            code="lecture_seule",
        )
    ns = _namespace_of(path)
    if ns not in ALLOWED_NAMESPACES:
        raise WordPressError(f"espace de noms REST non autorisé : {ns or '/'}", code="namespace_refuse")


class WordPressClient:
    def __init__(self, settings: Settings | None = None, http: httpx.AsyncClient | None = None):
        self.settings = settings or SETTINGS
        if not self.settings.base_url and http is None:
            raise WordPressError("WP_BASE_URL n'est pas défini", code="config")
        if self.settings.auth_mode not in AUTH_MODES:
            raise WordPressError(f"WP_AUTH_MODE inconnu : « {self.settings.auth_mode} » (basic ou cookie)",
                                 code="config")
        alt = self.settings.auth_header
        if alt and (not _HEADER_NAME_RE.fullmatch(alt) or alt.lower() in {"authorization", "cookie", "host"}):
            raise WordPressError(f"WP_AUTH_HEADER invalide : « {alt} »", code="config")
        self._cookie_mode = self.settings.auth_mode == "cookie" and self.settings.authenticated
        self._nonce: str | None = None
        self._login_lock = asyncio.Lock()
        auth = None
        extra_headers: dict[str, str] = {}
        if self.settings.authenticated and not self._cookie_mode:
            # WordPress affiche le mot de passe d'application par groupes de 4 séparés d'espaces.
            auth = httpx.BasicAuth(self.settings.user or "", (self.settings.app_password or "").replace(" ", ""))
            if alt:  # en-tête de remplacement : mêmes identifiants Basic, hors Authorization
                token = base64.b64encode(
                    f"{self.settings.user}:{(self.settings.app_password or '').replace(' ', '')}".encode()).decode()
                extra_headers[alt] = f"Basic {token}"
                auth = None
        self._http = http or httpx.AsyncClient(
            base_url=self.settings.base_url,
            timeout=self.settings.timeout,
            auth=auth,
            headers={"Accept": "application/json", "User-Agent": "wordpress-mcp/0.1 (lecture seule)",
                     **extra_headers},
            follow_redirects=True,
        )
        if http is not None and auth is not None and http.auth is None:
            http.auth = auth
        if http is not None and extra_headers:
            http.headers.update(extra_headers)

    # ------------------------------------------------------------------ transport
    async def _login(self) -> None:
        """Connexion par formulaire (mode cookie) puis lecture du jeton REST. Jamais journalisé."""
        st = self.settings
        self._http.cookies.clear()
        self._nonce = None
        self._http.cookies.set("wordpress_test_cookie", "WP Cookie check", domain=self._http.base_url.host)
        resp = await self._http.post(
            LOGIN_PATH,
            data={"log": st.user, "pwd": st.password, "wp-submit": "Log In",
                  "redirect_to": f"{st.base_url}/wp-admin/", "testcookie": "1"},
            follow_redirects=False,
        )
        if not any(c.name.startswith("wordpress_logged_in") for c in self._http.cookies.jar):
            raise WordPressError(
                "connexion refusée par wp-login.php : identifiant ou mot de passe incorrect, ou double "
                "authentification / captcha / extension de sécurité qui bloque la connexion",
                code="login_refuse", status=resp.status_code)
        r = await self._http.get(NONCE_PATH, params={"action": "rest-nonce"})
        nonce = r.text.strip()
        if r.status_code != 200 or not re.fullmatch(r"[A-Za-z0-9]{6,20}", nonce):
            raise WordPressError("connecté, mais le jeton REST (nonce) est introuvable "
                                 "(admin-ajax.php bloqué ?)", code="nonce_introuvable", status=r.status_code)
        self._nonce = nonce

    async def _ensure_login(self, stale: str | None = None) -> None:
        async with self._login_lock:
            if self._nonce is None or self._nonce == stale:
                await self._login()

    async def _send(self, method: str, url: str, params: dict[str, Any]) -> httpx.Response:
        headers = None
        if self._cookie_mode:
            if self._nonce is None:
                await self._ensure_login()
            headers = {"X-WP-Nonce": self._nonce or ""}
        return await self._http.request(method, url, params=params, headers=headers)

    @staticmethod
    def _session_expired(resp: httpx.Response) -> bool:
        if resp.status_code not in (401, 403):
            return False
        try:
            return resp.json().get("code") in STALE_SESSION_CODES
        except (ValueError, AttributeError):
            return False

    async def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        check_read_only(method, path)
        url = API_PREFIX + "/" + path.strip("/")
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != "" and v != []}
        resp = await self._send(method, url, clean)
        if self._cookie_mode and self._session_expired(resp):
            await self._ensure_login(stale=self._nonce)  # session ou jeton expiré : une reconnexion, une seule
            resp = await self._send(method, url, clean)
        if resp.status_code >= 400:
            code, message = None, f"HTTP {resp.status_code}"
            try:
                body = resp.json()
                code = body.get("code")
                message = body.get("message") or message
            except ValueError:
                pass
            if resp.status_code == 401:
                message += (" — identifiants absents ou refusés (WP_USER / WP_PASSWORD)" if self._cookie_mode
                            else " — identifiants absents ou refusés (WP_USER / WP_APP_PASSWORD)")
            raise WordPressError(message, code=code, status=resp.status_code)
        return resp

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return (await self.request("GET", path, params)).json()

    async def get_all(self, path: str, params: dict[str, Any] | None = None,
                      limit: int | None = None) -> list[dict[str, Any]]:
        """Liste complète en suivant la pagination WordPress (en-tête X-WP-TotalPages)."""
        limit = min(limit or self.settings.per_page_max, self.settings.per_page_max)
        per_page = min(100, limit)
        items: list[dict[str, Any]] = []
        page = 1
        while len(items) < limit:
            resp = await self.request("GET", path, {**(params or {}), "per_page": per_page, "page": page})
            batch = resp.json()
            if not isinstance(batch, list):
                raise WordPressError(f"réponse inattendue (liste attendue) pour {path}", code="format")
            items.extend(batch)
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or 1)
            if page >= total_pages or not batch:
                break
            page += 1
        return items[:limit]

    async def get_public_page(self, url: str) -> str:
        """HTML d'une page publique du MÊME site (GET, sans identifiants). Sert aux données que l'API
        REST n'expose pas (dates MEC). Refuse tout autre domaine, tout schéma non https/http."""
        from urllib.parse import urlsplit
        want = (urlsplit(self.settings.base_url or str(self._http.base_url)).hostname or "").removeprefix("www.")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or (parts.hostname or "").removeprefix("www.") != want:
            raise WordPressError(f"page publique refusée (autre domaine) : {parts.hostname}", code="domaine_refuse")
        async with httpx.AsyncClient(timeout=self.settings.timeout, follow_redirects=True,
                                     headers={"User-Agent": "wordpress-mcp/0.1 (lecture seule)"}) as http:
            resp = await http.get(url)
        if resp.status_code >= 400:
            raise WordPressError(f"HTTP {resp.status_code}", status=resp.status_code)
        return resp.text

    # ------------------------------------------------------------------ raccourcis
    async def site_info(self) -> dict[str, Any]:
        return await self.get("/")

    async def me(self) -> dict[str, Any]:
        return await self.get("/wp/v2/users/me", {"context": "edit"})

    @property
    def context(self) -> str:
        """`edit` (métadonnées, brouillons, contenu brut) si authentifié, sinon `view`."""
        return "edit" if self.settings.authenticated else "view"

    async def aclose(self) -> None:
        await self._http.aclose()
