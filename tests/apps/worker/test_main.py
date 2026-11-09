from __future__ import annotations

from pathlib import Path

import pytest

from apps.worker.main import main


def test_FR_000_04_worker_missing_setting_exits_naming_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # so no .env is read
    for name in ("SESSION_SECRET", "SYSTEM_CREDENTIAL_SECRET", "MONGO_URL_APP", "MONGO_URL_OWNER"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 2
    assert "setting SESSION_SECRET" in capsys.readouterr().err
