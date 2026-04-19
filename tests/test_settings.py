from __future__ import annotations

from pathlib import Path

from pansh.settings import Settings, ensure_settings_file


def test_settings_file_is_created(monkeypatch, tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    monkeypatch.setenv("pansh_CONFIG", str(settings_path))
    created = ensure_settings_file()
    assert created == settings_path
    assert created.exists()
    settings = Settings(created)
    assert settings.theme_mode == "auto"
    assert settings.default_jobs == 4
