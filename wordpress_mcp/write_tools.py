"""Outils d'écriture (v2) : préparer → aperçu → confirmer. Voir writes.py pour les verrous.

Outils : wp_article_preparer, wp_widget_texte_preparer, wp_page_restaurer_preparer,
wp_page_creer_preparer, wp_page_preparer, wp_page_regenerer_preparer, wp_page_modele_preparer, wp_menu_entree_preparer,
wp_menu_entree_ajouter_preparer, wp_media_envoyer_preparer, wp_medias_alt_preparer,
wp_redirection_preparer, wp_redirection_activer_preparer, wp_snippet_installer_preparer,
wp_snippet_activer_preparer, wp_ecriture_confirmer,
wp_ecriture_annuler, wp_ecritures_en_attente.

Chaque *_preparer ne fait que des lectures ; l'écriture n'a lieu que dans un exécuteur
(`EXECUTORS`), appelé par wp_ecriture_confirmer avec un jeton valide.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import html
import mimetypes
import os
import re
import shutil
import subprocess
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


def _is_builder(item: dict[str, Any]) -> bool:
    """Page affichée par Elementor (le champ `content` n'est alors qu'un texte de secours)."""
    return (item.get("meta") or {}).get("_elementor_edit_mode") == "builder"


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
    if new_content is None and _is_builder(item):
        new_content = E.build_fallback_html(new_raw)
        warnings.append("Le texte de secours (recherche, flux RSS, résumé de partage) sera régénéré à partir du "
                        "contenu Elementor, comme avec « Mettre à jour » dans Elementor.")
    elif new_content is None:
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



@mcp.tool()
async def wp_page_regenerer_preparer(id: int, type_contenu: str = "page") -> dict[str, Any]:
    """PRÉPARE l'équivalent du bouton « Mettre à jour » d'Elementor pour une page (ou un article)
    construite avec Elementor ; n'écrit rien. Régénère le texte de secours (champ `content`, utilisé
    par la recherche, le flux RSS et le résumé affiché quand on partage le lien) à partir du contenu
    Elementor actuel, puis vide le cache Elementor. Le contenu affiché de la page ne change pas.
    L'ancien texte de secours est sauvegardé localement."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    path = _content_path(type_contenu, id)
    item, raw, _ = await _read_elementor(client, path)
    if not _is_builder(item):
        raise WordPressError("ce contenu n'est pas affiché par Elementor (mode « builder » inactif) : son "
                             "champ content est le contenu réel, il ne faut pas le régénérer", code="pas_elementor")
    current = _raw(item.get("content"))
    new_content = E.build_fallback_html(raw)
    if not new_content.strip():
        raise WordPressError("aucun texte à extraire du contenu Elementor", code="aucun_changement")
    if E.html_to_text(new_content) == E.html_to_text(current):
        raise WordPressError("le texte de secours est déjà à jour", code="aucun_changement")
    preview = {"contenu": f"{type_contenu} {id} « {_raw(item.get('title'))} »", "lien": item.get("link"),
               "texte_de_secours_avant": _short(E.html_to_text(current), 700),
               "texte_de_secours_apres": _short(E.html_to_text(new_content), 700),
               "resume_de_partage_apres": _short(" ".join(E.html_to_text(new_content).split()), 160)}
    return gate.prepare("page_regenerer", {"path": path, "new_content": new_content},
                        summary=f"Régénérer le texte de secours de « {_raw(item.get('title'))} » (comme « Mettre à jour »)",
                        preview=preview, guard=_guard_of(item, raw),
                        warnings=["Le contenu affiché de la page ne change pas ; seuls la recherche, le flux RSS et "
                                  "le résumé de partage sont mis à jour."])


