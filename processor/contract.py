"""Validates queue messages against the shared contract in docs/contracts/."""

from __future__ import annotations

import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def _schema_path() -> Path:
    env = os.environ.get("EVENT_SCHEMA_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "docs" / "contracts" / "internal-event.schema.json"


_schema = json.loads(_schema_path().read_text(encoding="utf-8"))
_validator = Draft202012Validator(_schema, format_checker=FormatChecker())


class InvalidEvent(ValueError):
    """The message can never be processed; it goes to the dead-letter queue."""


def parse_event(body: bytes | str) -> dict:
    try:
        evt = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvalidEvent(f"not JSON: {e}") from e
    errors = sorted(_validator.iter_errors(evt), key=lambda e: list(e.path))
    if errors:
        raise InvalidEvent("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors))
    return evt
