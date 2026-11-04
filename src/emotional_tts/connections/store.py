"""Connection metadata store and OS-backed key storage."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..errors import ConnectionFailure
from .models import ConnectionRecord

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "moodvoice.tts.connection"


class KeyStore(Protocol):
    def set(self, connection_id: str, api_key: str) -> None: ...
    def get(self, connection_id: str) -> str | None: ...
    def delete(self, connection_id: str) -> None: ...
    @property
    def persistent(self) -> bool: ...


class MemoryKeyStore:
    """Session-only fallback; never writes plaintext keys to disk."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}
        self._lock = threading.RLock()

    @property
    def persistent(self) -> bool:
        return False

    def set(self, connection_id: str, api_key: str) -> None:
        with self._lock:
            self._keys[connection_id] = api_key

    def get(self, connection_id: str) -> str | None:
        with self._lock:
            return self._keys.get(connection_id)

    def delete(self, connection_id: str) -> None:
        with self._lock:
            self._keys.pop(connection_id, None)


class KeyringKeyStore(MemoryKeyStore):
    """Persist keys in the OS credential vault (Windows Credential Manager)."""

    def __init__(self) -> None:
        super().__init__()
        try:
            import keyring

            self._keyring = keyring
            backend = keyring.get_keyring()
            name = type(backend).__name__
            self._persistent = name not in {"FailKeyring", "NullKeyring", "PlaintextKeyring"}
        except Exception:
            self._keyring = None
            self._persistent = False

    @property
    def persistent(self) -> bool:
        return self._persistent

    def set(self, connection_id: str, api_key: str) -> None:
        super().set(connection_id, api_key)
        if not self._persistent:
            return
        try:
            self._keyring.set_password(_KEYRING_SERVICE, connection_id, api_key)
        except Exception as exc:
            raise ConnectionFailure(
                "无法写入系统密钥库，密钥仅保留在本次会话",
                code="key_store_unavailable",
                stage="key_store",
            ) from exc

    def get(self, connection_id: str) -> str | None:
        cached = super().get(connection_id)
        if cached:
            return cached
        if not self._persistent:
            return None
        try:
            value = self._keyring.get_password(_KEYRING_SERVICE, connection_id)
        except Exception:
            return None
        if value:
            super().set(connection_id, value)
        return value

    def delete(self, connection_id: str) -> None:
        super().delete(connection_id)
        if not self._persistent:
            return
        try:
            self._keyring.delete_password(_KEYRING_SERVICE, connection_id)
        except Exception:
            pass


class ConnectionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._records: dict[str, ConnectionRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw.get("connections", []):
                record = ConnectionRecord.model_validate(item)
                self._records[record.id] = record
        except Exception as exc:
            # A single bad byte must not brick startup: keep the file aside so
            # the user can recover/inspect it, then continue with no records.
            backup = self.path.with_name(
                f"{self.path.name}.corrupt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
            )
            try:
                os.replace(self.path, backup)
            except OSError:
                backup = None
            logger.warning(
                "connections.json 无法解析，已跳过（备份: %s）: %s", backup or "失败", exc
            )
            self._records.clear()

    def _save(self) -> None:
        payload = {
            "version": 1,
            "connections": [
                record.model_dump(mode="json")
                for record in sorted(self._records.values(), key=lambda r: r.created_at)
            ],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def list(self) -> list[ConnectionRecord]:
        with self._lock:
            return list(self._records.values())

    def get(self, connection_id: str) -> ConnectionRecord | None:
        with self._lock:
            return self._records.get(connection_id)

    def put(self, record: ConnectionRecord) -> None:
        with self._lock:
            self._records[record.id] = record
            self._save()

    def delete(self, connection_id: str) -> None:
        with self._lock:
            self._records.pop(connection_id, None)
            self._save()
