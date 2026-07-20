"""Derive stable source, event, lot, and exception identifiers."""

from __future__ import annotations

import hashlib
import json

StablePart = str | int

def stable_id(prefix: str, *parts: StablePart) -> str:
    """Hash stable facts into a readable deterministic identifier."""
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError(f"ID prefix must be alphanumeric or underscore, got {prefix!r}.")
    if any(isinstance(part, bool) for part in parts):
        raise ValueError("Boolean values are not valid stable ID facts.")
    payload = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()[:20]
    return f"{prefix}_{digest}"

def derive_source_id(
    *,
    fixture_set_id: str,
    wallet_id: str,
    fixture_record_id: str,
    signature: str,
    slot: int,
    instruction_index: int,
) -> str:
    """Identify one wallet view of one source instruction."""
    return stable_id(
        "src",
        fixture_set_id,
        wallet_id,
        fixture_record_id,
        signature,
        slot,
        instruction_index,
    )

def derive_event_id(*, source_id: str, component: str = "primary") -> str:
    """Identify a normalized component derived from stable source evidence."""
    return stable_id("evt", source_id, component)
