"""Outils MCP sur un site fictif (respx)."""
from __future__ import annotations

import httpx
import pytest
import respx

from wordpress_mcp import server as S

from ._helpers import (BASE, EVENTS, ME, MEDIA, MENU_ITEMS, MENUS, PAGES, POSTS, SITE_INFO, make_client)


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    client = make_client()
    monkeypatch.setattr(S, "_client", client)
    return client


def _list(path, items, total_pages=1):
    return respx.get(f"{BASE}/wp-json{path}").mock(
        return_value=httpx.Response(200, json=items, headers={"X-WP-TotalPages": str(total_pages)}))


@respx.mock
async def test_statut_authenticated():
    respx.get(f"{BASE}/wp-json/").mock(return_value=httpx.Response(200, json=SITE_INFO))
    respx.get(f"{BASE}/wp-json/wp/v2/users/me").mock(return_value=httpx.Response(200, json=ME))
    out = await S.wp_statut()
    assert out["connexion"] == "ok"
    assert out["mode"] == "lecture seule"
    assert out["site"] == "École Test"
    assert out["page_accueil_id"] == 10
    assert out["mots_de_passe_application"] is True
    assert out["extensions_rest"] == ["elementor-pro/v1", "elementor/v1", "mec/v1", "redirection/v1"]
    assert out["compte"]["roles"] == ["administrator"]
    assert "abcd" not in str(out) and "app_password" not in str(out)


@respx.mock
async def test_statut_connection_failure():
    respx.get(f"{BASE}/wp-json/").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(httpx.ConnectError):
        await S.wp_statut()


@respx.mock
async def test_pages_list_and_elementor_flag():
    route = _list("/wp/v2/pages", PAGES)
    out = await S.wp_pages_list()
    assert [p["id"] for p in out] == [10, 11, 12, 13, 14, 20]
    assert out[0]["elementor"] is True and out[4]["elementor"] is False
    assert out[2]["parent"] == 11
    params = dict(route.calls.last.request.url.params)
    assert params["context"] == "edit" and params["orderby"] == "menu_order" and params["status"] == "publish"


@respx.mock
async def test_pages_list_search_uses_relevance():
    route = _list("/wp/v2/pages", PAGES[:1])
    await S.wp_pages_list(recherche="accueil", statut="any", parent=0)
    params = dict(route.calls.last.request.url.params)
    assert params["search"] == "accueil" and params["orderby"] == "relevance" and params["parent"] == "0"


@respx.mock
async def test_arborescence():
    respx.get(f"{BASE}/wp-json/").mock(return_value=httpx.Response(200, json=SITE_INFO))
    _list("/wp/v2/pages", PAGES)
    out = await S.wp_arborescence()
    assert out["nombre_pages"] == 6
    roots = {n["slug"]: n for n in out["arbre"]}
    assert set(roots) == {"accueil", "ecole", "actualites"}
    assert [c["slug"] for c in roots["ecole"]["enfants"]] == ["maternelle", "elementaire"]
    assert [o["slug"] for o in out["orphelines"]] == ["orpheline"]


@respx.mock
async def test_articles_list():
    route = _list("/wp/v2/posts", POSTS)
    out = await S.wp_articles_list(categorie=3, depuis="2026-09-01")
    assert out[0]["titre"] == "Rentrée 2026"
    assert out[0]["extrait"] == "La rentrée a eu lieu…"
    assert out[0]["media_mis_en_avant"] == 55
    params = dict(route.calls.last.request.url.params)
    assert params["categories"] == "3" and params["after"] == "2026-09-01T00:00:00"


@respx.mock
async def test_contenu_lire_formats():
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(return_value=httpx.Response(200, json=PAGES[0]))
    texte = await S.wp_contenu_lire(10)
    assert texte["contenu"] == "Contenu de Accueil"
    assert texte["elementor_structure"]["elements"] == 8
    html = await S.wp_contenu_lire(10, format="html")
    assert "<b>Accueil</b>" in html["contenu"]
    el = await S.wp_contenu_lire(10, type_contenu="page", format="elementor")
    assert len(el["widgets"]) == 5
    assert "Bienvenue à l'école" in el["contenu"]


