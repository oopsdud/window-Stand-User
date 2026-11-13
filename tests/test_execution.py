import threading
import time
from types import SimpleNamespace as NS

import pytest

from windows_use.execution import ConsentBroker, route, workspace_operation
from windows_use.sandbox import prepare


def test_routes_cannot_disguise_shell_as_background():
    assert route("shell_tool", {"thought": "safe read only"}) == "blocked"
    for name in (
        "click_tool",
        "type_tool",
        "app_tool",
        "shortcut_tool",
        "custom_tool",
        "desktop_tool",
    ):
        assert route(name, {}) == "foreground"
    assert route("workspace_tool", {}) == "background"
    assert route("ui_control_tool", {"operation": "inspect"}) == "background"
    assert route("ui_control_tool", {"operation": "invoke"}) == "foreground"


def test_consent_exact_request_and_no_replay():
    emitted = []
    broker = ConsentBroker(
        lambda kind, data: emitted.append((kind, data)), threading.Event(), timeout=1
    )
    result = []
    thread = threading.Thread(
        target=lambda: result.append(broker.request("click_tool", {}, "test"))
    )
    thread.start()
    for _ in range(100):
        if emitted:
            break
        time.sleep(0.01)
    request = emitted[0][1]["request_id"]
    broker.resolve({"request_id": "wrong", "approved": True})
    assert thread.is_alive()
    broker.resolve({"request_id": request, "approved": True})
    thread.join(2)
    assert result == [True]
    assert broker.pending is None
    broker.resolve({"request_id": request, "approved": True})
    assert broker.pending is None


def test_consent_timeout_and_cancel_are_not_approval():
    cancelled = threading.Event()
    broker = ConsentBroker(lambda *_: None, cancelled, timeout=0.01)
    assert not broker.request("click_tool", {}, "test")
    cancelled.set()
    assert not broker.request("click_tool", {}, "test")


def test_workspace_does_not_escape_or_overwrite(tmp_path):
    workspace_operation(tmp_path, "create", "result.txt", "hello")
    assert workspace_operation(tmp_path, "read", "result.txt") == "hello"
    with pytest.raises(FileExistsError):
        workspace_operation(tmp_path, "create", "result.txt", "bad")
    for path in ("../outside.txt", "C:/outside.txt", "result.txt:stream"):
        with pytest.raises(ValueError):
            workspace_operation(tmp_path, "create", path, "bad")


def test_sandbox_only_shares_task_folders_and_disables_redirection(tmp_path):
    from xml.etree import ElementTree as ET

    path = prepare(tmp_path)
    root = ET.parse(path).getroot()
    assert root.findtext("Networking") == "Disable"
    assert root.findtext("ClipboardRedirection") == "Disable"
    folders = root.findall("MappedFolders/MappedFolder")
    assert len(folders) == 2
    assert [f.findtext("ReadOnly") for f in folders] == ["true", "false"]


@pytest.mark.asyncio
async def test_both_registry_paths_gate_before_dispatch():
    from windows_use.agent.registry.service import Registry
    from windows_use.agent.tools.views import App
    from windows_use.safety import SafetyPolicy
    from windows_use.tools import Tool

    calls = []

    @Tool("app_tool", model=App)
    def switch(**kwargs):
        calls.append(kwargs)
        return "switched"

    desktop = NS(authorize_action=lambda *_: "not approved")
    registry = Registry([switch], SafetyPolicy(["system"]))
    for result in (
        registry.execute("app_tool", {"thought": "switch", "mode": "switch"}, desktop),
        await registry.aexecute("app_tool", {"thought": "switch", "mode": "switch"}, desktop),
    ):
        assert not result.is_success and result.safety_blocked
    assert not calls


def scene():
    return NS(
        active_window=NS(handle=12, name="Editor"),
        tree_state=NS(
            interactive_nodes=[
                NS(
                    name="Document",
                    control_type="Edit",
                    hwnd=12,
                    center=NS(x=40, y=50),
                    safety_identity=(1, 2),
                    safety_bounds=(0, 0, 80, 100),
                    metadata={"has_focused": True},
                )
            ]
        ),
    )


