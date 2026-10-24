"""Local-only audio cache with strict filename validation.

Audio artifacts are written as ``.part`` files first, then atomically renamed.
No absolute path is ever returned to clients: ``audio_ref`` is a same-origin
URL under ``/api/v1/audio/``.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .errors import CacheError

FORMAT_MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
}
FORMAT_EXT = {
    "mp3": ".mp3",
    "wav": ".wav",
    "ogg": ".ogg",
    "opus": ".opus",
    "aac": ".aac",
    "flac": ".flac",
}
_ALLOWED_SUFFIXES = {value.lstrip(".") for value in FORMAT_EXT.values()}
_FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.(mp3|wav|ogg|opus|aac|flac)$")


def normalize_audio_format(value: str | None) -> str:
    fmt = (value or "mp3").strip().lower().lstrip(".")
    if fmt not in FORMAT_EXT:
        raise CacheError(f"不支持的音频格式: {fmt}")
    return fmt


def mime_for_format(fmt: str) -> str:
    return FORMAT_MIME[normalize_audio_format(fmt)]


@dataclass(frozen=True)
class CachedAudio:
    filename: str
    path: Path
    mime: str
    fmt: str
    size: int


class CacheWriter:
    def __init__(self, cache: AudioCache, fmt: str) -> None:
        self.cache = cache
        self.fmt = normalize_audio_format(fmt)
        self.token = secrets.token_hex(16)
        self.final_name = f"{self.token}{FORMAT_EXT[self.fmt]}"
        self.final_path = cache.root / self.final_name
        self.part_path = cache.root / f".{self.token}.part"
        self._file: BinaryIO | None = None
        self.size = 0
        self._done = False

    def append(self, chunk: bytes) -> None:
        if self._done:
            raise CacheError("缓存写入已结束")
        if not chunk:
            return
        if self._file is None:
            self.cache.root.mkdir(parents=True, exist_ok=True)
            self._file = open(self.part_path, "xb")
            _restrict_file(self.part_path)
        self._file.write(chunk)
        self.size += len(chunk)

    def finalize(self) -> CachedAudio:
        if self._done:
            raise CacheError("缓存写入已结束")
        if self._file is None or self.size == 0:
            self.abort()
            raise CacheError("厂商返回了空音频")
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self._file = None
        os.replace(self.part_path, self.final_path)
        _restrict_file(self.final_path)
        self._done = True
        self.cache.prune()
        return CachedAudio(
            filename=self.final_name,
            path=self.final_path,
            mime=mime_for_format(self.fmt),
            fmt=self.fmt,
            size=self.size,
        )

    def abort(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
        for path in (self.part_path, self.final_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._done = True


class AudioCache:
    def __init__(
        self,
        root: str | Path,
        *,
        max_files: int = 200,
        max_age_hours: int = 168,
        url_prefix: str = "/api/v1/audio",
    ) -> None:
        self.root = Path(root)
        self.max_files = max_files
        self.max_age_hours = max_age_hours
        self.url_prefix = url_prefix.rstrip("/")
        self.ensure()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _restrict_dir(self.root)

    def lease(self, fmt: str = "mp3") -> CacheWriter:
        return CacheWriter(self, fmt)

    def ref_for(self, filename: str) -> str:
        self._validate_filename(filename)
        return f"{self.url_prefix}/{filename}"

    def resolve(self, filename: str) -> Path:
        self._validate_filename(filename)
        path = (self.root / filename).resolve()
        root = self.root.resolve()
        if path.parent != root:
            raise CacheError("非法音频路径")
        if not path.is_file():
            raise CacheError("音频不存在或已被清理")
        return path

    def _validate_filename(self, filename: str) -> None:
        if not _FILENAME_RE.fullmatch(filename):
            raise CacheError("非法音频文件名")

    def prune(self) -> int:
        """Delete expired/excess cache entries. Returns deleted count."""

        now = time.time()
        max_age = self.max_age_hours * 3600
        files: list[tuple[float, Path]] = []
        deleted = 0
        self.ensure()
        for path in self.root.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.endswith(".part") or path.suffix.lstrip(".") in _ALLOWED_SUFFIXES:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                # Never delete a very fresh in-progress file.
                if path.name.endswith(".part") and now - stat.st_mtime < 3600:
                    continue
                files.append((stat.st_mtime, path))

        files.sort(key=lambda item: item[0])  # oldest first
        for mtime, path in files:
            expired = now - mtime > max_age
            excess = len(files) - deleted > self.max_files
            if expired or excess:
                try:
                    path.unlink()
                    deleted += 1
                except OSError:
                    pass
        return deleted

    def clear(self) -> int:
        deleted = 0
        self.ensure()
        for path in self.root.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.endswith(".part") or path.suffix.lstrip(".") in _ALLOWED_SUFFIXES:
                try:
                    path.unlink()
                    deleted += 1
                except OSError:
                    pass
        return deleted

    def stats(self) -> dict[str, int]:
        count = 0
        total = 0
        for path in self.root.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.endswith(".part") or path.suffix.lstrip(".") in _ALLOWED_SUFFIXES:
                count += 1
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return {
            "files": count,
            "bytes": total,
            "max_files": self.max_files,
            "max_age_hours": self.max_age_hours,
        }


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _restrict_dir(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
