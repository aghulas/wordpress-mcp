"""Aides communes : réglages de test, client sur un site fictif, réponses WordPress minimales.

Aucune donnée réelle : site `https://wp.test`, utilisateur `claude-test`.
"""
from __future__ import annotations

import dataclasses
import json

import httpx

from wordpress_mcp.client import WordPressClient
from wordpress_mcp.config import SETTINGS

BASE = "https://wp.test"


def make_settings(authenticated: bool = True, **overrides):
    return dataclasses.replace(
        SETTINGS,
        base_url=BASE,
        user="claude-test" if authenticated else None,
        app_password="abcd efgh ijkl" if authenticated else None,
        per_page_max=overrides.pop("per_page_max", 200),
        **overrides,
    )


def make_client(authenticated: bool = True, **overrides) -> WordPressClient:
    settings = make_settings(authenticated, **overrides)
    return WordPressClient(settings, http=httpx.AsyncClient(base_url=BASE, timeout=5))


SITE_INFO = {
    "name": "École Test", "description": "", "url": BASE, "home": BASE,
    "gmt_offset": "0", "timezone_string": "", "page_for_posts": 20, "page_on_front": 10,
    "namespaces": ["oembed/1.0", "mec/v1", "redirection/v1", "elementor/v1", "elementor-pro/v1",
                   "wp/v2", "wp-site-health/v1"],
    "authentication": {"application-passwords": {"endpoints": {"authorization": BASE + "/wp-admin/authorize-application.php"}}},
}

ME = {"id": 1, "username": "claude-test", "slug": "claude-test", "name": "Claude Test", "roles": ["administrator"]}


def page(id_, title, slug, parent=0, order=0, elementor=True, eldata=None):
    return {
        "id": id_, "type": "page", "title": {"rendered": title, "raw": title}, "slug": slug,
        "status": "publish", "link": f"{BASE}/{slug}/", "date": "2026-09-01T10:00:00",
        "modified": "2026-09-02T10:00:00", "parent": parent, "menu_order": order, "template": "",
        "content": {"rendered": f"<p>Contenu de <b>{title}</b></p>", "raw": f"<p>Contenu de {title}</p>"},
        "excerpt": {"rendered": ""},
        "meta": {"_elementor_edit_mode": "builder" if elementor else "",
                 "_elementor_data": eldata or "", "_elementor_page_settings": {}},
    }


ELEMENTOR_DATA = json.dumps([
    {"id": "s1", "elType": "section", "settings": {}, "elements": [
        {"id": "c1", "elType": "column", "settings": {}, "elements": [
            {"id": "w1", "elType": "widget", "widgetType": "heading",
             "settings": {"title": "Bienvenue &agrave; l'&eacute;cole", "header_size": "h1"}, "elements": []},
            {"id": "w2", "elType": "widget", "widgetType": "text-editor",
             "settings": {"editor": "<p>Premier paragraphe.</p><p>Second&nbsp;paragraphe.</p>"}, "elements": []},
            {"id": "w3", "elType": "widget", "widgetType": "button",
             "settings": {"text": "Nous contacter", "link": {"url": "/contact/", "is_external": ""}}, "elements": []},
            {"id": "w4", "elType": "widget", "widgetType": "icon-list",
             "settings": {"icon_list": [{"text": "Maternelle", "link": {"url": "/maternelle/"}},
                                        {"text": "Élémentaire", "link": {"url": ""}}]}, "elements": []},
            {"id": "w5", "elType": "widget", "widgetType": "image",
             "settings": {"image": {"url": BASE + "/wp-content/uploads/logo.png", "id": 77, "alt": "Logo"}}, "elements": []},
            {"id": "w6", "elType": "widget", "widgetType": "spacer", "settings": {"space": {"size": 20}}, "elements": []},
        ]},
    ]},
])

PAGES = [
    page(10, "Accueil", "accueil", order=0, eldata=ELEMENTOR_DATA),
    page(11, "L'école", "ecole", order=1),
    page(12, "Maternelle", "maternelle", parent=11, order=0),
    page(13, "Élémentaire", "elementaire", parent=11, order=1),
    page(14, "Orpheline", "orpheline", parent=999, order=0, elementor=False),
    page(20, "Actualités", "actualites", order=2, elementor=False),
]

POSTS = [
    {"id": 100, "type": "post", "title": {"rendered": "Rentrée 2026"}, "slug": "rentree-2026", "status": "publish",
     "link": f"{BASE}/rentree-2026/", "date": "2026-09-01T08:00:00", "modified": "2026-09-01T08:00:00",
     "categories": [3], "featured_media": 55, "excerpt": {"rendered": "<p>La rentrée a eu lieu…</p>"},
     "content": {"rendered": "<p>Texte de l'article.</p>"}, "meta": {}},
]

MEDIA = [
    {"id": 55, "title": {"rendered": "photo-rentree"}, "mime_type": "image/jpeg", "source_url": f"{BASE}/wp-content/uploads/2026/09/photo-rentree.jpg",
     "media_details": {"file": "2026/09/photo-rentree.jpg", "width": 1200, "height": 800, "filesize": 123456},
     "alt_text": "", "caption": {"rendered": ""}, "date": "2026-09-01T08:00:00", "post": 100},
    {"id": 56, "title": {"rendered": "calendrier"}, "mime_type": "application/pdf", "source_url": f"{BASE}/wp-content/uploads/2026/09/calendrier.pdf",
     "media_details": {"filesize": 4567}, "alt_text": "", "caption": {"rendered": ""}, "date": "2026-09-02T08:00:00", "post": None},
]

MENUS = [{"id": 2, "name": "Principal", "slug": "principal", "locations": ["primary"]}]
MENU_ITEMS = [
    {"id": 201, "title": {"rendered": "Accueil"}, "type": "post_type", "object": "page", "object_id": 10, "url": f"{BASE}/", "status": "publish", "parent": 0, "menu_order": 1},
    {"id": 202, "title": {"rendered": "L'école"}, "type": "post_type", "object": "page", "object_id": 11, "url": f"{BASE}/ecole/", "status": "publish", "parent": 0, "menu_order": 2},
    {"id": 203, "title": {"rendered": "Maternelle"}, "type": "post_type", "object": "page", "object_id": 12, "url": f"{BASE}/maternelle/", "status": "publish", "parent": 202, "menu_order": 3},
    {"id": 204, "title": {"rendered": "Portail"}, "type": "custom", "object": "custom", "object_id": 0, "url": "https://portail.example", "status": "publish", "parent": 0, "menu_order": 4},
]

EVENTS = [
    {"id": 300, "type": "mec-events", "title": {"rendered": "Kermesse"}, "slug": "kermesse", "status": "publish",
     "link": f"{BASE}/events/kermesse/", "date": "2026-05-01T08:00:00", "modified": "2026-05-01T08:00:00",
     "excerpt": {"rendered": ""}, "meta": {"mec_start_date": "2099-06-20", "mec_end_date": "2099-06-20", "mec_start_time_hour": "14", "mec_location_id": "4"}},
    {"id": 301, "type": "mec-events", "title": {"rendered": "Ancienne réunion"}, "slug": "reunion", "status": "publish",
     "link": f"{BASE}/events/reunion/", "date": "2020-01-01T08:00:00", "modified": "2020-01-01T08:00:00",
     "excerpt": {"rendered": ""}, "meta": {"mec_start_date": "2020-01-15", "mec_end_date": "2020-01-15"}},
]
