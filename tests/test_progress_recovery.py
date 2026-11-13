"""Regression cases from the 2026-11-10 left-monitor click/planning loop.

No network and no actual desktop clicks: tool implementations are local spies.
"""

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from windows_use.agent.events import Event
from windows_use.agent.loop import LoopGuard, ModelConfigurationError, ProgressHalt, semantic_scene
from windows_use.agent.registry.service import Registry
from windows_use.agent.registry.views import ToolResult
from windows_use.agent.service import Agent
from windows_use.agent.tools.views import Click, Type
from windows_use.agent.tree.views import BoundingBox, Center, TreeElementNode, TreeState
from windows_use.agent.views import AgentState
from windows_use.cognition import CognitiveRouter, Plan
from windows_use.messages import HumanMessage, SystemMessage
from windows_use.providers.events import LLMEvent, LLMEventType, ToolCall
from windows_use.providers.views import TokenUsage
from windows_use.safety import SafetyPolicy
from windows_use.tools import Tool


def scene():
    node = TreeElementNode(
        BoundingBox(-1200, 650, -1100, 690, 100, 40),
        Center(-1150, 672),
        name="讨论区 (10)",
        control_type="列表项目",
        window_name="课程窗口",
        hwnd=100,
    )
    return NS(
        active_window=NS(
            handle=100, name="课程窗口", bounding_box=BoundingBox(-1929, -9, 9, 1029, 1938, 1038)
        ),
        tree_state=TreeState(interactive_nodes=[node]),
    )


def response(name="done_tool", **params):
    return LLMEvent(
        type=LLMEventType.TOOL_CALL,
        usage=TokenUsage(),
        tool_call=ToolCall(id="test", name=name, params=params),
    )


def model(event=None):
    result = MagicMock()
    result.provider, result.model_name = "fake", "fake"
    result.invoke.return_value = event or response(answer="done", thought="done")
    return result


def router_fixture():
    plan = Plan(
        summary="使用当前控件编号打开讨论区",
        success_criteria="讨论区出现",
        next_actions=["点击控件编号 0"],
    )
    actor = model()
    brain = model(response("submit_plan", **plan.model_dump()))
    router = CognitiveRouter(actor, planner=brain)
    agent = NS(
        state=NS(step=0),
        instructions=[],
        registry=Registry([]),
        desktop=NS(
            desktop_state=scene(),
            describe_displays=lambda: "origin=(-1920,0); negative coordinates supported",
        ),
    )
    router.bind(agent)
    return router, agent, actor, brain


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_wrong_monitor_click_is_rejected_even_when_unverified_is_authorized(asynchronous):
    invoked = []

    @Tool(name="click_tool", model=Click)
    def fake_click(loc, **kwargs):
        invoked.append(loc)
        return "clicked"

    desktop = NS(desktop_state=scene(), safety_target_is_current=MagicMock(return_value=True))
    registry = Registry([fake_click], safety=SafetyPolicy(["unverified"]))
    execute = registry.aexecute if asynchronous else registry.execute
    params = {"thought": "open discussion", "loc": [80, 379]}
    bad = (
        await execute("click_tool", params, desktop)
        if asynchronous
        else execute("click_tool", params, desktop)
    )
    assert not bad.is_success and not bad.safety_blocked and "截图像素" in bad.error
    assert invoked == []
    params = {"thought": "use observed control", "element_id": 0}
    good = (
        await execute("click_tool", params, desktop)
        if asynchronous
        else execute("click_tool", params, desktop)
    )
    assert good.is_success and invoked == [[-1150, 672]]
    assert "loc" not in params  # Don't overwrite the original model decision.
    desktop.safety_target_is_current.assert_called_once()


def test_input_id_resolves_and_invalid_conflicting_invisible_ids_do_not_execute():
    invoked = []

    @Tool(name="type_tool", model=Type)
    def fake_type(loc, text, **kwargs):
        invoked.append((loc, text))
        return "typed"

    desktop = NS(desktop_state=scene(), safety_target_is_current=lambda *_: True)
    registry = Registry([fake_type])
    for extra in ({"element_id": 9}, {"element_id": 0, "loc": [80, 379]}):
        result = registry.execute(
            "type_tool", {"thought": "test", "text": "hello", **extra}, desktop
        )
        assert not result.is_success
    assert invoked == []
    assert registry.execute(
        "type_tool", {"thought": "test", "text": "hello", "element_id": 0}, desktop
    ).is_success
    assert invoked == [([-1150, 672], "hello")]


