"""One task per process; UIA stays on the worker's main thread, never the Qt thread."""

import json
import os
import sys
import threading
import time


class Cancelled(BaseException):
    """Bypass broad Exception handlers in tools/Agent when cancellation is requested."""


class Control:
    def __init__(self, emit):
        self.emit = emit
        self.cancelled = threading.Event()
        self.running = threading.Event()
        self.running.set()
        self.capture_ready = threading.Event()
        from windows_use.execution import ConsentBroker

        self.consent = ConsentBroker(emit, self.cancelled)

    def receive(self, message):
        command = message.get("command")
        if command == "consent":
            self.consent.resolve(message)
            return
        if command in {"cancel", "pause"}:
            self.consent.revoke()
        if command == "cancel":
            self.cancelled.set()
            self.running.set()
            self.capture_ready.set()
        elif command == "pause":
            self.running.clear()
        elif command == "resume":
            self.running.set()
        elif command == "capture_ready":
            self.capture_ready.set()

    def checkpoint(self):
        if self.cancelled.is_set():
            raise Cancelled()
        if not self.running.is_set():
            self.emit("paused", {})
            while not self.running.wait(0.1):
                if self.cancelled.is_set():
                    raise Cancelled()
            if self.cancelled.is_set():
                raise Cancelled()
            self.emit("resumed", {})
            return True
        return False

    def observe(self, capture, *args, check_pause=True, overlay_context=None, **kwargs):
        if check_pause:
            self.checkpoint()
        self.capture_ready.clear()
        self.emit("capture_start", overlay_context or {})
        try:
            deadline = time.monotonic() + 8
            while not self.capture_ready.wait(0.05):
                if self.cancelled.is_set():
                    raise Cancelled()
                if time.monotonic() >= deadline:
                    raise RuntimeError("桌宠未确认让出画面，任务已停止。")
            if self.cancelled.is_set():
                raise Cancelled()
            return capture(*args, **kwargs)
        finally:
            self.emit("capture_end", {})


def run_agent(config, task, control, emit):
    from desktop_app.runtime import DATA
    from desktop_app.settings import model_secrets
    from windows_use import Agent, Browser
    from windows_use.cognition import CognitiveRouter
    from windows_use.memory import MemoryStore

    secrets = model_secrets(config)
    memory = (
        MemoryStore(DATA / "memory.sqlite3", secrets)
        if config.get("memory_enabled", True)
        else None
    )
    task_id = memory.start(task) if memory else None
    if task_id:
        emit("task_started", {"task_id": task_id})
    try:
        _run_cognitive_agent(
            config, task, control, emit, memory, task_id, secrets, Agent, Browser, CognitiveRouter
        )
    except BaseException as error:
        if memory:
            memory.finish(
                task_id, "cancelled" if isinstance(error, Cancelled) else "failed", str(error)
            )
        raise
    finally:
        try:
            guest = getattr(control, "guest", None)
            if guest is not None:
                guest.close()
                control.guest = None
        finally:
            if memory:
                memory.close()


