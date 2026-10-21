"""Garde-fou des écritures WordPress : préparer (aperçu), puis confirmer.

Aucun outil n'écrit directement. Chaque outil `*_preparer` lit l'état actuel, construit
l'opération exacte, calcule un aperçu (avant → après) et la met en attente derrière un jeton à
usage unique. Seul `wp_ecriture_confirmer(jeton)` l'exécute — exactement celle prévisualisée.

Verrous cumulés :
1. WP_ENABLE_WRITES=1 (désactivé par défaut) — sinon même la préparation est refusée.
2. Jeton aléatoire, à usage unique, expirant (WP_WRITE_TTL, 10 min par défaut).
3. Liste blanche des écritures dans client.py (`ALLOWED_WRITES`, méthode + chemin exact) ;
   aucune suppression de contenu.
4. Contrôle de fraîcheur : à la confirmation, l'élément est relu ; s'il a changé depuis l'aperçu
   (date de modification, empreinte du contenu), l'écriture est refusée.
5. Modification d'une page publiée : sauvegarde locale du contenu Elementor avant d'écrire
   (WP_BACKUP_DIR), restaurable avec wp_page_restaurer_preparer.

Journal : `journal.jsonl` dans WP_BACKUP_DIR (date, opération, identifiants, issue, fichier de
sauvegarde) et stderr — jamais le contenu écrit ni un mot de passe.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SETTINGS, Settings


class WritesDisabledError(RuntimeError):
    """Écriture demandée alors que WP_ENABLE_WRITES n'est pas activé."""


class ConfirmationError(RuntimeError):
    """Jeton inconnu, expiré ou déjà utilisé."""


@dataclass
class PendingWrite:
    operation: str                 # nom de l'exécuteur (voir write_tools.EXECUTORS)
    args: dict[str, Any]           # données exactes de l'écriture
    summary: str
    preview: dict[str, Any]
    expires_at: float
    guard: dict[str, Any] = field(default_factory=dict)   # état attendu à la confirmation
    warnings: list[str] = field(default_factory=list)


def fingerprint(value: Any) -> str:
    """Empreinte courte et stable d'une valeur (texte ou JSON)."""
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _log(message: str) -> None:
    print(f"[wordpress-writes] {message}", file=sys.stderr, flush=True)


class WriteGate:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or SETTINGS
        self._pending: dict[str, PendingWrite] = {}

    @property
    def enabled(self) -> bool:
        return self.settings.enable_writes

    def ensure_enabled(self) -> None:
        if not self.enabled:
            raise WritesDisabledError(
                "Les écritures sont désactivées sur ce connecteur. Pour les activer : WP_ENABLE_WRITES=1 "
                "dans la configuration du serveur MCP, puis redémarrer.")

    def _purge(self) -> None:
        now = time.time()
        for token in [t for t, p in self._pending.items() if p.expires_at < now]:
            del self._pending[token]

    def prepare(self, operation: str, args: dict[str, Any], *, summary: str, preview: dict[str, Any],
                guard: dict[str, Any] | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
        self.ensure_enabled()
        self._purge()
        token = secrets.token_urlsafe(16)
        ttl = self.settings.write_ttl_seconds
        self._pending[token] = PendingWrite(operation, args, summary, preview, time.time() + ttl,
                                            dict(guard or {}), list(warnings or []))
        _log(f"préparé {operation} jeton={token[:6]}…")
        return {
            "statut": "EN ATTENTE DE CONFIRMATION — rien n'a encore été modifié sur le site",
            "resume": summary,
            "avertissements": list(warnings or []),
            "apercu": preview,
            "jeton": token,
            "expire_dans_secondes": ttl,
            "etape_suivante": ("Montrer cet aperçu à l'utilisateur et n'appeler wp_ecriture_confirmer avec ce "
                               "jeton qu'après son accord explicite. Sinon, wp_ecriture_annuler."),
        }

    def pop(self, token: str) -> PendingWrite:
        self.ensure_enabled()
        self._purge()
        pending = self._pending.pop(token, None)
        if pending is None:
            raise ConfirmationError("Jeton inconnu, expiré ou déjà utilisé — refaire la préparation "
                                    "(outil *_preparer) pour obtenir un nouvel aperçu.")
        return pending

    def cancel(self, token: str) -> bool:
        return self._pending.pop(token, None) is not None

    def list_pending(self) -> list[dict[str, Any]]:
        self._purge()
        now = time.time()
        return [{"jeton": t, "operation": p.operation, "resume": p.summary,
                 "expire_dans_secondes": int(p.expires_at - now)} for t, p in self._pending.items()]

    # ------------------------------------------------------------- sauvegardes et journal
    def backup_dir(self) -> Path:
        d = Path(self.settings.backup_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_backup(self, kind: str, item_id: int, data: dict[str, Any]) -> str:
        """Écrit une sauvegarde JSON datée et renvoie son nom de fichier (relatif au dossier)."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{kind}-{int(item_id)}-{stamp}-{secrets.token_hex(3)}.json"
        path = self.backup_dir() / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return name

    def load_backup(self, name: str) -> dict[str, Any]:
        """Relit une sauvegarde par son nom de fichier ; refuse tout chemin hors du dossier."""
        if not name or "/" in name or "\\" in name or name.startswith(".") or not name.endswith(".json"):
            raise ValueError(f"nom de sauvegarde invalide : {name!r}")
        path = self.backup_dir() / name
        if not path.is_file():
            raise ValueError(f"sauvegarde introuvable : {name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def journal(self, operation: str, ok: bool, **details: Any) -> None:
        entry = {"date": datetime.now().isoformat(timespec="seconds"), "operation": operation,
                 "issue": "succès" if ok else "échec", **details}
        _log(f"exécuté {operation} → {entry['issue']}")
        try:
            with (self.backup_dir() / "journal.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:  # le journal ne doit jamais faire échouer une écriture déjà faite
            _log(f"journal non écrit : {e}")
