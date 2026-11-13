from pydantic import BaseModel


class ToolResult(BaseModel):
    is_success: bool
    safety_blocked: bool = False
    content: str | None = None
    error: str | None = None
