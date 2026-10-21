"""Logique pure (sans réseau) : HTML → texte, et extraction du texte d'une page Elementor.

Une page construite avec Elementor stocke sa mise en page dans la méta `_elementor_data`
(JSON : arbre de conteneurs/sections/colonnes/widgets). Le champ `content.rendered` de l'API
contient alors le HTML généré, souvent lourd ; ce module fournit une vue lisible par widget.
"""
from __future__ import annotations

import html as _html
import json
import re
from typing import Any

# Clés de réglages Elementor qui portent du texte visible, par ordre d'intérêt.
TEXT_KEYS: tuple[str, ...] = (
    "title", "title_text", "heading", "editor", "text", "description", "description_text",
    "content", "html", "caption", "button_text", "cta_text", "label", "placeholder", "alert_title",
    "alert_description", "prefix", "suffix", "before_text", "after_text", "highlighted_text",
    "rotating_text", "testimonial_content", "testimonial_name", "testimonial_job", "tab_title",
    "tab_content", "item_title", "item_description", "field_label", "email_subject",
    "success_message", "error_message", "required_field_message", "invalid_message",
    "form_name", "address", "title_size", "link_text", "image_alt", "alt", "shortcode",
)
LINK_KEYS: tuple[str, ...] = ("link", "button_link", "image_link", "url")
LIST_KEYS: tuple[str, ...] = (
    "tabs", "icon_list", "slides", "items", "social_icon_list", "form_fields", "list",
    "price_list", "testimonials", "carousel", "gallery", "hotspot", "table_rows", "steps",
    "features", "cards", "accordion",
)

# Widgets qui affichent une liste de contenus calculée à l'affichage (pas de texte en dur) :
# on décrit leur requête à la place.
DYNAMIC_WIDGETS: dict[str, str] = {
    "posts": "liste d'articles",
    "archive-posts": "archive d'articles",
    "portfolio": "portfolio",
    "loop-grid": "grille de contenus",
    "loop-carousel": "carrousel de contenus",
    "wpr-grid": "grille de contenus (Royal Addons)",
    "wpr-magazine-grid": "grille magazine (Royal Addons)",
    "wpr-posts-timeline": "frise d'articles (Royal Addons)",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def html_to_text(value: Any) -> str:
    """HTML → texte brut lisible : paragraphes et sauts de ligne conservés, balises retirées."""
    if value is None:
        return ""
    s = str(value)
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</\s*(p|div|h[1-6]|section|article|blockquote)\s*>", "\n\n", s)
    s = re.sub(r"(?i)</\s*(li|tr)\s*>", "\n", s)
    s = re.sub(r"(?i)<\s*li[^>]*>", "• ", s)
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s).replace("\xa0", " ")
    s = _WS_RE.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return _NL_RE.sub("\n\n", s).strip()


def _link_url(v: Any) -> str | None:
    if isinstance(v, dict):
        url = v.get("url")
        return str(url) if url else None
    if isinstance(v, str) and v.startswith(("http://", "https://", "/", "mailto:", "tel:")):
        return v
    return None


def _settings_text(settings: dict[str, Any]) -> dict[str, Any]:
    """Champs textuels et liens d'un réglage de widget, listes comprises (un niveau)."""
    out: dict[str, Any] = {}
    for key in TEXT_KEYS:
        v = settings.get(key)
        if isinstance(v, str) and v.strip():
            text = html_to_text(v)
            if text and text not in ("default", "yes", "no"):
                out[key] = text
    for key in LINK_KEYS:
        url = _link_url(settings.get(key))
        if url:
            out[key] = url
    for key in LIST_KEYS:
        items = settings.get(key)
        if isinstance(items, list) and items:
            rows = []
            for it in items:
                if isinstance(it, dict):
                    row = _settings_text(it)
                    if row:
                        rows.append(row)
            if rows:
                out[key] = rows
    img = settings.get("image")
    if isinstance(img, dict) and img.get("url"):
        out["image"] = {"url": img.get("url"), "id": img.get("id"), "alt": img.get("alt")}
    return out


