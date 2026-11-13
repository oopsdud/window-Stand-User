"""Windowed entry point. Also accepts --worker for piped, isolated Agent execution."""

import ctypes
import sys
import traceback
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent
ROOT = FRONTEND.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(FRONTEND))

if __name__ == "__main__":
    try:
        from desktop_app.runtime import configure

        configure()
        if "--worker" in sys.argv:
            from desktop_app.worker import main
        else:
            from desktop_app.app import main
        main()
    except Exception:
        if "--worker" in sys.argv:
            raise
        log = ROOT / ".local/desktop-startup-error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(
            None,
            f"桌宠未能启动，请检查桌面依赖是否已安装。\n详细信息：{log}",
            "window-Stand User 桌宠",
            0x10,
        )