@respx.mock
async def test_contenu_lire_elementor_without_data():
    respx.get(f"{BASE}/wp-json/wp/v2/pages/20").mock(return_value=httpx.Response(200, json=PAGES[5]))
    out = await S.wp_contenu_lire(20, format="elementor")
    assert "pas de données Elementor" in out["contenu"]
    assert "elementor_structure" not in out


@respx.mock
async def test_contenu_lire_article_and_unknown_type():
    respx.get(f"{BASE}/wp-json/wp/v2/posts/100").mock(return_value=httpx.Response(200, json=POSTS[0]))
    out = await S.wp_contenu_lire(100, type_contenu="article")
    assert out["type"] == "post"
    with pytest.raises(S.WordPressError):
        await S.wp_contenu_lire(1, type_contenu="menu")


@respx.mock
async def test_recherche():
    _list("/wp/v2/search", [{"id": 10, "title": "Accueil", "subtype": "page", "url": f"{BASE}/"}])
    out = await S.wp_recherche("accueil")
    assert out == [{"id": 10, "titre": "Accueil", "type": "page", "lien": f"{BASE}/"}]


@respx.mock
async def test_medias_list_filters():
    route = _list("/wp/v2/media", MEDIA)
    out = await S.wp_medias_list(type_mime="application/pdf")
    assert dict(route.calls.last.request.url.params)["mime_type"] == "application/pdf"
    assert out[1]["fichier"] == "calendrier.pdf" and out[1]["type_mime"] == "application/pdf"
    assert out[0]["largeur"] == 1200 and out[0]["rattache_a"] == 100
    await S.wp_medias_list(type_mime="image")
    assert dict(route.calls.last.request.url.params)["media_type"] == "image"


@respx.mock
async def test_menus_hierarchy():
    _list("/wp/v2/menus", MENUS)
    _list("/wp/v2/menu-items", MENU_ITEMS)
    out = await S.wp_menus()
    assert out[0]["nom"] == "Principal" and out[0]["nombre_entrees"] == 4
    entries = out[0]["entrees"]
    assert [e["libelle"] for e in entries] == ["Accueil", "L'école", "Portail"]
    assert entries[1]["sous_menu"][0]["libelle"] == "Maternelle"
    assert entries[2]["type"] == "custom"


@respx.mock
async def test_evenements_upcoming_only():
    _list("/wp/v2/mec-events", EVENTS)
    out = await S.wp_evenements()
    assert [e["titre"] for e in out] == ["Kermesse"]
    assert out[0]["debut"] == "2099-06-20" and out[0]["lieu_id"] == "4"
    out = await S.wp_evenements(a_venir=False)
    assert [e["titre"] for e in out] == ["Ancienne réunion", "Kermesse"]


@respx.mock
async def test_categories_without_mec():
    _list("/wp/v2/categories", [{"id": 3, "name": "Actualités", "slug": "actualites", "count": 1, "parent": 0}])
    _list("/wp/v2/tags", [])
    respx.get(f"{BASE}/wp-json/wp/v2/mec_category").mock(
        return_value=httpx.Response(404, json={"code": "rest_no_route", "message": "Aucune route"}))
    out = await S.wp_categories()
    assert out["categories"][0]["nom"] == "Actualités"
    assert out["categories_evenements"] == []


NO_ROUTE = httpx.Response(404, json={"code": "rest_no_route", "message": "Aucune route.", "data": {"status": 404}})


