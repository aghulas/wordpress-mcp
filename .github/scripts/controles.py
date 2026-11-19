"""Contrôles communs du CI (connecteurs, agrégateur, marketplaces) — sans dépendance.

    python3 .github/scripts/controles.py

Fichier DÉPLOYÉ par l'outil deployer_ci.py du dépôt agrégateur (modèle outils/ci/controles.py) :
le modifier là-bas, pas dans le dépôt de destination.

1. Skills : chaque dossier `skills/<nom>/` (ou `plugins/<p>/skills/<nom>/`) a un SKILL.md dont l'en-tête
   porte `name` (identique au dossier, minuscules-chiffres-tirets, 64 caractères au plus) et `description`
   (non vide, 1024 caractères au plus).
2. Marketplace (si `.claude-plugin/marketplace.json` existe) : JSON valide, plugins sans doublon, chaque
   source relative existe avec son `plugin.json` et au moins une skill.
3. JSON : tout fichier .json suivi par git se lit.
4. Secrets : aucun jeton, clé privée ou IBAN dans les fichiers suivis par git. Un IBAN n'est signalé que si
   sa clé de contrôle est juste (les IBAN inventés des tests passent) et s'il n'est pas un exemple public connu.

Code de sortie 1 à la première série d'erreurs ; chaque erreur est aussi émise en annotation GitHub.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

RACINE = Path.cwd()
NOM = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SECRETS = {
    "clé privée": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:\\n|\s)*[A-Za-z0-9+/]{40}"),
    "jeton GitHub": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    "clé AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "jeton Slack": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b"),
    "clé API (sk-…)": re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b"),
    "IBAN": re.compile(r"\bFR\d{2}(?: ?\d{4}){5} ?\d{3}\b"),
}
IBAN_EXEMPLES = {"FR7630006000011234567890189", "FR7630001007941234567890185", "FR1420041010050500013M02606"}
erreurs: list[str] = []


def iban_reel(texte: str) -> bool:
    """Vrai si la clé de contrôle (modulo 97) est juste et que ce n'est pas un IBAN d'exemple public."""
    iban = texte.replace(" ", "").upper()
    if iban in IBAN_EXEMPLES:
        return False
    chiffres = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    return int(chiffres) % 97 == 1


def erreur(fichier: Path | str, message: str) -> None:
    erreurs.append(f"{fichier} : {message}")
    print(f"::error file={fichier}::{message}")


def entete(p: Path) -> dict[str, str] | None:
    m = re.match(r"^---\n(.*?)\n---\n", p.read_text(encoding="utf-8"), re.S)
    if not m:
        return None
    res = {}
    for ligne in m.group(1).split("\n"):
        cle, sep, val = ligne.partition(":")
        if not sep or cle != cle.strip():
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] == '"':
            val = val[1:-1].replace('\\"', '"')
        res[cle] = val
    return res


def fichiers_suivis() -> list[Path]:
    r = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True, cwd=RACINE)
    if r.returncode:
        return [p for p in RACINE.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [RACINE / f for f in r.stdout.split("\0") if f]


def controler_skills() -> int:
    dossiers = sorted({d for motif in ("skills/*", "plugins/*/skills/*") for d in RACINE.glob(motif) if d.is_dir()})
    for d in dossiers:
        rel = d.relative_to(RACINE)
        p = d / "SKILL.md"
        if not p.exists():
            erreur(rel, "dossier de skill sans SKILL.md")
            continue
        e = entete(p)
        if e is None:
            erreur(rel / "SKILL.md", "en-tête YAML (--- … ---) absent")
            continue
        nom, desc = e.get("name", ""), e.get("description", "")
        if nom != d.name:
            erreur(rel / "SKILL.md", f"name « {nom} » différent du dossier « {d.name} »")
        if not NOM.match(d.name) or len(d.name) > 64:
            erreur(rel, "nom de skill : minuscules, chiffres et tirets, 64 caractères au plus")
        if not desc:
            erreur(rel / "SKILL.md", "description absente")
        elif len(desc) > 1024:
            erreur(rel / "SKILL.md", f"description de {len(desc)} caractères (1024 au plus)")
    return len(dossiers)


def controler_marketplace() -> int | None:
    p = RACINE / ".claude-plugin" / "marketplace.json"
    if not p.exists():
        return None
    rel = p.relative_to(RACINE)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as x:
        erreur(rel, f"JSON illisible : {x}")
        return 0
    vus: set[str] = set()
    for pl in doc.get("plugins", []):
        nom, src = pl.get("name", "?"), pl.get("source")
        if nom in vus:
            erreur(rel, f"plugin « {nom} » en double")
        vus.add(nom)
        if not pl.get("description"):
            erreur(rel, f"plugin « {nom} » sans description")
        if isinstance(src, str):
            d = RACINE / src
            if not (d / ".claude-plugin" / "plugin.json").exists():
                erreur(rel, f"plugin « {nom} » : {src}/.claude-plugin/plugin.json absent")
            if not list(d.glob("skills/*/SKILL.md")):
                erreur(rel, f"plugin « {nom} » : aucune skill dans {src}/skills")
        elif not (isinstance(src, dict) and src.get("source") == "github" and src.get("repo")):
            erreur(rel, f"plugin « {nom} » : source non reconnue")
    declares = {RACINE / pl["source"] for pl in doc.get("plugins", []) if isinstance(pl.get("source"), str)}
    for d in sorted((RACINE / "plugins").glob("*")) if (RACINE / "plugins").is_dir() else []:
        if d.is_dir() and d.resolve() not in {x.resolve() for x in declares}:
            erreur(d.relative_to(RACINE), "dossier de plugin absent du catalogue")
    return len(vus)


def controler_fichiers() -> int:
    suivis = fichiers_suivis()
    for p in suivis:
        rel = p.relative_to(RACINE)
        try:
            texte = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if p.suffix == ".json":
            try:
                json.loads(texte)
            except ValueError as x:
                erreur(rel, f"JSON illisible : {x}")
        for quoi, motif in SECRETS.items():
            m = next((x for x in motif.finditer(texte) if quoi != "IBAN" or iban_reel(x.group(0))), None)
            if m:
                ligne = texte.count("\n", 0, m.start()) + 1
                erreur(rel, f"ligne {ligne} : ressemble à un secret ({quoi}) — à retirer du dépôt")
    return len(suivis)


def main() -> int:
    n_skills = controler_skills()
    n_plugins = controler_marketplace()
    n_fichiers = controler_fichiers()
    print(f"{n_skills} skill(s), {n_fichiers} fichier(s) suivi(s)"
          + (f", {n_plugins} plugin(s) au catalogue" if n_plugins is not None else ""))
    if erreurs:
        print(f"{len(erreurs)} erreur(s)", file=sys.stderr)
        return 1
    print("contrôles : rien à signaler")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
