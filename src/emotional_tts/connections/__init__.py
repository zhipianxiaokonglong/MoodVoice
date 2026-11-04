"""User-managed API connections (URL + key) and discovery."""

from .manager import ConnectionManager
from .models import (
    ConnectionRecord,
    DiscoverRequest,
    Protocol,
    SaveConnectionRequest,
    TestConnectionRequest,
)
from .store import ConnectionStore, KeyringKeyStore, KeyStore, MemoryKeyStore

__all__ = [
    "ConnectionManager",
    "ConnectionRecord",
    "ConnectionStore",
    "DiscoverRequest",
    "KeyStore",
    "KeyringKeyStore",
    "MemoryKeyStore",
    "Protocol",
    "SaveConnectionRequest",
    "TestConnectionRequest",
]