@respx.mock
async def test_formulaires_soumissions():
    respx.get(f"{BASE}/wp-json/wpmcp/v1/forminator/forms").mock(return_value=NO_ROUTE)
    respx.get(f"{BASE}/wp-json/elementor/v1/forms").mock(
        return_value=httpx.Response(200, json={"data": [{"value": "10_abc", "label": "Contact"}]}))
    respx.get(f"{BASE}/wp-json/elementor/v1/form-submissions").mock(return_value=httpx.Response(200, json={
        "data": [{"id": 7, "created_at": "2026-10-01 10:00:00", "is_read": False, "status": "unread",
                  "form": {"name": "Contact", "element_id": "abc"}, "post": {"title": "Nous contacter"},
                  "referer": f"{BASE}/contact/",
                  "values": [{"key": "name", "value": "Famille Test"}, {"key": "message", "value": "Bonjour"}]}],
        "meta": {"pagination": {"total": 1}},
    }))
    out = await S.wp_formulaires_soumissions(statut="unread")
    assert out["total"] == 1
    s = out["soumissions"][0]
    assert s["source"] == "elementor" and s["formulaire"] == "Contact" and s["lu"] is False
    assert s["champs"] == {"name": "Famille Test", "message": "Bonjour"}
    assert out["formulaires"] == [{"source": "elementor", "id": "10_abc", "nom": "Contact"}]
    assert len(out["notes"]) == 1 and "wpcode-snippet-forminator.php" in out["notes"][0]


FORMINATOR_FORMS = [
    {"id": 95, "nom": "Pré-inscription", "statut": "publish", "reponses": 2,
     "champs": [{"slug": "name-1", "libelle": "Nom de l'enfant", "type": "name"},
                {"slug": "email-1", "libelle": "E-mail", "type": "email"},
                {"slug": "address-1", "libelle": "", "type": "address"},
                {"slug": "name-2", "libelle": "Nom de l'enfant", "type": "name"}]},
    {"id": 96, "nom": "Ancien formulaire", "statut": "draft", "reponses": 0, "champs": []},
]


@respx.mock
async def test_formulaires_soumissions_forminator():
    respx.get(f"{BASE}/wp-json/wpmcp/v1/forminator/forms").mock(
        return_value=httpx.Response(200, json=FORMINATOR_FORMS))
    entries = respx.get(f"{BASE}/wp-json/wpmcp/v1/forminator/forms/95/entries").mock(
        return_value=httpx.Response(200, json={"formulaire": 95, "total": 2, "page": 1, "reponses": [
            {"id": 12, "date": "2026-10-02 09:00:00", "champs": {"name-1": "Enfant Test", "email-1": "a@b.test",
                                                              "address-1": "1 rue Test", "name-2": "Cadet Test"}},
            {"id": 11, "date": "2026-09-20 18:30:00", "champs": {"name-1": "Autre Enfant", "champ-inconnu": "x"}},
        ]}))
    respx.get(f"{BASE}/wp-json/elementor/v1/forms").mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(f"{BASE}/wp-json/elementor/v1/form-submissions").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"pagination": {"total": 0}}}))
    out = await S.wp_formulaires_soumissions(depuis="2026-09-01")
    assert out["total"] == 2 and "notes" not in out
    assert [f["id"] for f in out["formulaires"]] == [95, 96]
    first, second = out["soumissions"]
    assert first["source"] == "forminator" and first["formulaire"] == "Pré-inscription"
    assert first["champs"] == {"Nom de l'enfant": "Enfant Test", "E-mail": "a@b.test", "Adresse": "1 rue Test",
                               "Nom de l'enfant (2)": "Cadet Test"}  # libellé répété : numéroté, rien d'écrasé
    assert second["champs"]["champ-inconnu"] == "x"  # slug sans libellé : gardé tel quel
    assert entries.calls.last.request.url.params["after"] == "2026-09-01"
    assert not any("/forms/96/" in str(c.request.url) for c in respx.calls)  # aucune réponse : pas d'appel


