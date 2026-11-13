from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import windows_use.agent.desktop.service as module
from windows_use.agent.desktop.service import Desktop


def test_switch_does_not_claim_success_when_foreground_change_fails(monkeypatch):
    desktop = Desktop.__new__(Desktop)
    desktop._find_window_by_name = lambda name: (SimpleNamespace(handle=123, name=name), None)
    desktop.bring_window_to_top = MagicMock()
    desktop.activate_visible_caption = MagicMock(return_value=False)
    monkeypatch.setattr(module.uia, "IsIconic", lambda _: False)
    monkeypatch.setattr(module.uia, "GetForegroundWindow", lambda: 456)
    clock = iter([0, 2])
    monkeypatch.setattr(module, "perf_counter", lambda: next(clock))
    message, code = desktop.switch_app("Edge")
    assert code == 1
    assert "failed" in message
    desktop.switch_app = lambda _: (message, code)
    with pytest.raises(RuntimeError, match="failed"):
        desktop.app("switch", "Edge")


def test_already_foreground_does_not_switch_again(monkeypatch):
    desktop = Desktop.__new__(Desktop)
    desktop._find_window_by_name = lambda name: (SimpleNamespace(handle=123, name=name), None)
    desktop.bring_window_to_top = MagicMock()
    monkeypatch.setattr(module.uia, "GetForegroundWindow", lambda: 123)
    message, code = desktop.switch_app("Edge")
    assert code == 0 and "already" in message
    desktop.bring_window_to_top.assert_not_called()


def test_frontend_process_windows_are_excluded_from_observation(monkeypatch):
    desktop = Desktop.__new__(Desktop)
    desktop.ignored_process_ids = {55}
    monkeypatch.setattr(
        module.win32process, "GetWindowThreadProcessId", lambda hwnd: (1, 55 if hwnd == 123 else 66)
    )
    assert desktop.is_ignored_window(123)
    assert not desktop.is_ignored_window(456)
