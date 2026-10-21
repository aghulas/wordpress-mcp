"""Outils d'écriture (v2) : préparer → aperçu → confirmer. Voir writes.py pour les verrous.

Outils : wp_article_preparer, wp_widget_texte_preparer, wp_page_restaurer_preparer,
wp_page_preparer, wp_menu_entree_preparer, wp_media_envoyer_preparer, wp_medias_alt_preparer,
wp_redirection_preparer, wp_redirection_activer_preparer, wp_ecriture_confirmer,
wp_ecriture_annuler, wp_ecritures_en_attente.

Chaque *_preparer ne fait que des lectures ; l'écriture n'a lieu que dans un exécuteur
(`EXECUTORS`), appelé par wp_ecriture_confirmer avec un jeton valide.
"""
from __future__ import annotations

import copy
import hashlib
import html
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlsplit

from . import elementor as E
from .client import WordPressClient, WordPressError
from .server import _get_client, _raw, _rendered, mcp
from .writes import ConfirmationError, PendingWrite, WriteGate, WritesDisabledError, fingerprint

_gate: WriteGate | None = None

POST_STATUSES = {"draft": "brouillon", "pending": "en attente de relecture", "publish": "publié",
                 "future": "programmé", "private": "privé"}
UPLOAD_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
                ".webp": "image/webp", ".pdf": "application/pdf"}
REDIRECT_CODES = {301, 302, 307, 308}
CONTENT_PATHS = {"page": "/wp/v2/pages", "article": "/wp/v2/posts", "post": "/wp/v2/posts"}


def _get_gate() -> WriteGate:
    global _gate
    if _gate is None or _gate.settings is not _get_client().settings:
        _gate = WriteGate(_get_client().settings)
    return _gate


def _text_to_html(text: str) -> str:
    """Texte brut → paragraphes HTML (ligne vide = nouveau paragraphe, retour à la ligne = <br>)."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return "\n".join("<p>" + html.escape(b).replace("\n", "<br>") + "</p>" for b in blocks)


_HTML_TAG_RE = re.compile(r"</?(p|br|hr|ul|ol|li|a|strong|em|b|i|u|h[1-6]|div|span|img|table|thead|tbody|tr|td|th|"
                          r"blockquote|figure|figcaption|iframe|sup|sub)\b[^>]*>", re.I)


def _as_html(contenu: str) -> str:
    """HTML s'il contient de vraies balises HTML, sinon texte brut converti en paragraphes."""
    return contenu if _HTML_TAG_RE.search(contenu) else _text_to_html(contenu)


def _short(text: str, limit: int = 600) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"… ({len(text)} caractères)"


_SPACES = r"(?:\s|&nbsp;|&#160;|\xa0)+"