@respx.mock
async def test_formulaires_soumissions_filtre_forminator_sans_elementor():
    respx.get(f"{BASE}/wp-json/wpmcp/v1/forminator/forms").mock(
        return_value=httpx.Response(200, json=FORMINATOR_FORMS))
    respx.get(f"{BASE}/wp-json/wpmcp/v1/forminator/forms/95/entries").mock(
        return_value=httpx.Response(200, json={"formulaire": 95, "total": 0, "page": 1, "reponses": []}))
    elementor = respx.get(f"{BASE}/wp-json/elementor/v1/forms")
    out = await S.wp_formulaires_soumissions(formulaire="95")
    assert out["total"] == 0 and out["soumissions"] == []
    assert not elementor.called  # un id numérique désigne un formulaire Forminator


@respx.mock
async def test_redirections_with_404():
    respx.get(f"{BASE}/wp-json/redirection/v1/redirect").mock(return_value=httpx.Response(200, json={
        "total": 1, "items": [{"id": 1, "url": "/ancien", "action_data": {"url": "/nouveau"}, "action_code": 301,
                               "group_id": 1, "enabled": True, "hits": 12, "last_access": "2026-10-01"}]}))
    respx.get(f"{BASE}/wp-json/redirection/v1/404").mock(return_value=httpx.Response(200, json={
        "total": 1, "items": [{"url": "/introuvable", "created": "2026-10-02", "referrer": "", "agent": "x" * 200}]}))
    out = await S.wp_redirections(journal_404=True)
    assert out["redirections"][0]["cible"] == "/nouveau" and out["redirections"][0]["code"] == 301
    assert out["journal_404"][0]["url"] == "/introuvable" and len(out["journal_404"][0]["agent"]) == 80


@respx.mock
async def test_extensions():
    respx.get(f"{BASE}/wp-json/wp/v2/plugins").mock(return_value=httpx.Response(200, json=[
        {"plugin": "elementor/elementor", "name": "Elementor", "version": "3.30", "status": "active",
         "author": "<a href='x'>Elementor</a>", "plugin_uri": "", "requires_wp": "6.5", "requires_php": "7.4"},
        {"plugin": "hello", "name": "Hello Dolly", "version": "1.7", "status": "inactive", "author": "", "plugin_uri": ""},
    ]))
    respx.get(f"{BASE}/wp-json/wp/v2/themes").mock(return_value=httpx.Response(200, json=[
        {"stylesheet": "hello-elementor", "name": {"rendered": "Hello Elementor"}, "version": "3.1", "template": "hello-elementor"}]))
    out = await S.wp_extensions()
    assert out["nombre"] == 2 and out["inactives"] == ["Hello Dolly"]
    assert out["extensions"][0]["auteur"] == "Elementor"
    assert out["theme_actif"][0]["nom"] == "Hello Elementor"


@respx.mock
async def test_utilisateurs_and_revisions():
    _list("/wp/v2/users", [{"id": 1, "username": "admin", "name": "Admin", "roles": ["administrator"], "registered_date": "2020-01-01"}])
    out = await S.wp_utilisateurs()
    assert out[0]["login"] == "admin"
    _list("/wp/v2/pages/10/revisions", [{"id": 900, "modified": "2026-09-02T10:00:00", "author": 1, "title": {"rendered": "Accueil"}}])
    rev = await S.wp_revisions(10)
    assert rev[0]["id"] == 900 and rev[0]["auteur"] == 1


async def test_tool_count():
    tools = await S.mcp.list_tools()
    names = {t.name for t in tools}
    assert len(tools) == 17
    assert all(n.startswith("wp_") for n in names)
    # Aucun outil d'écriture ne doit exister dans cette version.
    assert not any(k in n for n in names for k in ("prepare", "confirm", "create", "update", "delete", "upload"))


def _health(status, label, actions=""):
    return httpx.Response(200, json={"label": label, "status": status, "badge": {"label": "Sécurité"},
                                     "description": f"<p>{label}</p>", "actions": actions, "test": "x"})