def _run_cognitive_agent(
    config, task, control, emit, memory, task_id, secrets, Agent, Browser, CognitiveRouter
):
    planner = config.get("planner", {})
    vision = config.get("vision_model", {})

    def check_cancelled():
        # Do not consume a pause/resume here: execute() must discard the old action.
        if control.cancelled.is_set():
            raise Cancelled()

    llm = CognitiveRouter(
        create_model(config),
        planner=create_model(planner) if planner.get("enabled") else None,
        vision=create_model(vision) if config.get("use_vision") and vision.get("enabled") else None,
        memory=memory,
        task_id=task_id,
        task=task,
        emit=emit,
        secrets=secrets,
        planning_interval=config.get("planning_interval", 8),
        max_plans=config.get("max_plans", 6),
        reuse=config.get("reuse_workflows", True),
        checkpoint=check_cancelled,
    )

    def agent_event(event):
        # Tool executions are recorded separately, including safety-blocked actions.
        if event.type.value == "error":
            llm.log("error", event.data)
        emit(event.type.value, event.data)

    agent = Agent(
        llm=llm,
        browser=Browser[config.get("browser", "EDGE")],
        max_steps=config.get("max_steps", 25),
        use_vision=config.get("use_vision", False),
        experimental=config.get("experimental", False),
        allowed_risks=tuple(config.get("allowed_risks", [])),
        secrets={f"model_key_{i}": value for i, value in enumerate(secrets)},
        instructions=["请使用中文简短说明每一步计划和最终结果。"]
        + ([config["instructions"]] if config.get("instructions") else []),
        log_to_console=False,
        log_to_file=False,
        event_subscriber=agent_event,
    )
    llm.bind(agent)
    from desktop_app.runtime import DATA
    from windows_use.timers import TimerStore

    timers = TimerStore(DATA / "timers.sqlite3")
    agent.desktop.timer_callback = lambda **params: timers.tool(config=config, **params)
    agent.instructions.append(
        "用户要求延后或指定时间操作时，使用 timer_tool 创建定时任务，可先 clock 查询时间；创建成功后告知时间和任务 ID，不要持续等待或假称已执行。"
    )
    from windows_use.execution import BLOCKED, authorize_host_action, route

    environment = config.get("execution_environment", "host")
    if environment not in {"host", "vm"}:
        raise ValueError("未知执行环境，任务已停止。")
    guest = None
    if environment == "vm":
        from windows_use.vm.client import GuestClient

        emit(
            "progress", {"phase": "observing", "content": "正在连接虚拟机桌面；不会操作主机窗口。"}
        )
        guest = control.guest = GuestClient(config, control.cancelled)
        state = guest.observe()
        agent.desktop.is_remote = True
        agent.desktop.desktop_state = state
        agent.desktop.remote_environment = guest.environment
        agent.desktop.describe_displays = lambda: guest.displays
        agent.instructions.append(
            "当前只操作独立虚拟机桌面，无法操作主机已打开的窗口或登录态。文件结果写入 workspace_tool 工作目录。"
        )
    llm.log("execution_environment", {"environment": environment})

    # Omit unsupported tools from model schemas as well as enforcing runtime guards.
    agent.registry.tools = [tool for tool in agent.registry.tools if tool.name not in BLOCKED]
    if guest:
        from windows_use.execution import BACKGROUND
        from windows_use.vm.protocol import GUEST_TOOLS

        agent.registry.tools = [
            tool for tool in agent.registry.tools if tool.name in GUEST_TOOLS | BACKGROUND
        ]
    agent.registry.tools_registry = agent.registry.registry()

    def consent_event(kind, data):
        llm.log(kind, data)
        emit(kind, data)

    control.consent.emit = consent_event
    overlay_pid = config.get("overlay_process_id")
    if isinstance(overlay_pid, int) and overlay_pid > 0:
        agent.desktop.ignored_process_ids.add(overlay_pid)
    original_capture = agent.desktop.get_state
    if guest:

        def remote_capture(*args, **kwargs):
            state = guest.observe(*args, **kwargs)
            agent.desktop.desktop_state = state
            agent.desktop.remote_environment = guest.environment
            return state

        original_capture = remote_capture

    def capture(*args, **kwargs):
        emit(
            "progress",
            {
                "phase": "observing",
                "step": agent.state.step,
                "content": "正在读取当前窗口、网页控件和画面…",
            },
        )
        state = (
            original_capture(*args, **kwargs)
            if guest
            else control.observe(
                original_capture,
                *args,
                overlay_context={"purpose": "observe", "filtered": bool(overlay_pid)},
                **kwargs,
            )
        )
        window = state.active_window.name if state.active_window else "桌面"
        llm.observe(
            {
                "window": window,
                "displays": agent.desktop.describe_displays(),
                "controls": state.tree_state.interactive_elements_to_string(
                    config.get("allowed_risks", [])
                )[:18000],
            }
        )
        emit(
            "progress",
            {
                "phase": "thinking",
                "step": agent.state.step,
                "content": f"已观察：{window}。正在等待模型决定下一步…",
            },
        )
        return state

    agent.desktop.get_state = capture

    def authorize(name, params):
        reason = authorize_host_action(
            agent.desktop,
            control.consent,
            name,
            params,
            lambda: control.observe(
                original_capture, overlay_context={"purpose": "observe", "filtered": True}
            ),
            control.checkpoint,
        )
        if reason:
            llm.log("consent_invalidated", {"tool": name, "reason": reason})
            return reason
        emit(
            "progress",
            {"phase": "executing", "content": "已获得本次动作授权，正在核对目标并执行。"},
        )
        return None

    agent.desktop.consent_callback = authorize
    original_execute = agent.registry.execute

    def execute(*args, **kwargs):
        if control.checkpoint():
            # Opening a panel during pause can change the target UI; discard coordinates
            # inferred before the pause and let the Agent observe again.
            from windows_use.agent.registry.views import ToolResult

            result = ToolResult(
                is_success=False, error="用户暂停后已恢复。请重新观察桌面，再决定下一步操作。"
            )
            llm.invalidate("用户暂停后已恢复，之前的执行目标已作废。")
            return result
        tool_name = kwargs.get("tool_name", args[0] if args else "")
        tool_params = kwargs.get("tool_params", args[1] if len(args) > 1 else {})
        if guest:
            from windows_use.vm.client import dispatch

            llm.log(
                "action_intent", {"tool": tool_name, "params": tool_params, "environment": "vm"}
            )
            emit(
                "progress",
                {
                    "phase": "executing",
                    "step": agent.state.step,
                    "tool_name": tool_name,
                    "tool_params": tool_params,
                },
            )
            result = dispatch(
                guest, tool_name, tool_params, lambda: original_execute(*args, **kwargs)
            )
            llm.after_action(tool_name, tool_params, result)
            return result
        try:
            resolved = agent.registry.resolve_params(tool_name, tool_params, agent.desktop)
        except ValueError:
            # Registry returns the precise diagnostic without touching the desktop.
            result = original_execute(*args, **kwargs)
            llm.after_action(tool_name, tool_params, result)
            return result
        emit(
            "progress",
            {
                "phase": "executing",
                "step": agent.state.step,
                "tool_name": tool_name,
                "tool_params": resolved,
            },
        )
        # Persist intent BEFORE a possibly irreversible action. Recovery never replays it.
        llm.log("action_intent", {"tool": tool_name, "params": tool_params, "resolved": resolved})
        if tool_name in {"click_tool", "type_tool"}:
            agent.desktop.last_target_check = None
        if route(tool_name, resolved) == "background":
            result = original_execute(
                tool_name=tool_name, tool_params=resolved, desktop=agent.desktop
            )
        else:
            result = control.observe(
                original_execute,
                tool_name=tool_name,
                tool_params=resolved,
                desktop=agent.desktop,
                check_pause=False,
                overlay_context={
                    "purpose": "action",
                    "tool_name": tool_name,
                    "tool_params": resolved,
                },
            )
        llm.after_action(tool_name, tool_params, result)
        if not result.is_success and tool_name in {"click_tool", "type_tool"}:
            diagnostic = getattr(agent.desktop, "last_target_check", None)
            if diagnostic:
                llm.log("target_validation", diagnostic)
        return result

    agent.registry.execute = execute
    control.checkpoint()
    emit("ready", {})
    result = agent.invoke(task=task)
    if result is None:
        raise RuntimeError("Agent 异常退出，请查看上方错误信息。")
    summary = llm.finish(result.is_done, result.content or result.error or "")
    if memory:
        memory.finish(task_id, "completed" if result.is_done else "failed", summary)
    emit("result", {"is_done": result.is_done, "content": result.content, "error": result.error})


