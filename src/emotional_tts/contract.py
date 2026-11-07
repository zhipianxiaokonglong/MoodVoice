"""Machine-readable interface contract.

Consumers (assembly layer, UI, launchers) should not have to read source code or
docs to learn which tags are accepted, which error codes exist, or whether an
error is worth retrying. This module derives the contract from the *live*
configuration (style book, routes, limits, error catalog and app routes) so the
published document cannot drift from behaviour.

Versioning rules (see docs/CONTRACT.md):

* additive change (new alias / new error code / new optional field) -> minor+1
* semantic change or removal -> major+1
* ``contract_hash`` covers only interface facts, not labels or vendor tuning,
  so copy edits do not look like contract drift.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import OutputEvent
from .errors import RETRYABLE_CODES
from .rendering import TYPE_ALIASES
from .styles import normalize_tag

CONTRACT_VERSION = "1.1.0"

#: Verified upstream tag sets this layer accepts. Only add a set once its
#: membership is confirmed against the other repository, not by guessing.
ACCEPTED_UPSTREAM_SETS: dict[str, list[str]] = {
    "omninexus_v1": [
        "joy",
        "sadness",
        "anger",
        "fear",
        "surprise",
        "confusion",
        "frustration",
        "gratitude",
        "neutral",
    ],
}

OUTPUT_STATUS_VALUES = ("synthesized", "text_fallback", "text_only", "preparing")
OUTPUT_HEADERS = (
    "X-MoodVoice-Status",
    "X-MoodVoice-Degraded",
    "X-MoodVoice-Egress",
    "X-MoodVoice-Contract-Version",
)

_CANONICAL_TYPES = ("text", "code", "list", "markdown")


def describe_endpoints(app: Any) -> dict[str, list[str]]:
    """Summarise registered routes so the contract cannot miss an endpoint."""

    rest: set[str] = set()
    websocket: set[str] = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        if not path:
            continue
        methods = getattr(route, "methods", None)
        if methods:
            for method in sorted(set(methods) - {"HEAD", "OPTIONS"}):
                rest.add(f"{method} {path}")
        elif path.startswith("/ws/"):
            websocket.add(path)
    return {"rest": sorted(rest), "ws": sorted(websocket)}


def _emotion_section(styles) -> dict[str, Any]:
    alias_map: dict[str, str] = {}
    for key, definition in styles.emotions.items():
        alias_map[key] = key
        for alias in definition.aliases:
            normalized = normalize_tag(alias)
            if normalized:
                alias_map[normalized] = key
    return {
        "canonical": sorted(styles.emotions),
        "fallback": styles.fallback_key,
        "unknown_policy": "fallback_and_flag",
        "unknown_flags": ["style.mapping_hit=false", "style.source_tag", "diagnostics"],
        "aliases": dict(sorted(alias_map.items())),
        "accepted_upstream_sets": {
            name: sorted(values) for name, values in ACCEPTED_UPSTREAM_SETS.items()
        },
    }


def _type_section() -> dict[str, Any]:
    aliases = {alias: target for alias, target in sorted(TYPE_ALIASES.items())}
    return {
        "canonical": list(_CANONICAL_TYPES),
        "coerce_to": "text",
        "unknown_policy": "coerce_to_default",
        "aliases": aliases,
    }


def _intent_section(router) -> dict[str, Any]:
    return {
        "canonical": sorted(router.model.intents),
        "unknown_policy": "route_to_unknown_destination",
        "unknown_destination": router.model.unknown_intent,
        "routes": {
            name: {"destination": route.destination, "synthesize": route.synthesize}
            for name, route in sorted(router.model.intents.items())
        },
    }


def _output_section() -> dict[str, Any]:
    schema_version = OutputEvent.model_fields["schema_version"].default
    return {
        "required_fields": ["event_id", "text", "audio_ref", "style"],
        "schema_version": schema_version,
        "status_values": list(OUTPUT_STATUS_VALUES),
        "headers": list(OUTPUT_HEADERS),
        "degraded_semantics": "text_always_intact_audio_ref_null",
    }


def _errors_section() -> dict[str, Any]:
    return {
        "shape": "error.code + error.message + error.retryable (+ request_id, details)",
        "retryable_semantics": (
            "retryable=true means the same request may succeed later; "
            "retryable=false means retrying it unchanged cannot help"
        ),
        "codes": {
            code: {"retryable": retryable} for code, retryable in sorted(RETRYABLE_CODES.items())
        },
    }


def _limits_section(config) -> dict[str, Any]:
    return {
        "max_input_chars": config.app.max_input_chars,
        "max_tts_chars": config.app.max_tts_chars,
    }


def _hash_payload(document: dict[str, Any]) -> dict[str, Any]:
    # Interface facts only: no labels, no module version, no vendor tuning.
    return {
        "contract_version": document["contract_version"],
        "schema_version": document["output"]["schema_version"],
        "tags": document["tags"],
        "output": document["output"],
        "errors": document["errors"],
        "limits": document["limits"],
        "endpoints": document["endpoints"],
    }


def compute_hash(document: dict[str, Any]) -> str:
    raw = json.dumps(
        _hash_payload(document), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_contract(*, styles, router, config, app) -> dict[str, Any]:
    """Build the contract document from the live configuration."""

    document: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "module_version": config.app.version,
        "contract_hash": "",
        "tags": {
            "emotion": _emotion_section(styles),
            "type": _type_section(),
            "intent": _intent_section(router),
        },
        "output": _output_section(),
        "errors": _errors_section(),
        "limits": _limits_section(config),
        "endpoints": describe_endpoints(app),
    }
    document["contract_hash"] = compute_hash(document)
    return document


class ContractProvider:
    """Build once, serve many.

    The document is static for a running process: style book, routes, limits and
    error catalog are loaded at startup. Endpoints are resolved on first use so
    every route decorator has already run.
    """

    def __init__(self, *, styles, router, config, app) -> None:
        self._styles = styles
        self._router = router
        self._config = config
        self._app = app
        self._document: dict[str, Any] | None = None

    def document(self) -> dict[str, Any]:
        if self._document is None:
            self._document = build_contract(
                styles=self._styles,
                router=self._router,
                config=self._config,
                app=self._app,
            )
        return self._document

    @property
    def version(self) -> str:
        return CONTRACT_VERSION

    @property
    def contract_hash(self) -> str:
        return str(self.document()["contract_hash"])

    def summary(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "contract_hash": self.contract_hash,
            "contract_url": "/api/v1/contract",
        }
