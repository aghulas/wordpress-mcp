"""Point d'entrée : python -m wordpress_mcp (ou la commande `wordpress-mcp` une fois installé).

  stdio (défaut, Claude Desktop) :
      python -m wordpress_mcp
  streamable-http (distant) — prévu, non déployé :
      ajouter l'authentification par utilisateur (mcp-entra-auth) avant tout hébergement.

Logs sur stderr uniquement (stdout = JSON-RPC) ; aucun mot de passe n'y figure.
"""
from __future__ import annotations

import argparse
import logging
import sys

from .server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Serveur MCP WordPress, lecture seule")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Jamais les en-têtes httpx (Authorization) dans les logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return
    raise SystemExit(
        "streamable-http non activé : brancher mcp-entra-auth (comme edumoov-mcp) avant tout hébergement distant."
    )


if __name__ == "__main__":
    main()
