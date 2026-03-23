"""Pydantic data models for BHPAN CLI v3."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TransferStatus(str, Enum):
    """传输任务状态枚举。"""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class TransferTask(BaseModel):
    """单个传输任务模型。"""

    remote_path: str = ""
    local_path: str = ""
    size: int = 0
    transferred: int = 0
    status: TransferStatus = TransferStatus.PENDING
    error: str | None = None
    speed: float = 0.0  # bytes per second
    docid: str | None = None


class FileMetaData(BaseModel):
    """文件元信息 — 对应 /file/metadata 响应。"""

    size: int = 0
    docid: str = ""
    rev: str = ""
    modified: int = 0
    client_mtime: int = 0
    name: str = ""
    editor: str = ""
    site: str = ""
    tags: list[str] = Field(default_factory=list)


class ResourceInfo(BaseModel):
    """路径解析后的资源信息 — 对应 /file/getinfobypath 响应。

    size == -1 表示目录。
    """

    size: int = 0
    docid: str = ""
    name: str = ""
    rev: str = ""
    client_mtime: int = 0
    modified: int = 0


class LinkInfo(BaseModel):
    """外链信息 — 对应 /link/getdetail 等响应。"""

    link: str = ""
    password: str = ""
    perm: int = 0
    endtime: int = 0
    limittimes: int = -1


class CachedToken(BaseModel):
    """缓存的 access token。"""

    token: str = ""
    expires: float = 0.0


class ThemeMode(str, Enum):
    """主题模式枚举。"""

    AUTO = "auto"
    DARK = "dark"
    LIGHT = "light"


DEFAULT_PUBKEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4E+eiWRwffhRIPQYvlXU
jf0b3HqCmosiCxbFCYI/gdfDBhrTUzbt3fL3o/gRQQBEPf69vhJMFH2ZMtaJM6oh
E3yQef331liPVM0YvqMOgvoID+zDa1NIZFObSsjOKhvZtv9esO0REeiVEPKNc+Dp
6il3x7TV9VKGEv0+iriNjqv7TGAexo2jVtLm50iVKTju2qmCDG83SnVHzsiNj70M
iviqiLpgz72IxjF+xN4bRw8I5dD0GwwO8kDoJUGWgTds+VckCwdtZA65oui9Osk5
t1a4pg6Xu9+HFcEuqwJTDxATvGAz1/YW0oUisjM0ObKTRDVSfnTYeaBsN6L+M+8g
CwIDAQAB
-----END PUBLIC KEY-----"""


class AppConfig(BaseModel):
    """应用持久化配置 (v4)。"""

    revision: int = 4
    host: str = "bhpan.buaa.edu.cn"
    pubkey: str = DEFAULT_PUBKEY
    username: str | None = None
    encrypted: str | None = None
    store_password: bool = True
    cached_token: CachedToken = Field(default_factory=CachedToken)
    theme: ThemeMode = ThemeMode.AUTO


class SearchResult(BaseModel):
    """搜索结果条目。"""

    path: str
    name: str
    size: int
    modified: int
    is_dir: bool


class DirEntry(BaseModel):
    """目录条目（文件或文件夹）。"""

    docid: str
    name: str
    size: int
    modified: int
    creator: str | None = None
    is_dir: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any], is_dir: bool = False) -> "DirEntry":
        """从 API 字典创建 DirEntry。"""
        return cls(
            docid=data.get("docid", ""),
            name=data.get("name", ""),
            size=data.get("size", 0),
            modified=data.get("modified", 0),
            creator=data.get("creator"),
            is_dir=is_dir,
        )


class ProgressInfo(BaseModel):
    """进度信息模型（用于传输进度跟踪）。"""

    task_id: int
    description: str
    total: int
    completed: int
    speed: float = 0.0
    start_time: datetime | None = None