def test_clock_ram_focus_changes_do_not_count_as_progress_but_url_does():
    state = scene()
    page = state.tree_state.interactive_nodes[0]
    page.name = "学习空间 - 内存使用率 - 424 MB"
    clock = TreeElementNode(
        BoundingBox(0, 0, 10, 10, 10, 10),
        Center(5, 5),
        name="时钟 19:41",
        window_name="任务栏",
        hwnd=200,
    )
    state.tree_state.interactive_nodes.append(clock)
    before = semantic_scene(state)
    clock.name = "时钟 19:42"
    page.name = "学习空间 - 内存使用率 - 425 MB"
    page.metadata["has_focused"] = 1
    assert semantic_scene(state) == before
    page.metadata["value"] = "https://example.test/discussion"
    assert semantic_scene(state) != before


def test_memory_and_planning_calls_do_not_fabricate_gui_loops():
    guard = LoopGuard()
    state = scene()
    guard.record_state(state)
    for tool in ["recall_history", "read_task_history", "consult_brain"] * 3:
        guard.record_action(tool, {})
        guard.record_state(state)
        assert guard.check() is None
    guard.record_action("click_tool", {"loc": [-1150, 672]})
    guard.record_state(state)
    assert guard.check() is not None


def test_existing_plan_prevents_recursive_consult_until_new_evidence():
    router, agent, actor, brain = router_fixture()
    messages = [HumanMessage(content="[Loop Warning] repeated scene")]
    router.invoke(messages, agent.registry.get_tools())
    assert brain.invoke.call_count == 1
    assert "consult_brain" not in [t.name for t in actor.invoke.call_args.kwargs["tools"]]
    agent.state.step = 1
    router.invoke(messages, agent.registry.get_tools())
    assert brain.invoke.call_count == 1  # Persistent warning is not a new reason to replan.
    result = agent.registry.execute("consult_brain", {"question": "please confirm the same plan"})
    assert result.is_success and "无新证据" in result.content and not router.need_plan
    router.after_action(
        "scrape_tool", {}, ToolResult(is_success=True, content="new discussion text")
    )
    agent.registry.execute("consult_brain", {"question": "synthesize newly read information"})
    assert router.need_plan
    router.invoke([HumanMessage(content="observe")], agent.registry.get_tools())
    assert brain.invoke.call_count == 2
    prompt = brain.invoke.call_args.kwargs["messages"]
    assert "负 x/y" in prompt[0].content
    assert "origin=(-1920,0)" in prompt[1].content


def test_repeated_consult_and_third_identical_action_are_stopped():
    router, agent, actor, brain = router_fixture()
    router.need_plan = "plan"
    router.invoke([HumanMessage(content="observe")], agent.registry.get_tools())
    for _ in range(2):
        agent.registry.execute("consult_brain", {"question": "again"})
    with pytest.raises(ProgressHalt, match="反复请求规划"):
        router.invoke([HumanMessage(content="observe")], agent.registry.get_tools())
    assert brain.invoke.call_count == 1
    router, agent, actor, _ = router_fixture()
    actor.invoke.return_value = response("click_tool", element_id=0, thought="click")
    for i in range(2):
        agent.state.step = i
        assert router.invoke(
            [HumanMessage(content="observe")], agent.registry.get_tools()
        ).tool_call
    agent.state.step = 2
    with pytest.raises(ProgressHalt, match="第三次"):
        router.invoke([HumanMessage(content="observe")], agent.registry.get_tools())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_progress_halt_is_not_retried_as_an_api_error(asynchronous):
    agent = Agent.__new__(Agent)
    agent._cached_system_message = SystemMessage(content="test")
    agent.state = AgentState(task="test", max_steps=5)
    agent.context = MagicMock()
    agent.context.need_compaction = False
    agent.context.task.return_value = HumanMessage(content="task")
    agent.context.state.return_value = HumanMessage(content="observe")
    agent.secrets = {}
    agent.desktop = NS(desktop_state=scene())
    agent.disable_loop_detection = True
    agent._loop_guard = LoopGuard()
    agent.event = Event()
    agent.registry = Registry([])
    agent.llm = model()
    agent.llm.invoke.side_effect = ProgressHalt("no progress")
    agent.llm.ainvoke = AsyncMock(side_effect=ProgressHalt("no progress"))
    result = await agent.aloop() if asynchronous else agent.loop()
    assert not result.is_done and result.error == "no progress"
    invoked = agent.llm.ainvoke if asynchronous else agent.llm.invoke
    assert invoked.call_count == 1


def test_window_transition_preserves_causal_action_for_planner():
    router, _, _, brain = router_fixture()
    router.observe({"window": "搜索窗口", "controls": "search"})
    router.after_action(
        "app_tool", {"mode": "switch", "name": "课程窗口"}, ToolResult(is_success=True)
    )
    router.observe({"window": "课程窗口", "controls": "course"})
    router.after_action("click_tool", {"loc": [80, 379]}, ToolResult(is_success=True))
    router.observe({"window": "搜索窗口", "controls": "search"})
    router.plan_now([HumanMessage(content="observe")], "loop")
    evidence = json.loads(brain.invoke.call_args.kwargs["messages"][-1].content)
    transitions = evidence["action_transitions"]
    assert transitions[0]["after_window"] == "课程窗口"
    assert transitions[1]["action"]["params"]["loc"] == [80, 379]
    assert transitions[1]["after_window"] == "搜索窗口"


