from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from windows_use.agent.desktop.service import Desktop
from windows_use.agent.events import AgentEvent, Event, EventType
from windows_use.agent.registry.service import Registry
from windows_use.agent.service import Agent
from windows_use.agent.tree.views import BoundingBox, Center, TreeElementNode, TreeState
from windows_use.agent.views import AgentState
from windows_use.messages import HumanMessage, SystemMessage, ToolMessage
from windows_use.providers.events import LLMEvent, LLMEventType, ToolCall
from windows_use.providers.views import TokenUsage
from windows_use.safety import SafetyPolicy, redact, redact_messages, risks_of


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Delete account", {"delete"}),
        ("Closed tickets", set()),
        ("Shared with me", set()),
        ("Send", {"send"}),
        ("立即支付", {"purchase"}),
        ("清空回收站", {"delete"}),
        ("退出登录", {"close"}),
        ("转发消息", {"send"}),
    ],
)
def test_risk_vocabulary(label, expected):
    assert risks_of(label) == expected


def tool(name):
    value = MagicMock()
    value.name = name
    value.validate_params.return_value = []
    value.invoke.return_value = "ok"
    value.ainvoke = AsyncMock(return_value="ok")
    return value


def desktop(label="Save", current=True):
    node = TreeElementNode(BoundingBox(0, 0, 20, 20, 20, 20), Center(10, 10), name=label)
    return NS(
        desktop_state=NS(tree_state=TreeState(interactive_nodes=[node])),
        safety_target_is_current=MagicMock(return_value=current),
    )


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("shell_tool", {"command": "anything"}),
        ("shortcut_tool", {"shortcut": "ctrl+enter"}),
        ("shortcut_tool", {"shortcut": "shift+delete"}),
        ("type_tool", {"loc": [10, 10], "text": "hello", "press_enter": True}),
        ("multi_select_tool", {"elements": [[10, 10]]}),
        ("move_tool", {"loc": [10, 10], "drag": True}),
        ("file_tool", {"mode": "delete"}),
        ("file_tool", {"mode": "write"}),
        ("memory_tool", {"mode": "update"}),
        ("desktop_tool", {"action": "remove"}),
        ("custom_tool", {}),
    ],
)
@pytest.mark.asyncio
async def test_both_registry_paths_refuse_before_invocation(name, params):
    action = tool(name)
    registry = Registry([action])
    for result in (
        registry.execute(name, params, desktop()),
        await registry.aexecute(name, params, desktop()),
    ):
        assert result.safety_blocked and not result.is_success
    action.invoke.assert_not_called()
    action.ainvoke.assert_not_awaited()


def test_risk_authorization_and_live_check_are_both_required():
    action = tool("click_tool")
    target = desktop("Delete")
    denied = Registry([action]).execute("click_tool", {"loc": [10, 10]}, target)
    assert denied.safety_blocked
    action.invoke.assert_not_called()
    registry = Registry([action], SafetyPolicy(["delete"]))
    assert registry.execute("click_tool", {"loc": [10, 10]}, target).is_success
    # Consumed even if caller tries a different action using the same observation.
    replay = registry.execute("click_tool", {"loc": [10, 10]}, target)
    assert not replay.is_success and not replay.safety_blocked
    assert action.invoke.call_count == 1
    stale = registry.execute("click_tool", {"loc": [10, 10]}, desktop("Delete", False))
    assert not stale.is_success
    assert action.invoke.call_count == 1


def test_unknown_coordinates_and_failed_validation_do_not_execute():
    action = tool("click_tool")
    registry = Registry([action])
    assert registry.execute("click_tool", {"loc": [90, 90]}, desktop()).safety_blocked
    target = desktop()
    target.safety_target_is_current.side_effect = RuntimeError("UIA unavailable")
    assert not registry.execute("click_tool", {"loc": [10, 10]}, target).is_success
    action.invoke.assert_not_called()


def test_partial_failure_consumes_snapshot():
    action = tool("click_tool")
    action.invoke.side_effect = RuntimeError("failed after click")
    registry = Registry([action])
    target = desktop()
    for _ in range(2):
        assert not registry.execute("click_tool", {"loc": [10, 10]}, target).is_success
    assert action.invoke.call_count == 1


def test_candidates_are_filtered_without_renumbering():
    tree = desktop("Delete").desktop_state.tree_state
    safe = desktop("Search").desktop_state.tree_state.interactive_nodes[0]
    tree.interactive_nodes.append(safe)
    text = tree.interactive_elements_to_string(())
    assert "Delete" not in text and "1|" in text
    assert "Delete" in tree.interactive_elements_to_string(("delete",))