def create_model(config):
    if config["provider"] == "azure_openai":
        from windows_use.providers.azure_openai import ChatAzureOpenAI

        return ChatAzureOpenAI(
            deployment_name=config["model"],
            api_key=config.get("api_key") or None,
            azure_endpoint=config.get("base_url") or None,
            api_version=config.get("api_version") or "2024-02-01",
        )
    from windows_use.cli.setup import create_llm

    return create_llm(
        config["provider"],
        config["model"],
        api_key=config.get("api_key") or None,
        base_url=config.get("base_url") or None,
    )


def test_connection(config, emit):
    from windows_use.agent.tools.service import done_tool
    from windows_use.messages import HumanMessage
    from windows_use.providers.events import LLMEventType

    llm = create_model(config)
    if config.get("_test_role") == "planner":
        from windows_use.cognition import Plan
        from windows_use.tools import Tool

        output = Tool(
            name="submit_plan",
            model=Plan,
            description="Return a test plan; do not operate the desktop.",
        )
        event = llm.invoke(
            messages=[
                HumanMessage(
                    content="This is a planner connectivity check. Call submit_plan with summary='Connected', success_criteria='Schema validated', facts=[], next_actions=[], clicks=[]."
                )
            ],
            tools=[output],
        )
        if event.type != LLMEventType.TOOL_CALL or event.tool_call.name != "submit_plan":
            raise RuntimeError("规划模型未返回 submit_plan，请确认接口支持结构化工具调用。")
        Plan.model_validate(event.tool_call.params)
        emit("connection", {"ok": True, "content": "规划模型连接成功，结构化计划校验通过。"})
        return
    if config.get("_test_role") == "vision_model":
        from PIL import Image, ImageDraw

        from windows_use.messages import ImageMessage

        picture = Image.new("RGB", (240, 100), "white")
        ImageDraw.Draw(picture).text((15, 35), "VISION TEST 42", fill="black", font_size=24)
        event = llm.invoke(
            messages=[ImageMessage(content="Read the text in this test image.", images=[picture])]
        )
        if event.type != LLMEventType.TEXT or "42" not in (event.content or ""):
            raise RuntimeError("视觉测试未能读出图片中的 42，请确认接口支持图像输入。")
        emit("connection", {"ok": True, "content": "视觉模型连接成功，图片识别通过。"})
        return
    event = llm.invoke(
        messages=[
            HumanMessage(
                content=(
                    "This is a tool-calling connectivity check. Call done_tool with thought='test' "
                    "and answer='Connected'. Do not operate the computer."
                )
            )
        ],
        tools=[done_tool],
    )
    if event.type != LLMEventType.TOOL_CALL or event.tool_call.name != "done_tool":
        raise RuntimeError("收到模型响应，但没有有效的工具调用。请确认模型和接口支持 tools。")
    errors = done_tool.validate_params(event.tool_call.params)
    if errors:
        raise RuntimeError("模型返回的工具参数不完整：" + "; ".join(errors))
    emit("connection", {"ok": True, "content": "连接成功，工具调用校验通过。"})


def main():
    protocol = sys.stdout
    # SDK/library prints cannot contaminate the JSON event channel.
    sys.stdout = sys.stderr
    request = json.loads(sys.stdin.readline())
    config = request["config"]
    from desktop_app.settings import model_secrets

    secrets = model_secrets(config)
    lock = threading.Lock()

    def emit(kind, data):
        payload = json.dumps({"type": kind, "data": data}, ensure_ascii=False, default=str)
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            payload = payload.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[已隐藏 Key]")
        with lock:
            protocol.write(payload + "\n")
            protocol.flush()

    control = Control(emit)

    def read_commands():
        for line in sys.stdin:
            try:
                control.receive(json.loads(line))
            except (ValueError, AttributeError):
                continue
        control.receive({"command": "cancel"})
        # A dead GUI must not leave a worker acting on the user's desktop.
        os._exit(0)

    threading.Thread(target=read_commands, daemon=True).start()
    try:
        emit("loading", {"content": "正在准备模型与执行环境…"})
        if request.get("operation") == "test":
            test_connection(config, emit)
        else:
            run_agent(config, request["task"], control, emit)
    except Cancelled:
        emit("cancelled", {})
    except Exception as error:
        emit("fatal", {"content": str(error)})
