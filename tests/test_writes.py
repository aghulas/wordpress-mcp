"""Écritures v2 : verrous (désactivé, liste blanche, jeton, fraîcheur) et chaque outil, site fictif."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from wordpress_mcp import server as S
from wordpress_mcp import write_tools as WT
from wordpress_mcp.client import WordPressError, check_write_allowed

from ._helpers import BASE, ELEMENTOR_DATA, make_client


@pytest.fixture
def writes_on(monkeypatch, tmp_path):
    uploads = tmp_path / "Documents"
    uploads.mkdir()
    client = make_client(enable_writes=True, backup_dir=str(tmp_path / "sauvegardes"),
                         upload_roots=(str(uploads),), upload_max_mb=1)
    monkeypatch.setattr(S, "_client", client)
    return client


@pytest.fixture
def writes_off(monkeypatch):
    client = make_client(enable_writes=False)
    monkeypatch.setattr(S, "_client", client)
    return client


def _writes():
    return [c for c in respx.calls if c.request.method != "GET"]


# ------------------------------------------------------------------ verrous
@pytest.mark.parametrize("method,path", [("POST", "/wp/v2/posts"), ("POST", "wp/v2/posts/12"),
                                         ("POST", "/wp/v2/pages/644"), ("POST", "/wp/v2/media"),
                                         ("POST", "/wp/v2/media/9"), ("POST", "/redirection/v1/redirect"),
                                         ("POST", "/redirection/v1/bulk/redirect/disable"),
                                         ("DELETE", "/elementor/v1/cache"), ("POST", "/wp/v2/menu-items/765"),
                                         ("POST", "/wp/v2/menu-items"), ("POST", "/wp/v2/pages"),
                                         ("POST", "/wpmcp/v1/snippets"), ("POST", "/wpmcp/v1/snippets/12"),
                                         ("POST", "/wpmcp/v1/snippets/12/statut"),
                                         ("POST", "/wpmcp/v1/tablepress/tables/6/cellules"),
                                         ("DELETE", "/wp/v2/media/9")])
def test_allowed_writes(method, path):
    check_write_allowed(method, path)


@pytest.mark.parametrize("method,path", [("DELETE", "/wp/v2/posts/12"), ("DELETE", "/wp/v2/pages/9"),
                                         ("DELETE", "/wp/v2/media"), ("DELETE", "/wp/v2/media/9/edit"),
                                         ("POST", "/wp/v2/users"), ("POST", "/wp/v2/plugins"),
                                         ("POST", "/wp/v2/settings"), ("PUT", "/wp/v2/pages/644"),
                                         ("POST", "/redirection/v1/bulk/redirect/delete"),
                                         ("POST", "/wp/v2/pages/644/revisions"), ("POST", "/wp/v2/posts/12/../../users"),
                                         ("POST", "/ai1wm/v1/import"), ("POST", "/wpmcp/v1/x"),
                                         ("DELETE", "/wp/v2/menu-items/765"), ("POST", "/wp/v2/menu-items/765/../1"),
                                         ("POST", "/wp/v2/menus/5"), ("DELETE", "/wpmcp/v1/snippets/12"),
                                         ("POST", "/wpmcp/v1/snippets/12/../../x"), ("POST", "/wpmcp/v1/snippets/a"),
                                         ("POST", "/wpmcp/v1/tablepress/tables/6"),
                                         ("DELETE", "/wpmcp/v1/tablepress/tables/6/cellules"),
                                         ("POST", "/wpmcp/v1/tablepress/tables/a/cellules")])
def test_refused_writes(method, path):
    with pytest.raises(WordPressError) as e:
        check_write_allowed(method, path)
    assert e.value.code == "ecriture_refusee"


@respx.mock
async def test_client_write_refused_when_disabled(writes_off):
    with pytest.raises(WordPressError) as e:
        await writes_off.write("POST", "/wp/v2/posts", json={"title": "x"})
    assert e.value.code == "ecritures_desactivees" and not respx.calls


@respx.mock
async def test_prepare_refused_when_disabled(writes_off):
    with pytest.raises(Exception) as e:
        await WT.wp_article_preparer(titre="T", contenu="x")
    assert "WP_ENABLE_WRITES" in str(e.value) and not respx.calls


# ------------------------------------------------------------------ articles
CATS = [{"id": 3, "name": "Information", "slug": "information"}, {"id": 4, "name": "Vie de l&rsquo;école", "slug": "vie"}]


@respx.mock
async def test_article_create_confirm_and_token_single_use(writes_on):
    respx.get(f"{BASE}/wp-json/wp/v2/categories").mock(return_value=httpx.Response(200, json=CATS))
    created = respx.post(f"{BASE}/wp-json/wp/v2/posts").mock(return_value=httpx.Response(201, json={
        "id": 501, "status": "draft", "link": f"{BASE}/?p=501"}))
    prep = await WT.wp_article_preparer(titre="Kermesse", contenu="Bonjour à tous.\n\nRendez-vous <samedi>.",
                                        categories=["information"])
    assert prep["statut"].startswith("EN ATTENTE") and not _writes()
    assert prep["apercu"]["categories"] == ["Information"] and prep["avertissements"] == []
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    body = json.loads(created.calls.last.request.content)
    assert body == {"status": "draft", "title": "Kermesse", "categories": [3],
                    "content": "<p>Bonjour à tous.</p>\n<p>Rendez-vous &lt;samedi&gt;.</p>"}
    assert out["id"] == 501 and out["statut"] == "brouillon" and "preview=true" in out["apercu"]
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "jeton_invalide" and created.call_count == 1
    journal = (writes_on.settings and __import__("pathlib").Path(writes_on.settings.backup_dir) / "journal.jsonl")
    assert "Kermesse" not in journal.read_text() and '"operation": "article"' in journal.read_text()


@respx.mock
async def test_article_publish_warning_and_unknown_category(writes_on):
    respx.get(f"{BASE}/wp-json/wp/v2/categories").mock(return_value=httpx.Response(200, json=CATS))
    with pytest.raises(WordPressError) as e:
        await WT.wp_article_preparer(titre="T", contenu="x", categories=["Sport"])
    assert e.value.code == "categorie_inconnue" and "Vie de l’école" in str(e.value)
    prep = await WT.wp_article_preparer(titre="T", contenu="<p>x</p>", statut="publish")
    assert "VISIBLE IMMÉDIATEMENT" in prep["avertissements"][0]
    with pytest.raises(WordPressError):
        await WT.wp_article_preparer(titre="T", contenu="x", statut="future")


ARTICLE = {"id": 100, "status": "publish", "link": f"{BASE}/rentree/", "modified": "2026-09-01T08:00:00",
           "title": {"raw": "Rentrée"}, "content": {"raw": "<p>Texte.</p>"}, "excerpt": {"raw": ""},
           "date": "2026-09-01T08:00:00", "categories": [3], "featured_media": 0}


@respx.mock
async def test_article_update_refused_if_modified_in_between(writes_on):
    route = respx.get(f"{BASE}/wp-json/wp/v2/posts/100").mock(return_value=httpx.Response(200, json=ARTICLE))
    post = respx.post(f"{BASE}/wp-json/wp/v2/posts/100").mock(return_value=httpx.Response(200, json=ARTICLE))
    prep = await WT.wp_article_preparer(id=100, contenu="Nouveau texte.", statut="publish")
    assert prep["apercu"]["changements"]["content"] == {"avant": "Texte.", "apres": "Nouveau texte."}
    assert "title" not in prep["apercu"]["changements"]
    route.mock(return_value=httpx.Response(200, json={**ARTICLE, "modified": "2026-10-21T20:00:00"}))
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and not post.called


@respx.mock
async def test_article_update_no_change(writes_on):
    respx.get(f"{BASE}/wp-json/wp/v2/posts/100").mock(return_value=httpx.Response(200, json=ARTICLE))
    with pytest.raises(WordPressError) as e:
        await WT.wp_article_preparer(id=100, titre="Rentrée", statut="publish")
    assert e.value.code == "aucun_changement"


async def test_expired_token(monkeypatch, tmp_path):
    client = make_client(enable_writes=True, write_ttl_seconds=-1, backup_dir=str(tmp_path))
    monkeypatch.setattr(S, "_client", client)
    prep = WT._get_gate().prepare("article", {}, summary="x", preview={})
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "jeton_invalide"


# ------------------------------------------------------------------ widgets Elementor
class FakePage:
    """Page 10 dont le contenu Elementor évolue au fil des POST (relecture après écriture)."""

    def __init__(self, raw=ELEMENTOR_DATA, content="<p>Premier paragraphe.</p><p>Second&nbsp;paragraphe.</p>",
                 builder=False):
        self.raw, self.content, self.n, self.builder = raw, content, 0, builder

    def json(self):
        return {"id": 10, "status": "publish", "link": f"{BASE}/accueil/", "title": {"raw": "Accueil"},
                "modified": f"2026-10-21T10:00:0{self.n}", "content": {"raw": self.content},
                "meta": {"_elementor_data": self.raw, "_elementor_edit_mode": "builder" if self.builder else ""}}

    def get(self, request):
        return httpx.Response(200, json=self.json())

    def post(self, request):
        body = json.loads(request.content)
        self.raw = (body.get("meta") or {}).get("_elementor_data", self.raw)
        self.content = body.get("content", self.content)
        self.n += 1
        return httpx.Response(200, json=self.json())


@respx.mock
async def test_widget_text_backup_write_verify_and_restore(writes_on):
    page = FakePage()
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.get)
    post = respx.post(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.post)
    cache = respx.delete(f"{BASE}/wp-json/elementor/v1/cache").mock(return_value=httpx.Response(200, json=True))
    prep = await WT.wp_widget_texte_preparer(10, "w4", "icon_list.1.text", "Élémentaire (CP-CM2)")
    assert prep["apercu"]["avant"] == "Élémentaire" and prep["apercu"]["apres"] == "Élémentaire (CP-CM2)"
    assert "VISIBLE IMMÉDIATEMENT" in prep["avertissements"][0] and not _writes()
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    tree = json.loads(page.raw)
    assert tree[0]["elements"][0]["elements"][3]["settings"]["icon_list"][1]["text"] == "Élémentaire (CP-CM2)"
    assert tree[0]["elements"][0]["elements"][0]["settings"]["title"] == "Bienvenue &agrave; l'&eacute;cole"
    assert cache.called and out["avertissements"] == []
    backup = WT._get_gate().load_backup(out["sauvegarde"])
    assert backup["elementor_data"] == ELEMENTOR_DATA and backup["chemin"] == "/wp/v2/pages/10"
    # restauration : aperçu des widgets qui changent, puis écriture de l'ancien contenu
    prep2 = await WT.wp_page_restaurer_preparer(out["sauvegarde"])
    assert prep2["apercu"]["nombre_widgets_modifies"] == 1 and prep2["apercu"]["widgets_modifies"][0]["widget"] == "w4"
    out2 = await WT.wp_ecriture_confirmer(prep2["jeton"])
    assert json.loads(page.raw) == json.loads(ELEMENTOR_DATA) and post.call_count == 2
    assert out2["sauvegarde_avant_restauration"] != out["sauvegarde"]


@respx.mock
async def test_widget_html_field_updates_fallback_content(writes_on):
    page = FakePage()
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.get)
    respx.post(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.post)
    respx.delete(f"{BASE}/wp-json/elementor/v1/cache").mock(return_value=httpx.Response(500, json={"code": "x"}))
    prep = await WT.wp_widget_texte_preparer(10, "w2", "editor", "Nouveau paragraphe.")
    assert prep["apercu"]["apres_html"] == "<p>Nouveau paragraphe.</p>"
    assert len(prep["avertissements"]) == 1  # texte de secours mis à jour : pas d'avertissement dédié
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert page.content == "<p>Nouveau paragraphe.</p>"
    assert "cache Elementor non vidé" in out["avertissements"][0]


@respx.mock
async def test_widget_refused_if_page_changed_and_bad_targets(writes_on):
    page = FakePage()
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.get)
    post = respx.post(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.post)
    for widget, champ, code in (("zz", "title", "widget_introuvable"), ("w1", "inexistant", "champ_introuvable"),
                                ("w4", "icon_list", "champ_introuvable"), ("w1", "title", "aucun_changement")):
        with pytest.raises(WordPressError) as e:
            await WT.wp_widget_texte_preparer(10, widget, champ, "Bienvenue &agrave; l'&eacute;cole")
        assert e.value.code == code, (widget, champ)
    prep = await WT.wp_widget_texte_preparer(10, "w1", "title", "Bienvenue")
    page.n = 5  # quelqu'un a enregistré la page dans Elementor entre-temps
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and not post.called


async def test_restore_refuses_path_tricks(writes_on):
    for name in ("../journal.jsonl", "/etc/passwd", ".cache.json", "x.txt"):
        with pytest.raises(WordPressError) as e:
            await WT.wp_page_restaurer_preparer(name)
        assert e.value.code == "sauvegarde_invalide"


# ------------------------------------------------------------------ médiathèque
@respx.mock
async def test_media_upload(writes_on, tmp_path):
    f = tmp_path / "Documents" / "Règlement 2026.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    respx.get(f"{BASE}/wp-json/wp/v2/media").mock(return_value=httpx.Response(200, json=[]))
    up = respx.post(f"{BASE}/wp-json/wp/v2/media").mock(return_value=httpx.Response(201, json={
        "id": 77, "source_url": f"{BASE}/wp-content/uploads/reglement.pdf", "link": f"{BASE}/reglement/"}))
    meta = respx.post(f"{BASE}/wp-json/wp/v2/media/77").mock(return_value=httpx.Response(200, json={
        "id": 77, "source_url": f"{BASE}/wp-content/uploads/reglement.pdf", "link": f"{BASE}/reglement/"}))
    prep = await WT.wp_media_envoyer_preparer(str(f), titre="Règlement intérieur 2026")
    assert prep["apercu"]["type"] == "application/pdf" and not _writes()
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    req = up.calls.last.request
    assert req.content == b"%PDF-1.4 test" and req.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''R%C3%A8glement%202026.pdf" in req.headers["content-disposition"]
    assert json.loads(meta.calls.last.request.content) == {"title": "Règlement intérieur 2026"}
    assert out["id"] == 77 and out["url"].endswith("reglement.pdf")


@respx.mock
async def test_media_refusals(writes_on, tmp_path):
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"x")
    exe = tmp_path / "Documents" / "script.sh"
    exe.write_bytes(b"x")
    big = tmp_path / "Documents" / "gros.jpg"
    big.write_bytes(b"0" * (1024 * 1024 + 1))
    for path in (outside, exe, big, tmp_path / "Documents" / "absent.png",
                 tmp_path / "Documents" / ".." / "secret.pdf"):
        with pytest.raises(WordPressError) as e:
            await WT.wp_media_envoyer_preparer(str(path))
        assert e.value.code == "fichier_refuse", path
    ok = tmp_path / "Documents" / "photo.png"
    ok.write_bytes(b"png1")
    respx.get(f"{BASE}/wp-json/wp/v2/media").mock(return_value=httpx.Response(200, json=[]))
    up = respx.post(f"{BASE}/wp-json/wp/v2/media")
    prep = await WT.wp_media_envoyer_preparer(str(ok))
    assert any("texte alternatif" in w for w in prep["avertissements"])
    ok.write_bytes(b"png2 modifie")  # le fichier change entre l'aperçu et la confirmation
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and not up.called


# ------------------------------------------------------------------ redirections
GROUPS = {"items": [{"id": 1, "name": "Redirections", "enabled": True}, {"id": 2, "name": "Articles modifiés"}]}
RULES = [{"id": 5, "url": "/ancien", "action_data": {"url": "/nouveau"}, "enabled": True, "hits": 3},
         {"id": 6, "url": "/lorem", "action_data": {"url": "/ipsum"}, "enabled": True, "hits": 0}]


@respx.mock
async def test_redirection_create(writes_on):
    state = {"items": list(RULES)}
    respx.get(f"{BASE}/wp-json/redirection/v1/group").mock(return_value=httpx.Response(200, json=GROUPS))
    respx.get(f"{BASE}/wp-json/redirection/v1/redirect").mock(side_effect=lambda r: httpx.Response(200, json=state))

    def create(request):
        body = json.loads(request.content)
        state["items"] = state["items"] + [{"id": 9, "url": body["url"], "action_data": body["action_data"], "enabled": True}]
        return httpx.Response(200, json=state)
    post = respx.post(f"{BASE}/wp-json/redirection/v1/redirect").mock(side_effect=create)
    with pytest.raises(WordPressError) as e:
        await WT.wp_redirection_preparer("/ancien/", "/ailleurs")
    assert e.value.code == "doublon"
    for src, tgt, code in (("ancien", "/x", 301), ("/a", "/a/", 301), ("/a", "/b", 404), ("//evil.com", "/b", 301)):
        with pytest.raises(WordPressError):
            await WT.wp_redirection_preparer(src, tgt, code)
    prep = await WT.wp_redirection_preparer("https://www.wp.test/tarifs-2024", "/tarifs/")
    assert prep["apercu"] == {"source": "/tarifs-2024", "cible": "/tarifs/", "code": 301, "groupe": "Redirections"}
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    body = json.loads(post.calls.last.request.content)
    assert body["url"] == "/tarifs-2024" and body["action_code"] == 301 and body["group_id"] == 1
    assert body["match_data"]["source"]["flag_regex"] is False and out["id"] == 9


@respx.mock
async def test_redirection_disable(writes_on):
    state = {"items": [dict(r) for r in RULES]}
    respx.get(f"{BASE}/wp-json/redirection/v1/redirect").mock(side_effect=lambda r: httpx.Response(200, json=state))

    def bulk(request):
        for r in state["items"]:
            if r["id"] in json.loads(request.content)["items"]:
                r["enabled"] = False
        return httpx.Response(200, json=state)
    route = respx.post(f"{BASE}/wp-json/redirection/v1/bulk/redirect/disable").mock(side_effect=bulk)
    with pytest.raises(WordPressError):
        await WT.wp_redirection_activer_preparer([99], active=False)
    prep = await WT.wp_redirection_activer_preparer([6], active=False)
    assert prep["apercu"]["regles"][0]["source"] == "/lorem" and not route.called
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert out["regles"] == [{"id": 6, "active": False}]
    with pytest.raises(WordPressError) as e:
        await WT.wp_redirection_activer_preparer([6], active=False)
    assert e.value.code == "aucun_changement"


async def test_cancel_and_pending(writes_on):
    prep = WT._get_gate().prepare("article", {}, summary="x", preview={})
    assert (await WT.wp_ecritures_en_attente())["en_attente"][0]["jeton"] == prep["jeton"]
    assert (await WT.wp_ecriture_annuler(prep["jeton"]))["annule"] is True
    assert (await WT.wp_ecritures_en_attente())["en_attente"] == []


# ------------------------------------------------------------------ v0.3 : remplacement ciblé
LONG = ("<p>Une organisation est à respecter, en particulier le protocole sanitaire en vigueur.</p>"
        "<ul><li>L\u2019entrée est limitée à un seul parent.</li><li>En respectant les règles de\u00a0distanciation.</li></ul>"
        "<p>Pénalité : <strong>5 €</strong> le quart d\u2019heure.</p>")


def test_find_excerpt_tolerant():
    assert len(WT.find_excerpt(LONG, "règles de distanciation")) == 1
    assert len(WT.find_excerpt(LONG, "L'entrée est limitée")) == 1          # apostrophe droite vs typographique
    assert WT.find_excerpt(LONG, "Pénalité : 5 €") == []                    # traverse <strong>
    assert len(WT.find_excerpt(LONG, "le")) > 1


@respx.mock
async def test_widget_targeted_replace_keeps_rest(writes_on):
    tree = json.loads(ELEMENTOR_DATA)
    tree[0]["elements"][0]["elements"][1]["settings"]["editor"] = LONG
    page = FakePage(raw=json.dumps(tree), content=LONG)
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.get)
    respx.post(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.post)
    respx.delete(f"{BASE}/wp-json/elementor/v1/cache").mock(return_value=httpx.Response(200, json=True))
    prep = await WT.wp_widget_texte_preparer(10, "w2", "editor", "", remplacer=", en particulier le protocole sanitaire en vigueur")
    assert prep["apercu"]["mode"].startswith("remplacement ciblé")
    assert "[[, en particulier le protocole sanitaire en vigueur]]" in prep["apercu"]["avant"]
    await WT.wp_ecriture_confirmer(prep["jeton"])
    new = json.loads(page.raw)[0]["elements"][0]["elements"][1]["settings"]["editor"]
    assert new == LONG.replace(", en particulier le protocole sanitaire en vigueur", "")
    assert "<strong>5 €</strong>" in new and "\u00a0distanciation" in new          # le reste est intact
    assert page.content == new                                                      # texte de secours suivi
    with pytest.raises(WordPressError) as e:
        await WT.wp_widget_texte_preparer(10, "w2", "editor", "15 €", remplacer="Pénalité : 5 €")
    assert e.value.code == "extrait_introuvable" and "mise en forme" in str(e.value)
    with pytest.raises(WordPressError) as e:
        await WT.wp_widget_texte_preparer(10, "w2", "editor", "x", remplacer="le")
    assert e.value.code == "extrait_ambigu"
    prep = await WT.wp_widget_texte_preparer(10, "w2", "editor", "15 €", remplacer="5 €")
    assert "[[15 €]]" in prep["apercu"]["apres"]


# ------------------------------------------------------------------ v0.3 : pages, menus, images
PAGE734 = {"id": 734, "status": "publish", "link": f"{BASE}/tarification/", "modified": "2021-01-04T13:54:05",
           "title": {"raw": "Tarifs &amp; inscription"}, "content": {"raw": ""}}
MENU_ITEMS_V3 = [{"id": 765, "title": {"raw": "Tarifs &#038; inscription"}, "url": f"{BASE}/tarification",
                  "status": "publish", "type": "post_type", "object": "page", "object_id": 734, "menus": 5}]


@respx.mock
async def test_page_to_draft_warns_about_menus(writes_on):
    route = respx.get(f"{BASE}/wp-json/wp/v2/pages/734").mock(return_value=httpx.Response(200, json=PAGE734))
    respx.get(f"{BASE}/wp-json/wp/v2/menus").mock(return_value=httpx.Response(200, json=[{"id": 5, "name": "Accès rapide"}]))
    respx.get(f"{BASE}/wp-json/wp/v2/menu-items").mock(return_value=httpx.Response(200, json=MENU_ITEMS_V3))
    post = respx.post(f"{BASE}/wp-json/wp/v2/pages/734").mock(
        return_value=httpx.Response(200, json={**PAGE734, "status": "draft"}))
    prep = await WT.wp_page_preparer(734, statut="draft")
    assert prep["apercu"]["changements"] == {"statut": {"avant": "publié", "apres": "brouillon"}}
    assert any("Accès rapide › Tarifs & inscription" in w for w in prep["avertissements"])
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert json.loads(post.calls.last.request.content) == {"status": "draft"} and out["statut"] == "brouillon"
    with pytest.raises(WordPressError):
        await WT.wp_page_preparer(734, statut="trash")
    prep = await WT.wp_page_preparer(734, titre="Mentions légales")
    route.mock(return_value=httpx.Response(200, json={**PAGE734, "modified": "2026-10-21T21:00:00"}))
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and post.call_count == 1


@respx.mock
async def test_menu_entry_repoint_url_and_hide(writes_on):
    item = dict(MENU_ITEMS_V3[0])
    route = respx.get(f"{BASE}/wp-json/wp/v2/menu-items/765").mock(side_effect=lambda r: httpx.Response(200, json=item))
    respx.get(f"{BASE}/wp-json/wp/v2/pages/1043").mock(return_value=httpx.Response(200, json={
        "id": 1043, "status": "publish", "link": f"{BASE}/tarifs/"}))
    respx.get(f"{BASE}/wp-json/wp/v2/pages/751").mock(return_value=httpx.Response(200, json={"id": 751, "status": "draft"}))
    post = respx.post(f"{BASE}/wp-json/wp/v2/menu-items/765").mock(side_effect=lambda r: httpx.Response(200, json={**item, **json.loads(r.content)}))
    prep = await WT.wp_menu_entree_preparer(765, page_id=1043)
    assert prep["apercu"]["avant"]["libelle"] == "Tarifs & inscription" and prep["apercu"]["apres"]["lien"] == f"{BASE}/tarifs/"
    await WT.wp_ecriture_confirmer(prep["jeton"])
    assert json.loads(post.calls.last.request.content) == {"type": "post_type", "object": "page", "object_id": 1043}
    with pytest.raises(WordPressError):
        await WT.wp_menu_entree_preparer(765, page_id=751)                     # page non publiée
    prep = await WT.wp_menu_entree_preparer(765, url="/", libelle="Accueil")
    assert json.dumps(prep["apercu"]["apres"]["lien"]) == json.dumps(BASE + "/")
    prep2 = await WT.wp_menu_entree_preparer(765, masquer=True)
    item["status"] = "draft"                                                   # modifiée entre-temps
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep2["jeton"])
    assert e.value.code == "modifie_entre_temps"
    with pytest.raises(WordPressError):
        await WT.wp_menu_entree_preparer(765, url="javascript:alert(1)")


@respx.mock
async def test_media_alt_batch(writes_on):
    medias = {55: {"id": 55, "mime_type": "image/jpeg", "alt_text": "", "title": {"raw": "img"}, "caption": {"raw": ""},
                   "media_details": {"file": "2021/05/kermesse.jpg"}},
              56: {"id": 56, "mime_type": "application/pdf", "alt_text": "", "title": {"raw": "doc"}, "caption": {"raw": ""}}}
    for mid, m in medias.items():
        respx.get(f"{BASE}/wp-json/wp/v2/media/{mid}").mock(return_value=httpx.Response(200, json=m))
    post = respx.post(f"{BASE}/wp-json/wp/v2/media/55").mock(return_value=httpx.Response(200, json={"id": 55, "alt_text": "Élèves à la kermesse"}))
    with pytest.raises(WordPressError):
        await WT.wp_medias_alt_preparer([{"id": 56, "texte_alternatif": "x"}])
    prep = await WT.wp_medias_alt_preparer([{"id": 55, "texte_alternatif": "Élèves à la kermesse"}])
    assert prep["apercu"]["images"][0]["fichier"] == "2021/05/kermesse.jpg" and not post.called
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert json.loads(post.calls.last.request.content) == {"alt_text": "Élèves à la kermesse"}
    assert out["images"] == [{"id": 55, "texte_alternatif": "Élèves à la kermesse"}]


# ------------------------------------------------------------------ v0.4
async def test_errors_reach_the_model_through_mcp(writes_off):
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
    with pytest.raises(ToolError) as e:
        await S.mcp.call_tool("wp_page_preparer", {"id": 734, "statut": "draft"})
    assert not isinstance(e.value, UnexpectedToolError)
    assert "WP_ENABLE_WRITES" in str(e.value)                  # le message n'est plus masqué


@respx.mock
async def test_wordpress_error_message_visible(writes_on):
    from mcp.server.mcpserver.exceptions import UnexpectedToolError
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=FakePage().get)
    with pytest.raises(Exception) as e:
        await S.mcp.call_tool("wp_widget_texte_preparer", {"id": 10, "widget_id": "zz", "champ": "title",
                                                            "nouveau_texte": "x"})
    assert not isinstance(e.value, UnexpectedToolError) and "widget zz introuvable" in str(e.value)


@respx.mock
async def test_regenerate_fallback_like_elementor_update(writes_on):
    from wordpress_mcp import elementor as E
    page = FakePage(content="<p>Vieux texte : 2 classe de PS</p>", builder=True)
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.get)
    post = respx.post(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.post)
    cache = respx.delete(f"{BASE}/wp-json/elementor/v1/cache").mock(return_value=httpx.Response(200, json=True))
    prep = await WT.wp_page_regenerer_preparer(10)
    assert "2 classe de PS" in prep["apercu"]["texte_de_secours_avant"]
    assert prep["apercu"]["resume_de_partage_apres"].startswith("Bienvenue à l'école")
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert json.loads(post.calls.last.request.content) == {"content": E.build_fallback_html(ELEMENTOR_DATA)}
    assert page.raw == ELEMENTOR_DATA and cache.called and out["sauvegarde"].startswith("pages-10-")
    assert WT._get_gate().load_backup(out["sauvegarde"])["content_raw"] == "<p>Vieux texte : 2 classe de PS</p>"
    with pytest.raises(WordPressError) as e:
        await WT.wp_page_regenerer_preparer(10)
    assert e.value.code == "aucun_changement"


@respx.mock
async def test_regenerate_refused_when_not_elementor(writes_on):
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=FakePage(builder=False).get)
    with pytest.raises(WordPressError) as e:
        await WT.wp_page_regenerer_preparer(10)
    assert e.value.code == "pas_elementor"


@respx.mock
async def test_widget_edit_regenerates_fallback_when_needed(writes_on):
    from wordpress_mcp import elementor as E
    page = FakePage(content="<p>texte de secours sans la liste</p>", builder=True)
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.get)
    respx.post(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=page.post)
    respx.delete(f"{BASE}/wp-json/elementor/v1/cache").mock(return_value=httpx.Response(200, json=True))
    prep = await WT.wp_widget_texte_preparer(10, "w4", "icon_list.1.text", "Élémentaire (CP-CM2)")
    assert any("régénéré" in w for w in prep["avertissements"])
    await WT.wp_ecriture_confirmer(prep["jeton"])
    assert page.content == E.build_fallback_html(page.raw) and "Élémentaire (CP-CM2)" in page.content


# ------------------------------------------------------------------ mise en page d'un modèle
class FakeDraft:
    """Page 30 en brouillon, vide, qui reçoit la mise en page du modèle 10 (FakePage)."""

    def __init__(self, status="draft", raw="", content=""):
        self.status, self.raw, self.content, self.n, self.meta = status, raw, content, 0, {}

    def json(self):
        return {"id": 30, "status": self.status, "link": f"{BASE}/?page_id=30", "title": {"raw": "La pastorale"},
                "modified": f"2026-10-22T10:00:0{self.n}", "content": {"raw": self.content}, "template": "",
                "meta": {"_elementor_data": self.raw, "_elementor_edit_mode": self.meta.get("_elementor_edit_mode", ""),
                         "_elementor_template_type": self.meta.get("_elementor_template_type", "")}}

    def get(self, request):
        return httpx.Response(200, json=self.json())

    def post(self, request):
        body = json.loads(request.content)
        self.meta.update(body.get("meta") or {})
        self.raw = self.meta.get("_elementor_data", self.raw)
        self.content = body.get("content", self.content)
        self.n += 1
        return httpx.Response(200, json=self.json())


@respx.mock
async def test_page_modele_copies_layout_fills_texts_and_renumbers(writes_on):
    model, draft = FakePage(builder=True), FakeDraft()
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=model.get)
    respx.get(f"{BASE}/wp-json/wp/v2/pages/30").mock(side_effect=draft.get)
    post = respx.post(f"{BASE}/wp-json/wp/v2/pages/30").mock(side_effect=draft.post)
    cache = respx.delete(f"{BASE}/wp-json/elementor/v1/cache").mock(return_value=httpx.Response(200, json=True))
    prep = await WT.wp_page_modele_preparer(30, 10, textes={"w1": "La pastorale", "w2": "Premier.\n\nSecond."},
                                            exclure=["w5", "w6"])
    assert not _writes()
    assert prep["apercu"]["structure"]["par_type"] == {"column": 1, "section": 1, "widget:button": 1,
                                                        "widget:heading": 1, "widget:icon-list": 1,
                                                        "widget:text-editor": 1}
    filled = {f["widget_modele"]: f for f in prep["apercu"]["textes_remplis"]}
    assert filled["w1"]["champ"] == "title" and filled["w2"]["champ"] == "editor"
    assert filled["w1"]["widget"] != "w1" and len(filled["w1"]["widget"]) == 7
    assert any("gardent le texte du modèle" in w for w in prep["avertissements"])
    assert prep["apercu"]["resume_de_partage_apres"].startswith("La pastorale Premier. Second.")
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    body = json.loads(post.calls.last.request.content)
    assert body["meta"]["_elementor_edit_mode"] == "builder" and body["meta"]["_elementor_template_type"] == "wp-page"
    tree = json.loads(draft.raw)
    widgets = tree[0]["elements"][0]["elements"]
    assert [w["widgetType"] for w in widgets] == ["heading", "text-editor", "button", "icon-list"]
    assert widgets[0]["settings"]["title"] == "La pastorale"
    assert widgets[1]["settings"]["editor"] == "<p>Premier.</p>\n<p>Second.</p>"
    assert widgets[2]["settings"]["text"] == "Nous contacter"  # non rempli : texte du modèle
    ids = {el["id"] for el in [tree[0], tree[0]["elements"][0], *widgets]}
    assert len(ids) == 6 and not ids & {"s1", "c1", "w1", "w2", "w3", "w4"}
    assert "<h1>La pastorale</h1>" in draft.content and cache.called
    assert out["statut"] == "brouillon" and out["sauvegarde"].startswith("pages-30-")
    assert [w["widget"] for w in out["widgets"]] == [w["id"] for w in widgets]
    assert model.raw == ELEMENTOR_DATA  # le modèle n'est pas touché


@respx.mock
async def test_page_modele_refusals(writes_on):
    model = FakePage(builder=True)
    respx.get(f"{BASE}/wp-json/wp/v2/pages/10").mock(side_effect=model.get)
    respx.get(f"{BASE}/wp-json/wp/v2/pages/30").mock(side_effect=FakeDraft(status="publish").get)
    respx.get(f"{BASE}/wp-json/wp/v2/pages/31").mock(side_effect=FakeDraft().get)
    respx.get(f"{BASE}/wp-json/wp/v2/pages/14").mock(side_effect=FakePage(builder=False).get)
    post = respx.post(f"{BASE}/wp-json/wp/v2/pages/31").mock(return_value=httpx.Response(200, json={}))
    cases = [((30, 10), {}, "page_publiee"), ((31, 31), {}, "parametre"), ((31, 14), {}, "pas_elementor"),
             ((31, 10), {"exclure": ["zz"]}, "widget_introuvable"),
             ((31, 10), {"textes": {"w9": "x"}}, "widget_introuvable"),
             ((31, 10), {"textes": {"w6": "x"}}, "champ_introuvable")]
    for args, kw, code in cases:
        with pytest.raises(WordPressError) as e:
            await WT.wp_page_modele_preparer(*args, **kw)
        assert e.value.code == code, (args, kw)
    # modèle modifié entre l'aperçu et la confirmation
    prep = await WT.wp_page_modele_preparer(31, 10)
    model.raw = ELEMENTOR_DATA.replace("Nous contacter", "Contact")
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and not post.called


# ------------------------------------------------------------------ ajout d'une entrée de menu
MENU_MAIN = [
    {"id": 899, "title": {"raw": "Accueil"}, "status": "publish", "type": "custom", "object": "custom",
     "object_id": 899, "url": f"{BASE}/", "menus": 2, "parent": 0, "menu_order": 1},
    {"id": 738, "title": {"raw": "Restauration"}, "status": "publish", "type": "post_type", "object": "page",
     "object_id": 736, "url": f"{BASE}/restauration", "menus": 2, "parent": 0, "menu_order": 15},
    {"id": 743, "title": {"raw": "La pastorale"}, "status": "draft", "type": "post_type", "object": "page",
     "object_id": 726, "url": f"{BASE}/?page_id=726", "menus": 2, "parent": 0, "menu_order": 7},
]
PAGE1884 = {"id": 1884, "status": "publish", "link": f"{BASE}/menus-de-la-cantine",
            "title": {"raw": "Menus de la cantine"}}


@respx.mock
async def test_menu_entry_add_under_parent(writes_on):
    items = [dict(i) for i in MENU_MAIN]
    respx.get(f"{BASE}/wp-json/wp/v2/menus").mock(return_value=httpx.Response(
        200, json=[{"id": 2, "name": "Menu", "slug": "menu"}, {"id": 5, "name": "Accès rapide", "slug": "acces-rapide"}]))
    respx.get(f"{BASE}/wp-json/wp/v2/menu-items").mock(side_effect=lambda r: httpx.Response(200, json=items))
    respx.get(f"{BASE}/wp-json/wp/v2/pages/1884").mock(return_value=httpx.Response(200, json=PAGE1884))
    respx.get(f"{BASE}/wp-json/wp/v2/pages/726").mock(return_value=httpx.Response(
        200, json={**PAGE1884, "id": 726, "status": "draft"}))

    def create(request):
        body = json.loads(request.content)
        new = {**body, "id": 2001, "title": {"raw": body["title"]}, "url": f"{BASE}/menus-de-la-cantine"}
        items.append(new)
        return httpx.Response(201, json=new)
    post = respx.post(f"{BASE}/wp-json/wp/v2/menu-items").mock(side_effect=create)
    respx.get(f"{BASE}/wp-json/wp/v2/menu-items/2001").mock(
        side_effect=lambda r: httpx.Response(200, json=next(i for i in items if i["id"] == 2001)))
    prep = await WT.wp_menu_entree_ajouter_preparer("menu", page_id=1884, parent=738)
    assert prep["apercu"]["emplacement"] == "sous « Restauration »" and prep["apercu"]["libelle"] == "Menus de la cantine"
    assert prep["apercu"]["ordre"] == 16 and not _writes()
    assert "VISIBLE IMMÉDIATEMENT" in prep["avertissements"][0]
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    body = json.loads(post.calls.last.request.content)
    assert body == {"menus": 2, "parent": 738, "status": "publish", "type": "post_type", "object": "page",
                    "object_id": 1884, "title": "Menus de la cantine", "menu_order": 16}
    assert out["id"] == 2001 and out["annuler"] == "wp_menu_entree_preparer(id=2001, masquer=True)"
    # refus : déjà présent au même endroit, page non publiée, menu ou parent inconnus, lien sans libellé
    cases = [(("menu",), {"page_id": 1884, "parent": 738}, "deja_present"),
             (("menu",), {"page_id": 726}, "parametre"),
             (("inconnu",), {"page_id": 1884}, "parametre"),
             (("2",), {"page_id": 1884, "parent": 999}, "parametre"),
             (("2",), {"url": "/contact"}, "parametre"),
             (("2",), {}, "parametre")]
    for args, kw, code in cases:
        with pytest.raises(WordPressError) as e:
            await WT.wp_menu_entree_ajouter_preparer(*args, **kw)
        assert e.value.code == code, (args, kw)
    # menu modifié entre l'aperçu et la confirmation
    prep = await WT.wp_menu_entree_ajouter_preparer("Accès rapide", url="/contact", libelle="Contact")
    items.append({"id": 3000, "parent": 0, "menu_order": 2, "status": "publish", "object_id": 0, "url": "x"})
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and post.call_count == 1


# ------------------------------------------------------------------ création d'une page (brouillon)
@respx.mock
async def test_page_create_draft(writes_on):
    pages = [{"id": 1884, "status": "publish", "title": {"raw": "Menus de la cantine"}}]
    respx.get(f"{BASE}/wp-json/wp/v2/pages").mock(side_effect=lambda r: httpx.Response(200, json=pages))
    def create(request):
        body = json.loads(request.content)
        new = {"id": 1900, "status": body["status"], "title": {"raw": body["title"]}}
        pages.append(new)
        return httpx.Response(201, json=new)
    post = respx.post(f"{BASE}/wp-json/wp/v2/pages").mock(side_effect=create)
    prep = await WT.wp_page_creer_preparer("Applications de l'école", slug="applications-de-l-ecole")
    assert prep["apercu"]["statut"].startswith("brouillon") and not _writes()
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert json.loads(post.calls.last.request.content) == {"title": "Applications de l'école", "status": "draft",
                                                           "slug": "applications-de-l-ecole"}
    assert out["id"] == 1900 and out["statut"] == "brouillon" and "page_id=1900" in out["apercu"]
    for args, kw, code in [(("Menus de la cantine",), {}, "deja_present"), (("",), {}, "parametre"),
                           (("Autre",), {"slug": "Pas Bon!"}, "parametre")]:
        with pytest.raises(WordPressError) as e:
            await WT.wp_page_creer_preparer(*args, **kw)
        assert e.value.code == code
    # même titre créé entre l'aperçu et la confirmation
    prep = await WT.wp_page_creer_preparer("Contact")
    pages.append({"id": 1901, "status": "draft", "title": {"raw": "Contact"}})
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "deja_present" and post.call_count == 1


# ------------------------------------------------------------------ changement d'adresse (slug)
@respx.mock
async def test_page_slug_change_warns_and_checks_conflicts(writes_on):
    page728 = {"id": 728, "status": "publish", "slug": "logec", "link": f"{BASE}/logec",
               "modified": "2026-09-07T21:12:05", "title": {"raw": "L’OGEC"}}
    respx.get(f"{BASE}/wp-json/wp/v2/pages/728").mock(side_effect=lambda r: httpx.Response(200, json=page728))

    def by_slug(request):
        s = request.url.params.get("slug")
        return httpx.Response(200, json=[{"id": 30, "title": {"raw": "Contact"}}] if s == "contact" else [])
    respx.get(f"{BASE}/wp-json/wp/v2/pages").mock(side_effect=by_slug)
    post = respx.post(f"{BASE}/wp-json/wp/v2/pages/728").mock(
        side_effect=lambda r: httpx.Response(200, json={**page728, **json.loads(r.content), "link": f"{BASE}/ogec"}))
    prep = await WT.wp_page_preparer(728, slug="ogec")
    assert prep["apercu"]["changements"]["adresse"] == {"avant": f"{BASE}/logec", "apres": f"{BASE}/ogec"}
    assert any("wp_redirection_preparer(source='/logec', cible='/ogec')" in w for w in prep["avertissements"])
    await WT.wp_ecriture_confirmer(prep["jeton"])
    assert json.loads(post.calls.last.request.content) == {"slug": "ogec"}
    for kw, code in [({"slug": "contact"}, "deja_present"), ({"slug": "Mauvais Slug"}, "parametre"),
                     ({"slug": "logec"}, "aucun_changement"), ({}, "parametre")]:
        with pytest.raises(WordPressError) as e:
            await WT.wp_page_preparer(728, **kw)
        assert e.value.code == code, kw


# ------------------------------------------------------------------ snippets WPCode
import hashlib


def _h(code):
    return hashlib.sha256(code.encode()).hexdigest()


class FakeSnippets:
    """Routes wpmcp/v1/snippets simulées (comportement du snippet docs/wpcode-snippet-gestion.php)."""

    def __init__(self):
        self.items = {5: {"id": 5, "titre": "MCP Gestion des snippets", "actif": True, "code": "// gestion\n"},
                      7: {"id": 7, "titre": "MCP Auth", "actif": True, "code": "// auth\n"},
                      9: {"id": 9, "titre": "Forminator lecture", "actif": False, "code": "// form\n"}}
        self.next = 20
        respx.get(url__regex=rf"{BASE}/wp-json/wpmcp/v1/snippets(/\d+)?$").mock(side_effect=self.get)
        self.post = respx.post(url__regex=rf"{BASE}/wp-json/wpmcp/v1/snippets(/\d+)?(/statut)?$").mock(
            side_effect=self.write)

    def view(self, it, code=False):
        v = {"id": it["id"], "titre": it["titre"], "actif": it["actif"], "type": "php", "emplacement": "everywhere",
             "modifie": "2026-10-22 10:00:00", "sha256": _h(it["code"]), "taille": len(it["code"]),
             "protege": it["titre"] in ("MCP Gestion des snippets", "MCP Auth"), "erreur": False}
        if code:
            v["code"] = it["code"]
        return v

    def get(self, request):
        parts = request.url.path.rstrip("/").split("/")
        if parts[-1].isdigit():
            return httpx.Response(200, json=self.view(self.items[int(parts[-1])], code=True))
        return httpx.Response(200, json=[self.view(i) for i in self.items.values()])

    def write(self, request):
        body = json.loads(request.content)
        parts = request.url.path.rstrip("/").split("/")
        if parts[-1] == "statut":
            it = self.items[int(parts[-2])]
            it["actif"] = bool(body["actif"])
            return httpx.Response(200, json=self.view(it))
        if parts[-1].isdigit():
            it = self.items[int(parts[-1])]
            assert body["sha256_actuel"] == _h(it["code"]) and body["sha256"] == _h(body["code"])
            it["code"] = body["code"]
            return httpx.Response(200, json={**self.view(it), "integre": True})
        assert body["titre"].startswith("MCP ") and body["sha256"] == _h(body["code"])
        it = {"id": self.next, "titre": body["titre"], "actif": False, "code": body["code"]}
        self.items[self.next] = it
        self.next += 1
        return httpx.Response(200, json={**self.view(it), "integre": True})


@pytest.fixture
def snippets_dir(writes_on, tmp_path, monkeypatch):
    d = tmp_path / "docs"
    d.mkdir()
    import dataclasses
    monkeypatch.setattr(writes_on, "settings", dataclasses.replace(writes_on.settings, snippets_dir=str(d)))
    return d


@respx.mock
async def test_snippet_install_create_then_update_then_activate(snippets_dir):
    fake = FakeSnippets()
    f = snippets_dir / "wpcode-snippet-securite.php"
    f.write_text("<?php\n/** Sécurité */\nadd_action('init', function () { $r = '\\d+'; });\n", encoding="utf-8")
    code = "/** Sécurité */\nadd_action('init', function () { $r = '\\d+'; });\n"
    prep = await WT.wp_snippet_installer_preparer("wpcode-snippet-securite.php", "MCP Sécurité")
    assert prep["apercu"]["code_complet"] == code and prep["apercu"]["operation"].startswith("création")
    assert any("INACTIF" in w for w in prep["avertissements"]) and not _writes()
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert out["snippet"]["id"] == 20 and fake.items[20]["code"] == code and not fake.items[20]["actif"]
    # même fichier, même code : rien à faire
    with pytest.raises(WordPressError) as e:
        await WT.wp_snippet_installer_preparer("wpcode-snippet-securite.php", "MCP Sécurité")
    assert e.value.code == "deja_a_jour"
    # activation
    prep = await WT.wp_snippet_activer_preparer(20, True)
    assert prep["apercu"]["code_complet"] == code
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert fake.items[20]["actif"] and out["statut"] == "activé"
    # mise à jour d'un snippet actif : différences + sauvegarde
    f.write_text("<?php\n/** Sécurité v2 */\nadd_action('init', function () { $r = '\\d+'; });\n", encoding="utf-8")
    prep = await WT.wp_snippet_installer_preparer("wpcode-snippet-securite.php", "MCP Sécurité")
    assert "-/** Sécurité */" in prep["apercu"]["differences"] and "+/** Sécurité v2 */" in prep["apercu"]["differences"]
    assert prep["avertissements"][0].startswith("Snippet ACTIF")
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert "v2" in fake.items[20]["code"] and out["sauvegarde"].startswith("snippet-20-")
    # fichier modifié entre l'aperçu et la confirmation
    f.write_text("<?php\n// v3\n", encoding="utf-8")
    prep = await WT.wp_snippet_installer_preparer("wpcode-snippet-securite.php", "MCP Sécurité")
    f.write_text("<?php\n// v4 non relue\n", encoding="utf-8")
    n = fake.post.call_count
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and fake.post.call_count == n


@respx.mock
async def test_snippet_refusals(snippets_dir):
    fake = FakeSnippets()
    (snippets_dir / "wpcode-snippet-x.php").write_text("<?php\n// x\n", encoding="utf-8")
    cases = [(("../secret.php", "MCP X"), "parametre"), (("/etc/passwd", "MCP X"), "parametre"),
             (("wpcode-snippet-absent.php", "MCP X"), "introuvable"), (("wpcode-snippet-x.php", "Sécurité"), "parametre"),
             (("wpcode-snippet-x.php", "MCP Auth"), "snippet_protege")]
    for args, code in cases:
        with pytest.raises(WordPressError) as e:
            await WT.wp_snippet_installer_preparer(*args)
        assert e.value.code == code, args
    with pytest.raises(WordPressError) as e:
        await WT.wp_snippet_activer_preparer(5, False)
    assert e.value.code == "snippet_protege"
    with pytest.raises(WordPressError) as e:
        await WT.wp_snippet_activer_preparer(7, True)
    assert e.value.code == "deja_a_jour"
    # un snippet non « MCP » peut être activé (ex. lecture Forminator), avec aperçu du code
    prep = await WT.wp_snippet_activer_preparer(9, True)
    fake.items[9]["code"] = "// changé\n"
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and not _writes()


@respx.mock
async def test_snippets_missing_routes(snippets_dir):
    (snippets_dir / "wpcode-snippet-x.php").write_text("<?php\n// x\n", encoding="utf-8")
    respx.get(f"{BASE}/wp-json/wpmcp/v1/snippets").mock(return_value=httpx.Response(
        404, json={"code": "rest_no_route", "message": "Aucune route", "data": {"status": 404}}))
    out = await S.wp_snippets()
    assert out["snippets"] == [] and "wpcode-snippet-gestion.php" in out["note"]
    with pytest.raises(WordPressError) as e:
        await WT.wp_snippet_installer_preparer("wpcode-snippet-x.php", "MCP X")
    assert e.value.code == "snippets_indisponibles"



# ------------------------------------------------------------------ extraits
@respx.mock
async def test_extraits_batch(writes_on):
    pages = {676: {"id": 676, "status": "publish", "title": {"raw": "Notre école"}, "excerpt": {"raw": ""}},
             749: {"id": 749, "status": "publish", "title": {"raw": "Les horaires"}, "excerpt": {"raw": "Ancien"}}}
    respx.get(url__regex=rf"{BASE}/wp-json/wp/v2/pages/\d+(\?.*)?$").mock(
        side_effect=lambda r: httpx.Response(200, json=pages[int(r.url.path.rsplit("/", 1)[1])]))

    def write(request):
        pid = int(request.url.path.rsplit("/", 1)[1])
        pages[pid]["excerpt"] = {"raw": json.loads(request.content)["excerpt"]}
        return httpx.Response(200, json=pages[pid])
    post = respx.post(url__regex=rf"{BASE}/wp-json/wp/v2/pages/\d+(\?.*)?$").mock(side_effect=write)
    long = "x" * 170
    prep = await WT.wp_extraits_preparer([{"id": 676, "extrait": "  L'école   accueille 400 élèves. "},
                                          {"id": 749, "extrait": long}])
    rows = prep["apercu"]["extraits"]
    assert rows[0]["avant"] == "(résumé automatique)" and rows[0]["apres"] == "L'école accueille 400 élèves."
    assert any("170 caractères" in w for w in prep["avertissements"]) and not _writes()
    out = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert pages[676]["excerpt"]["raw"] == "L'école accueille 400 élèves." and len(out["extraits_ecrits"]) == 2
    for elements, code in [([{"id": 676, "extrait": "<b>gras</b>"}], "parametre"),
                           ([{"id": 676, "extrait": "y" * 301}], "parametre"),
                           ([{"id": 676, "extrait": "L'école accueille 400 élèves."}], "aucun_changement"),
                           ([], "parametre")]:
        with pytest.raises(WordPressError) as e:
            await WT.wp_extraits_preparer(elements)
        assert e.value.code == code
    # extrait modifié entre l'aperçu et la confirmation
    prep = await WT.wp_extraits_preparer([{"id": 749, "extrait": "Horaires de l'école."}])
    pages[749]["excerpt"] = {"raw": "Changé ailleurs"}
    n = post.call_count
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert post.call_count == n


class FakeTablePress:
    """Routes wpmcp/v1/tablepress simulées (comportement de docs/wpcode-snippet-tablepress.php)."""

    def __init__(self):
        self.cells = [["GARDERIE/ETUDE – Tarif", ""], ["Matin<br>7h 30", "215€"], ["Présence : 5.50€", "9€"]]
        self.saved = None
        respx.get(url__regex=rf"{BASE}/wp-json/wpmcp/v1/tablepress/tables(/\d+)?$").mock(side_effect=self.get)
        self.post = respx.post(url__regex=rf"{BASE}/wp-json/wpmcp/v1/tablepress/tables/\d+/cellules$").mock(
            side_effect=self.write)

    def sha(self):
        return _h(json.dumps(self.cells))

    def get(self, request):
        if request.url.path.rstrip("/").split("/")[-1] == "tables":
            return httpx.Response(200, json=[{"id": "6", "nom": "Garderie", "lignes": 3, "colonnes": 2}])
        if not request.url.path.endswith("/6"):
            return httpx.Response(404, json={"code": "tableau_introuvable", "message": "introuvable"})
        return httpx.Response(200, json={"id": "6", "nom": "Garderie", "cellules": self.cells, "sha256": self.sha()})

    def write(self, request):
        body = json.loads(request.content)
        if body["sha256_actuel"] != self.sha():
            return httpx.Response(409, json={"code": "modifie_entre_temps", "message": "changé"})
        for c in body["cellules"]:
            self.cells[c["ligne"] - 1][c["colonne"] - 1] = c["valeur"]
        self.saved = body
        return httpx.Response(200, json={"id": "6", "cellules": self.cells, "sha256": self.sha(), "integre": True})


@respx.mock
async def test_tableaux_read_and_hint_when_snippet_missing(writes_on):
    FakeTablePress()
    out = await S.wp_tableaux()
    assert out["nombre"] == 1
    t = await S.wp_tableaux(6)
    assert t["lignes_numerotees"][1] == {"ligne": 2, "cellules": {"1": "Matin<br>7h 30", "2": "215€"}}
    assert t["lignes_numerotees"][0]["cellules"] == {"1": "GARDERIE/ETUDE – Tarif"}  # cellules vides omises


@respx.mock
async def test_tableaux_snippet_missing_note(writes_on):
    respx.get(f"{BASE}/wp-json/wpmcp/v1/tablepress/tables").mock(
        return_value=httpx.Response(404, json={"code": "rest_no_route", "message": "x"}))
    out = await S.wp_tableaux()
    assert "wpcode-snippet-tablepress.php" in out["note"]


@respx.mock
async def test_tableau_cellules_prepare_confirm(writes_on):
    fake = FakeTablePress()
    prep = await WT.wp_tableau_cellules_preparer(6, [
        {"ligne": 1, "colonne": 1, "remplacer": "ETUDE", "nouveau": "ÉTUDE"},
        {"ligne": 3, "colonne": 1, "remplacer": "5.50€", "nouveau": "5,50 €"},
        {"ligne": 2, "colonne": 2, "nouveau": "215 €"},
    ])
    assert not fake.post.called
    assert [(c["ligne"], c["colonne"]) for c in prep["apercu"]["cellules"]] == [(1, 1), (2, 2), (3, 1)]
    assert prep["apercu"]["cellules"][2]["apres"] == "Présence : 5,50 €"
    res = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert res["cellules_corrigees"] == 3 and res["sauvegarde"].startswith("tableau-6-")
    assert fake.cells[0][0] == "GARDERIE/ÉTUDE – Tarif" and fake.cells[1][0] == "Matin<br>7h 30"


@respx.mock
async def test_tableau_cellules_refusals(writes_on):
    fake = FakeTablePress()
    with pytest.raises(WordPressError) as e:  # extrait absent
        await WT.wp_tableau_cellules_preparer(6, [{"ligne": 2, "colonne": 2, "remplacer": "999", "nouveau": "x"}])
    assert e.value.code == "extrait_introuvable"
    with pytest.raises(WordPressError):  # cellule inexistante
        await WT.wp_tableau_cellules_preparer(6, [{"ligne": 9, "colonne": 1, "nouveau": "x"}])
    with pytest.raises(WordPressError):  # script refusé
        await WT.wp_tableau_cellules_preparer(6, [{"ligne": 2, "colonne": 2, "nouveau": "<script>x</script>"}])
    with pytest.raises(WordPressError) as e:  # aucun changement
        await WT.wp_tableau_cellules_preparer(6, [{"ligne": 2, "colonne": 2, "remplacer": "215€", "nouveau": "215€"}])
    assert e.value.code == "aucun_changement"
    prep = await WT.wp_tableau_cellules_preparer(6, [{"ligne": 2, "colonne": 2, "nouveau": "215 €"}])
    fake.cells[2][1] = "10€"  # modifié entre l'aperçu et la confirmation
    with pytest.raises(WordPressError) as e:
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert e.value.code == "modifie_entre_temps" and not fake.post.called


class FakeMediaDelete:
    """Médiathèque simulée pour la suppression : médias 9 (libre) et 11 (image à la une de la page 10)."""

    def __init__(self):
        self.items = {9: {"id": 9, "type": "attachment", "media_type": "image", "mime_type": "image/png",
                          "title": {"raw": "Illustration", "rendered": "Illustration"}, "alt_text": "x", "modified": "2026-10-23T10:00:00",
                          "source_url": f"{BASE}/wp-content/uploads/2026/10/illu.png", "media_details": {"file": "2026/10/illu.png", "filesize": 2048},
                          "post": None, "date": "2026-10-23T10:00:00"},
                      11: {"id": 11, "type": "attachment", "media_type": "image", "mime_type": "image/jpeg",
                           "title": {"raw": "Une", "rendered": "Une"}, "alt_text": "", "modified": "2026-10-23T10:00:00",
                           "source_url": f"{BASE}/wp-content/uploads/2026/10/une.jpg", "media_details": {"file": "2026/10/une.jpg", "filesize": 10},
                           "post": 10, "date": "2026-10-23T10:00:00"}}
        self.deleted = []
        self.file_ok = True
        respx.get(url__regex=rf"{BASE}/wp-json/wp/v2/media/\d+(\?.*)?$").mock(side_effect=self.get)
        respx.delete(url__regex=rf"{BASE}/wp-json/wp/v2/media/\d+(\?.*)?$").mock(side_effect=self.delete)
        respx.get(url__regex=rf"{BASE}/wp-json/wp/v2/(posts|pages)(\?.*)?$").mock(side_effect=self.listing)
        respx.get(url__regex=rf"{BASE}/wp-json/wp/v2/mec-events(\?.*)?$").mock(
            return_value=httpx.Response(404, json={"code": "rest_no_route", "message": "x"}))
        respx.get(url__regex=rf"{BASE}/wp-json/wp/v2/search(\?.*)?$").mock(side_effect=self.search)
        respx.get(url__regex=rf"{BASE}/wp-content/uploads/.*").mock(side_effect=self.file)

    def get(self, request):
        mid = int(request.url.path.rstrip("/").split("/")[-1])
        if mid not in self.items:
            return httpx.Response(404, json={"code": "rest_post_invalid_id", "message": "introuvable"})
        return httpx.Response(200, json=self.items[mid])

    def listing(self, request):
        if request.url.path.endswith("/pages"):
            return httpx.Response(200, json=[{"id": 10, "type": "page", "title": {"rendered": "Accueil"}, "featured_media": 11}],
                                  headers={"X-WP-TotalPages": "1"})
        return httpx.Response(200, json=[], headers={"X-WP-TotalPages": "1"})

    def search(self, request):
        if "illu" in str(request.url):
            return httpx.Response(200, json=[{"id": 12, "subtype": "post", "title": "Carnaval"}])
        return httpx.Response(200, json=[])

    def file(self, request):
        return httpx.Response(200, content=b"PNGDATA") if self.file_ok else httpx.Response(500, text="boom")

    def delete(self, request):
        assert "force=true" in str(request.url)
        mid = int(request.url.path.rstrip("/").split("/")[-1])
        self.deleted.append(mid)
        return httpx.Response(200, json={"deleted": True, "previous": self.items.pop(mid)})


@respx.mock
async def test_medias_supprimer_prepare_confirm_with_backup(writes_on):
    fake = FakeMediaDelete()
    prep = await WT.wp_medias_supprimer_preparer([9], motif="illustration remplacée")
    assert prep["apercu"]["medias"][0]["fichier"] == "illu.png"
    assert any("DÉFINITIVE" in w for w in prep["avertissements"])
    assert any("Carnaval" in w for w in prep["avertissements"])  # nom cité dans un contenu : avertissement
    assert fake.deleted == []
    res = await WT.wp_ecriture_confirmer(prep["jeton"])
    assert res["supprimes"] == [9] and fake.deleted == [9]
    copie = res["copies_locales"][0]
    assert copie.endswith("9-illu.png") and open(copie, "rb").read() == b"PNGDATA"


@respx.mock
async def test_medias_supprimer_refuses_featured_image(writes_on):
    FakeMediaDelete()
    with pytest.raises(WordPressError) as e:
        await WT.wp_medias_supprimer_preparer([11])
    assert e.value.code == "media_utilise" and "Accueil" in str(e.value)


@respx.mock
async def test_medias_supprimer_aborts_if_backup_fails(writes_on):
    fake = FakeMediaDelete()
    prep = await WT.wp_medias_supprimer_preparer([9])
    fake.file_ok = False
    with pytest.raises(WordPressError):
        await WT.wp_ecriture_confirmer(prep["jeton"])
    assert fake.deleted == []  # rien supprimé sans copie locale


@respx.mock
async def test_medias_supprimer_refusals(writes_on):
    FakeMediaDelete()
    with pytest.raises(WordPressError):
        await WT.wp_medias_supprimer_preparer([])
    with pytest.raises(WordPressError):
        await WT.wp_medias_supprimer_preparer([9, 9])
    with pytest.raises(WordPressError):
        await WT.wp_medias_supprimer_preparer([999])
