"""Run only inside the project VM's interactive logged-in Windows session."""

import json
import os
import time
import uuid
from pathlib import Path

from windows_use.vm.protocol import encode, read_message, validate_request, write_message


def main():
    import windows_use.uia as uia
    from windows_use.agent.desktop.service import Desktop
    from windows_use.agent.registry.service import Registry
    from windows_use.agent.tools import BUILTIN_TOOLS
    from windows_use.safety import SafetyPolicy

    identity = json.loads(
        Path(r"\\vboxsvr\StandUserCode\identity.json").read_text(encoding="utf-8")
    )
    inputs, outputs = Path(r"\\vboxsvr\StandUserInput"), Path(r"\\vboxsvr\StandUserOutput")
    desktop = Desktop()
    desktop.workspace_root = outputs / "results"
    # These tools touch guest input only. The host never accepts guest approval messages.
    desktop.consent_callback = lambda *_: None
    session_id, generation, seen = None, None, set()
    registry = None
    while True:
        time.sleep(0.1)
        if int(time.monotonic() * 10) % 20 == 0:
            write_message(
                outputs / "heartbeat.json", {"vm_id": identity["vm_id"], "time": time.time()}
            )
        if not (inputs / "session.json").is_file() or not (inputs / "request.json").is_file():
            continue
        session = read_message(inputs / "session.json")
        if not session.get("active") or session.get("expires", 0) < time.time():
            continue
        if session_id != session.get("id"):
            session_id, generation, seen = session.get("id"), None, set()
            registry = Registry(BUILTIN_TOOLS, SafetyPolicy(session.get("allowed_risks", [])))
            desktop.use_vision = bool(session.get("use_vision"))
        request = read_message(inputs / "request.json")
        revoked_path = inputs / "revoked.json"
        if revoked_path.is_file() and read_message(revoked_path).get("session") == session_id:
            continue
        if request.get("id") in seen:
            continue
        response = {"id": request.get("id"), "session": session_id}
        try:
            ident = validate_request(request, session, identity["vm_id"], seen, generation)
            seen.add(ident)
            if len(seen) > 5000:
                raise ValueError("任务请求数超限，请重新启动任务。")
            payload = request.get("payload", {})
            if request["operation"] == "observe":
                state = desktop.get_state(as_bytes=bool(payload.get("as_bytes")))
                if not uia.GetForegroundWindow():
                    raise RuntimeError("客户机没有可观察的活动窗口；请检查是否锁屏或尚未登录。")
                generation = uuid.uuid4().hex
                width, height = uia.GetScreenSize()
                response["result"] = {
                    "state": encode(state),
                    "displays": desktop.describe_displays(),
                    "environment": {
                        "os": desktop.get_windows_version(),
                        "language": desktop.get_default_language(),
                        "user": os.environ.get("USERNAME", "StandUser"),
                        "home_dir": str(Path.home()),
                        "download_directory": str(Path.home() / "Downloads"),
                        "resolution": f"Guest display {width}x{height}",
                    },
                }
            else:
                # Consume BEFORE dispatch, including exceptions/partial side effects.
                generation = None
                response["result"] = registry.execute(
                    payload["tool"], payload.get("params", {}), desktop
                ).model_dump()
        except Exception as error:
            response["error"] = str(error)
        response["generation"] = generation
        write_message(outputs / "response.json", response)


if __name__ == "__main__":
    main()
