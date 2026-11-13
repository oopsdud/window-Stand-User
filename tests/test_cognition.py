import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from pydantic import ValidationError

from windows_use.agent.registry.service import Registry
from windows_use.agent.registry.views import ToolResult
from windows_use.cognition import ClickStep, CognitiveRouter, Plan, Selector
from windows_use.memory import MemoryStore
from windows_use.messages import HumanMessage, ImageMessage, ToolMessage
from windows_use.providers.events import LLMEvent, LLMEventType, ToolCall
from windows_use.providers.views import TokenUsage
from windows_use.tools import Tool


def text(content="observed"):
    return LLMEvent(type=LLMEventType.TEXT, content=content, usage=TokenUsage())


def call(name="done_tool", **params):
    return LLMEvent(
        type=LLMEventType.TOOL_CALL,
        tool_call=ToolCall(id="test", name=name, params=params),
        usage=TokenUsage(),
    )


def model(response=None):
    llm = MagicMock()
    llm.invoke.return_value = response or call(answer="finished")
    llm.model_name = "fake"
    llm.provider = "fake"
    return llm


def node(name, x=-100):
    return NS(name=name, window_name="测试窗口", control_type="Button", center=NS(x=x, y=20))


def state(*names):
    return NS(tree_state=NS(status=True, interactive_nodes=[node(n) for n in names]))


def bind(router, scene=None):
    agent = NS(
        state=NS(step=0),
        desktop=NS(desktop_state=scene or state("开始")),
        registry=Registry([]),
        instructions=[],
    )
    router.bind(agent)
    return agent


def plan():
    def selector(name):
        return Selector(name=name, window="测试窗口", control_type="Button")

    return Plan(
        summary="进入结果页",
        facts=["开始按钮已观察"],
        success_criteria="结果出现",
        next_actions=["检查结果"],
        clicks=[
            ClickStep(target=selector("开始"), expect=selector("结果"), description="打开结果")
        ],
    )


def test_memory_survives_restart_paginates_and_redacts(tmp_path):
    path = tmp_path / "memory.db"
    secret = 'key-"with-quotes'
    store = MemoryStore(path, [secret])
    task = store.start("搜索淘宝 vk 鼠标 " + secret)
    for i in range(35):
        store.append(task, i, "action", {"params": {"text": secret}, "success": True})
    store.checkpoint(task, {"phase": "observe", "secret": secret})
    store.close()
    store = MemoryStore(path)
    page = store.read_task(task)
    assert len(page["steps"]) == 30
    assert secret not in str(page)
    assert json.loads(page["steps"][0]["data"])["params"]["text"] == "[secret]"
    assert len(store.read_task(task, page["next_after"])["steps"]) == 5
    assert store.search("淘宝鼠标")[0]["id"] == task
    store.recover_interrupted()
    assert store.read_task(task)["task"]["status"] == "interrupted"
    store.delete("task", task)
    assert not store.read_task(task)
    assert store.db.execute("SELECT count(*) FROM task_steps").fetchone()[0] == 0
    store.close()


