"""Configuration du serveur MCP WordPress — tout est piloté par variables d'environnement.

Rien de sensible n'est codé en dur. Le mot de passe d'application WordPress (Profil →
« Mots de passe d'application ») n'est jamais journalisé ni renvoyé par un outil.

Variables :
  WP_BASE_URL        URL du site, ex. https://www.exemple.fr (obligatoire)
  WP_USER            identifiant WordPress (login) — facultatif : sans lui, accès anonyme
  WP_AUTH_MODE       `basic` (défaut) : mot de passe d'application en HTTP Basic ;
                     `cookie` : connexion par wp-login.php + jeton REST (nonce), pour les
                     hébergements qui retirent l'en-tête Authorization
  WP_AUTH_HEADER     (mode basic) nom d'un en-tête de remplacement, ex. X-Claude-Auth : les
                     identifiants Basic y sont envoyés au lieu de Authorization, que certains
                     hébergements retirent. Demande le snippet PHP de docs/wpcode-snippet.php
                     côté site (voir README)
  WP_APP_PASSWORD    (mode basic) mot de passe d'application de cet utilisateur (espaces ignorés)
  WP_PASSWORD        (mode cookie) mot de passe WordPress du compte — mot de passe complet,
                     à réserver à un compte dédié aux droits limités
  WP_TIMEOUT         délai HTTP en secondes (défaut 30)
  WP_PER_PAGE_MAX    plafond d'éléments renvoyés par un outil de liste (défaut 200)

Écritures (v2, voir writes.py) — désactivées tant que WP_ENABLE_WRITES ne vaut pas 1 :
  WP_ENABLE_WRITES   1 pour autoriser les outils *_preparer + wp_ecriture_confirmer (défaut 0)
  WP_WRITE_TTL       durée de validité d'un jeton de confirmation, en secondes (défaut 600)
  WP_BACKUP_DIR      dossier local des sauvegardes de pages et du journal des écritures
                     (défaut ~/.wordpress-mcp/sauvegardes)
  WP_UPLOAD_ROOTS    dossiers d'où un fichier peut être envoyé à la médiathèque, séparés par
                     « : » (défaut ~/Documents:~/Downloads:~/Desktop)
  WP_UPLOAD_MAX_MB   taille maximale d'un fichier envoyé (défaut 20)
  WP_SNIPPETS_DIR    dossier des snippets WPCode relus (wpcode-snippet-*.php) que le connecteur peut
                     installer sur le site (défaut : dossier docs/ du connecteur)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    base_url: str = os.environ.get("WP_BASE_URL", "").rstrip("/")
    user: str | None = os.environ.get("WP_USER") or None
    auth_header: str | None = os.environ.get("WP_AUTH_HEADER") or None
    auth_mode: str = (os.environ.get("WP_AUTH_MODE") or "basic").strip().lower()
    app_password: str | None = field(
        default=(os.environ.get("WP_APP_PASSWORD") or "").replace(" ", "") or None, repr=False)
    password: str | None = field(default=os.environ.get("WP_PASSWORD") or None, repr=False)
    timeout: float = float(os.environ.get("WP_TIMEOUT", "30"))
    per_page_max: int = int(os.environ.get("WP_PER_PAGE_MAX", "200"))
    enable_writes: bool = os.environ.get("WP_ENABLE_WRITES", "0").strip().lower() in ("1", "true", "yes", "oui")
    write_ttl_seconds: int = int(os.environ.get("WP_WRITE_TTL", "600"))
    backup_dir: str = os.environ.get("WP_BACKUP_DIR") or str(Path.home() / ".wordpress-mcp" / "sauvegardes")
    upload_roots: tuple[str, ...] = tuple(
        p for p in (os.environ.get("WP_UPLOAD_ROOTS") or "~/Documents:~/Downloads:~/Desktop").split(":") if p.strip())
    upload_max_mb: float = float(os.environ.get("WP_UPLOAD_MAX_MB", "20"))
    snippets_dir: str = os.environ.get("WP_SNIPPETS_DIR") or str(Path(__file__).resolve().parent.parent / "docs")

    @property
    def authenticated(self) -> bool:
        if self.auth_mode == "cookie":
            return bool(self.user and self.password)
        return bool(self.user and self.app_password)


SETTINGS = Settings()