def find_excerpt(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Positions de `needle` dans `haystack`, en tolérant les variantes d'espaces (insécables,
    &nbsp;) et d'apostrophes (' ’). Le texte cherché ne doit pas traverser une balise HTML."""
    tokens = [t for t in re.split(r"\s+", (needle or "").strip()) if t]
    if not tokens:
        return []
    def tok(t: str) -> str:
        return re.sub(r"\\?['’]", "['’]", re.escape(t))
    pattern = _SPACES.join(tok(t) for t in tokens)
    return [m.span() for m in re.finditer(pattern, haystack)]


def _context(text: str, start: int, end: int, width: int = 90) -> str:
    left = E.html_to_text(text[max(0, start - width * 3):start])[-width:]
    right = E.html_to_text(text[end:end + width * 3])[:width]
    sep_l = " " if start > 0 and (text[start - 1].isspace() or text[start - 1] == "\xa0") else ""
    sep_r = " " if end < len(text) and (text[end].isspace() or text[end] == "\xa0") else ""
    return f"…{left}{sep_l}[[{E.html_to_text(text[start:end])}]]{sep_r}{right}…"


def _stale_error(what: str) -> WordPressError:
    return WordPressError(f"{what} a été modifié depuis l'aperçu : écriture annulée. Refaire la préparation "
                          f"pour voir la version actuelle.", code="modifie_entre_temps")


# ======================================================================= articles
async def _resolve_categories(client: WordPressClient, categories: list[str | int]) -> list[dict[str, Any]]:
    """Noms ou identifiants → catégories existantes (aucune création)."""
    existing = await client.get_all("/wp/v2/categories", {"context": "view", "hide_empty": False})
    out = []
    for c in categories:
        key = str(c).strip()
        match = next((x for x in existing if str(x.get("id")) == key
                      or str(x.get("name", "")).strip().lower() == key.lower()
                      or str(x.get("slug", "")).lower() == key.lower()), None)
        if match is None:
            raise WordPressError(f"catégorie inconnue : « {key} ». Catégories existantes : "
                                 + ", ".join(sorted(html.unescape(x.get("name", "")) for x in existing)),
                                 code="categorie_inconnue")
        out.append({"id": match["id"], "nom": html.unescape(match.get("name", ""))})
    return out


@mcp.tool()
async def wp_article_preparer(titre: str | None = None, contenu: str | None = None, statut: str = "draft",
                              id: int | None = None, categories: list[str] | None = None,
                              image_une: int | None = None, extrait: str | None = None,
                              date: str | None = None) -> dict[str, Any]:
    """PRÉPARE la création (sans `id`) ou la modification (avec `id`) d'un article ; n'écrit rien.
    `contenu` : HTML, ou texte brut (ligne vide = nouveau paragraphe). `statut` : draft (défaut),
    pending, publish (visible tout de suite), future (avec `date` 'AAAA-MM-JJTHH:MM:SS'), private.
    `categories` : noms ou identifiants existants. `image_une` : identifiant d'un média existant.
    En modification, seuls les champs fournis changent. Renvoie un aperçu et un jeton pour
    wp_ecriture_confirmer."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    if statut not in POST_STATUSES:
        raise WordPressError(f"statut inconnu : {statut} (choix : {', '.join(POST_STATUSES)})", code="parametre")
    if statut == "future" and not date:
        raise WordPressError("statut future : préciser `date` (AAAA-MM-JJTHH:MM:SS)", code="parametre")
    payload: dict[str, Any] = {"status": statut}
    if titre is not None:
        payload["title"] = titre.strip()
    if contenu is not None:
        payload["content"] = _as_html(contenu)
    if extrait is not None:
        payload["excerpt"] = extrait.strip()
    if date:
        payload["date"] = date
    cats = await _resolve_categories(client, categories) if categories else []
    if cats:
        payload["categories"] = [c["id"] for c in cats]
    if image_une is not None:
        media = await client.get(f"/wp/v2/media/{int(image_une)}")
        payload["featured_media"] = int(image_une)
        image_label = f"{_rendered(media.get('title'))} ({media.get('source_url')})"
    warnings: list[str] = []
    if statut == "publish":
        warnings.append("L'article sera VISIBLE IMMÉDIATEMENT sur le site public après confirmation.")
    guard: dict[str, Any] = {}
    if id is None:
        if not payload.get("title") or not payload.get("content"):
            raise WordPressError("création : `titre` et `contenu` sont obligatoires", code="parametre")
        preview: dict[str, Any] = {"action": "créer un article", "statut": POST_STATUSES[statut],
                                   "titre": payload["title"], "texte": _short(E.html_to_text(payload["content"]))}
    else:
        cur = await client.get(f"/wp/v2/posts/{int(id)}", {"context": "edit"})
        guard = {"modified": cur.get("modified"), "content": fingerprint(_raw(cur.get("content")))}
        preview = {"action": f"modifier l'article {int(id)}", "lien": cur.get("link"), "changements": {}}
        before = {"title": _raw(cur.get("title")), "content": E.html_to_text(_raw(cur.get("content"))),
                  "excerpt": E.html_to_text(_raw(cur.get("excerpt"))), "status": cur.get("status"),
                  "date": cur.get("date"), "categories": cur.get("categories"),
                  "featured_media": cur.get("featured_media")}
        for key, val in payload.items():
            new = E.html_to_text(val) if key in ("content", "excerpt") else val
            if new != before.get(key):
                preview["changements"][key] = {"avant": _short(str(before.get(key))), "apres": _short(str(new))}
        if not preview["changements"]:
            raise WordPressError("aucun changement par rapport à l'article actuel", code="aucun_changement")
        if cur.get("status") == "publish":
            warnings.append("Article déjà publié : la modification sera visible immédiatement.")
    if cats:
        preview["categories"] = [c["nom"] for c in cats]
    if image_une is not None:
        preview["image_une"] = image_label
    summary = (f"{'Créer' if id is None else 'Modifier'} l'article « {payload.get('title') or id} » "
               f"({POST_STATUSES[statut]})")
    return gate.prepare("article", {"id": id, "payload": payload}, summary=summary, preview=preview,
                        guard=guard, warnings=warnings)


