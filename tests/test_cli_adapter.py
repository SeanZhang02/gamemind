"""Unit tests for `gamemind adapter validate` CLI subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

from gamemind import cli
from gamemind.adapter.schema import CURRENT_SCHEMA_VERSION

VALID_YAML = f"""\
schema_version: {CURRENT_SCHEMA_VERSION}
display_name: "Test Game"
actions:
  forward: "W"
  attack: "MouseLeft"
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


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def adapters_root(tmp_path: Path) -> Path:
    root = tmp_path / "adapters"
    root.mkdir()
    return root


def test_validate_valid_adapter_returns_0(
    adapters_root: Path, capsys: pytest.CaptureFixture
) -> None:
    f = adapters_root / "ok.yaml"
    _write(f, VALID_YAML)
    rc = cli._cmd_adapter_validate(f, adapters_root)
    assert rc == 0
    captured = capsys.readouterr()
    assert "OK" in captured.out
    assert "Test Game" in captured.out
    assert "2 bindings" in captured.out
    assert "test_goal" in captured.out
    assert "schema_version" in captured.out


def test_validate_invalid_schema_returns_1(
    adapters_root: Path, capsys: pytest.CaptureFixture
) -> None:
    f = adapters_root / "bad.yaml"
    _write(f, "schema_version: 99\ndisplay_name: x\nactions: {}\ngoal_grammars: {}")
    rc = cli._cmd_adapter_validate(f, adapters_root)
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.out


def test_validate_missing_file_returns_2(
    adapters_root: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = cli._cmd_adapter_validate(adapters_root / "missing.yaml", adapters_root)
    assert rc == 2
    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_validate_path_traversal_returns_1(
    adapters_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    outside = tmp_path / "outside.yaml"
    _write(outside, VALID_YAML)
    rc = cli._cmd_adapter_validate(outside, adapters_root)
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.out


def test_validate_malformed_yaml_returns_1(
    adapters_root: Path, capsys: pytest.CaptureFixture
) -> None:
    f = adapters_root / "broken.yaml"
    _write(f, "schema_version: 1\n  bad_indent: [unclosed")
    rc = cli._cmd_adapter_validate(f, adapters_root)
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.out


def test_parser_accepts_adapter_validate() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["adapter", "validate", "path/to/foo.yaml"])
    assert args.command == "adapter"
    assert args.adapter_cmd == "validate"
    assert args.path == Path("path/to/foo.yaml")
    assert args.adapters_root is None


def test_parser_accepts_adapter_validate_with_root() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["adapter", "validate", "foo.yaml", "--adapters-root", "/custom/root"])
    assert args.adapters_root == Path("/custom/root")


def test_main_wires_adapter_command(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    adapters_root = tmp_path / "adapters"
    adapters_root.mkdir()
    f = adapters_root / "ok.yaml"
    _write(f, VALID_YAML)
    rc = cli.main(
        [
            "adapter",
            "validate",
            str(f),
            "--adapters-root",
            str(adapters_root),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "OK" in captured.out


def test_shipped_minecraft_adapter_validates() -> None:
    """Regression: the shipped adapters/minecraft.yaml must validate."""
    repo_root = Path(__file__).resolve().parent.parent
    adapter_path = repo_root / "adapters" / "minecraft.yaml"
    adapters_root = repo_root / "adapters"
    if not adapter_path.exists():
        pytest.skip("adapters/minecraft.yaml not shipped")
    rc = cli._cmd_adapter_validate(adapter_path, adapters_root)
    assert rc == 0
