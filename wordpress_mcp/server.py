"""Serveur MCP WordPress — lecture seule.

Outils :
  wp_statut, wp_reglages, wp_arborescence, wp_pages_list, wp_articles_list, wp_contenu_lire,
  wp_revisions, wp_recherche, wp_medias_list, wp_menus, wp_categories, wp_evenements,
  wp_formulaires_soumissions, wp_redirections, wp_extensions, wp_utilisateurs, wp_sante

Lecture : seule la méthode GET passe par `client.request` (voir client.py). Écritures (v2) :
outils *_preparer + wp_ecriture_confirmer dans write_tools.py, désactivés sauf WP_ENABLE_WRITES=1.
Les mots de passe d'application ne sont jamais renvoyés ni journalisés.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import elementor as E
from . import mec as M
from . import wporg as W
from .client import WordPressClient, WordPressError

log = logging.getLogger("wordpress_mcp.server")

mcp = MCPServer(
    name="wordpress",
    title="Site WordPress (lecture seule)",
    instructions=(
        "Accès au site WordPress configuré (WP_BASE_URL) via son API REST, avec un mot de passe "
        "d'application. Lecture : relire le contenu (pages, articles, médias, menus, événements), retrouver "
        "un texte, auditer le site (santé, mises à jour, extensions, redirections, 404, formulaires). Les "
        "pages Elementor sont lues widget par widget (wp_contenu_lire, format 'elementor'). Écriture "
        "(seulement si wp_statut indique « écritures activées ») : toujours en deux temps — un outil "
        "*_preparer renvoie un aperçu avant → après et un jeton ; montrer l'aperçu à l'utilisateur et "
        "n'appeler wp_ecriture_confirmer qu'après son accord explicite. Aucune suppression n'est possible. "
        "Commencer par wp_statut pour vérifier la connexion, les droits et le mode."
    ),
)

_client: WordPressClient | None = None

CONTENT_TYPES = {
    "page": "/wp/v2/pages",
    "post": "/wp/v2/posts",
    "article": "/wp/v2/posts",
    "media": "/wp/v2/media",
    "evenement": "/wp/v2/mec-events",
    "mec-events": "/wp/v2/mec-events",
    "modele": "/wp/v2/elementor_library",
    "elementor_library": "/wp/v2/elementor_library",
}


def _get_client() -> WordPressClient:
    global _client
    if _client is None:
        _client = WordPressClient()
    return _client


def _rendered(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("rendered") or obj.get("raw") or "")
    return str(obj or "")


def _raw(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("raw") if obj.get("raw") is not None else obj.get("rendered") or "")
    return str(obj or "")


def _is_elementor(item: dict[str, Any]) -> bool | None:
    meta = item.get("meta")
    if not isinstance(meta, dict) or "_elementor_edit_mode" not in meta:
        return None
    return meta.get("_elementor_edit_mode") == "builder"


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": item.get("id"),
        "type": item.get("type"),
        "titre": E.html_to_text(_rendered(item.get("title"))),
        "slug": item.get("slug"),
        "statut": item.get("status"),
        "lien": item.get("link"),
        "date": item.get("date"),
        "modifie": item.get("modified"),
    }
    if "parent" in item:
        out["parent"] = item.get("parent")
    if "menu_order" in item:
        out["ordre"] = item.get("menu_order")
    if item.get("template"):
        out["modele_theme"] = item.get("template")
    el = _is_elementor(item)
    if el is not None:
        out["elementor"] = el
    if item.get("categories"):
        out["categories"] = item.get("categories")
    if item.get("featured_media"):
        out["media_mis_en_avant"] = item.get("featured_media")
    return out


def _path_for(type_contenu: str) -> str:
    key = (type_contenu or "page").strip().lower()
    if key not in CONTENT_TYPES:
        raise WordPressError(f"type inconnu « {type_contenu} » ; attendu : {', '.join(sorted(CONTENT_TYPES))}")
    return CONTENT_TYPES[key]


# ---------------------------------------------------------------------------
@mcp.tool()
async def wp_statut() -> dict[str, Any]:
    """État du connecteur : site joint, version/espaces de noms REST disponibles, compte configuré
    (login et rôles, jamais le mot de passe), extensions détectées (Elementor, MEC, Redirection…).
    À appeler en premier pour vérifier la connexion."""
    client = _get_client()
    st = client.settings
    out: dict[str, Any] = {
        "base_url": st.base_url,
        "identifiants_configures": st.authenticated,
        "utilisateur": st.user,
        "authentification": st.auth_mode,
        "mode": ("lecture + écriture avec aperçu et confirmation" if st.enable_writes else "lecture seule"),
        "ecritures": "activées (confirmation obligatoire)" if st.enable_writes else "désactivées",
    }
    try:
        info = await client.site_info()
        ns = info.get("namespaces") or []
        out.update({
            "site": info.get("name"),
            "description": info.get("description"),
            "url": info.get("url"),
            "page_accueil_id": info.get("page_on_front"),
            "page_articles_id": info.get("page_for_posts"),
            "fuseau": info.get("timezone_string") or f"GMT{info.get('gmt_offset')}",
            "mots_de_passe_application": "application-passwords" in (info.get("authentication") or {}),
            "extensions_rest": sorted(n for n in ns if not n.startswith(("wp/", "wp-", "oembed"))),
            "connexion": "ok",
        })
    except WordPressError as e:
        out["connexion"] = f"échec : {e}"
        return out
    if st.authenticated:
        try:
            me = await client.me()
            out["compte"] = {"id": me.get("id"), "login": me.get("username") or me.get("slug"),
                             "nom": me.get("name"), "roles": me.get("roles")}
        except WordPressError as e:
            out["compte"] = f"échec : {e}"
    return out


@mcp.tool()
async def wp_reglages() -> dict[str, Any]:
    """Réglages généraux du site (titre, slogan, langue, fuseau, formats de date, page d'accueil,
    articles par page, inscriptions…). Nécessite un compte administrateur."""
    s = await _get_client().get("/wp/v2/settings")
    keys = ("title", "description", "url", "email", "timezone", "date_format", "time_format",
            "start_of_week", "language", "use_smilies", "default_category", "default_post_format",
            "posts_per_page", "show_on_front", "page_on_front", "page_for_posts",
            "default_ping_status", "default_comment_status", "site_logo", "site_icon")
    return {k: s.get(k) for k in keys if k in s}


@mcp.tool()
async def wp_pages_list(recherche: str | None = None, statut: str = "publish", parent: int | None = None,
                        limite: int = 100) -> list[dict[str, Any]]:
    """Pages du site : id, titre, slug, statut, lien, dates, parent, ordre, modèle, et `elementor`
    (True si la page est construite avec Elementor — visible avec un compte authentifié).
    `statut` : publish, draft, pending, private, future, trash ou any (brouillons : compte requis).
    `recherche` : texte dans le titre/contenu. `parent` : id de la page parente (0 = racine)."""
    client = _get_client()
    params = {"context": client.context, "search": recherche, "status": statut,
              "orderby": "menu_order" if not recherche else "relevance", "order": "asc"}
    if parent is not None:
        params["parent"] = parent
    items = await client.get_all("/wp/v2/pages", params, limite)
    return [_summary(i) for i in items]


@mcp.tool()
async def wp_arborescence(statut: str = "publish") -> dict[str, Any]:
    """Plan du site : arbre des pages (titre, slug, id, enfants), page d'accueil et page des
    articles, puis la liste des pages orphelines (parent inexistant)."""
    client = _get_client()
    info = await client.site_info()
    pages = await client.get_all("/wp/v2/pages", {"context": client.context, "status": statut,
                                                  "orderby": "menu_order", "order": "asc"})
    by_id = {p["id"]: p for p in pages}
    children: dict[int, list[dict[str, Any]]] = {}
    for p in pages:
        children.setdefault(p.get("parent") or 0, []).append(p)

    def node(p: dict[str, Any]) -> dict[str, Any]:
        n = {"id": p["id"], "titre": E.html_to_text(_rendered(p.get("title"))), "slug": p.get("slug"),
             "statut": p.get("status")}
        kids = children.get(p["id"])
        if kids:
            n["enfants"] = [node(k) for k in kids]
        return n

    orphans = [p for p in pages if p.get("parent") and p["parent"] not in by_id]
    return {
        "page_accueil_id": info.get("page_on_front"),
        "page_articles_id": info.get("page_for_posts"),
        "nombre_pages": len(pages),
        "arbre": [node(p) for p in children.get(0, [])],
        "orphelines": [node(p) for p in orphans],
    }


@mcp.tool()
async def wp_articles_list(recherche: str | None = None, statut: str = "publish",
                           categorie: int | None = None, depuis: str | None = None,
                           limite: int = 50) -> list[dict[str, Any]]:
    """Articles (actualités) du plus récent au plus ancien : id, titre, slug, statut, lien, date,
    catégories, média mis en avant, extrait. `depuis` : 'YYYY-MM-DD'. `categorie` : id de
    catégorie (voir wp_categories). Brouillons : compte requis."""
    client = _get_client()
    params: dict[str, Any] = {"context": client.context, "search": recherche, "status": statut,
                              "categories": [categorie] if categorie else None,
                              "after": f"{depuis}T00:00:00" if depuis else None}
    items = await client.get_all("/wp/v2/posts", params, limite)
    out = []
    for i in items:
        s = _summary(i)
        s["extrait"] = E.html_to_text(_rendered(i.get("excerpt")))[:300]
        out.append(s)
    return out


@mcp.tool()
async def wp_contenu_lire(id: int, type_contenu: str = "page", format: str = "texte") -> dict[str, Any]:
    """Contenu complet d'une page, d'un article, d'un événement ou d'un modèle Elementor.
    `type_contenu` : page, article (post), evenement (mec-events), modele (elementor_library).
    `format` : 'texte' (HTML rendu converti en texte lisible), 'html' (HTML rendu), 'brut'
    (contenu tel qu'en base, compte requis) ou 'elementor' (widget par widget, avec les textes,
    liens et images, à partir de la méta _elementor_data — compte requis). Renvoie aussi la
    structure Elementor (comptage par type d'élément) quand la page en est une."""
    client = _get_client()
    item = await client.get(f"{_path_for(type_contenu)}/{id}", {"context": client.context})
    out = _summary(item)
    out["extrait"] = E.html_to_text(_rendered(item.get("excerpt")))
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    eldata = meta.get("_elementor_data")
    if eldata:
        out["elementor_structure"] = E.structure_summary(eldata)
        settings = meta.get("_elementor_page_settings")
        if isinstance(settings, dict) and settings:
            out["elementor_reglages_page"] = settings
    fmt = (format or "texte").lower()
    if fmt == "elementor":
        if not eldata:
            out["contenu"] = ("pas de données Elementor sur cet élément (page non Elementor, ou compte "
                              "non authentifié : la méta n'est visible qu'en contexte edit)")
        else:
            widgets = E.extract_widgets(eldata)
            out["widgets"] = widgets
            out["contenu"] = E.widgets_to_text(widgets)
    elif fmt == "html":
        out["contenu"] = _rendered(item.get("content"))
    elif fmt == "brut":
        out["contenu"] = _raw(item.get("content"))
    else:
        out["contenu"] = E.html_to_text(_rendered(item.get("content")))
    if item.get("type") == "mec-events" and meta:
        out["evenement"] = {k: v for k, v in meta.items() if k.startswith("mec_") and v not in ("", None, [])}
    return out


@mcp.tool()
async def wp_revisions(id: int, type_contenu: str = "page", limite: int = 10) -> list[dict[str, Any]]:
    """Historique des révisions d'une page ou d'un article (auteur, date, titre), la plus
    récente en premier. Compte requis."""
    client = _get_client()
    items = await client.get_all(f"{_path_for(type_contenu)}/{id}/revisions", {"context": "view"}, limite)
    return [{"id": r.get("id"), "date": r.get("modified") or r.get("date"), "auteur": r.get("author"),
             "titre": E.html_to_text(_rendered(r.get("title")))} for r in items]


@mcp.tool()
async def wp_recherche(terme: str, type_contenu: str | None = None, limite: int = 20) -> list[dict[str, Any]]:
    """Recherche plein texte dans le contenu publié (pages, articles, événements…) : id, titre,
    type, lien. `type_contenu` : page, post, mec-events… (défaut : tous)."""
    client = _get_client()
    params = {"search": terme, "subtype": type_contenu or "any"}
    items = await client.get_all("/wp/v2/search", params, limite)
    return [{"id": i.get("id"), "titre": E.html_to_text(i.get("title")), "type": i.get("subtype"),
             "lien": i.get("url")} for i in items]


@mcp.tool()
async def wp_medias_list(recherche: str | None = None, type_mime: str | None = None,
                         depuis: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
    """Médiathèque : id, titre, nom de fichier, type MIME, URL, dimensions, texte alternatif,
    légende, date, élément rattaché. `type_mime` : image, application/pdf, video… `depuis` :
    'YYYY-MM-DD'. Utile pour retrouver un PDF (circulaire, calendrier) ou une image sans alt."""
    client = _get_client()
    params: dict[str, Any] = {"context": client.context, "search": recherche,
                              "media_type": type_mime if type_mime in ("image", "video", "audio", "application") else None,
                              "mime_type": type_mime if type_mime and "/" in type_mime else None,
                              "after": f"{depuis}T00:00:00" if depuis else None}
    items = await client.get_all("/wp/v2/media", params, limite)
    out = []
    for m in items:
        details = m.get("media_details") or {}
        out.append({
            "id": m.get("id"),
            "titre": E.html_to_text(_rendered(m.get("title"))),
            "fichier": (details.get("file") or m.get("source_url") or "").rsplit("/", 1)[-1],
            "type_mime": m.get("mime_type"),
            "url": m.get("source_url"),
            "largeur": details.get("width"),
            "hauteur": details.get("height"),
            "taille_octets": details.get("filesize"),
            "alt": m.get("alt_text"),
            "legende": E.html_to_text(_rendered(m.get("caption"))),
            "date": m.get("date"),
            "rattache_a": m.get("post"),
        })
    return out


@mcp.tool()
async def wp_menus() -> list[dict[str, Any]]:
    """Menus de navigation (classiques) et leurs entrées, dans l'ordre et avec la hiérarchie :
    libellé, type (page, lien personnalisé, catégorie…), cible, id de l'objet lié. Compte requis.
    Les menus construits dans Elementor (Royal Addons méga-menu) ne sont pas tous ici."""
    client = _get_client()
    menus = await client.get_all("/wp/v2/menus", {"context": "edit"})
    out = []
    for menu in menus:
        items = await client.get_all("/wp/v2/menu-items", {"context": "edit", "menus": menu["id"],
                                                           "orderby": "menu_order", "order": "asc"})
        children: dict[int, list[dict[str, Any]]] = {}
        for it in items:
            children.setdefault(int(it.get("parent") or 0), []).append(it)

        def node(it: dict[str, Any]) -> dict[str, Any]:
            n = {"id": it.get("id"), "libelle": E.html_to_text(_rendered(it.get("title"))),
                 "type": it.get("type"), "objet": it.get("object"), "objet_id": it.get("object_id"),
                 "url": it.get("url"), "statut": it.get("status")}
            kids = children.get(it["id"])
            if kids:
                n["sous_menu"] = [node(k) for k in kids]
            return n

        out.append({"id": menu.get("id"), "nom": menu.get("name"), "slug": menu.get("slug"),
                    "emplacements": menu.get("locations"), "nombre_entrees": len(items),
                    "entrees": [node(i) for i in children.get(0, [])]})
    return out


@mcp.tool()
async def wp_categories() -> dict[str, Any]:
    """Catégories et étiquettes des articles, et catégories d'événements (MEC) : id, nom, slug,
    nombre d'éléments, parent."""
    client = _get_client()
    def fmt(terms):
        return [{"id": t.get("id"), "nom": t.get("name"), "slug": t.get("slug"), "nombre": t.get("count"),
                 "parent": t.get("parent")} for t in terms]
    out: dict[str, Any] = {
        "categories": fmt(await client.get_all("/wp/v2/categories", {"hide_empty": False})),
        "etiquettes": fmt(await client.get_all("/wp/v2/tags", {"hide_empty": False})),
    }
    try:
        out["categories_evenements"] = fmt(await client.get_all("/wp/v2/mec_category", {"hide_empty": False}))
    except WordPressError:
        out["categories_evenements"] = []
    return out


@mcp.tool()
async def wp_evenements(a_venir: bool = True, recherche: str | None = None, statut: str = "publish",
                        limite: int = 50) -> list[dict[str, Any]]:
    """Événements du calendrier (Modern Events Calendar) : id, titre, lien, dates de début/fin
    (lues dans le JSON-LD de la page publique, MEC Lite ne les expose pas en REST), lieu, statut.
    `a_venir=False` pour inclure les événements passés."""
    client = _get_client()
    items = await client.get_all("/wp/v2/mec-events", {"context": client.context, "search": recherche,
                                                        "status": statut, "orderby": "date", "order": "desc"},
                                 max(limite * 3, limite))
    today = date.today().isoformat()
    sem = asyncio.Semaphore(5)

    async def details(item: dict[str, Any]) -> dict[str, Any]:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        if meta.get("mec_start_date"):
            return {"debut": meta.get("mec_start_date"), "fin": meta.get("mec_end_date") or meta.get("mec_start_date")}
        link = item.get("link")
        if not link:
            return {}
        try:  # l'API REST de MEC n'expose pas les dates : on lit le JSON-LD de la page publique
            async with sem:
                return M.parse_event_jsonld(await client.get_public_page(link))
        except Exception as exc:  # noqa: BLE001 - une page en échec ne bloque pas la liste
            log.warning("dates MEC illisibles pour l'événement %s : %s", item.get("id"), type(exc).__name__)
            return {}

    infos = await asyncio.gather(*(details(i) for i in items))
    out = []
    for i, d in zip(items, infos):
        start, end = d.get("debut"), d.get("fin") or d.get("debut")
        if a_venir and end and str(end) < today:
            continue
        meta = i.get("meta") if isinstance(i.get("meta"), dict) else {}
        s = _summary(i)
        s.update({"debut": start, "fin": end, "heure_debut": meta.get("mec_start_time_hour"),
                  "lieu": d.get("lieu"), "lieu_id": meta.get("mec_location_id"),
                  "repetition": meta.get("mec_repeat_type") or None,
                  "extrait": E.html_to_text(_rendered(i.get("excerpt")))[:300]})
        out.append(s)
    out.sort(key=lambda e: (e.get("debut") or "", e.get("date") or ""))
    return out[:limite]


FORMINATOR_SNIPPET = "docs/wpcode-snippet-forminator.php"

# Libellé par défaut d'un champ Forminator laissé sans libellé, selon son type.
FORMINATOR_DEFAULT_LABELS = {
    "address": "Adresse", "name": "Nom", "email": "E-mail", "phone": "Téléphone", "date": "Date",
    "upload": "Fichier", "postdata": "Contenu", "gdprcheckbox": "Consentement RGPD",
}


def _forminator_labels(champs: list[dict]) -> dict[str, str]:
    """slug → libellé lisible et unique : un libellé répété (un bloc par enfant…) est numéroté
    « Libellé (2) », « Libellé (3) » dans l'ordre du formulaire, pour ne rien écraser."""
    base = [(c.get("slug"), (c.get("libelle") or "").strip()
             or FORMINATOR_DEFAULT_LABELS.get(c.get("type") or "", c.get("slug") or ""))
            for c in champs if c.get("slug")]
    total: dict[str, int] = {}
    for _, label in base:
        total[label] = total.get(label, 0) + 1
    seen: dict[str, int] = {}
    out: dict[str, str] = {}
    for slug, label in base:
        seen[label] = seen.get(label, 0) + 1
        out[slug] = f"{label} ({seen[label]})" if total[label] > 1 and seen[label] > 1 else label
    return out


SNIPPETS_GESTION = "docs/wpcode-snippet-gestion.php"


@mcp.tool()
async def wp_snippets() -> dict[str, Any]:
    """Snippets WPCode du site : id, titre, actif, type, emplacement, empreinte SHA-256 du code,
    protégé (ne peut pas être désactivé à distance), erreur signalée par WPCode. Nécessite le
    snippet docs/wpcode-snippet-gestion.php côté site (WPCode n'a pas d'API REST)."""
    client = _get_client()
    try:
        items = await client.get("/wpmcp/v1/snippets")
    except WordPressError as e:
        if e.code == "rest_no_route" or e.status == 404:
            return {"snippets": [], "note": f"snippets non lisibles : installer {SNIPPETS_GESTION} dans WPCode "
                                            "(titre « MCP Gestion des snippets »)"}
        raise
    return {"nombre": len(items or []), "actifs": sum(1 for i in items or [] if i.get("actif")),
            "snippets": items or []}


async def _forminator_submissions(client: WordPressClient, form_id: int | None, depuis: str | None,
                                  limite: int) -> tuple[list[dict], list[dict], int, str | None]:
    """Formulaires et réponses Forminator, via les routes du snippet (wpmcp/v1)."""
    try:
        forms = await client.get("/wpmcp/v1/forminator/forms")
    except WordPressError as e:
        if e.code == "rest_no_route" or e.status == 404:
            return [], [], 0, f"Forminator : réponses non lisibles, installer le snippet {FORMINATOR_SNIPPET} (WPCode)"
        if e.code == "forminator_absent":
            return [], [], 0, None  # pas de Forminator sur ce site : rien à signaler
        return [], [], 0, f"Forminator : échec ({e})"
    listed, subs, total = [], [], 0
    for f in forms or []:
        labels = _forminator_labels(f.get("champs") or [])
        listed.append({"source": "forminator", "id": f.get("id"), "nom": f.get("nom"),
                       "statut": f.get("statut"), "reponses": f.get("reponses")})
        if form_id is not None and f.get("id") != form_id:
            continue
        if not f.get("reponses"):
            continue
        data = await client.get(f"/wpmcp/v1/forminator/forms/{f.get('id')}/entries",
                                {"per_page": min(100, limite), "after": depuis})
        total += int(data.get("total") or 0)
        for r in data.get("reponses") or []:
            subs.append({
                "source": "forminator", "id": r.get("id"), "date": r.get("date"),
                "formulaire": f.get("nom"), "page_origine": None, "url_origine": None, "lu": None,
                "statut": None,
                "champs": {labels.get(k, k): v for k, v in (r.get("champs") or {}).items()},
            })
    return listed, subs, total, None


async def _elementor_submissions(client: WordPressClient, statut: str, form_id: str | None,
                                 depuis: str | None, limite: int) -> tuple[list[dict], list[dict], int | None, str | None]:
    """Formulaires et soumissions Elementor Pro (elementor/v1)."""
    try:
        forms = await client.get("/elementor/v1/forms", {"context": "options"})
        data = await client.get("/elementor/v1/form-submissions",
                                {"status": statut, "form": form_id, "after": depuis,
                                 "per_page": min(100, limite), "order": "desc"})
    except WordPressError as e:
        if e.code == "rest_no_route" or e.status == 404:
            return [], [], None, None  # pas d'Elementor Pro ou module Formulaires désactivé
        return [], [], None, f"Elementor : échec ({e})"
    rows = data.get("data") if isinstance(data, dict) else data
    subs = [{
        "source": "elementor", "id": r.get("id"), "date": r.get("created_at"),
        "formulaire": (r.get("form") or {}).get("name") or (r.get("form") or {}).get("element_id"),
        "page_origine": (r.get("post") or {}).get("title") or r.get("referer_title"),
        "url_origine": r.get("referer"), "lu": r.get("is_read"), "statut": r.get("status"),
        "champs": {(v.get("key") or v.get("id")): v.get("value") for v in (r.get("values") or [])
                   if isinstance(v, dict)},
    } for r in rows or []]
    flist = forms.get("data") if isinstance(forms, dict) else forms
    listed = [{"source": "elementor", "id": f.get("value"), "nom": f.get("label")}
              for f in flist or [] if isinstance(f, dict)]
    meta = data.get("meta") if isinstance(data, dict) else None
    total = (meta or {}).get("pagination", {}).get("total") if isinstance(meta, dict) else None
    return listed, subs, total, None


@mcp.tool()
async def wp_formulaires_soumissions(statut: str = "all", formulaire: str | None = None,
                                     depuis: str | None = None, limite: int = 50) -> dict[str, Any]:
    """Réponses aux formulaires du site (pré-inscription, contact…), la plus récente en premier :
    date, formulaire, champs saisis (par libellé). Deux sources : Forminator (lu grâce au snippet
    docs/wpcode-snippet-forminator.php) et Elementor Pro. `formulaire` : id numérique Forminator ou
    identifiant Elementor {post_id}_{element_id} (voir `formulaires`). `statut` (Elementor seul) :
    all, unread, read, trash. `depuis` : 'YYYY-MM-DD'. Compte administrateur requis. Données
    personnelles de familles : ne restituer que ce qui est demandé."""
    client = _get_client()
    fid = formulaire.strip() if formulaire else None
    forminator_id = int(fid) if fid and fid.isdigit() else None
    notes: list[str] = []
    f_forms, f_subs, f_total = [], [], 0
    if fid is None or forminator_id is not None:
        f_forms, f_subs, f_total, note = await _forminator_submissions(client, forminator_id, depuis, limite)
        if note:
            notes.append(note)
    e_forms, e_subs, e_total = [], [], None
    if fid is None or forminator_id is None:
        e_forms, e_subs, e_total, note = await _elementor_submissions(client, statut, fid, depuis, limite)
        if note:
            notes.append(note)
    subs = sorted(f_subs + e_subs, key=lambda r: str(r.get("date") or ""), reverse=True)
    out: dict[str, Any] = {"formulaires": f_forms + e_forms, "total": f_total + (e_total or len(e_subs)),
                           "soumissions": subs[:limite]}
    if notes:
        out["notes"] = notes
    return out


@mcp.tool()
async def wp_redirections(journal_404: bool = False, limite: int = 100) -> dict[str, Any]:
    """Extension Redirection : règles (source → cible, code HTTP, groupe, nombre d'utilisations,
    dernier accès). `journal_404=True` renvoie aussi les URL introuvables récemment demandées
    (pour créer les redirections manquantes). Compte requis."""
    client = _get_client()
    out: dict[str, Any] = {}
    data = await client.get("/redirection/v1/redirect", {"per_page": min(200, limite), "orderby": "last_access",
                                                          "direction": "desc"})
    out["total"] = data.get("total")
    out["redirections"] = [{
        "id": r.get("id"), "source": r.get("url"), "cible": (r.get("action_data") or {}).get("url"),
        "code": r.get("action_code"), "groupe": r.get("group_id"), "activee": r.get("enabled"),
        "utilisations": r.get("hits"), "dernier_acces": r.get("last_access"),
    } for r in data.get("items") or []]
    if journal_404:
        logs = await client.get("/redirection/v1/404", {"per_page": min(200, limite), "orderby": "",
                                                        "direction": "desc"})
        out["total_404"] = logs.get("total")
        out["journal_404"] = [{"url": l.get("url"), "date": l.get("created"), "referrer": l.get("referrer"),
                               "agent": (l.get("agent") or "")[:80]} for l in logs.get("items") or []]
    return out


@mcp.tool()
async def wp_extensions() -> dict[str, Any]:
    """Extensions installées (nom, version, active ou non, auteur, URI) et thème actif. Compte
    administrateur requis. Sert à l'audit : extensions inactives à retirer, versions à suivre."""
    client = _get_client()
    plugins = await client.get("/wp/v2/plugins")
    out: dict[str, Any] = {"extensions": [{
        "id": p.get("plugin"), "nom": p.get("name"), "version": p.get("version"), "statut": p.get("status"),
        "auteur": E.html_to_text(p.get("author")), "uri": p.get("plugin_uri"),
        "wp_minimum": p.get("requires_wp"), "php_minimum": p.get("requires_php"),
    } for p in plugins]}
    out["nombre"] = len(out["extensions"])
    out["inactives"] = [p["nom"] for p in out["extensions"] if p["statut"] != "active"]
    try:
        themes = await client.get("/wp/v2/themes", {"status": "active"})
        out["theme_actif"] = [{"id": t.get("stylesheet"), "nom": _rendered(t.get("name")),
                               "version": t.get("version"), "parent": t.get("template")} for t in themes]
    except WordPressError as e:
        out["theme_actif"] = f"échec : {e}"
    return out


@mcp.tool()
async def wp_utilisateurs() -> list[dict[str, Any]]:
    """Comptes WordPress : id, login, nom affiché, rôles, date d'inscription. Compte
    administrateur requis. Sert à vérifier qui peut publier et à repérer les comptes obsolètes."""
    client = _get_client()
    users = await client.get_all("/wp/v2/users", {"context": "edit", "orderby": "registered_date", "order": "asc"})
    return [{"id": u.get("id"), "login": u.get("username"), "nom": u.get("name"), "roles": u.get("roles"),
             "inscrit": u.get("registered_date")} for u in users]


HEALTH_TESTS = ("background-updates", "loopback-requests", "https-status", "dotorg-communication",
                "authorization-header", "page-cache")
HEALTH_STATUS = {"good": "bon", "recommended": "recommandé", "critical": "critique"}
SIZE_KEYS = {"wordpress_size": "wordpress", "themes_size": "themes", "plugins_size": "extensions",
             "uploads_size": "medias", "database_size": "base_de_donnees", "total_size": "total"}


def _human_size(n: int) -> str:
    for unit, factor in (("Go", 1024 ** 3), ("Mo", 1024 ** 2), ("Ko", 1024)):
        if n >= factor:
            return f"{n / factor:.2f} {unit}".replace(".", ",")
    return f"{n} o"


async def _health_test(client: WordPressClient, name: str) -> dict[str, Any]:
    try:
        t = await client.get(f"/wp-site-health/v1/tests/{name}")
    except WordPressError as e:
        return {"test": name, "statut": "échec", "erreur": str(e)}
    out = {"test": name, "statut": HEALTH_STATUS.get(t.get("status"), t.get("status")),
           "intitule": E.html_to_text(t.get("label")), "categorie": (t.get("badge") or {}).get("label"),
           "description": E.html_to_text(t.get("description"))}
    actions = E.html_to_text(t.get("actions"))
    if actions:
        out["actions"] = actions
    if name == "authorization-header" and t.get("status") != "good" and client.settings.auth_header:
        out["note"] = (f"attendu : l'hébergeur retire l'en-tête Authorization ; le connecteur passe par "
                       f"{client.settings.auth_header} (snippet WPCode)")
    return out


async def _directory_sizes(client: WordPressClient) -> dict[str, Any]:
    try:
        d = await client.get("/wp-site-health/v1/directory-sizes")
    except WordPressError as e:
        return {"erreur": str(e)}
    out: dict[str, Any] = {label: (d.get(key) or {}).get("size") for key, label in SIZE_KEYS.items()}
    raw = {label: (d.get(key) or {}).get("raw") for key, label in SIZE_KEYS.items()}
    parts = [raw.get(k) for k in ("wordpress", "themes", "extensions", "medias")]
    if all(isinstance(v, int) for v in parts):
        reste = parts[0] - sum(parts[1:])
        if reste > 0:
            out["autres_dans_wordpress"] = _human_size(reste)
            out["note"] = ("« autres_dans_wordpress » : cœur WordPress et autres dossiers de wp-content "
                           "(sauvegardes, cache, journaux…)")
    return out


async def _versions(client: WordPressClient) -> dict[str, Any]:
    out: dict[str, Any] = {}
    plugins = await client.get("/wp/v2/plugins")
    themes = await client.get("/wp/v2/themes")
    installed_core = None
    try:
        installed_core = W.version_from_feed(await client.get_public_page(
            (client.settings.base_url or "").rstrip("/") + "/feed/"))
    except WordPressError:
        pass
    try:
        latest = await W.WordPressOrg(timeout=client.settings.timeout).latest_versions(
            [W.plugin_slug(p.get("plugin")) or "" for p in plugins],
            [W.theme_slug(t.get("stylesheet")) or "" for t in themes])
    except Exception as e:  # wordpress.org injoignable : on donne au moins les versions installées
        latest = {"core": None, "plugins": {}, "themes": {}}
        out["note"] = f"wordpress.org injoignable ({type(e).__name__}) : dernières versions inconnues"
    out["wordpress"] = {"installee": installed_core, "derniere": latest["core"],
                        "mise_a_jour": W.is_outdated(installed_core, latest["core"])}

    def row(kind: str, slug: str | None, ident: Any, name: Any, status: Any, version: Any) -> dict[str, Any]:
        last = latest[kind].get(slug) if slug else None
        known = bool(slug) and slug in latest[kind]
        return {"id": ident, "nom": name, "statut": status, "version": version, "derniere": last,
                "mise_a_jour": W.is_outdated(version, last),
                "source": "wordpress.org" if last else ("hors wordpress.org" if known else "inconnue")}

    out["extensions"] = [row("plugins", W.plugin_slug(p.get("plugin")), p.get("plugin"), p.get("name"),
                             p.get("status"), p.get("version")) for p in plugins]
    out["themes"] = [row("themes", W.theme_slug(t.get("stylesheet")), t.get("stylesheet"),
                         _rendered(t.get("name")), t.get("status"), t.get("version")) for t in themes]
    items = out["extensions"] + out["themes"]
    out["a_mettre_a_jour"] = ([f"WordPress {installed_core} → {latest['core']}"]
                              if out["wordpress"]["mise_a_jour"] else []) + \
        [f"{i['nom']} {i['version']} → {i['derniere']}" for i in items if i["mise_a_jour"]]
    out["hors_wordpress_org"] = [i["nom"] for i in items if i["source"] == "hors wordpress.org"]
    if out["hors_wordpress_org"]:
        out["note_hors_wordpress_org"] = ("versions des extensions et thèmes commerciaux à vérifier dans "
                                          "wp-admin › Tableau de bord › Mises à jour")
    return out


@mcp.tool()
async def wp_sante(mises_a_jour: bool = True) -> dict[str, Any]:
    """Santé du site : tests de WordPress (HTTPS, mises à jour automatiques, tâches planifiées,
    communication avec wordpress.org, en-tête d'autorisation, cache de page), taille des dossiers
    et de la base, et, si `mises_a_jour`, versions installées comparées aux dernières publiées sur
    wordpress.org (cœur, extensions, thèmes ; seul le nom public des extensions est envoyé à
    wordpress.org). Compte administrateur requis. Ne corrige rien."""
    client = _get_client()
    tests = await asyncio.gather(*(_health_test(client, n) for n in HEALTH_TESTS))
    resume: dict[str, int] = {}
    for t in tests:
        resume[t["statut"]] = resume.get(t["statut"], 0) + 1
    out: dict[str, Any] = {"resume": resume, "tests": list(tests), "tailles": await _directory_sizes(client)}
    if mises_a_jour:
        try:
            out["versions"] = await _versions(client)
        except WordPressError as e:
            out["versions"] = {"erreur": str(e)}
    return out


# Outils d'écriture (v2) : enregistrés sur le même serveur, inactifs sans WP_ENABLE_WRITES=1.
from . import write_tools  # noqa: E402,F401
