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
    "form_name", "address", "title_size", "link_text", "image_alt", "alt",
)
LINK_KEYS: tuple[str, ...] = ("link", "button_link", "image_link", "url")
LIST_KEYS: tuple[str, ...] = (
    "tabs", "icon_list", "slides", "items", "social_icon_list", "form_fields", "list",
    "price_list", "testimonials", "carousel", "gallery", "hotspot", "table_rows", "steps",
    "features", "cards", "accordion",
)

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
        fields = _settings_text(el.get("settings") or {})
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
            elif isinstance(val, dict):
                lines.append(f"{indent}  {key}: {val.get('url')}")
            else:
                lines.append(f"{indent}  {key}: {val}")
    return "\n".join(lines)