async def _exec_page_regenerer(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    path = p.args["path"]
    item, raw, _ = await _check_guard(client, path, p.guard, "Le contenu Elementor")
    backup = gate.save_backup(path.strip("/").split("/")[-2], int(item["id"]), _backup_payload(path, item, raw))
    await client.write("POST", path, json={"content": p.args["new_content"]})
    check = await client.get(path, {"context": "edit"})
    if E.html_to_text(_raw(check.get("content"))) != E.html_to_text(p.args["new_content"]):
        raise WordPressError("le texte relu ne correspond pas : restaurer depuis la sauvegarde si besoin",
                             code="verification_echouee")
    if (check.get("meta") or {}).get("_elementor_data") != raw:
        raise WordPressError("le contenu Elementor a changé pendant l'écriture : vérifier la page",
                             code="verification_echouee")
    warnings = []
    try:
        await client.write("DELETE", "/elementor/v1/cache")
    except WordPressError as e:
        warnings.append(f"cache Elementor non vidé ({e})")
    gate.journal("page_regenerer", True, chemin=path, sauvegarde=backup)
    return {"lien": item.get("link"), "sauvegarde": backup, "avertissements": warnings}


# ======================================================================= pages : mise en page d'un modèle
MAIN_TEXT_FIELDS = ("editor", "title", "text", "description_text", "title_text", "description")


def _main_text_field(el: dict[str, Any]) -> str | None:
    """Champ texte principal d'un widget (celui que `textes=` remplit)."""
    if el.get("widgetType") in ("divider", "spacer", "shortcode", "html"):
        return None
    settings = el.get("settings") or {}
    for key in MAIN_TEXT_FIELDS:
        if isinstance(settings.get(key), str):
            return key
    return None


def _renumber(tree: list[dict[str, Any]]) -> dict[str, str]:
    """Nouveaux identifiants (7 caractères hexadécimaux, comme Elementor) pour tous les éléments ;
    renvoie la correspondance ancien → nouveau."""
    import secrets
    mapping: dict[str, str] = {}
    for el, _ in E.walk(tree):
        old = str(el.get("id") or "")
        new = secrets.token_hex(4)[:7]
        while new in mapping.values():
            new = secrets.token_hex(4)[:7]
        mapping[old] = new
        el["id"] = new
    return mapping


def _drop_elements(tree: list[dict[str, Any]], ids: set[str]) -> int:
    removed = 0
    for el in list(tree):
        if str(el.get("id")) in ids:
            tree.remove(el)
            removed += 1
            continue
        children = el.get("elements")
        if isinstance(children, list):
            removed += _drop_elements(children, ids)
    return removed


def _widgets_view(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for el, _ in E.walk(tree):
        if el.get("elType") == "widget":
            field = _main_text_field(el)
            text = E.html_to_text((el.get("settings") or {}).get(field)) if field else ""
            out.append({"widget": el.get("id"), "type": el.get("widgetType"), "champ": field,
                        "texte": _short(text, 120)})
    return out


@mcp.tool()
async def wp_page_modele_preparer(id: int, modele_id: int, textes: dict[str, str] | None = None,
                                  exclure: list[str] | None = None, type_contenu: str = "page") -> dict[str, Any]:
    """PRÉPARE la reprise de la mise en page Elementor d'une page modèle (`modele_id`, construite
    avec Elementor) dans une page existante **en brouillon** (`id`) ; n'écrit rien. Sert à remplir
    une page vide avec la même structure qu'une page du site (titres, blocs de texte, séparateurs),
    sans rien créer : la page cible doit déjà exister (wp-admin) et ne pas être publiée.
    `textes` : {identifiant de widget du modèle (voir wp_contenu_lire(modele_id, format='elementor')):
    nouveau texte} pour remplir en même temps les titres et blocs de texte (HTML ou texte brut
    converti en paragraphes) ; `exclure` : identifiants de widgets (ou sections) du modèle à ne pas
    reprendre. Les autres widgets gardent le texte du modèle : les corriger ensuite avec
    wp_widget_texte_preparer (les nouveaux identifiants sont renvoyés). Le contenu actuel de la
    page cible est sauvegardé puis remplacé ; la page reste en brouillon (publier avec
    wp_page_preparer après relecture)."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    if int(id) == int(modele_id):
        raise WordPressError("la page cible et le modèle doivent être différents", code="parametre")
    path = _content_path(type_contenu, id)
    model_path = _content_path(type_contenu, modele_id)
    target = await client.get(path, {"context": "edit"})
    if target.get("status") == "publish":
        raise WordPressError(f"{type_contenu} {id} est publié : le passer d'abord en brouillon avec "
                             f"wp_page_preparer(id={id}, statut='draft'), ou choisir une autre page cible",
                             code="page_publiee")
    model, model_raw, model_tree = await _read_elementor(client, model_path)
    if not _is_builder(model):
        raise WordPressError(f"le modèle {modele_id} n'est pas affiché par Elementor", code="pas_elementor")
    new_tree = copy.deepcopy(model_tree)
    excluded = {str(x) for x in (exclure or [])}
    unknown = [x for x in excluded if E.find_element(model_tree, x) is None]
    if unknown:
        raise WordPressError(f"éléments à exclure introuvables dans le modèle : {', '.join(unknown)}",
                             code="widget_introuvable")
    _drop_elements(new_tree, excluded)
    filled: list[dict[str, Any]] = []
    for wid, text in (textes or {}).items():
        el = E.find_element(new_tree, str(wid))
        if el is None or el.get("elType") != "widget":
            raise WordPressError(f"widget {wid} introuvable dans le modèle (ou exclu)", code="widget_introuvable")
        field = _main_text_field(el)
        if field is None:
            raise WordPressError(f"le widget {wid} ({el.get('widgetType')}) n'a pas de champ texte principal",
                                 code="champ_introuvable")
        value = _as_html(text) if E.is_html_field(field) else text
        E.set_setting(el["settings"], field, value)
        filled.append({"widget_modele": str(wid), "champ": field, "apres": _short(E.html_to_text(value), 200)})
    mapping = _renumber(new_tree)
    for f in filled:
        f["widget"] = mapping.get(f["widget_modele"])
    new_raw = E.dump_elementor_data(new_tree)
    new_content = E.build_fallback_html(new_raw)
    meta = model.get("meta") or {}
    page_settings = meta.get("_elementor_page_settings")
    payload: dict[str, Any] = {
        "content": new_content,
        "template": model.get("template") or "",
        "meta": {"_elementor_data": new_raw, "_elementor_edit_mode": "builder",
                 "_elementor_template_type": meta.get("_elementor_template_type") or "wp-page",
                 **({"_elementor_page_settings": page_settings} if isinstance(page_settings, dict) else {})},
    }
    target_raw = (target.get("meta") or {}).get("_elementor_data") or ""
    warnings = [f"La page reste en brouillon ({PAGE_STATUSES.get(target.get('status'), target.get('status'))}) : "
                "relire l'aperçu en étant connecté à wp-admin, puis publier avec wp_page_preparer."]
    if target_raw or _raw(target.get("content")).strip():
        warnings.append("La page cible a déjà un contenu : il sera remplacé (sauvegardé avant, restaurable "
                        "avec wp_page_restaurer_preparer).")
    if len(filled) < sum(1 for w in _widgets_view(new_tree) if w["champ"]):
        warnings.append("Les widgets non remplis gardent le texte du modèle : les corriger ensuite avec "
                        "wp_widget_texte_preparer, ou les retirer avec `exclure`.")
    preview = {"cible": f"{type_contenu} {id} « {_raw(target.get('title'))} »", "lien": target.get("link"),
               "modele": f"{type_contenu} {modele_id} « {_raw(model.get('title'))} »", "lien_modele": model.get("link"),
               "structure": E.structure_summary(new_raw), "exclus": sorted(excluded),
               "textes_remplis": filled, "widgets_apres": _widgets_view(new_tree),
               "resume_de_partage_apres": _short(" ".join(E.html_to_text(new_content).split()), 160)}
    return gate.prepare("page_modele", {"path": path, "model_path": model_path, "payload": payload,
                                        "widgets": _widgets_view(new_tree)},
                        summary=f"Reprendre la mise en page de « {_raw(model.get('title'))} » dans "
                                f"« {_raw(target.get('title'))} » ({len(filled)} texte(s) rempli(s))",
                        preview=preview, warnings=warnings,
                        guard={"modified": target.get("modified"), "status": target.get("status"),
                               "modele": fingerprint(model_raw)})


async def _exec_page_modele(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    path, model_path, payload = p.args["path"], p.args["model_path"], p.args["payload"]
    target = await client.get(path, {"context": "edit"})
    if target.get("modified") != p.guard.get("modified") or target.get("status") != p.guard.get("status"):
        raise _stale_error("La page cible")
    if target.get("status") == "publish":
        raise WordPressError("la page cible a été publiée entre-temps : écriture annulée", code="page_publiee")
    model = await client.get(model_path, {"context": "edit"})
    if fingerprint((model.get("meta") or {}).get("_elementor_data") or "") != p.guard.get("modele"):
        raise _stale_error("Le modèle")
    target_raw = (target.get("meta") or {}).get("_elementor_data") or ""
    backup = gate.save_backup(path.strip("/").split("/")[-2], int(target["id"]),
                              _backup_payload(path, target, target_raw))
    await client.write("POST", path, json=payload)
    check = await client.get(path, {"context": "edit"})
    meta = check.get("meta") or {}
    if E.parse_elementor_data(meta.get("_elementor_data")) != E.parse_elementor_data(payload["meta"]["_elementor_data"]) \
            or meta.get("_elementor_edit_mode") != "builder":
        raise WordPressError("le contenu relu après écriture ne correspond pas : vérifier la page et la "
                             "restaurer depuis la sauvegarde si besoin", code="verification_echouee")
    warnings = []
    try:
        await client.write("DELETE", "/elementor/v1/cache")
    except WordPressError as e:
        warnings.append(f"cache Elementor non vidé ({e})")
    gate.journal("page_modele", True, chemin=path, modele=model_path, sauvegarde=backup)
    return {"lien": check.get("link"), "statut": PAGE_STATUSES.get(check.get("status"), check.get("status")),
            "sauvegarde": backup, "widgets": p.args["widgets"], "avertissements": warnings,
            "etape_suivante": "relire la page (aperçu wp-admin), corriger les textes restants avec "
                              "wp_widget_texte_preparer, puis publier avec wp_page_preparer(statut='publish')"}

# ======================================================================= pages : titre et statut
PAGE_STATUSES = {"publish": "publié", "draft": "brouillon", "private": "privé", "pending": "en attente de relecture"}


@mcp.tool()
async def wp_page_creer_preparer(titre: str, slug: str | None = None, parent: int = 0) -> dict[str, Any]:
    """PRÉPARE la création d'une page vide **en brouillon** (invisible du public) ; n'écrit rien.
    `titre` : titre de la page ; `slug` : adresse souhaitée (ex. « applications-de-l-ecole »,
    sinon tirée du titre à la publication) ; `parent` : page parente (0 = aucune). Étapes suivantes :
    mise en page avec wp_page_modele_preparer, relecture, puis publication avec
    wp_page_preparer(statut='publish'). Aucune page n'est jamais créée publiée."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    title = (titre or "").strip()
    if not title:
        raise WordPressError("`titre` obligatoire", code="parametre")
    payload: dict[str, Any] = {"title": title, "status": "draft"}
    if slug:
        s = slug.strip().strip("/").lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", s):
            raise WordPressError(f"slug invalide : « {slug} » (minuscules, chiffres et tirets)", code="parametre")
        payload["slug"] = s
    if parent:
        par = await client.get(f"/wp/v2/pages/{int(parent)}", {"context": "edit"})
        payload["parent"] = int(par["id"])
    existing = await client.get_all("/wp/v2/pages", {"context": "edit", "search": title,
                                                     "status": "publish,draft,pending,private"})
    same = [p for p in existing if html.unescape(_raw(p.get("title"))).strip().lower() == title.lower()]
    if same:
        raise WordPressError("une page porte déjà ce titre : " + ", ".join(
            f"{p['id']} ({PAGE_STATUSES.get(p.get('status'), p.get('status'))})" for p in same)
            + " — la réutiliser plutôt que d'en créer une autre", code="deja_present")
    preview = {"titre": title, "statut": "brouillon (invisible du public)",
               "adresse_souhaitee": payload.get("slug", "tirée du titre à la publication"),
               "parent": payload.get("parent", 0)}
    return gate.prepare("page_creer", {"payload": payload},
                        summary=f"Créer la page « {title} » en brouillon", preview=preview,
                        guard={"titre": title.lower()},
                        warnings=["La page est créée vide et en brouillon : rien n'apparaît sur le site public."])


async def _exec_page_creer(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    title = p.args["payload"]["title"]
    existing = await client.get_all("/wp/v2/pages", {"context": "edit", "search": title,
                                                     "status": "publish,draft,pending,private"})
    if any(html.unescape(_raw(x.get("title"))).strip().lower() == p.guard.get("titre") for x in existing):
        raise WordPressError("une page portant ce titre a été créée entre-temps : écriture annulée",
                             code="deja_present")
    data = await client.write("POST", "/wp/v2/pages", json=p.args["payload"])
    if data.get("status") != "draft":
        raise WordPressError(f"page {data.get('id')} créée avec le statut {data.get('status')} : la vérifier",
                             code="verification_echouee")
    gate.journal("page_creer", True, id=data.get("id"))
    return {"id": data.get("id"), "titre": _raw(data.get("title")), "statut": "brouillon",
            "apercu": f"{client.settings.base_url.rstrip('/')}/?page_id={data.get('id')}&preview=true",
            "etape_suivante": f"wp_page_modele_preparer(id={data.get('id')}, modele_id=…, textes=…)"}


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
                           type_contenu: str = "page", slug: str | None = None) -> dict[str, Any]:
    """PRÉPARE le changement du titre, du statut et/ou de l'adresse (`slug`) d'une page (ou d'un
    article) existante ; n'écrit rien. `statut` : draft (brouillon : la page n'est plus visible,
    rien n'est supprimé), private, pending, publish. `slug` : nouvelle fin d'adresse (ex. « ogec ») ;
    les menus suivent automatiquement, mais l'ancienne adresse ne fonctionne plus : prévoir une
    redirection (wp_redirection_preparer). Signale les entrées de menu qui pointent vers la page."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    path = _content_path(type_contenu, id)
    if titre is None and statut is None and slug is None:
        raise WordPressError("indiquer `titre`, `statut` et/ou `slug`", code="parametre")
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
    warnings = []
    if slug is not None:
        s = slug.strip().strip("/").lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", s):
            raise WordPressError(f"slug invalide : « {slug} » (minuscules, chiffres et tirets)", code="parametre")
        if s != cur.get("slug"):
            base = CONTENT_PATHS[type_contenu]
            taken = [x for x in await client.get_all(base, {"context": "edit", "slug": s,
                                                            "status": "publish,draft,pending,private"})
                     if int(x.get("id")) != int(id)]
            if taken:
                raise WordPressError(f"l'adresse « {s} » est déjà utilisée par {type_contenu} {taken[0]['id']} "
                                     f"(« {html.unescape(_raw(taken[0].get('title')))} »)", code="deja_present")
            payload["slug"] = s
            old_link = cur.get("link") or ""
            changes["adresse"] = {"avant": old_link, "apres": old_link.rstrip("/").rsplit("/", 1)[0] + "/" + s
                                  if cur.get("status") == "publish" else s}
            if cur.get("status") == "publish":
                old_path = urlsplit(old_link).path or f"/{cur.get('slug')}"
                warnings.append(f"L'ancienne adresse {old_path} ne fonctionnera plus : créer une redirection "
                                f"(wp_redirection_preparer(source='{old_path.rstrip('/')}', cible='/{s}')). "
                                "Les entrées de menu suivent automatiquement ; les liens écrits à la main dans "
                                "les textes, eux, sont à corriger.")
    if not payload:
        raise WordPressError("aucun changement", code="aucun_changement")
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


def _menu_fingerprint(items: list[dict[str, Any]]) -> str:
    return fingerprint(sorted((i.get("id"), i.get("parent"), i.get("menu_order"), i.get("status"),
                               i.get("object_id"), i.get("url")) for i in items))


@mcp.tool()
async def wp_menu_entree_ajouter_preparer(menu: str, page_id: int | None = None, url: str | None = None,
                                          libelle: str | None = None, parent: int = 0,
                                          apres: int | None = None) -> dict[str, Any]:
    """PRÉPARE l'ajout d'une entrée à un menu existant (identifiant, nom ou slug : voir wp_menus) ;
    n'écrit rien. `page_id` : page publiée (libellé par défaut = son titre) ou `url` (https://… ou
    chemin du site, `libelle` obligatoire). `parent` : identifiant de l'entrée sous laquelle la
    placer (sous-menu) ; 0 = premier niveau. `apres` : identifiant de l'entrée sœur après laquelle
    la placer (défaut : en dernier parmi ses sœurs). L'entrée est visible dès la confirmation ;
    elle peut ensuite être masquée avec wp_menu_entree_preparer(masquer=True). Aucune suppression."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    if (page_id is None) == (url is None):
        raise WordPressError("indiquer `page_id` OU `url`", code="parametre")
    menus = await client.get_all("/wp/v2/menus", {"context": "edit"})
    key = str(menu).strip().lower()
    target = next((m for m in menus if str(m.get("id")) == key or str(m.get("slug", "")).lower() == key
                   or html.unescape(str(m.get("name", ""))).strip().lower() == key), None)
    if target is None:
        raise WordPressError(f"menu inconnu : « {menu} ». Menus : " + ", ".join(
            f"{html.unescape(m.get('name', ''))} ({m.get('id')})" for m in menus), code="parametre")
    menu_id = int(target["id"])
    items = await client.get_all("/wp/v2/menu-items", {"context": "edit", "menus": menu_id,
                                                       "status": "publish,draft"})
    by_id = {int(i["id"]): i for i in items}
    parent = int(parent or 0)
    if parent and parent not in by_id:
        raise WordPressError(f"l'entrée parente {parent} n'appartient pas au menu « {target.get('name')} »",
                             code="parametre")
    payload: dict[str, Any] = {"menus": menu_id, "parent": parent, "status": "publish"}
    if page_id is not None:
        page = await client.get(f"/wp/v2/pages/{int(page_id)}", {"context": "edit"})
        if page.get("status") != "publish":
            raise WordPressError(f"la page {page_id} n'est pas publiée : la publier d'abord (wp_page_preparer)",
                                 code="parametre")
        dup = [i for i in items if i.get("type") == "post_type" and i.get("object_id") == int(page_id)
               and int(i.get("parent") or 0) == parent]
        if dup:
            raise WordPressError(f"la page {page_id} est déjà dans ce menu à cet endroit (entrée {dup[0]['id']}"
                                 f"{', masquée' if dup[0].get('status') != 'publish' else ''}) : utiliser "
                                 "wp_menu_entree_preparer", code="deja_present")
        label = (libelle or "").strip() or html.unescape(_raw(page.get("title")))
        payload.update({"type": "post_type", "object": "page", "object_id": int(page_id), "title": label})
        link = page.get("link")
    else:
        if not (libelle or "").strip():
            raise WordPressError("`libelle` obligatoire pour un lien personnalisé", code="parametre")
        u = url.strip()
        if not u.startswith("https://"):
            u = client.settings.base_url.rstrip("/") + _site_path(client, u, "url")
        label = libelle.strip()
        payload.update({"type": "custom", "url": u, "title": label})
        link = u
    siblings = sorted((i for i in items if int(i.get("parent") or 0) == parent),
                      key=lambda i: i.get("menu_order") or 0)
    if apres is not None:
        ref = next((i for i in siblings if int(i["id"]) == int(apres)), None)
        if ref is None:
            raise WordPressError(f"l'entrée {apres} n'est pas une entrée sœur à cet endroit du menu", code="parametre")
        order = int(ref.get("menu_order") or 0) + 1
    else:
        ref_orders = [int(i.get("menu_order") or 0) for i in siblings]
        if parent:
            ref_orders.append(int(by_id[parent].get("menu_order") or 0))
        order = max(ref_orders, default=0) + 1
    payload["menu_order"] = order
    where = (f"sous « {html.unescape(_raw(by_id[parent].get('title')))} »" if parent else "au premier niveau")
    preview = {"menu": f"{html.unescape(target.get('name', ''))} ({menu_id})", "emplacement": where,
               "libelle": label, "lien": link, "ordre": order,
               "soeurs_actuelles": [html.unescape(_raw(i.get("title"))) for i in siblings]}
    warnings = ["L'entrée sera VISIBLE IMMÉDIATEMENT dans le menu, sur toutes les pages du site.",
                "Si une extension de cache de page est active, les pages déjà en cache montreront l'ancien "
                "menu jusqu'à leur expiration (ou vider le cache de l'extension)."]
    return gate.prepare("menu_entree_ajouter", {"payload": payload, "menu_id": menu_id},
                        summary=f"Ajouter « {label} » au menu « {html.unescape(target.get('name', ''))} » {where}",
                        preview=preview, guard={"menu": _menu_fingerprint(items)}, warnings=warnings)


async def _exec_menu_entree_ajouter(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    items = await client.get_all("/wp/v2/menu-items", {"context": "edit", "menus": p.args["menu_id"],
                                                       "status": "publish,draft"})
    if _menu_fingerprint(items) != p.guard.get("menu"):
        raise _stale_error("Le menu")
    data = await client.write("POST", "/wp/v2/menu-items", json=p.args["payload"])
    new_id = data.get("id")
    check = await client.get(f"/wp/v2/menu-items/{int(new_id)}", {"context": "edit"})
    expected = p.args["payload"]
    if check.get("menus") != expected["menus"] or int(check.get("parent") or 0) != expected["parent"]:
        raise WordPressError(f"entrée {new_id} créée mais relue différente : vérifier dans Apparence › Menus",
                             code="verification_echouee")
    gate.journal("menu_entree_ajouter", True, id=new_id, menu=expected["menus"], parent=expected["parent"])
    return {"id": new_id, "entree": _item_view(check), "parent": check.get("parent"),
            "ordre": check.get("menu_order"),
            "annuler": f"wp_menu_entree_preparer(id={new_id}, masquer=True)"}


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
# ------------------------------------------------------------------ snippets WPCode
SNIPPET_FILE_RE = re.compile(r"wpcode-snippet-[a-z0-9]+(?:-[a-z0-9]+)*\.php")
SNIPPETS_PROTECTED = {"MCP Gestion des snippets", "MCP Auth"}
SNIPPET_MAX_BYTES = 64 * 1024
SNIPPET_PREVIEW_MAX = 40_000
SNIPPETS_HINT = ("routes de gestion des snippets absentes : installer une fois docs/wpcode-snippet-gestion.php "
                 "dans WPCode (titre « MCP Gestion des snippets », « Exécuter partout », Activer)")


def _sha(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _snippet_file(client: WordPressClient, fichier: str) -> tuple[str, str]:
    """Lit un snippet relu du dossier des snippets : (code sans « <?php », sha256). Seul un nom simple
    wpcode-snippet-xxx.php est accepté (pas de chemin) ; vérifié avec `php -l` si PHP est présent."""
    name = (fichier or "").strip()
    if not SNIPPET_FILE_RE.fullmatch(name):
        raise WordPressError(f"fichier refusé : « {fichier} » — nom simple attendu (wpcode-snippet-xxx.php), "
                             "pris dans le dossier des snippets relus du connecteur", code="parametre")
    base = Path(client.settings.snippets_dir).expanduser().resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_file():
        raise WordPressError(f"{name} introuvable dans {base}", code="introuvable")
    if path.stat().st_size > SNIPPET_MAX_BYTES:
        raise WordPressError(f"{name} dépasse {SNIPPET_MAX_BYTES // 1024} Ko", code="trop_volumineux")
    raw = path.read_text(encoding="utf-8")
    code = re.sub(r"\A﻿?\s*<\?php[ \t]*(?:\r?\n)?", "", raw, count=1).lstrip("\r\n")
    if not code.strip():
        raise WordPressError(f"{name} est vide", code="parametre")
    if re.match(r"\s*<\?php", code):
        raise WordPressError(f"{name} : balise <?php en double", code="parametre")
    php = shutil.which("php")
    if php:
        res = subprocess.run([php, "-l", str(path)], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise WordPressError(f"{name} : erreur de syntaxe PHP — {(res.stdout or res.stderr).strip()[:400]}",
                                 code="syntaxe_php")
    return code, _sha(code)


async def _snippets_get(client: WordPressClient, path: str = "") -> Any:
    try:
        return await client.get("/wpmcp/v1/snippets" + path)
    except WordPressError as e:
        if e.code == "rest_no_route" or (e.status == 404 and not path):
            raise WordPressError(SNIPPETS_HINT, code="snippets_indisponibles") from e
        raise


def _snippet_brief(sn: dict[str, Any]) -> dict[str, Any]:
    return {k: sn.get(k) for k in ("id", "titre", "actif", "type", "emplacement", "modifie", "taille", "sha256")}


@mcp.tool()
async def wp_snippet_installer_preparer(fichier: str, titre: str) -> dict[str, Any]:
    """PRÉPARE l'installation d'un snippet WPCode PHP depuis un fichier **relu** du dossier des snippets
    du connecteur (docs/ par défaut : `fichier` = nom simple, ex. « wpcode-snippet-securite.php ») ;
    n'écrit rien. `titre` doit commencer par « MCP ». S'il n'existe pas de snippet de ce titre : création
    **inactive** (aperçu = code complet ; activer ensuite avec wp_snippet_activer_preparer). S'il existe :
    remplacement de son code (aperçu = différences complètes ; s'il est actif, le nouveau code s'applique
    dès la confirmation, après le test de WPCode). Jamais de code saisi dans la conversation."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    title = (titre or "").strip()
    if not title.startswith("MCP ") or len(title) < 5:
        raise WordPressError("le titre doit commencer par « MCP » (ex. « MCP Sécurité »)", code="parametre")
    if title in SNIPPETS_PROTECTED:
        raise WordPressError(f"« {title} » se met à jour à la main dans WPCode (il porte l'accès du connecteur)",
                             code="snippet_protege")
    code, sha = _snippet_file(client, fichier)
    items = await _snippets_get(client)
    same = [sn for sn in items or [] if (sn.get("titre") or "").strip() == title]
    if not same:
        if len(code) > SNIPPET_PREVIEW_MAX:
            raise WordPressError("code trop long pour être montré en entier dans l'aperçu", code="trop_volumineux")
        preview = {"operation": "création (inactif)", "titre": title, "fichier": fichier, "type": "PHP",
                   "emplacement": "Exécuter partout", "taille": len(code.encode("utf-8")), "sha256": sha,
                   "code_complet": code}
        return gate.prepare("snippet_installer", {"mode": "creation", "fichier": fichier, "titre": title,
                                                  "code": code, "sha256": sha},
                            summary=f"Créer le snippet WPCode « {title} » (inactif) depuis {fichier}",
                            preview=preview, guard={"titre": title},
                            warnings=["Le snippet est créé INACTIF : rien ne change sur le site avant "
                                      "wp_snippet_activer_preparer.",
                                      "Relire le code complet de l'aperçu avant de confirmer."])
    cur = await _snippets_get(client, f"/{int(same[0]['id'])}")
    if cur.get("sha256") == sha:
        raise WordPressError(f"le snippet {cur['id']} « {title} » a déjà exactement ce code", code="deja_a_jour")
    diff = "".join(difflib.unified_diff((cur.get("code") or "").splitlines(keepends=True),
                                        code.splitlines(keepends=True),
                                        fromfile=f"en ligne (snippet {cur['id']})", tofile=fichier, n=2))
    if len(diff) > SNIPPET_PREVIEW_MAX:
        raise WordPressError("différences trop longues pour être montrées en entier dans l'aperçu",
                             code="trop_volumineux")
    preview = {"operation": "remplacement du code", "id": cur["id"], "titre": title, "fichier": fichier,
               "actif": bool(cur.get("actif")), "sha256_avant": cur.get("sha256"), "sha256_apres": sha,
               "differences": diff}
    warnings = ["L'ancien code est sauvegardé localement avant le remplacement."]
    if cur.get("actif"):
        warnings.insert(0, "Snippet ACTIF : le nouveau code s'applique à tout le site dès la confirmation "
                           "(WPCode le teste d'abord et désactive le snippet s'il est en erreur). Vider ensuite "
                           "le cache WP Super Cache.")
    return gate.prepare("snippet_installer", {"mode": "maj", "id": int(cur["id"]), "fichier": fichier,
                                              "titre": title, "code": code, "sha256": sha},
                        summary=f"Remplacer le code du snippet {cur['id']} « {title} » par {fichier}",
                        preview=preview, guard={"sha256_actuel": cur.get("sha256"), "actif": bool(cur.get("actif"))},
                        warnings=warnings)


def _check_snippet_result(data: dict[str, Any], sha: str) -> None:
    if data.get("sha256") != sha or data.get("integre") is False:
        raise WordPressError(f"snippet {data.get('id')} enregistré mais son code diffère du fichier "
                             "(empreinte) : le vérifier dans WPCode", code="verification_echouee")


async def _exec_snippet_installer(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    a = p.args
    code, sha = _snippet_file(client, a["fichier"])
    if sha != a["sha256"]:
        raise WordPressError(f"{a['fichier']} a changé depuis l'aperçu : écriture annulée, préparer à nouveau",
                             code="modifie_entre_temps")
    if a["mode"] == "creation":
        items = await _snippets_get(client)
        if any((sn.get("titre") or "").strip() == a["titre"] for sn in items or []):
            raise WordPressError("un snippet de ce titre a été créé entre-temps : écriture annulée",
                                 code="deja_present")
        data = await client.write("POST", "/wpmcp/v1/snippets",
                                  json={"titre": a["titre"], "code": code, "sha256": sha})
        _check_snippet_result(data, sha)
        if data.get("actif"):
            raise WordPressError(f"snippet {data.get('id')} créé actif : le vérifier", code="verification_echouee")
        gate.journal("snippet_installer", True, id=data.get("id"), titre=a["titre"], sha256=sha)
        return {"snippet": _snippet_brief(data), "statut": "créé, inactif",
                "etape_suivante": f"wp_snippet_activer_preparer(id={data.get('id')}, actif=True)"}
    cur = await _snippets_get(client, f"/{a['id']}")
    if cur.get("sha256") != p.guard.get("sha256_actuel") or bool(cur.get("actif")) != p.guard.get("actif"):
        raise WordPressError("le snippet a changé depuis l'aperçu : écriture annulée, préparer à nouveau",
                             code="modifie_entre_temps")
    backup = gate.save_backup("snippet", a["id"], {"titre": cur.get("titre"), "actif": cur.get("actif"),
                                                   "sha256": cur.get("sha256"), "code": cur.get("code")})
    data = await client.write("POST", f"/wpmcp/v1/snippets/{a['id']}",
                              json={"code": code, "sha256": sha, "sha256_actuel": cur.get("sha256")})
    _check_snippet_result(data, sha)
    gate.journal("snippet_installer", True, id=a["id"], sha256=sha, sauvegarde=backup)
    out = {"snippet": _snippet_brief(data), "sauvegarde": backup,
           "statut": "code remplacé" + (", actif" if data.get("actif") else ", inactif")}
    if data.get("avertissement"):
        out["avertissement"] = data["avertissement"]
    return out


@mcp.tool()
async def wp_snippet_activer_preparer(id: int, actif: bool) -> dict[str, Any]:
    """PRÉPARE l'activation (`actif=True`) ou la désactivation (`actif=False`) d'un snippet WPCode
    (identifiants : wp_snippets) ; n'écrit rien. À l'activation, WPCode teste le code et refuse s'il est
    en erreur. « MCP Gestion des snippets » et « MCP Auth » ne peuvent pas être désactivés à distance."""
    gate = _get_gate()
    gate.ensure_enabled()
    client = _get_client()
    cur = await _snippets_get(client, f"/{int(id)}")
    want = bool(actif)
    if bool(cur.get("actif")) == want:
        raise WordPressError(f"le snippet {cur['id']} est déjà {'actif' if want else 'inactif'}", code="deja_a_jour")
    if not want and (cur.get("protege") or (cur.get("titre") or "").strip() in SNIPPETS_PROTECTED):
        raise WordPressError(f"« {cur.get('titre')} » ne peut pas être désactivé à distance (il porte l'accès du "
                             "connecteur) : le faire dans WPCode si nécessaire", code="snippet_protege")
    preview: dict[str, Any] = {**_snippet_brief(cur), "actif": {"avant": bool(cur.get("actif")), "apres": want}}
    code = cur.get("code") or ""
    if want:
        preview["code_complet" if len(code) <= SNIPPET_PREVIEW_MAX else "debut_du_code"] = code[:SNIPPET_PREVIEW_MAX]
        warnings = ["Le code s'exécute sur tout le site dès la confirmation (WPCode le teste d'abord et refuse "
                    "l'activation s'il est en erreur).",
                    "Vider ensuite le cache WP Super Cache pour que les pages en cache en tiennent compte."]
    else:
        warnings = ["Les fonctions de ce snippet s'arrêtent dès la confirmation (réversible)."]
    return gate.prepare("snippet_activer", {"id": int(cur["id"]), "actif": want},
                        summary=f"{'Activer' if want else 'Désactiver'} le snippet {cur['id']} « {cur.get('titre')} »",
                        preview=preview, guard={"sha256": cur.get("sha256"), "actif": bool(cur.get("actif"))},
                        warnings=warnings)


async def _exec_snippet_activer(client: WordPressClient, gate: WriteGate, p: PendingWrite) -> dict[str, Any]:
    sid, want = p.args["id"], p.args["actif"]
    cur = await _snippets_get(client, f"/{sid}")
    if cur.get("sha256") != p.guard.get("sha256") or bool(cur.get("actif")) != p.guard.get("actif"):
        raise WordPressError("le snippet a changé depuis l'aperçu : écriture annulée, préparer à nouveau",
                             code="modifie_entre_temps")
    data = await client.write("POST", f"/wpmcp/v1/snippets/{sid}/statut", json={"actif": want})
    if bool(data.get("actif")) != want:
        raise WordPressError(f"le snippet {sid} est resté {'actif' if data.get('actif') else 'inactif'}",
                             code="verification_echouee")
    _check_snippet_result(data, p.guard.get("sha256"))
    gate.journal("snippet_activer", True, id=sid, actif=want)
    return {"snippet": _snippet_brief(data), "statut": "activé" if want else "désactivé",
            "a_faire": "vider le cache WP Super Cache puis vérifier le site" if want else None}


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
    "page_regenerer": _exec_page_regenerer,
    "page_modele": _exec_page_modele,
    "menu_entree_ajouter": _exec_menu_entree_ajouter,
    "page_creer": _exec_page_creer,
    "snippet_installer": _exec_snippet_installer,
    "snippet_activer": _exec_snippet_activer,
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
