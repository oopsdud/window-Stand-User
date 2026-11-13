import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from windows_use.vm.manager import Manager, write_json
from windows_use.vm.protocol import decode, encode, read_message, validate_request, write_message


class FakeVBox(Manager):
    def __init__(self, home):
        home.mkdir()
        binary = home / "VBoxManage.exe"
        binary.touch()
        super().__init__(home, binary)
        self.calls = []
        self.registered = {}
        self.state = "poweroff"
        self.tag = None

    def protect(self):
        pass

    def prepare_bundle(self, ident):
        pass

    def copy_bundle(self, ident):
        pass

    def command(self, *args, **kwargs):
        args = tuple(map(str, args))
        self.calls.append(args)
        if args[0] == "createvm":
            name, ident = args[args.index("--name") + 1], args[args.index("--uuid") + 1]
            path = Path(args[args.index("--basefolder") + 1]) / name
            path.mkdir()
            (path / (name + ".vbox")).write_text("test")
            self.registered[ident] = path
        elif args[0] == "setextradata":
            self.tag = args[-1]
        elif args[0] == "getextradata":
            return "Value: " + self.tag
        elif args[0] == "showvminfo":
            path = self.registered[args[1]]
            return f'CfgFile="{path / (path.name + ".vbox")}"\nVMState="{self.state}"\n"SATA-0-0"="{path / "system.vdi"}"'
        elif args[0] == "createmedium":
            Path(args[args.index("--filename") + 1]).write_text("disk")
        elif args[:2] == ("list", "vms"):
            return "\n".join(f'"VM" {{{key}}}' for key in self.registered)
        elif args[0] == "unregistervm":
            self.registered.pop(args[1])
        elif args[0] == "controlvm" and args[2] == "savestate":
            self.state = "saved"
        return ""


@pytest.fixture
def manager(tmp_path, monkeypatch):
    import psutil

    import windows_use.vm.manager as module

    monkeypatch.setattr(psutil, "virtual_memory", lambda: NS(total=16 * 2**30, available=8 * 2**30))
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: NS(free=100 * 2**30))
    return FakeVBox(tmp_path / "vm")


def test_prepare_without_iso_does_not_boot_or_change_host_settings(manager, tmp_path):
    data = manager.create()
    assert data["phase"] == "awaiting_iso"
    assert manager.load()["directory"] == data["directory"]
    assert not any(
        c[0] in {"startvm", "setproperty", "hostonlyif", "natnetwork"} for c in manager.calls
    )
    modify = next(c for c in manager.calls if c[0] == "modifyvm")
    assert modify[modify.index("--clipboard-mode") + 1] == "disabled"
    assert modify[modify.index("--tpm-type") + 1] == "2.0"
    assert modify[modify.index("--nic1") + 1] == "nat"
    with pytest.raises(RuntimeError, match="配置未完成"):
        manager.start()
    iso = tmp_path / "Windows.iso"
    iso.write_bytes(b"fixture")
    manager.finish_setup(iso, unattended=False)
    manager.start()
    assert manager.calls[-1] == ("startvm", data["id"], "--type", "headless")


def test_existing_vm_is_not_overwritten_or_deleted_without_matching_id(manager):
    data = manager.create()
    with pytest.raises(RuntimeError, match="已有"):
        manager.create()
    with pytest.raises(ValueError, match="确认"):
        manager.remove("wrong")
    manager.state = "running"
    with pytest.raises(RuntimeError, match="关机"):
        manager.remove(data["id"])
    assert Path(data["directory"]).exists()
    manager.state = "poweroff"
    manager.remove(data["id"])
    assert not Path(data["directory"]).exists()
    assert not manager.manifest.exists()
    assert not any("--delete" in call for call in manager.calls)


def test_tampered_directory_and_owner_fail_closed(manager, tmp_path):
    data = manager.create()
    data["directory"] = str(tmp_path)
    write_json(manager.manifest, data)
    with pytest.raises(ValueError, match="范围"):
        manager.load()
    data["owner"] = "another-app"
    write_json(manager.manifest, data)
    with pytest.raises(ValueError, match="归属"):
        manager.remove(data["id"])


def test_foreign_reference_prevents_removal(manager):
    data = manager.create()
    manager.state = "saved"
    other = str(uuid.uuid4())
    manager.registered[other] = Path(data["directory"])
    with pytest.raises(RuntimeError, match="其他虚拟机"):
        manager.remove(data["id"])
    assert data["id"] in manager.registered
    assert not any(call[0] == "discardstate" for call in manager.calls)


def test_task_lock_prevents_vm_management_and_live_lock_recovery(manager):
    import os

    data = manager.create()
    (manager.home / "task.lock").write_text(str(os.getpid()))
    with pytest.raises(RuntimeError, match="Agent"):
        manager.remove(data["id"])
    with pytest.raises(RuntimeError, match="仍在运行"):
        manager.recover_locks()


