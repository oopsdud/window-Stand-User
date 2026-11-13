"""Guest startup entry: reuse read-only runtime and libraries, keep secrets on host."""

import os
import site
import sys
import time
import traceback
from pathlib import Path

os.environ["WINDOWS_USE_HOME"] = str(Path.home() / "StandUserData")
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
site.addsitedir(r"\\vboxsvr\StandUserLibs")
sys.path.insert(0, r"\\vboxsvr\StandUserCode")

# UIA needs an interactive desktop, so this is a logon application, never a service.
if __name__ == "__main__":
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel.CreateMutexW(None, False, "Local\\StandUserGuest")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() != 183:
        while True:
            try:
                from windows_use.vm.guest import main

                main()
            except Exception:
                try:
                    Path(r"\\vboxsvr\StandUserOutput\guest-error.txt").write_text(
                        traceback.format_exc()[-8000:], encoding="utf-8"
                    )
                except OSError:
                    pass
                time.sleep(5)
