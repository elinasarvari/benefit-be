from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "ci" / "resolve-sibling-ref.sh"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def sibling(tmp_path: Path) -> str:
    """A bare repo standing in for the sibling, with `main` and `feature/x` branches."""
    work, bare = tmp_path / "work", tmp_path / "sibling.git"
    work.mkdir()
    git("init", "-b", "main", cwd=work)
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "--allow-empty",
        "-m",
        "x",
        cwd=work,
    )
    git("branch", "feature/x", cwd=work)
    git("clone", "--bare", str(work), str(bare), cwd=tmp_path)
    return str(bare)


def resolve(url: str, branch: str, summary: Path | None = None) -> str:
    env = {**os.environ, "GITHUB_HEAD_REF": branch}
    env.pop("GITHUB_REF_NAME", None)
    if summary:
        env["GITHUB_STEP_SUMMARY"] = str(summary)
    done = subprocess.run(  # noqa: S603
        [str(SCRIPT), url], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def test_FR_000_25_uses_same_named_branch_when_present(sibling: str, tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"

    assert resolve(sibling, "feature/x", summary) == "feature/x"
    assert "Sibling ref: feature/x" in summary.read_text()


def test_FR_000_25_falls_back_to_main(sibling: str) -> None:
    assert resolve(sibling, "feature/does-not-exist") == "main"
