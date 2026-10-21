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
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


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

    @property
    def authenticated(self) -> bool:
        if self.auth_mode == "cookie":
            return bool(self.user and self.password)
        return bool(self.user and self.app_password)


SETTINGS = Settings()