def test_protocol_rejects_replay_stale_observation_expired_and_shell():
    ident = uuid.uuid4().hex
    session = {"id": "session", "vm_id": "vm", "active": True, "expires": time.time() + 20}
    request = {
        "id": ident,
        "session": "session",
        "expires": time.time() + 10,
        "operation": "execute",
        "generation": "view",
        "payload": {"tool": "click_tool"},
    }
    assert validate_request(request, session, "vm", set(), "view") == ident
    for changed, sess, seen, gen in [
        (request, session, {ident}, "view"),
        (request, session, set(), "old"),
        ({**request, "expires": 0}, session, set(), "view"),
        ({**request, "payload": {"tool": "shell_tool"}}, session, set(), "view"),
        (request, {**session, "active": False}, set(), "view"),
        ({**request, "session": "old-task"}, session, set(), "view"),
    ]:
        with pytest.raises(ValueError):
            validate_request(changed, sess, "vm", seen, gen)


def test_observation_roundtrip_keeps_geometry_and_never_live_controls():
    from PIL import Image

    from windows_use.agent.desktop.views import DesktopState, Status, Window
    from windows_use.agent.tree.views import BoundingBox, Center, TreeElementNode, TreeState

    box = BoundingBox(-100, 20, 0, 120, 100, 100)
    node = TreeElementNode(box, Center(-50, 70), name="editor", safety_identity=(1, 2))
    node.control = object()
    window = Window("guest", False, 0, Status.NORMAL, box, 10, 20)
    state = DesktopState(
        {"name": "guest"},
        [],
        [],
        window,
        Image.new("RGB", (3, 3)),
        TreeState(interactive_nodes=[node]),
    )
    restored = decode(encode(state))
    assert restored.tree_state.interactive_nodes[0].safety_identity == (1, 2)
    assert restored.tree_state.interactive_nodes[0].center.x == -50
    assert restored.tree_state.interactive_nodes[0].control is None
    assert restored.screenshot.size == (3, 3)
    with pytest.raises(ValueError):
        decode({"_type": "Popen", "args": "run"})


def test_transport_rejects_oversize_and_non_object(tmp_path, monkeypatch):
    import windows_use.vm.protocol as module

    path = tmp_path / "message.json"
    monkeypatch.setattr(module, "MAX_MESSAGE", 64)
    with pytest.raises(ValueError):
        write_message(path, {"value": "x" * 100})
    path.write_text("[]")
    with pytest.raises(ValueError):
        read_message(path)


def test_guest_client_ignores_old_response_and_consumes_generation(manager):
    from windows_use.vm.client import GuestClient

    manager.create()
    manager.state = "running"
    inputs, outputs = manager.home / "exchange/input", manager.home / "exchange/output"
    inputs.mkdir(parents=True)
    outputs.mkdir()
    write_message(outputs / "response.json", {"id": "old", "session": "old", "result": "wrong"})
    client = GuestClient(
        {"api_key": "never-export-this", "planner": {"api_key": "brain-key"}},
        threading.Event(),
        manager,
    )
    assert "never-export-this" not in (inputs / "session.json").read_text()
    assert "brain-key" not in (inputs / "session.json").read_text()

    def respond():
        deadline = time.monotonic() + 3
        while not (inputs / "request.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        request = read_message(inputs / "request.json")
        write_message(
            outputs / "response.json",
            {
                "id": request["id"],
                "session": client.session["id"],
                "generation": "new",
                "result": "correct",
            },
        )

    thread = threading.Thread(target=respond)
    thread.start()
    try:
        assert client.call("observe", timeout=2) == "correct"
        assert client.generation == "new"
        manager.revoke_task(client.session["pid"])
        with pytest.raises(RuntimeError, match="撤销"):
            client.renew()
    finally:
        client.close()
        thread.join(3)
    assert not (manager.home / "task.lock").exists()
    assert not read_message(inputs / "session.json")["active"]


def test_failed_guest_actions_never_fallback_to_host():
    from windows_use.vm.client import dispatch
    from windows_use.vm.protocol import GUEST_TOOLS

    calls = []

    def fail(*args):
        raise TimeoutError("guest unavailable")

    guest = NS(call=fail)
    for tool in GUEST_TOOLS | {"shell_tool", "desktop_tool", "unknown_tool"}:
        result = dispatch(guest, tool, {}, lambda: calls.append("host input"))
        assert not result.is_success and result.safety_blocked
    assert not calls
    assert dispatch(guest, "recall_history", {}, lambda: "local-memory") == "local-memory"


def test_guest_system_prompt_never_reads_host_geometry(monkeypatch):
    import windows_use.agent.context.service as module
    from windows_use.agent.context.service import Context
    from windows_use.agent.desktop.views import Browser

    def forbidden():
        raise AssertionError("host geometry queried")

    monkeypatch.setattr(module.uia, "GetScreenSize", forbidden)
    desktop = NS(
        is_remote=True,
        remote_environment={
            "os": "Guest Windows",
            "language": "zh-CN",
            "user": "StandUser",
            "home_dir": "C:/Users/StandUser",
            "download_directory": "C:/Users/StandUser/Downloads",
            "resolution": "Guest 1280x800",
        },
    )
    text = Context(None)._build_system_prompt("normal", desktop, Browser.EDGE, 25, [])
    assert "Guest 1280x800" in text and "Guest Windows" in text


def test_unattended_bootstrap_is_local_and_temporary_password_is_removed(
    manager, tmp_path, monkeypatch
):
    import base64

    from windows_use.vm import credentials

    monkeypatch.setattr(credentials, "protect", lambda _: "encrypted-guest-password")
    data = manager.create()
    (manager.home / "guest").mkdir()
    (manager.binary.parent / "VBoxGuestAdditions.iso").touch()
    iso = tmp_path / "Windows.iso"
    iso.touch()
    data["iso"] = str(iso)
    manager._unattended(data)
    call = manager.calls[-1]
    assert call[:2] == ("unattended", "install")
    command = call[call.index("--post-install-command") + 1]
    setup = base64.b64decode(command.split("-EncodedCommand ")[1]).decode("utf-16le")
    assert "SystemProductName" in setup and "DefaultPassword" in setup
    assert "For attempt = 1 To 60" in setup
    assert not (Path(data["directory"]) / "guest-password.txt").exists()
    assert manager.load()["guest_password_protected"] == "encrypted-guest-password"


def test_low_memory_refuses_start_before_allocation(manager, monkeypatch, tmp_path):
    import psutil

    iso = tmp_path / "Windows.iso"
    iso.touch()
    manager.create(iso, unattended=False)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: NS(total=16 * 2**30, available=4 * 2**30))
    with pytest.raises(RuntimeError, match="可用内存不足"):
        manager.start()
    assert not any(call[0] == "startvm" for call in manager.calls)