def test_registered_secrets_are_copied_and_redacted_at_boundaries():
    raw = {"nested": ["long-secret short", ("long-secret",)]}
    assert "long-secret" not in str(redact(raw, ["long-secret", "short"]))
    assert raw["nested"][0] == "long-secret short"
    message = ToolMessage(
        id="1", name="type_tool", params={"text": "long-secret"}, content="long-secret"
    )
    cleaned = redact_messages([message], ["long-secret"])[0]
    assert cleaned.params["text"] == "[secret]" and cleaned.content == "[secret]"
    assert message.params["text"] == "long-secret"
    events = []
    emitter = Event(secrets=["long-secret"])
    emitter.add_subscriber(events.append)
    event = AgentEvent(EventType.TOOL_CALL, {"params": {"text": "long-secret"}})
    emitter.emit(event)
    assert events[0].data["params"]["text"] == "[secret]"
    assert event.data["params"]["text"] == "long-secret"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_agent_stops_on_safety_refusal_without_second_model_call(asynchronous):
    agent = Agent.__new__(Agent)
    agent._cached_system_message = SystemMessage(content="system")
    agent.state = AgentState(task="test", max_steps=5)
    agent.secrets = {"password": "private-value"}
    agent.context = MagicMock()
    agent.context.need_compaction = False
    agent.context.task.return_value = HumanMessage(content="private-value")
    agent.context.state.return_value = HumanMessage(content="observation")
    agent.desktop = desktop()
    agent.disable_loop_detection = True
    agent._loop_guard = MagicMock()
    agent.event = Event()
    events = []
    agent.event.add_subscriber(events.append)
    action = tool("shell_tool")
    agent.registry = Registry([action])
    response = LLMEvent(
        type=LLMEventType.TOOL_CALL,
        tool_call=ToolCall(id="1", name="shell_tool", params={"command": "noop"}),
        usage=TokenUsage(),
    )
    agent.llm = MagicMock()
    agent.llm.invoke.return_value = response
    agent.llm.ainvoke = AsyncMock(return_value=response)
    result = await agent.aloop() if asynchronous else agent.loop()
    assert not result.is_done and "system" in result.error
    call = agent.llm.ainvoke if asynchronous else agent.llm.invoke
    assert call.call_count == 1
    assert "private-value" not in str(call.call_args)
    assert events[-1].data["error_source"] == "safety"
    action.invoke.assert_not_called()
    action.ainvoke.assert_not_awaited()


def test_unknown_permissions_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        SafetyPolicy(["typo"])


def test_windows_target_guard_rejects_window_identity_and_geometry_changes(monkeypatch):
    import windows_use.agent.desktop.service as desktop_module

    control = MagicMock()
    control.GetRuntimeId.return_value = [7, 8]
    control.Name = "Save"
    control.BoundingRectangle = NS(left=0, top=0, right=20, bottom=20)
    target = desktop().desktop_state.tree_state.interactive_nodes[0]
    target.control = control
    target.safety_identity = (7, 8)
    target.safety_bounds = (0, 0, 20, 20)
    target.safety_name = "Save"
    instance = Desktop.__new__(Desktop)
    instance.desktop_state = NS(active_window=NS(handle=123))
    monkeypatch.setattr(desktop_module.uia, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(desktop_module.uia, "ControlFromPoint", lambda *_: control)
    assert instance.safety_target_is_current(target, [10, 10])
    # Visible clipped coordinates differ from the original UIA bounds.
    target.bounding_box.right = 15
    assert instance.safety_target_is_current(target, [10, 10])
    control.Name = "Delete"
    assert not instance.safety_target_is_current(target, [10, 10])

    control.Name = "Save"
    control.BoundingRectangle.right = 21
    assert not instance.safety_target_is_current(target, [10, 10])
    control.BoundingRectangle.right = 20
    other = MagicMock()
    other.GetRuntimeId.return_value = [9, 10]
    other.GetParentControl.return_value = None
    monkeypatch.setattr(desktop_module.uia, "ControlFromPoint", lambda *_: other)
    assert not instance.safety_target_is_current(target, [10, 10])
    monkeypatch.setattr(desktop_module.uia, "ControlFromPoint", lambda *_: control)
    monkeypatch.setattr(desktop_module.uia, "GetForegroundWindow", lambda: 456)
    assert not instance.safety_target_is_current(target, [10, 10])


def test_observation_freezes_original_control_identity_and_bounds():
    control = MagicMock()
    control.GetRuntimeId.return_value = [1, 2]
    control.CachedName = "Save"
    control.CachedBoundingRectangle = NS(left=0, top=0, right=30, bottom=30)
    node = TreeElementNode(BoundingBox(0, 0, 20, 20, 20, 20), Center(10, 10), control=control)
    control.GetRuntimeId.return_value = [3, 4]
    control.CachedBoundingRectangle.right = 40
    assert node.safety_identity == (1, 2)
    assert node.safety_bounds == (0, 0, 30, 30)
    assert node.safety_name == "Save"