def _first(settings: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        v = settings.get(key)
        if v not in (None, "", [], {}):
            return v
    return None


def dynamic_query(widget_type: str | None, settings: dict[str, Any]) -> dict[str, Any] | None:
    """Description lisible de la requête d'un widget de liste dynamique (articles, boucle…)."""
    label = DYNAMIC_WIDGETS.get(widget_type or "")
    if not label:
        return None
    skin = settings.get("_skin") or "classic"
    out: dict[str, Any] = {"contenu": label}
    out["source"] = _first(settings, "posts_post_type", "post_query_post_type", "query_source",
                           "query_post_type") or "post"
    nombre = _first(settings, f"{skin}_posts_per_page", "posts_per_page", "post_query_posts_per_page",
                    "query_posts_per_page")
    if nombre is not None:
        out["nombre"] = nombre
    termes = _first(settings, "posts_include_term_ids", "post_query_include_term_ids",
                    "query_selection_categories", "query_taxonomy_category")
    if termes:
        out["termes"] = termes
    ids = _first(settings, "posts_posts_ids", "post_query_posts_ids", "query_manual_post")
    if ids:
        out["contenus_choisis"] = ids
    suite = _first(settings, f"{skin}_read_more_text", "read_more_text", "read_more_text_custom")
    if isinstance(suite, str) and suite.strip():
        out["lien_suite"] = html_to_text(suite)
    modele = _first(settings, "template_id")
    if modele:
        out["modele"] = modele
    return out


def parse_elementor_data(raw: Any) -> list[dict[str, Any]]:
    """`_elementor_data` (chaîne JSON ou liste déjà décodée) → liste d'éléments racine."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return []
    else:
        data = raw
    return data if isinstance(data, list) else []


def walk(elements: list[dict[str, Any]], depth: int = 0):
    """Parcours en profondeur : (élément, profondeur)."""
    for el in elements:
        if not isinstance(el, dict):
            continue
        yield el, depth
        children = el.get("elements")
        if isinstance(children, list):
            yield from walk(children, depth + 1)


def extract_widgets(raw: Any) -> list[dict[str, Any]]:
    """Texte visible de chaque widget : id, type, profondeur, champs (titre, texte, lien…)."""
    out: list[dict[str, Any]] = []
    for el, depth in walk(parse_elementor_data(raw)):
        if el.get("elType") != "widget":
            continue
        settings = el.get("settings") or {}
        fields = _settings_text(settings)
        query = dynamic_query(el.get("widgetType"), settings)
        if query:
            fields["requete"] = query
        if not fields:
            continue
        out.append({
            "id": el.get("id"),
            "widget": el.get("widgetType"),
            "profondeur": depth,
            "champs": fields,
        })
    return out


def structure_summary(raw: Any) -> dict[str, Any]:
    """Comptage des éléments par type (section, container, column, widget:<type>)."""
    counts: dict[str, int] = {}
    for el, _ in walk(parse_elementor_data(raw)):
        kind = el.get("elType") or "?"
        if kind == "widget":
            kind = f"widget:{el.get('widgetType') or '?'}"
        counts[kind] = counts.get(kind, 0) + 1
    return {"elements": sum(counts.values()), "par_type": dict(sorted(counts.items()))}


def widgets_to_text(widgets: list[dict[str, Any]]) -> str:
    """Rendu texte compact des widgets extraits (un bloc par widget)."""
    lines: list[str] = []
    for w in widgets:
        indent = "  " * w["profondeur"]
        lines.append(f"{indent}[{w['widget']} #{w['id']}]")
        for key, val in w["champs"].items():
            if isinstance(val, list):
                for row in val:
                    lines.append(f"{indent}  - " + " | ".join(f"{k}: {v}" for k, v in row.items() if not isinstance(v, (list, dict))))
            elif isinstance(val, dict) and "url" in val:
                lines.append(f"{indent}  {key}: {val.get('url')}")
            elif isinstance(val, dict):
                lines.append(f"{indent}  {key}: " + ", ".join(f"{k}={v}" for k, v in val.items()))
            else:
                lines.append(f"{indent}  {key}: {val}")
    return "\n".join(lines)


# ---------------------------------------------------------------- écriture (v2)
def find_element(elements: list[dict[str, Any]], element_id: str) -> dict[str, Any] | None:
    """Élément (widget, section…) d'identifiant `element_id` dans l'arbre décodé, ou None."""
    for el, _ in walk(elements):
        if str(el.get("id")) == str(element_id):
            return el
    return None


def _split_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for p in (path or "").split("."):
        if not p:
            raise ValueError(f"champ invalide : {path!r}")
        parts.append(int(p) if p.isdigit() else p)
    return parts


def get_setting(settings: dict[str, Any], path: str) -> Any:
    """Valeur d'un réglage : `editor`, `title`, `icon_list.2.text` (index de liste à partir de 0)."""
    cur: Any = settings
    for p in _split_path(path):
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                raise ValueError(f"champ introuvable : {path}")
            cur = cur[p]
        else:
            if not isinstance(cur, dict) or p not in cur:
                raise ValueError(f"champ introuvable : {path}")
            cur = cur[p]
    return cur


def set_setting(settings: dict[str, Any], path: str, value: str) -> None:
    """Remplace un réglage texte EXISTANT (jamais de création de clé ni de changement de type)."""
    parts = _split_path(path)
    parent = get_setting(settings, ".".join(str(p) for p in parts[:-1])) if len(parts) > 1 else settings
    last = parts[-1]
    current = get_setting(settings, path)
    if not isinstance(current, str):
        raise ValueError(f"le champ {path} n'est pas un texte (type {type(current).__name__})")
    if isinstance(last, int):
        parent[last] = value
    else:
        parent[last] = value


def is_html_field(path: str) -> bool:
    """Champs Elementor dont la valeur est du HTML (éditeur de texte, descriptions…)."""
    leaf = (path or "").split(".")[-1]
    return leaf in {"editor", "description", "description_text", "content", "html", "tab_content",
                    "item_description", "testimonial_content", "alert_description"}


def dump_elementor_data(elements: list[dict[str, Any]]) -> str:
    """Arbre → chaîne JSON compacte, comme Elementor la stocke."""
    return json.dumps(elements, ensure_ascii=False, separators=(",", ":"))


def _esc(text: str) -> str:
    return _html.escape(text or "", quote=False)


def _fallback_for_widget(el: dict[str, Any]) -> str:
    wt = el.get("widgetType") or ""
    st = el.get("settings") or {}
    if wt in DYNAMIC_WIDGETS or wt in ("shortcode", "spacer", "divider", "html", "google_maps", "video"):
        return ""
    if wt == "heading":
        size = str(st.get("header_size") or "h2")
        size = size if size in {"h1", "h2", "h3", "h4", "h5", "h6"} else "h2"
        return f"<{size}>{_esc(html_to_text(st.get('title')))}</{size}>" if st.get("title") else ""
    if wt == "text-editor":
        return str(st.get("editor") or "")
    if wt == "image":
        img = st.get("image") or {}
        return f'<img src="{_html.escape(str(img.get("url")))}" alt="{_html.escape(str(img.get("alt") or ""))}">' \
            if isinstance(img, dict) and img.get("url") else ""
    if wt == "button":
        url = _link_url(st.get("link"))
        text = _esc(html_to_text(st.get("text")))
        return (f'<p><a href="{_html.escape(url)}">{text}</a></p>' if url else f"<p>{text}</p>") if text else ""
    parts: list[str] = []
    fields = _settings_text(st)
    for key, val in fields.items():
        if key in LINK_KEYS or key == "image":
            continue
        if isinstance(val, list):
            items = []
            for row in val:
                label = " — ".join(str(v) for k, v in row.items() if isinstance(v, str) and k not in LINK_KEYS)
                if label:
                    items.append(f"<li>{_esc(label)}</li>")
            if items:
                parts.append("<ul>" + "".join(items) + "</ul>")
        elif isinstance(val, str):
            parts.append(f"<p>{_esc(val)}</p>")
    return "\n".join(parts)


def build_fallback_html(raw: Any) -> str:
    """Texte de secours d'une page Elementor (champ `content` : recherche, flux RSS, résumé de
    partage), reconstruit widget par widget dans l'ordre d'affichage — l'équivalent de ce
    qu'Elementor enregistre quand on clique « Mettre à jour ». Les widgets calculés (articles,
    shortcodes) sont ignorés."""
    blocks = []
    for el, _ in walk(parse_elementor_data(raw)):
        if el.get("elType") == "widget":
            html_block = _fallback_for_widget(el).strip()
            if html_block:
                blocks.append(html_block)
    return "\n".join(blocks)

