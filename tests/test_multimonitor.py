from unittest.mock import MagicMock

import pytest

import windows_use.agent.desktop.service as desktop_module
import windows_use.agent.tree.service as tree_module
import windows_use.uia.core as core
from windows_use.agent.desktop.service import Desktop
from windows_use.agent.tree.service import Tree


def test_tree_keeps_controls_on_left_and_upper_monitors(monkeypatch):
    tree = Tree.__new__(Tree)
    monkeypatch.setattr(tree_module, "GetVirtualScreenRect", lambda: (-1920, -1080, 3840, 2160))
    tree.refresh_screen_box()
    window = core.Rect(-1800, -1000, -200, 900)
    control = core.Rect(-1700, -800, -1400, -700)
    box = tree.iou_bounding_box(window, control)
    assert (box.left, box.top, box.right, box.bottom) == (-1700, -800, -1400, -700)
    assert box.width == 300 and box.height == 100
    monkeypatch.setattr(tree_module, "GetVirtualScreenRect", lambda: (0, 0, 1920, 1080))
    tree.refresh_screen_box()
    assert tree.screen_box.left == 0


@pytest.mark.parametrize("function", [core.Click, core.RightClick, core.MiddleClick])
def test_mouse_uses_negative_physical_coordinates_without_primary_screen_normalization(
    monkeypatch, function
):
    position = MagicMock(return_value=True)
    events = MagicMock()
    monkeypatch.setattr(core, "SetCursorPos", position)
    monkeypatch.setattr(core, "mouse_event", events)
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    function(-1100, -50, waitTime=0)
    position.assert_called_once_with(-1100, -50)
    assert events.call_count == 2
    for call in events.call_args_list:
        assert not call.args[0] & core.MouseEventFlag.Absolute
        assert call.args[1:3] == (0, 0)


def test_failed_cursor_position_does_not_click_at_old_location(monkeypatch):
    monkeypatch.setattr(core, "SetCursorPos", lambda *_: False)
    mouse = MagicMock()
    monkeypatch.setattr(core, "mouse_event", mouse)
    with pytest.raises(RuntimeError, match="position"):
        core.Click(-1100, 200)
    mouse.assert_not_called()


def test_model_receives_monitor_and_screenshot_origin(monkeypatch):
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    desktop.use_annotation = False
    monkeypatch.setattr(desktop_module.uia, "GetVirtualScreenRect", lambda: (-1920, 0, 3840, 1080))
    monkeypatch.setattr(
        desktop_module.uia,
        "GetMonitorsRect",
        lambda: [core.Rect(-1920, 0, 0, 1080), core.Rect(0, 0, 1920, 1080)],
    )
    result = desktop.describe_displays()
    assert "Monitor 2" in result
    assert "origin is (-1920, 0)" in result
    assert "negative coordinates" in result


def test_caption_fallback_does_not_click_occluded_window(monkeypatch):
    desktop = Desktop.__new__(Desktop)
    monkeypatch.setattr(desktop_module.win32gui, "GetWindowRect", lambda _: (-1900, 0, -10, 1000))
    monkeypatch.setattr(desktop_module.win32gui, "WindowFromPoint", lambda _: 456)
    monkeypatch.setattr(desktop_module.win32gui, "GetAncestor", lambda *_: 456)
    click = MagicMock()
    monkeypatch.setattr(desktop_module.uia, "Click", click)
    assert not desktop.activate_visible_caption(123)
    click.assert_not_called()