async def _exec_article(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    post_id = p.args.get("id")
    if post_id is not None:
        cur = await client.get(f"/wp/v2/posts/{int(post_id)}", {"context": "edit"})
        if (cur.get("modified") != p.guard.get("modified")
                or fingerprint(_raw(cur.get("content"))) != p.guard.get("content")):
            raise _stale_error(f"L'article {post_id}")
    path = "/wp/v2/posts" if post_id is None else f"/wp/v2/posts/{int(post_id)}"
    data = await client.write("POST", path, json=p.args["payload"])
    gate.journal("article", True, id=data.get("id"), statut=data.get("status"))
    out = {"id": data.get("id"), "statut": POST_STATUSES.get(data.get("status"), data.get("status")),
           "lien": data.get("link")}
    if data.get("status") != "publish":
        out["apercu"] = f"{client.settings.base_url}/?p={data.get('id')}&preview=true"
    out["modifier_dans_wp_admin"] = f"{client.settings.base_url}/wp-admin/post.php?post={data.get('id')}&action=edit"
    return out


# ======================================================================= pages Elementor
def _content_path(type_contenu: str, item_id: int) -> str:
    base = CONTENT_PATHS.get(type_contenu)
    if not base:
        raise WordPressError(f"type inconnu : {type_contenu} (page ou article)", code="parametre")
    return f"{base}/{int(item_id)}"


async def _read_elementor(client: WordPressClient, path: str) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    item = await client.get(path, {"context": "edit"})
    raw = (item.get("meta") or {}).get("_elementor_data") or ""
    tree = E.parse_elementor_data(raw)
    if not tree:
        raise WordPressError("ce contenu n'est pas construit avec Elementor (ou contenu Elementor illisible)",
                             code="pas_elementor")
    return item, raw, tree


def _guard_of(item: dict[str, Any], raw: str) -> dict[str, Any]:
    return {"modified": item.get("modified"), "elementor": fingerprint(raw)}


async def _check_guard(client: WordPressClient, path: str, guard: dict[str, Any], what: str):
    item, raw, tree = await _read_elementor(client, path)
    if item.get("modified") != guard.get("modified") or fingerprint(raw) != guard.get("elementor"):
        raise _stale_error(what)
    return item, raw, tree


def _backup_payload(path: str, item: dict[str, Any], raw: str) -> dict[str, Any]:
    return {"chemin": path, "id": item.get("id"), "titre": _raw(item.get("title")), "lien": item.get("link"),
            "modifie": item.get("modified"), "elementor_data": raw, "content_raw": _raw(item.get("content"))}


async def _write_elementor(client: WordPressClient, path: str, new_raw: str, content_raw: str | None) -> list[str]:
    """Écrit le contenu Elementor, vérifie la relecture, vide le cache Elementor. Renvoie des avertissements."""
    payload: dict[str, Any] = {"meta": {"_elementor_data": new_raw}}
    if content_raw is not None:
        payload["content"] = content_raw
    await client.write("POST", path, json=payload)
    check = await client.get(path, {"context": "edit"})
    if E.parse_elementor_data((check.get("meta") or {}).get("_elementor_data")) != E.parse_elementor_data(new_raw):
        raise WordPressError("le contenu relu après écriture ne correspond pas : vérifier la page et la "
                             "restaurer depuis la sauvegarde si besoin", code="verification_echouee")
    try:
        await client.write("DELETE", "/elementor/v1/cache")
        return []
    except WordPressError as e:
        return [f"cache Elementor non vidé ({e}) : le vider dans Elementor › Outils si l'ancien texte reste affiché"]


@mcp.tool()
async def wp_widget_texte_preparer(id: int, widget_id: str, champ: str, nouveau_texte: str,
                                   type_contenu: str = "page", remplacer: str | None = None) -> dict[str, Any]:
    """PRÉPARE le remplacement du texte d'un widget Elementor d'une page (ou d'un article) ; n'écrit
    rien. Repérer `widget_id` et `champ` avec wp_contenu_lire(format='elementor') : `champ` = clé du
    réglage (`title`, `editor`, `text`…) ou chemin dans une liste (`icon_list.2.text`, index à partir
    de 0). Champ HTML (`editor`…) : `nouveau_texte` en HTML, ou texte brut converti en paragraphes.
    Remplacement ciblé : avec `remplacer` (extrait exact du texte actuel, présent une seule fois),
    seul cet extrait est remplacé par `nouveau_texte` (inséré tel quel, mise en forme du reste
    conservée) — à préférer pour corriger une phrase dans un long bloc. `nouveau_texte` vide
    supprime l'extrait. Sur une page publiée, la modification est en ligne dès la confirmation ;
    l'ancien contenu est sauvegardé localement (restaurable avec wp_page_restaurer_preparer)."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    path = _content_path(type_contenu, id)
    item, raw, tree = await _read_elementor(client, path)
    el = E.find_element(tree, widget_id)
    if el is None or el.get("elType") != "widget":
        raise WordPressError(f"widget {widget_id} introuvable dans {type_contenu} {id}", code="widget_introuvable")
    try:
        old = E.get_setting(el.get("settings") or {}, champ)
    except ValueError as e:
        raise WordPressError(str(e), code="champ_introuvable") from e
    if not isinstance(old, str):
        raise WordPressError(f"le champ {champ} n'est pas un texte", code="champ_introuvable")
    context_before = context_after = None
    if remplacer is not None:
        spans = find_excerpt(old, remplacer)
        if len(spans) != 1:
            hint = (" — il figure dans le texte affiché mais traverse une mise en forme (gras, lien…) : "
                    "choisir un extrait plus court" if not spans and find_excerpt(E.html_to_text(old), remplacer)
                    else "")
            raise WordPressError(f"extrait trouvé {len(spans)} fois dans {champ} (il doit l'être exactement une "
                                 f"fois){hint}", code="extrait_introuvable" if not spans else "extrait_ambigu")
        start, end = spans[0]
        new = old[:start] + nouveau_texte + old[end:]
        context_before = _context(old, start, end)
        context_after = _context(new, start, start + len(nouveau_texte))
    else:
        new = _as_html(nouveau_texte) if E.is_html_field(champ) else nouveau_texte
    if new == old:
        raise WordPressError("aucun changement : le texte est déjà celui-ci", code="aucun_changement")
    new_tree = copy.deepcopy(tree)
    E.set_setting(E.find_element(new_tree, widget_id)["settings"], champ, new)
    new_raw = E.dump_elementor_data(new_tree)
    content = _raw(item.get("content"))
    new_content = content.replace(old, new) if old and content.count(old) == 1 else None
    warnings = []
    if item.get("status") == "publish":
        warnings.append("Contenu publié : le nouveau texte sera VISIBLE IMMÉDIATEMENT après confirmation "
                        "(l'ancien contenu est sauvegardé avant).")
    if new_content is None:
        warnings.append("Le texte de secours de la page (recherche, flux RSS) ne sera mis à jour qu'au prochain "
                        "enregistrement dans Elementor.")
    preview = {"contenu": f"{type_contenu} {id} « {_raw(item.get('title'))} »", "lien": item.get("link"),
               "widget": f"{el.get('widgetType')} #{widget_id}", "champ": champ,
               "avant": _short(E.html_to_text(old)), "apres": _short(E.html_to_text(new))}
    if context_before is not None:
        preview["avant"], preview["apres"] = context_before, context_after
        preview["mode"] = "remplacement ciblé (le reste du bloc est inchangé)"
    elif E.is_html_field(champ):
        preview["apres_html"] = _short(new, 1500)
    return gate.prepare("widget_texte", {"path": path, "new_raw": new_raw, "new_content": new_content,
                                         "widget_id": widget_id, "champ": champ},
                        summary=f"Remplacer le texte du widget {widget_id} ({champ}) de « {_raw(item.get('title'))} »",
                        preview=preview, guard=_guard_of(item, raw), warnings=warnings)


async def _exec_widget_texte(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    path = p.args["path"]
    item, raw, _ = await _check_guard(client, path, p.guard, "Le contenu Elementor")
    backup = gate.save_backup(path.strip("/").split("/")[-2], int(item["id"]), _backup_payload(path, item, raw))
    warnings = await _write_elementor(client, path, p.args["new_raw"], p.args.get("new_content"))
    gate.journal("widget_texte", True, chemin=path, widget=p.args["widget_id"], champ=p.args["champ"],
                 sauvegarde=backup)
    return {"lien": item.get("link"), "sauvegarde": backup, "avertissements": warnings,
            "annuler": f"wp_page_restaurer_preparer(sauvegarde='{backup}')"}


@mcp.tool()
async def wp_page_restaurer_preparer(sauvegarde: str) -> dict[str, Any]:
    """PRÉPARE la restauration du contenu Elementor d'une page ou d'un article depuis une sauvegarde
    locale (nom de fichier renvoyé lors d'une modification de widget) ; n'écrit rien. Le contenu
    actuel est lui-même sauvegardé avant la restauration."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    try:
        data = gate.load_backup(sauvegarde)
    except ValueError as e:
        raise WordPressError(str(e), code="sauvegarde_invalide") from e
    path = str(data.get("chemin") or "")
    if not re.fullmatch(r"/wp/v2/(pages|posts)/\d+", path) or not data.get("elementor_data"):
        raise WordPressError("sauvegarde incomplète ou d'un autre type", code="sauvegarde_invalide")
    item, raw, tree = await _read_elementor(client, path)
    if raw == data["elementor_data"]:
        raise WordPressError("le contenu actuel est déjà identique à la sauvegarde", code="aucun_changement")
    now = {w["id"]: w["champs"] for w in E.extract_widgets(raw)}
    then = {w["id"]: w["champs"] for w in E.extract_widgets(data["elementor_data"])}
    changed = [{"widget": wid, "actuel": _short(E.widgets_to_text([{"id": wid, "widget": "", "profondeur": 0, "champs": now.get(wid, {})}]), 300),
                "restaure": _short(E.widgets_to_text([{"id": wid, "widget": "", "profondeur": 0, "champs": then.get(wid, {})}]), 300)}
               for wid in sorted(set(now) | set(then)) if now.get(wid) != then.get(wid)]
    preview = {"contenu": f"« {_raw(item.get('title'))} »", "lien": item.get("link"),
               "sauvegarde_du": data.get("modifie"), "widgets_modifies": changed[:20],
               "nombre_widgets_modifies": len(changed)}
    warnings = ["Contenu publié : la restauration sera visible immédiatement."] if item.get("status") == "publish" else []
    content_raw = data.get("content_raw")
    return gate.prepare("page_restaurer", {"path": path, "new_raw": data["elementor_data"],
                                           "new_content": content_raw if content_raw else None,
                                           "source": sauvegarde},
                        summary=f"Restaurer « {_raw(item.get('title'))} » depuis {sauvegarde}",
                        preview=preview, guard=_guard_of(item, raw), warnings=warnings)


async def _exec_page_restaurer(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    path = p.args["path"]
    item, raw, _ = await _check_guard(client, path, p.guard, "Le contenu Elementor")
    backup = gate.save_backup(path.strip("/").split("/")[-2], int(item["id"]), _backup_payload(path, item, raw))
    warnings = await _write_elementor(client, path, p.args["new_raw"], p.args.get("new_content"))
    gate.journal("page_restaurer", True, chemin=path, source=p.args["source"], sauvegarde=backup)
    return {"lien": item.get("link"), "sauvegarde_avant_restauration": backup, "avertissements": warnings}


# ======================================================================= médiathèque
def _check_upload_file(client: WordPressClient, chemin: str) -> dict[str, Any]:
    """Fichier local autorisé : dans WP_UPLOAD_ROOTS, type image/PDF, sous la taille maximale."""
    path = Path(os.path.expanduser(chemin)).resolve()
    roots = [Path(os.path.expanduser(r)).resolve() for r in client.settings.upload_roots]
    if not any(path == r or r in path.parents for r in roots):
        raise WordPressError(f"fichier hors des dossiers autorisés ({', '.join(map(str, roots))})",
                             code="fichier_refuse")
    if not path.is_file():
        raise WordPressError(f"fichier introuvable : {path}", code="fichier_refuse")
    mime = UPLOAD_TYPES.get(path.suffix.lower())
    if not mime:
        raise WordPressError(f"type de fichier refusé : {path.suffix or 'sans extension'} "
                             f"(autorisés : {', '.join(sorted(UPLOAD_TYPES))})", code="fichier_refuse")
    size = path.stat().st_size
    if size == 0 or size > client.settings.upload_max_mb * 1024 * 1024:
        raise WordPressError(f"taille refusée : {size} octets (maximum {client.settings.upload_max_mb:g} Mo)",
                             code="fichier_refuse")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"chemin": str(path), "nom": path.name, "type": mime, "taille": size, "sha256": digest}


@mcp.tool()
async def wp_media_envoyer_preparer(chemin: str, titre: str | None = None, texte_alternatif: str | None = None,
                                    legende: str | None = None) -> dict[str, Any]:
    """PRÉPARE l'envoi d'un fichier local (image JPG/PNG/GIF/WEBP ou PDF) dans la médiathèque ;
    n'écrit rien. Le fichier doit se trouver dans un dossier autorisé (WP_UPLOAD_ROOTS, par défaut
    Documents, Téléchargements, Bureau). Un fichier envoyé obtient une nouvelle adresse : pour
    remplacer un PDF en ligne, envoyer le nouveau puis mettre à jour les liens (wp_widget_texte_preparer)."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    info = _check_upload_file(client, chemin)
    meta = {k: v.strip() for k, v in (("title", titre), ("alt_text", texte_alternatif), ("caption", legende)) if v}
    warnings = []
    same = await client.get("/wp/v2/media", {"search": Path(info["nom"]).stem, "per_page": 5})
    if same:
        warnings.append("Des fichiers au nom proche existent déjà : " + ", ".join(
            str(m.get("source_url")) for m in same[:5]) + " — WordPress donnera un nom distinct au nouveau.")
    if info["type"].startswith("image/") and "alt_text" not in meta:
        warnings.append("Image sans texte alternatif : en prévoir un pour l'accessibilité.")
    preview = {"fichier": info["nom"], "type": info["type"], "taille_ko": round(info["taille"] / 1024, 1),
               **{({"title": "titre", "alt_text": "texte_alternatif", "caption": "legende"})[k]: v for k, v in meta.items()}}
    return gate.prepare("media_envoyer", {"fichier": info, "meta": meta},
                        summary=f"Envoyer {info['nom']} dans la médiathèque", preview=preview, warnings=warnings)


async def _exec_media_envoyer(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    expected = p.args["fichier"]
    info = _check_upload_file(client, expected["chemin"])
    if info["sha256"] != expected["sha256"]:
        raise _stale_error("Le fichier local")
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", info["nom"])
    headers = {"Content-Type": info["type"],
               "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(info['nom'])}"}
    data = await client.write("POST", "/wp/v2/media", content=Path(info["chemin"]).read_bytes(), headers=headers)
    media_id = data.get("id")
    warnings = []
    if p.args["meta"]:
        try:
            data = await client.write("POST", f"/wp/v2/media/{int(media_id)}", json=p.args["meta"])
        except WordPressError as e:
            warnings.append(f"fichier envoyé, mais titre/texte alternatif non enregistrés : {e}")
    gate.journal("media_envoyer", True, id=media_id, fichier=info["nom"], sha256=info["sha256"][:16])
    return {"id": media_id, "url": data.get("source_url"), "page_du_media": data.get("link"),
            "avertissements": warnings}


# ======================================================================= redirections
def _site_path(client: WordPressClient, value: str, what: str) -> str:
    v = (value or "").strip()
    host = (urlsplit(client.settings.base_url).hostname or "").removeprefix("www.")
    parts = urlsplit(v)
    if parts.scheme in ("http", "https") and (parts.hostname or "").removeprefix("www.") == host:
        v = parts.path or "/"
        if parts.query:
            v += "?" + parts.query
    if not v.startswith("/") or v.startswith("//") or any(c in v for c in " \n\t"):
        raise WordPressError(f"{what} invalide : « {value} » (chemin du site, ex. /ancienne-page)", code="parametre")
    return v


@mcp.tool()
async def wp_redirection_preparer(source: str, cible: str, code: int = 301,
                                  groupe: str | None = None) -> dict[str, Any]:
    """PRÉPARE une nouvelle règle de l'extension Redirection ; n'écrit rien. `source` : chemin du site
    (/ancienne-page) ou adresse complète du site. `cible` : chemin du site ou adresse externe https.
    `code` : 301 (définitif, défaut), 302, 307, 308. `groupe` : nom ou id (défaut : premier groupe)."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    src = _site_path(client, source, "source")
    tgt = cible.strip()
    if not tgt.startswith(("https://", "http://")):
        tgt = _site_path(client, tgt, "cible")
    if int(code) not in REDIRECT_CODES:
        raise WordPressError(f"code {code} refusé (301, 302, 307, 308)", code="parametre")
    if tgt.rstrip("/") == src.rstrip("/"):
        raise WordPressError("la source et la cible sont identiques (boucle)", code="parametre")
    groups = (await client.get("/redirection/v1/group")).get("items") or []
    chosen = next((g for g in groups if groupe is not None and (str(g.get("id")) == str(groupe)
                   or str(g.get("name", "")).lower() == str(groupe).lower())), None) if groupe else \
        next((g for g in groups if g.get("enabled", True)), None)
    if chosen is None:
        raise WordPressError("groupe introuvable : " + ", ".join(f"{g.get('id')} {g.get('name')}" for g in groups),
                             code="parametre")
    existing = (await client.get("/redirection/v1/redirect", {"per_page": 200})).get("items") or []
    dup = next((r for r in existing if str(r.get("url", "")).rstrip("/") == src.rstrip("/")), None)
    if dup:
        raise WordPressError(f"une règle existe déjà pour {src} (id {dup.get('id')} → "
                             f"{(dup.get('action_data') or {}).get('url')})", code="doublon")
    payload = {"url": src, "match_type": "url", "action_type": "url", "action_code": int(code),
               "action_data": {"url": tgt}, "group_id": int(chosen["id"]), "regex": False, "title": "",
               "match_data": {"source": {"flag_query": "exact", "flag_case": False, "flag_trailing": False,
                                         "flag_regex": False}}}
    preview = {"source": src, "cible": tgt, "code": int(code), "groupe": chosen.get("name")}
    return gate.prepare("redirection", {"payload": payload},
                        summary=f"Rediriger {src} → {tgt} ({code})", preview=preview,
                        warnings=["La règle est active dès la confirmation."])


async def _exec_redirection(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    src = p.args["payload"]["url"]
    existing = (await client.get("/redirection/v1/redirect", {"per_page": 200})).get("items") or []
    if any(str(r.get("url", "")).rstrip("/") == src.rstrip("/") for r in existing):
        raise _stale_error("La liste des redirections")
    await client.write("POST", "/redirection/v1/redirect", json=p.args["payload"])
    after = (await client.get("/redirection/v1/redirect", {"per_page": 200})).get("items") or []
    rule = next((r for r in after if str(r.get("url", "")).rstrip("/") == src.rstrip("/")), None)
    gate.journal("redirection", rule is not None, id=(rule or {}).get("id"), source=src)
    if rule is None:
        raise WordPressError("règle introuvable après création : vérifier dans Outils › Redirection",
                             code="verification_echouee")
    return {"id": rule.get("id"), "source": rule.get("url"), "cible": (rule.get("action_data") or {}).get("url"),
            "active": rule.get("enabled")}


@mcp.tool()
async def wp_redirection_activer_preparer(ids: list[int], active: bool) -> dict[str, Any]:
    """PRÉPARE l'activation (`active=True`) ou la désactivation (`active=False`) de règles de
    l'extension Redirection ; n'écrit rien. Les règles ne sont jamais supprimées."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    wanted = sorted({int(i) for i in ids})
    if not wanted:
        raise WordPressError("aucune règle indiquée", code="parametre")
    existing = {int(r["id"]): r for r in (await client.get("/redirection/v1/redirect", {"per_page": 200})).get("items") or []}
    missing = [i for i in wanted if i not in existing]
    if missing:
        raise WordPressError(f"règles introuvables : {missing}", code="parametre")
    todo = [i for i in wanted if bool(existing[i].get("enabled")) != active]
    if not todo:
        raise WordPressError("ces règles sont déjà dans l'état demandé", code="aucun_changement")
    preview = {"action": "activer" if active else "désactiver",
               "regles": [{"id": i, "source": existing[i].get("url"),
                           "cible": (existing[i].get("action_data") or {}).get("url"),
                           "utilisations": existing[i].get("hits")} for i in todo]}
    return gate.prepare("redirection_activer", {"ids": todo, "active": active},
                        summary=f"{'Activer' if active else 'Désactiver'} {len(todo)} règle(s) de redirection",
                        preview=preview)


async def _exec_redirection_activer(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    action = "enable" if p.args["active"] else "disable"
    await client.write("POST", f"/redirection/v1/bulk/redirect/{action}", json={"items": p.args["ids"]})
    after = {int(r["id"]): r for r in (await client.get("/redirection/v1/redirect", {"per_page": 200})).get("items") or []}
    state = {i: bool((after.get(i) or {}).get("enabled")) for i in p.args["ids"]}
    ok = all(v == p.args["active"] for v in state.values())
    gate.journal("redirection_activer", ok, ids=p.args["ids"], active=p.args["active"])
    if not ok:
        raise WordPressError(f"état après écriture inattendu : {state}", code="verification_echouee")
    return {"regles": [{"id": i, "active": v} for i, v in state.items()]}


# ======================================================================= pages : titre et statut
PAGE_STATUSES = {"publish": "publié", "draft": "brouillon", "private": "privé", "pending": "en attente de relecture"}


async def _menus_pointing_to(client: WordPressClient, item_id: int) -> list[str]:
    try:
        menus = {m["id"]: html.unescape(m.get("name", "")) for m in await client.get_all("/wp/v2/menus")}
        items = await client.get_all("/wp/v2/menu-items", {"context": "edit"})
    except WordPressError:
        return []
    return [f"{menus.get(i.get('menus'), i.get('menus'))} › {html.unescape(_raw(i.get('title')))}"
            for i in items if i.get("object_id") == item_id and i.get("type") == "post_type"
            and i.get("status") == "publish"]


@mcp.tool()
async def wp_page_preparer(id: int, titre: str | None = None, statut: str | None = None,
                           type_contenu: str = "page") -> dict[str, Any]:
    """PRÉPARE le changement du titre et/ou du statut d'une page (ou d'un article) existante ;
    n'écrit rien. `statut` : draft (brouillon : la page n'est plus visible, rien n'est supprimé),
    private, pending, publish. Signale les entrées de menu qui pointent vers la page."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    path = _content_path(type_contenu, id)
    if titre is None and statut is None:
        raise WordPressError("indiquer `titre` et/ou `statut`", code="parametre")
    if statut is not None and statut not in PAGE_STATUSES:
        raise WordPressError(f"statut inconnu : {statut} ({', '.join(PAGE_STATUSES)})", code="parametre")
    cur = await client.get(path, {"context": "edit"})
    payload: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    if titre is not None and titre.strip() != _raw(cur.get("title")):
        payload["title"] = titre.strip()
        changes["titre"] = {"avant": _raw(cur.get("title")), "apres": titre.strip()}
    if statut is not None and statut != cur.get("status"):
        payload["status"] = statut
        changes["statut"] = {"avant": PAGE_STATUSES.get(cur.get("status"), cur.get("status")),
                             "apres": PAGE_STATUSES[statut]}
    if not payload:
        raise WordPressError("aucun changement", code="aucun_changement")
    warnings = []
    if cur.get("status") == "publish" and statut and statut != "publish":
        menus = await _menus_pointing_to(client, int(id))
        warnings.append("La page ne sera plus visible du public ; ses liens renverront une erreur 404.")
        if menus:
            warnings.append("Entrées de menu qui pointent vers elle (WordPress les masque) : " + " ; ".join(menus))
    if statut == "publish" and cur.get("status") != "publish":
        warnings.append("La page sera VISIBLE IMMÉDIATEMENT sur le site public.")
    preview = {"contenu": f"{type_contenu} {id}", "lien": cur.get("link"), "changements": changes}
    return gate.prepare("page", {"path": path, "payload": payload},
                        summary=f"Modifier {type_contenu} {id} « {_raw(cur.get('title'))} » ({', '.join(changes)})",
                        preview=preview, guard={"modified": cur.get("modified"),
                                                "status": cur.get("status")}, warnings=warnings)


async def _exec_page(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    path = p.args["path"]
    cur = await client.get(path, {"context": "edit"})
    if cur.get("modified") != p.guard.get("modified") or cur.get("status") != p.guard.get("status"):
        raise _stale_error("Le contenu")
    data = await client.write("POST", path, json=p.args["payload"])
    gate.journal("page", True, chemin=path, champs=sorted(p.args["payload"]))
    return {"id": data.get("id"), "titre": _raw(data.get("title")),
            "statut": PAGE_STATUSES.get(data.get("status"), data.get("status")), "lien": data.get("link")}


# ======================================================================= menus
def _item_view(it: dict[str, Any]) -> dict[str, Any]:
    return {"libelle": html.unescape(_raw(it.get("title"))), "lien": it.get("url"),
            "type": it.get("type"), "page_id": it.get("object_id") if it.get("type") == "post_type" else None,
            "visible": it.get("status") == "publish"}


@mcp.tool()
async def wp_menu_entree_preparer(id: int, libelle: str | None = None, page_id: int | None = None,
                                  url: str | None = None, masquer: bool | None = None) -> dict[str, Any]:
    """PRÉPARE la modification d'une entrée de menu existante (id : voir wp_menus) ; n'écrit rien.
    `page_id` : faire pointer l'entrée vers une autre page publiée ; `url` : lien personnalisé
    (https://… ou chemin du site) ; `libelle` : texte affiché ; `masquer=True` : l'entrée n'est plus
    affichée (passée en brouillon, réversible avec `masquer=False`). Aucune entrée n'est supprimée."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    if page_id is not None and url is not None:
        raise WordPressError("choisir `page_id` OU `url`", code="parametre")
    cur = await client.get(f"/wp/v2/menu-items/{int(id)}", {"context": "edit"})
    before = _item_view(cur)
    payload: dict[str, Any] = {}
    if libelle is not None and libelle.strip() != before["libelle"]:
        payload["title"] = libelle.strip()
    if page_id is not None:
        page = await client.get(f"/wp/v2/pages/{int(page_id)}")
        if page.get("status") != "publish":
            raise WordPressError(f"la page {page_id} n'est pas publiée", code="parametre")
        payload.update({"type": "post_type", "object": "page", "object_id": int(page_id)})
        after_link = page.get("link")
    elif url is not None:
        u = url.strip()
        if not u.startswith("https://"):
            u = client.settings.base_url.rstrip("/") + _site_path(client, u, "url")
        payload.update({"type": "custom", "url": u})
        after_link = u
    else:
        after_link = before["lien"]
    if masquer is not None and masquer == before["visible"]:
        payload["status"] = "draft" if masquer else "publish"
    if not payload:
        raise WordPressError("aucun changement", code="aucun_changement")
    after = {"libelle": payload.get("title", before["libelle"]), "lien": after_link,
             "visible": (payload.get("status", cur.get("status")) == "publish")}
    preview = {"entree": int(id), "avant": before, "apres": after}
    warnings = ["Le menu change dès la confirmation, sur toutes les pages du site."]
    return gate.prepare("menu_entree", {"id": int(id), "payload": payload},
                        summary=f"Modifier l'entrée de menu « {before['libelle']} » ({int(id)})",
                        preview=preview, guard={"fp": fingerprint(_item_view(cur))}, warnings=warnings)


async def _exec_menu_entree(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    path = f"/wp/v2/menu-items/{p.args['id']}"
    cur = await client.get(path, {"context": "edit"})
    if fingerprint(_item_view(cur)) != p.guard.get("fp"):
        raise _stale_error("L'entrée de menu")
    data = await client.write("POST", path, json=p.args["payload"])
    gate.journal("menu_entree", True, id=p.args["id"], champs=sorted(p.args["payload"]))
    return {"entree": _item_view(data)}


# ======================================================================= textes alternatifs
@mcp.tool()
async def wp_medias_alt_preparer(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """PRÉPARE la mise à jour des textes alternatifs (et, si fournis, titre ou légende) d'images de
    la médiathèque ; n'écrit rien. `elements` : liste (50 au plus) de
    {"id": 123, "texte_alternatif": "…", "titre": "…" (facultatif), "legende": "…" (facultatif)}.
    Le texte alternatif décrit l'image pour les personnes aveugles ; vide = image décorative."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    if not elements or len(elements) > 50:
        raise WordPressError("de 1 à 50 éléments", code="parametre")
    plan, rows, guard = [], [], {}
    for e in elements:
        mid = int(e.get("id"))
        media = await client.get(f"/wp/v2/media/{mid}", {"context": "edit"})
        if not str(media.get("mime_type", "")).startswith("image/"):
            raise WordPressError(f"le média {mid} n'est pas une image", code="parametre")
        payload = {}
        for key, field in (("texte_alternatif", "alt_text"), ("titre", "title"), ("legende", "caption")):
            if key in e and e[key] is not None:
                current = media.get(field) if field == "alt_text" else _raw(media.get(field))
                if str(e[key]).strip() != (current or ""):
                    payload[field] = str(e[key]).strip()
        if payload:
            plan.append({"id": mid, "payload": payload})
            guard[str(mid)] = fingerprint([media.get("alt_text"), _raw(media.get("title")), _raw(media.get("caption"))])
            rows.append({"id": mid, "fichier": (media.get("media_details") or {}).get("file") or media.get("source_url"),
                         "avant": media.get("alt_text") or "", "apres": payload.get("alt_text", media.get("alt_text") or ""),
                         **({"titre": payload["title"]} if "title" in payload else {})})
    if not plan:
        raise WordPressError("aucun changement", code="aucun_changement")
    return gate.prepare("medias_alt", {"plan": plan},
                        summary=f"Mettre à jour le texte alternatif de {len(plan)} image(s)",
                        preview={"images": rows}, guard=guard)


async def _exec_medias_alt(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    for step in p.args["plan"]:
        media = await client.get(f"/wp/v2/media/{step['id']}", {"context": "edit"})
        if fingerprint([media.get("alt_text"), _raw(media.get("title")), _raw(media.get("caption"))]) != p.guard.get(str(step["id"])):
            raise _stale_error(f"L'image {step['id']}")
    done = []
    for step in p.args["plan"]:
        data = await client.write("POST", f"/wp/v2/media/{step['id']}", json=step["payload"])
        done.append({"id": data.get("id"), "texte_alternatif": data.get("alt_text")})
    gate.journal("medias_alt", True, ids=[s["id"] for s in p.args["plan"]])
    return {"images": done}


# ======================================================================= confirmation
EXECUTORS: dict[str, Callable[[WordPressClient, WriteGate, PendingWrite], Awaitable[dict[str, Any]]]] = {
    "article": _exec_article,
    "widget_texte": _exec_widget_texte,
    "page_restaurer": _exec_page_restaurer,
    "media_envoyer": _exec_media_envoyer,
    "redirection": _exec_redirection,
    "redirection_activer": _exec_redirection_activer,
    "page": _exec_page,
    "menu_entree": _exec_menu_entree,
    "medias_alt": _exec_medias_alt,
}


@mcp.tool()
async def wp_ecriture_confirmer(jeton: str) -> dict[str, Any]:
    """EXÉCUTE une écriture préparée par un outil *_preparer. À n'appeler qu'après avoir montré
    l'aperçu à l'utilisateur et obtenu son accord explicite. Jeton à usage unique, valable 10 min.
    Si le contenu a changé depuis l'aperçu, l'écriture est refusée."""
    gate = _get_gate()
    try:
        pending = gate.pop(jeton)
    except (ConfirmationError, WritesDisabledError) as e:
        raise WordPressError(str(e), code="jeton_invalide") from e
    client = _get_client()
    try:
        result = await EXECUTORS[pending.operation](client, gate, pending)
    except WordPressError as e:
        gate.journal(pending.operation, False, erreur=e.code or "erreur")
        raise
    return {"statut": "effectué", "resume": pending.summary, **result}


@mcp.tool()
async def wp_ecriture_annuler(jeton: str) -> dict[str, Any]:
    """Abandonne une écriture préparée (le jeton devient inutilisable). Rien n'est modifié."""
    return {"annule": _get_gate().cancel(jeton)}


@mcp.tool()
async def wp_ecritures_en_attente() -> dict[str, Any]:
    """Écritures préparées non encore confirmées (jeton, opération, résumé, délai restant)."""
    gate = _get_gate()
    return {"ecritures_activees": gate.enabled, "en_attente": gate.list_pending()}
