"""Create .env from .env.example and fill in the generated secrets. Standard library only."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent


def token() -> str:
    return secrets.token_urlsafe(48)


def main() -> int:
    target = ROOT / ".env"
    if target.exists():
        print(".env already exists; leaving it alone.")
        return 0
    owner_pw, app_pw = token(), token()
    port = os.environ.get("MONGO_PORT", "27017")
    hosts = f"localhost:{port}/?replicaSet=rs0&authSource=admin"
    generated = {
        "SESSION_SECRET": token(),
        "SYSTEM_CREDENTIAL_SECRET": token(),
        "DEMO_PASSWORD": token()[:24],
        "MONGO_URL_OWNER": f"mongodb://benefit_owner:{quote(owner_pw, safe='')}@{hosts}",
        "MONGO_URL_APP": f"mongodb://benefit_app:{quote(app_pw, safe='')}@{hosts}",
    }
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        key = line.split("=", 1)[0]
        # Quote the URLs: they contain `&`, which a shell would treat as an operator when sourced.
        value = f'"{generated[key]}"' if key.startswith("MONGO_URL") else generated.get(key)
        lines.append(f"{key}={value}" if key in generated else line)
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o600)
    print("Created .env with generated secrets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