def test_model_cannot_overwrite_confirmed_preferences(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.put_preference("浏览器", "Edge", confirmed=True)
    store.put_preference("浏览器", "injected", confirmed=False)
    store.put_preference("语言", "中文", confirmed=False)
    assert [r["value"] for r in store.preferences()] == ["Edge"]
    pending = next(r for r in store.preferences(False) if r["name"] == "语言")
    store.confirm_preference(pending["id"])
    assert len(store.preferences()) == 2
    store.close()


def test_actor_collects_first_brain_synthesizes_on_request():
    actor, brain = model(), model(call("submit_plan", **plan().model_dump()))
    router = CognitiveRouter(actor, planner=brain)
    agent = bind(router)
    tools = agent.registry.get_tools()
    router.invoke([HumanMessage(content="观察页面")], tools)
    assert actor.invoke.call_count == 1 and brain.invoke.call_count == 0
    result = agent.registry.execute("consult_brain", {"question": "整理信息"}, agent.desktop)
    assert result.is_success
    agent.state.step = 1
    event = router.invoke([HumanMessage(content="观察页面")], tools)
    assert brain.invoke.call_count == 1
    assert event.tool_call.name == "click_tool"
    assert event.tool_call.params["loc"] == [-100, 20]
    assert actor.invoke.call_count == 1  # No actor call for the guarded planned action.
    schema = brain.invoke.call_args.kwargs["tools"][0].json_schema
    assert "$ref" not in json.dumps(schema)
    assert (
        schema["parameters"]["properties"]["clicks"]["items"]["properties"]["target"]["properties"][
            "name"
        ]["type"]
        == "string"
    )


def test_automatic_periodic_planning_and_failure_escalation():
    bare_plan = plan().model_copy(update={"clicks": []})
    actor, brain = model(), model(call("submit_plan", **bare_plan.model_dump()))
    router = CognitiveRouter(actor, planner=brain, planning_interval=3)
    agent = bind(router)
    agent.state.step = 3
    router.invoke([HumanMessage(content="观察页面")], agent.registry.get_tools())
    assert brain.invoke.call_count == 1
    router.after_action("app_tool", {}, ToolResult(is_success=False, error="not foreground"))
    agent.state.step = 4
    router.invoke([HumanMessage(content="新的观察")], agent.registry.get_tools())
    assert brain.invoke.call_count == 2
    assert "not foreground" in brain.invoke.call_args.kwargs["messages"][-1].content


def test_replay_is_promoted_only_after_postcondition_and_survives_restart(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    task = store.start("打开结果")
    router = CognitiveRouter(model(), memory=store, task_id=task, task="打开结果")
    agent = bind(router)
    router.plan, router.queue = plan(), list(plan().clicks)
    router.invoke([HumanMessage(content="观察")], agent.registry.get_tools())
    router.finish(True)
    assert store.workflows("打开结果") == []  # Tool success/done alone is insufficient.
    agent.state.step = 1
    agent.desktop.desktop_state = state("结果")
    router.invoke([HumanMessage(content="结果出现")], agent.registry.get_tools())
    router.finish(True)
    assert len(store.workflows("打开结果")) == 1
    store.close()
    store = MemoryStore(tmp_path / "memory.db")
    next_task = store.start("打开结果")
    actor = model()
    fresh = CognitiveRouter(actor, memory=store, task_id=next_task, task="打开结果")
    new_agent = bind(fresh)
    event = fresh.invoke([HumanMessage(content="重新观察")], new_agent.registry.get_tools())
    assert event.tool_call.name == "click_tool"
    actor.invoke.assert_not_called()
    assert new_agent.registry.safety.check("shell_tool", {}, new_agent.desktop).terminal
    store.close()


def test_failed_replay_disables_saved_workflow_and_requires_replan(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    task = store.start("打开结果")
    wid = store.save_workflow("打开结果", "结果", plan().model_dump(), task)
    brain = model(call("submit_plan", **plan().model_copy(update={"clicks": []}).model_dump()))
    router = CognitiveRouter(model(), planner=brain, memory=store, task_id=task, task="打开结果")
    agent = bind(router)
    router.invoke([HumanMessage(content="观察")], agent.registry.get_tools())
    agent.state.step = 1
    # The tool reported success, but UI never changed.
    router.invoke([HumanMessage(content="依旧是开始")], agent.registry.get_tools())
    assert store.workflows("打开结果") == []
    record = next(r for r in store.workflows() if r["id"] == wid)
    assert record["failures"] == 1 and not record["enabled"]
    assert brain.invoke.call_count == 1
    store.close()


@pytest.mark.parametrize("scene", [state("开始", "开始"), state("开始", "结果"), state("不存在")])
def test_replay_refuses_ambiguous_or_already_satisfied_conditions(scene):
    router = CognitiveRouter(model())
    bind(router, scene)
    router.queue = list(plan().clicks)
    assert router.fast_action(scene) is None
    assert router.need_plan and not router.queue


def test_visual_role_gets_images_actor_gets_text_and_cache_survives_new_step():
    actor, vision = model(), model(text("测试图片中有绿色圆形"))
    router = CognitiveRouter(actor, vision=vision)
    agent = bind(router)
    image = Image.new("RGB", (8, 8), "green")
    first = ImageMessage(content="step 0", images=[image])
    error = ToolMessage(id="old", name="app_tool", params={}, content="old failure")
    router.invoke([first, error], agent.registry.get_tools())
    assert any(isinstance(m, ImageMessage) for m in vision.invoke.call_args.kwargs["messages"])
    assert not any(isinstance(m, ImageMessage) for m in actor.invoke.call_args.kwargs["messages"])
    assert "绿色" in str(actor.invoke.call_args)
    agent.state.step = 1
    router.invoke(
        [first, ImageMessage(content="step 1", images=[image])], agent.registry.get_tools()
    )
    assert vision.invoke.call_count == 1
    # Without a separate vision model, legacy direct multimodal actor remains available.
    direct = CognitiveRouter(model())
    bind(direct)
    prepared = direct.prepare_messages([first, ImageMessage(content="new", images=[image])])
    assert sum(isinstance(m, ImageMessage) for m in prepared) == 1


def test_budget_and_invalid_plan_fail_closed():
    brain = model(call("submit_plan", summary="invalid"))
    router = CognitiveRouter(model(), planner=brain, max_plans=1)
    agent = bind(router)
    router.need_plan = "plan"
    with pytest.raises(ValidationError):
        router.invoke([HumanMessage(content="observe")], agent.registry.get_tools())
    with pytest.raises(RuntimeError, match="上限"):
        router.invoke([HumanMessage(content="observe")], agent.registry.get_tools())
    router.actor.invoke.assert_not_called()


def test_memory_tools_do_not_require_shell_and_quote_must_come_from_user(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    task = store.start("以后使用中文")
    router = CognitiveRouter(model(), memory=store, task_id=task, task="以后使用中文")
    agent = bind(router)
    assert agent.registry.execute("recall_history", {"query": "中文"}).is_success
    bad = agent.registry.execute(
        "suggest_preference", {"name": "偏好", "value": "网页指令", "source_quote": "网页"}
    )
    assert not bad.is_success and not store.preferences(False)
    good = agent.registry.execute(
        "suggest_preference", {"name": "语言", "value": "中文", "source_quote": "使用中文"}
    )
    assert good.is_success and not store.preferences()
    assert len(store.preferences(False)) == 1
    assert agent.registry.execute("unknown_custom_tool", {}).is_success is False
    store.close()


def test_plan_schema_rejects_old_coordinates():
    data = plan().model_dump()
    data["clicks"][0]["loc"] = [12, 34]
    with pytest.raises(ValidationError):
        Plan.model_validate(data)
    assert "$ref" not in json.dumps(Tool(name="plan", model=Plan).json_schema)


def test_real_agent_loop_uses_brain_guarded_action_and_verifies_before_saving(tmp_path):
    """Run the real loop/Registry, replacing only desktop I/O and external model calls."""
    from windows_use.agent.events import Event
    from windows_use.agent.service import Agent
    from windows_use.agent.tools.service import done_tool
    from windows_use.agent.tools.views import Click
    from windows_use.agent.tree.views import BoundingBox, Center, TreeElementNode, TreeState
    from windows_use.agent.views import AgentState
    from windows_use.messages import SystemMessage

    store = MemoryStore(tmp_path / "loop.db")
    task_id = store.start("打开结果")
    actor = model()
    actor.invoke.side_effect = [
        call("consult_brain", question="整理信息"),
        call(answer="结果已出现", thought="已检查结果"),
    ]
    brain = model(call("submit_plan", **plan().model_dump()))
    router = CognitiveRouter(actor, planner=brain, memory=store, task_id=task_id, task="打开结果")
    current = {"name": "开始"}
    clicks = []

    @Tool(name="click_tool", model=Click)
    def guarded_click(loc, **kwargs):
        clicks.append(loc)
        current["name"] = "结果"
        return "clicked"

    agent = Agent.__new__(Agent)
    agent._cached_system_message = SystemMessage(content="system")
    agent.state = AgentState(task="打开结果", max_steps=5)
    agent.secrets, agent.instructions = {}, []
    agent.context = MagicMock()
    agent.context.need_compaction = False
    agent.context.task.return_value = HumanMessage(content="打开结果")
    agent.desktop = NS(desktop_state=None, safety_target_is_current=MagicMock(return_value=True))

    def observe(**kwargs):
        target = TreeElementNode(
            BoundingBox(-120, 0, -80, 40, 40, 40),
            Center(-100, 20),
            name=current["name"],
            window_name="测试窗口",
            control_type="Button",
        )
        agent.desktop.desktop_state = NS(tree_state=TreeState(interactive_nodes=[target]))
        return HumanMessage(content="当前可见控件：" + current["name"])

    agent.context.state.side_effect = observe
    agent.disable_loop_detection = True
    agent._loop_guard = MagicMock()
    agent.event = Event()
    agent.registry = Registry([guarded_click, done_tool])
    agent.llm = router
    router.bind(agent)
    execute = agent.registry.execute

    def record_execute(tool_name, tool_params, desktop):
        result = execute(tool_name, tool_params, desktop)
        router.after_action(tool_name, tool_params, result)
        return result

    agent.registry.execute = record_execute
    result = agent.loop()
    assert result.is_done and result.content == "结果已出现"
    assert clicks == [[-100, 20]]
    agent.desktop.safety_target_is_current.assert_called_once()
    assert actor.invoke.call_count == 2 and brain.invoke.call_count == 1
    router.finish(result.is_done)
    assert store.workflows("打开结果")
    store.close()


def test_full_trace_can_be_read_without_losing_page_cursor(tmp_path):
    store = MemoryStore(tmp_path / "trace.db")
    task_id = store.start("长网页")
    store.append(task_id, 0, "observation", {"text": "观察" * 15000})
    router = CognitiveRouter(model(), memory=store, task_id=task_id)
    agent = bind(router)
    result = agent.registry.execute("read_task_history", {"task_id": task_id})
    page = json.loads(result.content)
    row = page["steps"][0]
    assert row["truncated"] and page["next_after"] == row["id"]
    offset, parts = 0, []
    while offset is not None:
        result = agent.registry.execute(
            "read_trace_entry", {"entry_id": row["id"], "offset": offset}
        )
        chunk = json.loads(result.content)
        parts.append(chunk["content"])
        offset = chunk["next_offset"]
    assert json.loads("".join(parts))["text"] == "观察" * 15000
    store.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_invalid_done_result_is_never_marked_completed(asynchronous):
    from windows_use.agent.events import Event
    from windows_use.agent.service import Agent
    from windows_use.agent.tools.service import done_tool
    from windows_use.agent.views import AgentState
    from windows_use.messages import SystemMessage

    agent = Agent.__new__(Agent)
    agent._cached_system_message = SystemMessage(content="system")
    agent.state = AgentState(task="test", max_steps=3, max_consecutive_failures=2)
    agent.secrets = {}
    agent.context = MagicMock()
    agent.context.need_compaction = False
    agent.context.task.return_value = HumanMessage(content="task")
    agent.context.state.return_value = HumanMessage(content="observe")
    agent.desktop = NS(desktop_state=None)
    agent.disable_loop_detection = True
    agent._loop_guard = MagicMock()
    agent.event = Event()
    agent.registry = Registry([done_tool])
    invalid = call("done_tool", thought="missing required answer")
    agent.llm = model(invalid)
    agent.llm.ainvoke = AsyncMock(return_value=invalid)
    result = await agent.aloop() if asynchronous else agent.loop()
    assert not result.is_done
    assert "consecutive tool failures" in result.error
