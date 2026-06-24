# src/core/models.py
# """Data models yang dipakai di seluruh aplikasi."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
import json
import hashlib


class Platform(Enum):
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    NEWS = "news"


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class ScrapingTask:
    """Representasi satu tugas scraping."""
    platform: str
    url: str
    task_type: str              # "comments", "profile", "article", dll
    params: dict = field(default_factory=dict)
    priority: int = 5           # 1 (tertinggi) - 10 (terendah)
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def id(self) -> str:
        content = f"{self.platform}:{self.url}:{self.task_type}:{json.dumps(self.params)}"
        return hashlib.md5(content.encode()).hexdigest()[:12]


@dataclass
class ScrapingResult:
    """Hasil dari satu task scraping."""
    task_id: str
    platform: str
    data: list[dict]
    metadata: dict = field(default_factory=dict)
    scraped_at: datetime = field(default_factory=datetime.now)
    status: TaskStatus = TaskStatus.SUCCESS
    error: Optional[str] = None

    @property
    def count(self) -> int:
        return len(self.data)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, ensure_ascii=False, indent=2)


@dataclass
class Comment:
    """Model universal untuk komentar."""
    platform: str
    post_url: str
    username: str
    text: str
    timestamp: Optional[str] = None
    likes: int = 0
    replies: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Article:
    """Model universal untuk artikel berita."""
    source: str
    url: str
    title: str
    content: str
    author: Optional[str] = None
    published_at: Optional[str] = None
    tags: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SocialPost:
    """Model universal untuk post social media."""
    platform: str
    post_id: str
    url: str
    username: str
    text: str
    timestamp: Optional[str] = None
    likes: int = 0
    comments_count: int = 0
    shares: int = 0
    media_urls: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)