"""Configuration management using platformdirs with theme support."""

from __future__ import annotations

import json
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from .models import AppConfig, ThemeMode

APP_NAME = "bhpan"

# ── 路径 ────────────────────────────────────────────────────────
_config_dir = Path(user_config_dir(APP_NAME))
_data_dir = Path(user_data_dir(APP_NAME))
CONFIG_FILE = _config_dir / "config.json"
CERT_FILE = _data_dir / "missing_cert.pem"


def get_data_dir() -> Path:
    """返回应用数据目录（存放证书等运行时文件）。"""
    _data_dir.mkdir(parents=True, exist_ok=True)
    return _data_dir


# ── 配置读写 ────────────────────────────────────────────────────
_CURRENT_REVISION = 4


def load_config() -> AppConfig:
    """从磁盘加载配置，不存在则返回默认值。"""
    _config_dir.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        old_rev = raw.get("revision", 0)
        if old_rev < _CURRENT_REVISION:
            raw = _migrate_config(raw, old_rev)
        return AppConfig.model_validate(raw)
    return AppConfig()


def _migrate_config(raw: dict, old_rev: int) -> dict:
    """配置版本迁移。"""
    if old_rev < 2:
        raw.pop("encrypted", None)
    if old_rev < 4:
        raw["theme"] = ThemeMode.AUTO.value
    raw["revision"] = _CURRENT_REVISION
    return raw


def save_config(cfg: AppConfig) -> None:
    """将配置持久化到磁盘。"""
    _config_dir.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        cfg.model_dump_json(indent=2),
        encoding="utf-8",
    )
