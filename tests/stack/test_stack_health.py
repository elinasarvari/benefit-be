"""Every component of the running stack reports ready (FR-000-01).

Runs against `make dev` locally, or `Procfile.ci` in CI. Marked `stack` by its folder.
"""

from __future__ import annotations

import subprocess
import sys


def test_FR_000_01_every_component_ready() -> None:
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "scripts.check_health"], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stdout + done.stderr
