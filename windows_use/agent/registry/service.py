from windows_use.agent.desktop.service import Desktop
from windows_use.agent.registry.views import ToolResult
from windows_use.agent.tools import Tool
from windows_use.safety import SafetyPolicy


class Registry:
    def __init__(self, tools: list[Tool] = [], safety: SafetyPolicy | None = None):
        self.tools = tools
        self.safety = safety if safety is not None else SafetyPolicy()
        self.tools_registry = self.registry()

    def registry(self) -> dict[str, Tool]:
        return {tool.name: tool for tool in self.tools}

    def get_tools(self, exclude_tools: list[str] = []) -> list[Tool]:
        if not exclude_tools:
            return self.tools
        return [tool for tool in self.tools if tool.name not in exclude_tools]

    def get_tool(self, tool_name: str) -> Tool | None:
        return self.tools_registry.get(tool_name, None)

    def get_tools_schema(self) -> list[dict]:
        tools = [tool.json_schema for tool in self.tools]
        return tools

    def resolve_params(self, tool_name, params, desktop):
        """Ground an observation-local element ID before safety and overlay hit testing."""
        if tool_name not in {"click_tool", "type_tool"} or params.get("element_id") is None:
            return params
        state = getattr(desktop, "desktop_state", None)
        tree = getattr(state, "tree_state", None)
        nodes = getattr(tree, "interactive_nodes", None) or []
        index = params["element_id"]
        if type(index) is not int or index < 0 or index >= len(nodes):
            raise ValueError("控件编号不在本次观察中，请重新观察并选择当前 element_id。")
        node = nodes[index]
        if params.get("target_name") and params["target_name"] != node.name:
            raise ValueError("目标名称与当前控件编号不一致，请重新观察，不要把输入框当成发送按钮。")
        box = node.bounding_box
        if box.width <= 0 or box.height <= 0:
            raise ValueError("该控件没有可点击的可见区域，请先滚动或切换到目标界面。")
        loc = [node.center.x, node.center.y]
        if params.get("loc") is not None and list(params["loc"]) != loc:
            raise ValueError("控件编号与坐标不一致；请只传当前 element_id，不要从截图另猜坐标。")
        return {k: v for k, v in {**params, "loc": loc}.items() if k != "element_id"}

    def execute(
        self, tool_name: str, tool_params: dict, desktop: Desktop | None = None
    ) -> ToolResult:
        tool = self.get_tool(tool_name)
        if not tool:
            return ToolResult(is_success=False, error=f"Tool '{tool_name}' not found.")
        errors = tool.validate_params(tool_params)
        if errors:
            error_msg = "\n".join(errors)
            return ToolResult(
                is_success=False, error=f"Tool '{tool_name}' validation failed:\n{error_msg}"
            )
        try:
            tool_params = self.resolve_params(tool_name, tool_params, desktop)
        except ValueError as error:
            return ToolResult(is_success=False, error=str(error))
        decision = self.safety.check(tool_name, tool_params, desktop)
        if decision:
            return ToolResult(
                is_success=False, safety_blocked=decision.terminal, error=decision.reason
            )
        try:
            authorize = getattr(desktop, "authorize_action", None)
            if authorize:
                reason = authorize(tool_name, tool_params)
                if isinstance(reason, str) and reason:
                    return ToolResult(is_success=False, safety_blocked=True, error=reason)
                # Approval may have refreshed the desktop; repeat policy checks.
                decision = self.safety.check(tool_name, tool_params, desktop)
                if decision:
                    return ToolResult(
                        is_success=False, safety_blocked=decision.terminal, error=decision.reason
                    )
            self.safety.consume(tool_name, desktop)
            content = tool.invoke(**({"desktop": desktop} | tool_params))
            return ToolResult(is_success=True, content=content)
        except Exception as error:
            error_msg = str(error)
            return ToolResult(
                is_success=False, error=f"Tool '{tool_name}' execution failed:\n{error_msg}"
            )

    async def aexecute(
        self, tool_name: str, tool_params: dict, desktop: Desktop | None = None
    ) -> ToolResult:
        tool = self.get_tool(tool_name)
        if not tool:
            return ToolResult(is_success=False, error=f"Tool '{tool_name}' not found.")
        errors = tool.validate_params(tool_params)
        if errors:
            error_msg = "\n".join(errors)
            return ToolResult(
                is_success=False, error=f"Tool '{tool_name}' validation failed:\n{error_msg}"
            )
        try:
            tool_params = self.resolve_params(tool_name, tool_params, desktop)
        except ValueError as error:
            return ToolResult(is_success=False, error=str(error))
        decision = self.safety.check(tool_name, tool_params, desktop)
        if decision:
            return ToolResult(
                is_success=False, safety_blocked=decision.terminal, error=decision.reason
            )
        try:
            authorize = getattr(desktop, "authorize_action", None)
            if authorize:
                reason = authorize(tool_name, tool_params)
                if isinstance(reason, str) and reason:
                    return ToolResult(is_success=False, safety_blocked=True, error=reason)
                decision = self.safety.check(tool_name, tool_params, desktop)
                if decision:
                    return ToolResult(
                        is_success=False, safety_blocked=decision.terminal, error=decision.reason
                    )
            self.safety.consume(tool_name, desktop)
            content = await tool.ainvoke(**({"desktop": desktop} | tool_params))
            return ToolResult(is_success=True, content=content)
        except Exception as error:
            error_msg = str(error)
            return ToolResult(
                is_success=False, error=f"Tool '{tool_name}' async execution failed:\n{error_msg}"
            )
