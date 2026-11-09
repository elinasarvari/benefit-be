"""Check the tools the project needs (Brewfile) and the local setup. Standard library only."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {
    "uv": (["uv", "--version"], None),
    "pnpm": (["pnpm", "--version"], None),
    "node": (["node", "--version"], r"^v24\."),
    "mongod": (["mongod", "--version"], r"db version v8\.0\."),
    "mongosh": (["mongosh", "--version"], None),
    "temporal": (["temporal", "--version"], None),
    "gitleaks": (["gitleaks", "version"], None),
}


def check_tool(name: str, command: list[str], pattern: str | None) -> str | None:
    if shutil.which(command[0]) is None:
        return f"{name}: not installed (run brew bundle)"
    out = subprocess.run(  # noqa: S603 - fixed commands from TOOLS
        command, capture_output=True, text=True, check=False
    ).stdout
    if pattern and not re.search(pattern, out, re.MULTILINE):
        return f"{name}: wrong version, expected {pattern}"
    return None


def main() -> int:
    problems = [p for n, (c, r) in TOOLS.items() if (p := check_tool(n, c, r))]
    if not (ROOT / ".env").exists():
        problems.append(".env: missing (run make setup)")
    frontend = Path(os.environ.get("BENEFIT_FE_DIR", ROOT.parent / "benefit-fe"))
    if not (frontend / "package.json").exists():
        problems.append(f"BENEFIT_FE_DIR: {frontend} is not a benefit-fe checkout")
    for problem in problems:
        print(f"MISSING  {problem}", file=sys.stderr)
    print("doctor: all good" if not problems else f"doctor: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
