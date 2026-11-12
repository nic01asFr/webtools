"""Ecrit la configuration de connexion d'un deploiement (API et MCP).

Lit l'environnement du service (MCP_PUBLIC_URL, WEBTOOLS_PUBLIC_API,
WEBTOOLS_API_KEY, MCP_ACCESS_TOKEN) et ecrit un fichier markdown lisible par
l'exploitant, en droits 600 : il contient les secrets d'acces. Appele par
start_all.sh quand WEBTOOLS_CONNEXION_README est defini.

Usage :
    python scripts/config_connexion.py <chemin du fichier>
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def render(env: dict[str, str]) -> str:
    """Contenu markdown de la configuration ; une valeur absente est signalee."""

    def get(name: str) -> str:
        return env.get(name, "").strip() or f"(non defini : {name})"

    mcp_base = env.get("MCP_PUBLIC_URL", "").strip().rstrip("/")
    mcp_url = f"{mcp_base}/mcp" if mcp_base else "(non defini : MCP_PUBLIC_URL)"
    api = get("WEBTOOLS_PUBLIC_API")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "\n".join([
        "# Webtools : configuration de connexion",
        "",
        f"Ecrit au demarrage du service le {now}. Contient des secrets : ne pas",
        "partager ni versionner.",
        "",
        "## Serveur MCP (connecteur claude.ai, Claude Desktop, Claude Code)",
        "",
        f"- URL du connecteur : `{mcp_url}`",
        "- Authentification : OAuth 2.1, decouverte automatique",
        f"- Cle demandee par la page d'autorisation : `{get('MCP_ACCESS_TOKEN')}`",
        "",
        "Sur claude.ai : Parametres, Connecteurs, ajouter un connecteur",
        "personnalise, coller l'URL, puis coller la cle sur la page",
        "« webtools - Autorisation requise » (valable 10 minutes).",
        "",
        "L'identifiant et le mot de passe affiches par Onyxia ouvrent le pod",
        "Jupyter, pas le serveur MCP.",
        "",
        "## API REST",
        "",
        f"- URL : `{api}`",
        f"- En-tete : `X-API-Key: {get('WEBTOOLS_API_KEY')}`",
        f"- Sante : `{api.rstrip('/')}/health`" if not api.startswith("(") else "- Sante : /health",
        "",
    ])


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(render(dict(os.environ)))
    os.chmod(path, 0o600)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
