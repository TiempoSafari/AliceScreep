from __future__ import annotations

import sys
from dataclasses import dataclass

SOURCE_ALICESW = "alicesw"
SOURCE_SILVERNOELLE = "silvernoelle"
SOURCE_GENERIC = "generic"


@dataclass
class Chapter:
    title: str
    url: str
    order: int = sys.maxsize


@dataclass
class ChapterContent:
    title: str
    content: str
    source_url: str


@dataclass
class NovelMeta:
    title: str
    author: str
    language: str = "zh-Hant"


@dataclass
class DownloadPayload:
    meta: NovelMeta
    chapters: list[ChapterContent]
    cover_bytes: bytes | None
    cover_type: str | None
    cover_name: str | None