@respx.mock
async def test_sante(monkeypatch, fake_client):
    import dataclasses
    from wordpress_mcp import wporg as W
    fake_client.settings = dataclasses.replace(fake_client.settings, auth_header="X-Claude-Auth")
    for name in S.HEALTH_TESTS:
        respx.get(f"{BASE}/wp-json/wp-site-health/v1/tests/{name}").mock(return_value=_health("good", f"OK {name}"))
    respx.get(f"{BASE}/wp-json/wp-site-health/v1/tests/authorization-header").mock(
        return_value=_health("recommended", "L'entête d'autorisation est invalide.", "<p><a href='x'>Regénérer</a></p>"))
    respx.get(f"{BASE}/wp-json/wp-site-health/v1/tests/page-cache").mock(
        return_value=httpx.Response(500, json={"code": "boom", "message": "Erreur"}))
    G = 1024 ** 3
    respx.get(f"{BASE}/wp-json/wp-site-health/v1/directory-sizes").mock(return_value=httpx.Response(200, json={
        "wordpress_size": {"size": "4 Go", "raw": 4 * G}, "themes_size": {"size": "0,5 Go", "raw": G // 2},
        "plugins_size": {"size": "0,5 Go", "raw": G // 2}, "uploads_size": {"size": "1 Go", "raw": G},
        "database_size": {"size": "50 Mo", "raw": 50 * 1024 ** 2}, "total_size": {"size": "4,05 Go", "raw": 0}}))
    respx.get(f"{BASE}/wp-json/wp/v2/plugins").mock(return_value=httpx.Response(200, json=[
        {"plugin": "elementor/elementor", "name": "Elementor", "version": "4.3.5", "status": "active"},
        {"plugin": "elementor-pro/elementor-pro", "name": "Elementor Pro", "version": "4.2.3", "status": "active"},
    ]))
    respx.get(f"{BASE}/wp-json/wp/v2/themes").mock(return_value=httpx.Response(200, json=[
        {"stylesheet": "ashe-pro", "name": {"rendered": "Ashe Pro"}, "version": "3.9", "status": "active"}]))
    respx.get(f"{BASE}/feed/").mock(return_value=httpx.Response(
        200, text="<rss><generator>https://wordpress.org/?v=7.1.2</generator></rss>"))

    async def fake_latest(self, plugins, themes):
        assert sorted(plugins) == ["elementor", "elementor-pro"] and themes == ["ashe-pro"]
        return {"core": "7.1.3", "plugins": {"elementor": "4.4.0", "elementor-pro": None}, "themes": {"ashe-pro": None}}
    monkeypatch.setattr(W.WordPressOrg, "latest_versions", fake_latest)

    out = await S.wp_sante()
    assert out["resume"] == {"bon": 4, "recommandé": 1, "échec": 1}
    auth = next(t for t in out["tests"] if t["test"] == "authorization-header")
    assert auth["actions"] == "Regénérer" and "X-Claude-Auth" in auth["note"]
    assert out["tailles"]["autres_dans_wordpress"] == "2,00 Go" and out["tailles"]["base_de_donnees"] == "50 Mo"
    v = out["versions"]
    assert v["wordpress"] == {"installee": "7.1.2", "derniere": "7.1.3", "mise_a_jour": True}
    assert v["a_mettre_a_jour"] == ["WordPress 7.1.2 → 7.1.3", "Elementor 4.3.5 → 4.4.0"]
    assert v["hors_wordpress_org"] == ["Elementor Pro", "Ashe Pro"] and "note_hors_wordpress_org" in v


@respx.mock
async def test_sante_sans_mises_a_jour_ni_wordpress_org(monkeypatch):
    for name in S.HEALTH_TESTS:
        respx.get(f"{BASE}/wp-json/wp-site-health/v1/tests/{name}").mock(return_value=_health("good", "OK"))
    respx.get(f"{BASE}/wp-json/wp-site-health/v1/directory-sizes").mock(
        return_value=httpx.Response(403, json={"code": "rest_forbidden", "message": "Interdit"}))
    out = await S.wp_sante(mises_a_jour=False)
    assert out["resume"] == {"bon": 6} and "versions" not in out and "erreur" in out["tailles"]