@pytest.mark.parametrize("change", ["window", "identity", "bounds", "focus", "position"])
def test_approved_action_is_invalidated_when_scene_changes(monkeypatch, change):
    import copy

    import windows_use.execution as module

    old = scene()
    fresh = copy.deepcopy(old)
    node = fresh.tree_state.interactive_nodes[0]
    if change == "window":
        fresh.active_window.handle = 13
    if change == "identity":
        node.safety_identity = (3, 4)
    if change == "bounds":
        node.safety_bounds = (0, 0, 85, 100)
    if change == "focus":
        node.metadata["has_focused"] = False
    if change == "position":
        node.center.x = 80
    reason = module.authorize_host_action(
        NS(desktop_state=old),
        NS(request=lambda *_: True),
        "click_tool",
        {},
        lambda: fresh,
        lambda: False,
    )
    assert reason


def test_unchanged_approved_action_passes_and_rejection_never_observes(monkeypatch):
    import windows_use.execution as module

    state = scene()
    reads = []

    # Windows input timestamps change even for a harmless pointer move.
    import ctypes

    tick = 100

    def moving_mouse(pointer):
        nonlocal tick
        tick += 1
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))[1] = tick
        return 1

    monkeypatch.setattr(ctypes.windll.user32, "GetLastInputInfo", moving_mouse)

    def refresh():
        reads.append(True)
        return state

    assert module.authorize_host_action(
        NS(desktop_state=state),
        NS(request=lambda *_: False),
        "click_tool",
        {},
        refresh,
        lambda: False,
    )
    assert not reads
    assert (
        module.authorize_host_action(
            NS(desktop_state=state),
            NS(request=lambda *_: True),
            "click_tool",
            {},
            refresh,
            lambda: False,
        )
        is None
    )
    assert reads == [True]


def test_workspace_write_still_requires_separate_data_permission(tmp_path, monkeypatch):
    from windows_use.agent.registry.service import Registry
    from windows_use.agent.tools import service
    from windows_use.safety import SafetyPolicy

    monkeypatch.setattr(service, "WINDOWS_USE_DIR", tmp_path)
    registry = Registry([service.workspace_tool], SafetyPolicy())
    result = registry.execute(
        "workspace_tool",
        {"thought": "save", "operation": "create", "path": "a.txt", "content": "ok"},
    )
    assert result.safety_blocked and not (tmp_path / "workspace" / "a.txt").exists()
    registry.safety = SafetyPolicy(["file_write"])
    assert registry.execute(
        "workspace_tool",
        {"thought": "save", "operation": "create", "path": "a.txt", "content": "ok"},
    ).is_success
    assert (tmp_path / "workspace" / "a.txt").read_text() == "ok"


def test_uia_pattern_does_not_fallback_to_mouse_and_verifies_value():
    from unittest.mock import Mock

    from windows_use.agent.tools.service import ui_control_tool

    control = Mock()
    control.GetRuntimeId.return_value = [1, 2]
    control.Name = "Editor"
    control.GetValuePattern.return_value = None
    node = NS(control=control, safety_identity=(1, 2), safety_name="Editor", name="Editor")
    desktop = NS(desktop_state=NS(tree_state=NS(interactive_nodes=[node])))
    with pytest.raises(ValueError, match="未回退"):
        ui_control_tool.invoke(element_id=0, operation="set_value", value="test", desktop=desktop)
    pattern = control.GetValuePattern.return_value = Mock(Value="wrong")
    with pytest.raises(RuntimeError, match="验证不一致"):
        ui_control_tool.invoke(element_id=0, operation="set_value", value="test", desktop=desktop)
    pattern.SetValue.assert_called_once_with("test")
    assert not control.SetFocus.called


@pytest.mark.asyncio
async def test_real_desktop_gate_cannot_be_bypassed_by_risk_permission():
    from windows_use.agent.desktop.service import Desktop
    from windows_use.agent.registry.service import Registry
    from windows_use.agent.tools.views import Shell
    from windows_use.safety import SafetyPolicy
    from windows_use.tools import Tool

    calls = []

    @Tool("shell_tool", model=Shell)
    def shell(**params):
        calls.append(params)
        return "ran"

    desktop = Desktop.__new__(Desktop)
    desktop.consent_callback = lambda *_: None
    registry = Registry([shell], SafetyPolicy(["system"]))
    params = {"thought": "bypass", "command": "echo fake"}
    for result in (
        registry.execute("shell_tool", params, desktop),
        await registry.aexecute("shell_tool", params, desktop),
    ):
        assert result.safety_blocked and "Shell" in result.error
    assert not calls