def test_auto_save_only_dead_matching_task_and_preserves_manual_vm(manager, monkeypatch):
    from windows_use.vm import resources

    data = manager.create()
    manager.state = "running"
    owner = {"pid": 12345, "created": 10.0}
    monkeypatch.setattr(resources, "same_process", lambda *_: False)
    assert "手动" in resources.release_task(manager, owner)
    assert not any(call[0] == "controlvm" for call in manager.calls)
    write_json(manager.home / "auto-run.json", {**owner, "vm_id": data["id"]})
    assert "其他任务" in resources.release_task(manager, {**owner, "created": 11.0})
    monkeypatch.setattr(resources, "same_process", lambda *_: True)
    with pytest.raises(RuntimeError, match="仍在运行"):
        resources.release_task(manager, owner)
    monkeypatch.setattr(resources, "same_process", lambda *_: False)
    assert "释放" in resources.release_task(manager, owner)
    assert manager.state == "saved"
    assert not (manager.home / "auto-run.json").exists()


def test_auto_save_refuses_other_vm_and_new_live_task(manager, monkeypatch):
    import os

    from windows_use.vm import resources

    data = manager.create()
    manager.state = "running"
    owner = {"pid": 12345, "created": 10.0}
    monkeypatch.setattr(resources, "same_process", lambda *_: False)
    path = manager.home / "auto-run.json"
    write_json(path, {**owner, "vm_id": "another-vm"})
    with pytest.raises(RuntimeError, match="归属"):
        resources.release_task(manager, owner)
    write_json(path, {**owner, "vm_id": data["id"]})
    (manager.home / "task.lock").write_text(str(os.getpid()))
    with pytest.raises(RuntimeError, match="仍在运行"):
        resources.release_task(manager, owner)
    assert manager.state == "running"


def test_manual_management_cancels_automatic_release(manager, tmp_path):
    iso = tmp_path / "Windows.iso"
    iso.touch()
    data = manager.create(iso, unattended=False)
    manager.start(auto_owner={"pid": 12345, "created": 10.0})
    assert read_message(manager.home / "auto-run.json")["vm_id"] == data["id"]
    manager.state = "running"
    manager.start()  # explicit UI/CLI start means the user wants it kept running
    assert not (manager.home / "auto-run.json").exists()


def test_watcher_failure_during_reuse_does_not_leave_task_lock(manager, monkeypatch):
    from windows_use.vm import resources
    from windows_use.vm.client import GuestClient

    data = manager.create()
    manager.state = "running"
    write_json(manager.home / "auto-run.json", {"pid": -1, "created": 1, "vm_id": data["id"]})

    def fail(*_):
        raise OSError("watcher unavailable")

    monkeypatch.setattr(resources, "watch_task", fail)
    with pytest.raises(OSError, match="watcher"):
        GuestClient({}, threading.Event(), manager)
    assert not (manager.home / "task.lock").exists()


def test_exchange_retries_transient_windows_sharing_violation(tmp_path, monkeypatch):
    from windows_use.vm import files

    real_replace = files.os.replace
    attempts = []

    def locked_then_release(source, target):
        attempts.append(source)
        if len(attempts) < 3:
            raise PermissionError("reader temporarily holds the destination")
        real_replace(source, target)

    monkeypatch.setattr(files.os, "replace", locked_then_release)
    monkeypatch.setattr(files.time, "sleep", lambda *_: None)
    path = tmp_path / "response.json"
    path.write_text('{"old": true}')
    write_message(path, {"new": True})
    assert read_message(path) == {"new": True}
    assert len(attempts) == 3 and not list(tmp_path.glob("*.tmp"))
