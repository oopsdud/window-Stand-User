import subprocess
from types import SimpleNamespace

from windows_use.agent.desktop.service import Desktop


def test_background_powershell_uses_no_console_and_keeps_diagnostics(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=b"quiet-ok", stderr=b"", returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    result = Desktop.__new__(Desktop).execute_command("Get-Date")
    assert result == ("quiet-ok", 0)
    assert calls[0][1]["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert calls[0][1]["capture_output"] is True
