"""First-run readiness and destructive-cleanup boundaries without downloading anything."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows deployment entry points")


def script(root, name, *args):
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / name),
            *args,
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "new checkout"
    (root / "scripts").mkdir(parents=True)
    for name in ("Setup.ps1", "Cleanup.ps1"):
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, root / name)
    return root


@pytest.mark.parametrize("name", ["Setup.ps1", "Cleanup.ps1"])
def test_deployment_powershell_syntax(name):
    # Constant repo-relative names, no user text interpolated into executable commands.
    code = (
        "$issues=$null; $tokens=$null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PWD 'scripts/{name}'),[ref]$tokens,[ref]$issues); "
        "if ($issues.Count) { $issues | Out-String | Write-Output; exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", code],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ready_requires_matching_checkout_and_dependency_fingerprint(checkout):
    initial = json.loads(script(checkout, "Setup.ps1", "-Action", "Check").stdout)
    assert not initial["ready"]
    (checkout / ".venv/Scripts").mkdir(parents=True)
    (checkout / ".venv/Scripts/python.exe").touch()
    (checkout / ".local").mkdir()
    receipt = checkout / ".local/setup.json"
    record = {"root": str(checkout), "fingerprint": initial["fingerprint"]}
    receipt.write_text(json.dumps(record), encoding="utf-8")
    assert json.loads(script(checkout, "Setup.ps1", "-Action", "Check").stdout)["ready"]
    record["root"] = str(checkout / "another-copy")
    receipt.write_text(json.dumps(record), encoding="utf-8")
    assert not json.loads(script(checkout, "Setup.ps1", "-Action", "Check").stdout)["ready"]
    record["root"] = str(checkout)
    receipt.write_text(json.dumps(record), encoding="utf-8")
    with (checkout / "uv.lock").open("a") as file:
        file.write("\n# dependency revision\n")
    assert not json.loads(script(checkout, "Setup.ps1", "-Action", "Check").stdout)["ready"]


def test_cleanup_plan_is_read_only_and_vm_blocks_runtime_deletion(checkout):
    (checkout / ".venv").mkdir()
    marker = checkout / ".venv/keep.txt"
    marker.write_text("keep")
    result = script(checkout, "Cleanup.ps1", "-Action", "Runtime")
    assert result.returncode == 0 and not json.loads(result.stdout)["apply"]
    vm = checkout / ".windows-use/vm"
    vm.mkdir(parents=True)
    (vm / "machine.json").write_text("{}")
    blocked = script(checkout, "Cleanup.ps1", "-Action", "Runtime", "-Apply")
    assert blocked.returncode != 0
    assert marker.read_text() == "keep" and (vm / "machine.json").exists()
