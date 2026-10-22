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
                                         ("DELETE", "/elementor/v1/cache"), ("POST", "/wp/v2/menu-items/765")])
def test_allowed_writes(method, path):
    check_write_allowed(method, path)


@pytest.mark.parametrize("method,path", [("DELETE", "/wp/v2/posts/12"), ("DELETE", "/wp/v2/media/9"),
                                         ("POST", "/wp/v2/users"), ("POST", "/wp/v2/plugins"),
                                         ("POST", "/wp/v2/settings"), ("PUT", "/wp/v2/pages/644"),
                                         ("POST", "/redirection/v1/bulk/redirect/delete"),
                                         ("POST", "/wp/v2/pages"), ("POST", "/wp/v2/posts/12/../../users"),
                                         ("POST", "/ai1wm/v1/import"), ("POST", "/wpmcp/v1/x"),
                                         ("DELETE", "/wp/v2/menu-items/765"), ("POST", "/wp/v2/menu-items"),
                                         ("POST", "/wp/v2/menus/5")])
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
