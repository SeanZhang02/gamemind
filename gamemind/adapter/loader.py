"""Adapter YAML loader.

Per Amendment A8, loading is:
  1. Path-traversal guard (Amendment A9): resolve the adapter path and
     verify it's within `adapters/`. Reject symlinks.
  2. yaml.safe_load — blocks Python object tag injection by default.
  3. Pydantic strict validation — unknown keys raise AdapterSchemaError.

The v2.4 py-code rejector string-walk heuristic is NOT applied (it was
wrong-threat security theater per §10.6.C Amendment A8). Real adversarial
risk from adapter text is addressed at brain-prompt assembly time via
Design Rule 4 observation tags.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from gamemind.adapter.schema import Adapter
from gamemind.errors import (
    AdapterPathTraversalError,
    AdapterPyInjectionError,
    AdapterSchemaError,
    AdapterYAMLParseError,
)


def _resolve_and_guard(adapter_path: Path | str, adapters_root: Path) -> Path:
    """Resolve the adapter path and verify it's within adapters_root.

    Amendment A9:
      - `Path(...).resolve()` follows symlinks and normalizes `..`
      - `is_relative_to(adapters_root.resolve())` rejects escapes
      - Direct symlink rejection (via `is_symlink()`) prevents
        symlink-to-outside tricks that resolve might accept

    Raises AdapterPathTraversalError on any violation.
    """
    p = Path(adapter_path)
    # Reject symlinks at the adapter path itself (not just within).
    # Intermediate symlinks are still allowed because `resolve()` normalizes.
    if p.is_symlink():
        raise AdapterPathTraversalError(
            cause=f"symlink rejected: {p}",
            path=str(p),
        )
    resolved = p.resolve()
    root_resolved = adapters_root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise AdapterPathTraversalError(
            cause=f"path escapes adapters_root: {resolved} not under {root_resolved}",
            path=str(resolved),
            root=str(root_resolved),
        ) from exc
    return resolved


def _parse_yaml(resolved_path: Path) -> Any:
    """yaml.safe_load with error mapping to AdapterYAMLParseError."""
    try:
        text = resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AdapterYAMLParseError(
            cause=f"cannot read adapter file: {exc}",
            path=str(resolved_path),
        ) from exc

    # yaml.safe_load rejects !!python/... tags. If the YAML does contain
    # a python-object tag, safe_load raises yaml.constructor.ConstructorError.
    # We map that to AdapterPyInjectionError for a clearer signal.
    try:
        return yaml.safe_load(text)
    except yaml.constructor.ConstructorError as exc:
        raise AdapterPyInjectionError(
            cause=f"yaml.safe_load rejected Python tag: {exc}",
            path=str(resolved_path),
        ) from exc
    except yaml.YAMLError as exc:
        raise AdapterYAMLParseError(
            cause=f"YAML parse error: {exc}",
            path=str(resolved_path),
        ) from exc


def load(adapter_path: Path | str, *, adapters_root: Path | None = None) -> Adapter:
    """Load and validate an adapter YAML.

    Args:
      adapter_path: path to the YAML file (absolute or relative to cwd)
      adapters_root: path under which all adapters must live. Defaults
                     to `./adapters` relative to cwd.

    Returns:
      Validated Adapter pydantic model (frozen).

    Raises:
      AdapterPathTraversalError: path escape or symlink
      AdapterPyInjectionError: YAML contained Python tag (safe_load rejected)
      AdapterYAMLParseError: YAML syntax error
      AdapterSchemaError: pydantic validation error (unknown key,
                          wrong type, missing required field, bad
                          schema_version)
    """
    if adapters_root is None:
        adapters_root = Path("adapters")
    resolved = _resolve_and_guard(adapter_path, adapters_root)
    data = _parse_yaml(resolved)
    if not isinstance(data, dict):
        raise AdapterSchemaError(
            cause=f"adapter top-level must be a YAML mapping, got {type(data).__name__}",
            path=str(resolved),
        )
    try:
        return Adapter.model_validate(data)
    except ValidationError as exc:
        raise AdapterSchemaError(
            cause=f"pydantic validation failed: {exc.error_count()} error(s)",
            path=str(resolved),
            errors=exc.errors(),
        ) from exc


def validate(adapter_path: Path | str, *, adapters_root: Path | None = None) -> list[str]:
    """Validate an adapter YAML without raising.

    Returns a list of human-readable error messages. Empty list means
    the adapter is valid.

    Use this for `gamemind adapter validate <path>` CLI subcommand
    (Phase C Step 3 scope, DX-F5 checklist item).
    """
    try:
        load(adapter_path, adapters_root=adapters_root)
    except (
        AdapterPathTraversalError,
        AdapterPyInjectionError,
        AdapterYAMLParseError,
        AdapterSchemaError,
    ) as exc:
        return [str(exc)]
    return []