@pytest.mark.parametrize("status", [401, 403])
def test_planner_auth_failure_is_not_a_loop_repair(status):
    router, agent, actor, brain = router_fixture()
    error = RuntimeError("invalid credentials")
    error.status_code = status
    brain.invoke.side_effect = error
    with pytest.raises(ModelConfigurationError, match=f"规划大脑.*HTTP {status}"):
        router.invoke(
            [HumanMessage(content="[Loop Warning] repeated scene")], agent.registry.get_tools()
        )
    assert brain.invoke.call_count == 1
    assert not router.loop_repairs
    actor.invoke.assert_not_called()


def test_failed_planning_retries_do_not_consume_repair_budget():
    router, agent, _, brain = router_fixture()
    success = brain.invoke.return_value
    brain.invoke.side_effect = [
        RuntimeError("temporary outage"),
        RuntimeError("temporary outage"),
        success,
    ]
    messages = [HumanMessage(content="[Loop Warning] repeated scene")]
    for _ in range(2):
        with pytest.raises(RuntimeError, match="temporary outage"):
            router.invoke(messages, agent.registry.get_tools())
        assert not router.loop_repairs
    router.invoke(messages, agent.registry.get_tools())
    assert router.loop_repairs[semantic_scene(agent.desktop.desktop_state)] == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_enter_checks_observed_and_live_focus_before_dispatch(asynchronous):
    from windows_use.agent.tools.views import Shortcut

    sent = []

    @Tool(name="shortcut_tool", model=Shortcut)
    def key(shortcut, **kwargs):
        sent.append(shortcut)
        return "dispatched"

    desktop = NS(
        desktop_state=scene(), safety_keyboard_target_is_current=MagicMock(return_value=True)
    )
    node = desktop.desktop_state.tree_state.interactive_nodes[0]
    node.control_type = "编辑"
    node.metadata["has_focused"] = 0
    registry = Registry([key], safety=SafetyPolicy(["system"]))

    async def run(params):
        return (
            await registry.aexecute("shortcut_tool", params, desktop)
            if asynchronous
            else registry.execute("shortcut_tool", params, desktop)
        )

    params = {"thought": "focus then enter", "shortcut": "enter", "element_id": 0}
    assert not (await run(params)).is_success
    assert not sent
    node.metadata["has_focused"] = 1
    desktop.safety_keyboard_target_is_current.return_value = False
    assert not (await run(params)).is_success
    assert not sent
    desktop.safety_keyboard_target_is_current.return_value = True
    assert (await run(params)).is_success
    assert sent == ["enter"]


def test_coordinate_click_cannot_call_an_editor_a_send_button():
    state = scene()
    state.tree_state.interactive_nodes[0].name = "editor"
    desktop = NS(desktop_state=state, safety_target_is_current=lambda *_: True)
    denied = SafetyPolicy(["send"]).check(
        "click_tool", {"loc": [-1150, 672], "target_name": "send"}, desktop
    )
    assert denied and not denied.terminal


def test_no_change_is_feedback_not_success_and_shell_recovery_is_rejected():
    router, agent, actor, _ = router_fixture()
    router.observe({"window": "course", "controls": "input"})
    router.after_action("click_tool", {"element_id": 0}, ToolResult(is_success=True))
    router.observe({"window": "course", "controls": "input"})
    assert router.last_action_feedback["event_dispatched"]
    assert not router.last_action_feedback["ui_changed"]
    assert not router.last_action_feedback["task_success_verified"]
    actor.invoke.return_value = response("shell_tool", command="echo ok", thought="click send")
    with pytest.raises(ProgressHalt, match="未提供"):
        router.invoke([HumanMessage(content="observe")], agent.registry.get_tools())
    assert "shell_tool" not in [t.name for t in actor.invoke.call_args.kwargs["tools"]]


def test_recovery_can_correct_a_bad_proposal_without_executing_it():
    router, agent, actor, _ = router_fixture()
    router.observe({"window": "course", "controls": "input"})
    router.after_action("click_tool", {"element_id": 0}, ToolResult(is_success=True))
    router.observe({"window": "course", "controls": "input"})
    actor.invoke.side_effect = [
        response("shell_tool", command="echo ok"),
        response("click_tool", element_id=0),
    ]
    result = router.invoke([HumanMessage(content="observe")], agent.registry.get_tools())
    assert result.tool_call.name == "click_tool"
    assert result.tool_call.params["element_id"] == 0
    assert actor.invoke.call_count == 2
