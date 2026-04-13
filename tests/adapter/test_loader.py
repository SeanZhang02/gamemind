"""Unit tests for adapter loader — path traversal + py-code injection + schema."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gamemind.adapter.loader import load, validate
from gamemind.adapter.schema import CURRENT_SCHEMA_VERSION
from gamemind.errors import (
    AdapterPathTraversalError,
    AdapterPyInjectionError,
    AdapterSchemaError,
    AdapterYAMLParseError,
)


MINIMAL_YAML = f"""\
schema_version: {CURRENT_SCHEMA_VERSION}
display_name: "Test Game"
actions:
  forward: "W"
goal_grammars:
  test_goal:
    description: "A test"
    preconditions: []
    success_check:
      predicate:
        type: time_limit
        seconds: 10.0
    abort_conditions: []
"""


@pytest.fixture
def adapters_dir(tmp_path: Path) -> Path:
    root = tmp_path / "adapters"
    root.mkdir()
    return root


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_minimal_valid(adapters_dir: Path) -> None:
    adapter_file = adapters_dir / "test.yaml"
    _write(adapter_file, MINIMAL_YAML)
    adapter = load(adapter_file, adapters_root=adapters_dir)
    assert adapter.display_name == "Test Game"
    assert "forward" in adapter.actions


def test_load_rejects_path_outside_root(adapters_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.yaml"
    _write(outside, MINIMAL_YAML)
    with pytest.raises(AdapterPathTraversalError):
        load(outside, adapters_root=adapters_dir)


def test_load_rejects_dotdot_escape(adapters_dir: Path, tmp_path: Path) -> None:
    # Write adapter at tmp_path/sneaky.yaml, try to load via adapters/../sneaky.yaml
    sneaky = tmp_path / "sneaky.yaml"
    _write(sneaky, MINIMAL_YAML)
    escape_path = adapters_dir / ".." / "sneaky.yaml"
    with pytest.raises(AdapterPathTraversalError):
        load(escape_path, adapters_root=adapters_dir)


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires admin on Windows")
def test_load_rejects_symlink(adapters_dir: Path, tmp_path: Path) -> None:
    target = tmp_path / "real.yaml"
    _write(target, MINIMAL_YAML)
    link = adapters_dir / "link.yaml"
    link.symlink_to(target)
    with pytest.raises(AdapterPathTraversalError):
        load(link, adapters_root=adapters_dir)


def test_load_rejects_python_object_tag(adapters_dir: Path) -> None:
    # yaml.safe_load rejects !!python/object tags — we map that to
    # AdapterPyInjectionError for a clearer signal.
    malicious = """\
schema_version: 1
display_name: !!python/object/apply:os.system ["echo hacked"]
actions:
  forward: "W"
goal_grammars:
  test:
    description: "x"
    success_check:
      predicate:
        type: time_limit
        seconds: 10
"""
    f = adapters_dir / "malicious.yaml"
    _write(f, malicious)
    with pytest.raises(AdapterPyInjectionError):
        load(f, adapters_root=adapters_dir)


def test_load_rejects_malformed_yaml(adapters_dir: Path) -> None:
    f = adapters_dir / "bad.yaml"
    _write(f, "schema_version: 1\n  bad_indent: [unclosed")
    with pytest.raises(AdapterYAMLParseError):
        load(f, adapters_root=adapters_dir)


def test_load_rejects_non_mapping_top_level(adapters_dir: Path) -> None:
    f = adapters_dir / "list.yaml"
    _write(f, "- one\n- two\n")
    with pytest.raises(AdapterSchemaError, match="mapping"):
        load(f, adapters_root=adapters_dir)


def test_load_wraps_pydantic_error(adapters_dir: Path) -> None:
    f = adapters_dir / "invalid_schema.yaml"
    # Missing required field `display_name`
    _write(
        f,
        f"""\
schema_version: {CURRENT_SCHEMA_VERSION}
actions:
  forward: "W"
goal_grammars:
  test_goal:
    description: "x"
    success_check:
      predicate:
        type: time_limit
        seconds: 10.0
""",
    )
    with pytest.raises(AdapterSchemaError) as exc_info:
        load(f, adapters_root=adapters_dir)
    assert "pydantic validation failed" in str(exc_info.value)


def test_validate_returns_empty_on_valid(adapters_dir: Path) -> None:
    f = adapters_dir / "ok.yaml"
    _write(f, MINIMAL_YAML)
    assert validate(f, adapters_root=adapters_dir) == []


def test_validate_returns_errors_on_invalid(adapters_dir: Path) -> None:
    f = adapters_dir / "bad.yaml"
    _write(f, "schema_version: 99\ndisplay_name: x\nactions: {}\ngoal_grammars: {}")
    errors = validate(f, adapters_root=adapters_dir)
    assert len(errors) == 1
    # Could be schema_version error OR empty actions; both are reasons
    assert "AdapterSchemaError" in errors[0] or "schema_version" in errors[0]


def test_load_ships_minecraft_adapter() -> None:
    """Regression: the shipped adapters/minecraft.yaml must load cleanly."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    adapter_path = repo_root / "adapters" / "minecraft.yaml"
    adapters_root = repo_root / "adapters"
    if not adapter_path.exists():
        pytest.skip("adapters/minecraft.yaml not shipped yet")
    adapter = load(adapter_path, adapters_root=adapters_root)
    assert adapter.display_name == "Minecraft Java Edition"
    assert "chop_logs" in adapter.goal_grammars
    assert adapter.actions["forward"] == "W"


def test_minecraft_adapter_has_spatial_schema() -> None:
    """Minecraft adapter ships with spatial_schema in perception."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    adapter_path = repo_root / "adapters" / "minecraft.yaml"
    adapters_root = repo_root / "adapters"
    if not adapter_path.exists():
        pytest.skip("adapters/minecraft.yaml not shipped yet")
    adapter = load(adapter_path, adapters_root=adapters_root)
    ss = adapter.perception.spatial_schema
    assert ss.facing_categories == ["looking_down", "looking_at_horizon", "looking_up"]
    assert ss.distance_categories == ["close", "medium", "far"]
    assert ss.direction_categories == [
        "ahead",
        "ahead_left",
        "ahead_right",
        "left",
        "right",
        "behind",
    ]
    assert ss.anchor_max_age_frames == 20


def test_minecraft_adapter_has_intents() -> None:
    """Minecraft adapter ships with 4 intents."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    adapter_path = repo_root / "adapters" / "minecraft.yaml"
    adapters_root = repo_root / "adapters"
    if not adapter_path.exists():
        pytest.skip("adapters/minecraft.yaml not shipped yet")
    adapter = load(adapter_path, adapters_root=adapters_root)
    assert len(adapter.intents) == 4
    assert set(adapter.intents.keys()) == {"approach", "look_around", "attack_target", "retreat"}
    assert adapter.intents["attack_target"].stall_threshold_frames == 16
    assert adapter.intents["retreat"].stall_threshold_frames == 6
