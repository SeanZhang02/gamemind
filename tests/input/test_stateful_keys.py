"""Tests for stateful key management (key_down / key_up / release_all).

Uses a mock pydirectinput module to verify routing and held-key tracking
without requiring Windows or the real pydirectinput-rgx package.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_pydirectinput(monkeypatch):
    """Inject a mock pydirectinput module so tests run on any platform."""
    mock_mod = ModuleType("pydirectinput")
    mock_mod.keyDown = MagicMock()
    mock_mod.keyUp = MagicMock()
    mock_mod.mouseDown = MagicMock()
    mock_mod.mouseUp = MagicMock()
    monkeypatch.setitem(sys.modules, "pydirectinput", mock_mod)
    # Force platform to win32 so __init__ doesn't bail
    monkeypatch.setattr(sys, "platform", "win32")
    return mock_mod


@pytest.fixture
def backend(_mock_pydirectinput):
    """Create a fresh PyDirectInputBackend with mock pydirectinput."""
    from gamemind.input.pydirectinput_backend import PyDirectInputBackend

    b = PyDirectInputBackend.__new__(PyDirectInputBackend)
    b._initialized = True
    b._init_error = None
    b._held_keys = set()
    return b


@pytest.fixture
def pdi(_mock_pydirectinput):
    """Return the mock pydirectinput module."""
    return _mock_pydirectinput


HWND = 0  # hwnd=0 skips focus checks


class TestKeyDown:
    def test_key_down_calls_pydirectinput(self, backend, pdi):
        backend.key_down(HWND, "w")

        pdi.keyDown.assert_called_once_with("w")
        assert "w" in backend._held_keys

    def test_key_down_idempotent(self, backend, pdi):
        """Calling key_down twice for the same key should not double-press."""
        backend.key_down(HWND, "w")
        backend.key_down(HWND, "w")

        pdi.keyDown.assert_called_once_with("w")
        assert "w" in backend._held_keys


class TestKeyUp:
    def test_key_up_calls_pydirectinput(self, backend, pdi):
        # Hold the key first so _held_keys is populated
        backend.key_down(HWND, "space")
        pdi.keyDown.reset_mock()

        backend.key_up(HWND, "space")

        pdi.keyUp.assert_called_once_with("space")
        assert "space" not in backend._held_keys

    def test_key_up_without_prior_down(self, backend, pdi):
        """key_up should still call pydirectinput even if key wasn't tracked."""
        backend.key_up(HWND, "a")

        pdi.keyUp.assert_called_once_with("a")
        assert "a" not in backend._held_keys


class TestReleaseAll:
    def test_release_all_releases_held_keys(self, backend, pdi):
        backend.key_down(HWND, "w")
        backend.key_down(HWND, "space")
        pdi.keyUp.reset_mock()

        backend.release_all(HWND)

        assert backend._held_keys == set()
        released_keys = {call.args[0] for call in pdi.keyUp.call_args_list}
        assert released_keys == {"w", "space"}

    def test_release_all_empty(self, backend, pdi):
        """release_all with no held keys is a no-op."""
        backend.release_all(HWND)

        pdi.keyUp.assert_not_called()
        pdi.mouseUp.assert_not_called()


class TestMouseKeyRouting:
    def test_mouse_left_routes_to_mouse_down(self, backend, pdi):
        backend.key_down(HWND, "mouse_left")

        pdi.mouseDown.assert_called_once_with(button="left")
        pdi.keyDown.assert_not_called()
        assert "mouse_left" in backend._held_keys

    def test_mouse_right_routes_to_mouse_down(self, backend, pdi):
        backend.key_down(HWND, "mouse_right")

        pdi.mouseDown.assert_called_once_with(button="right")

    def test_mouse_middle_routes_to_mouse_down(self, backend, pdi):
        backend.key_down(HWND, "mouse_middle")

        pdi.mouseDown.assert_called_once_with(button="middle")

    def test_mouse_key_up_routes_to_mouse_up(self, backend, pdi):
        backend.key_down(HWND, "mouse_left")
        pdi.mouseDown.reset_mock()

        backend.key_up(HWND, "mouse_left")

        pdi.mouseUp.assert_called_once_with(button="left")
        assert "mouse_left" not in backend._held_keys

    def test_release_all_with_mixed_keys(self, backend, pdi):
        """release_all handles both keyboard and mouse keys."""
        backend.key_down(HWND, "w")
        backend.key_down(HWND, "mouse_left")
        pdi.keyUp.reset_mock()
        pdi.mouseUp.reset_mock()

        backend.release_all(HWND)

        assert backend._held_keys == set()
        pdi.keyUp.assert_called_once_with("w")
        pdi.mouseUp.assert_called_once_with(button="left")
