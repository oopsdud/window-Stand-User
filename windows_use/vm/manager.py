"""One owned VM, explicit lifecycle operations, no global hypervisor reconfiguration."""

import base64
import contextlib
import csv
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from windows_use.vm.files import atomic_text

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOME = ROOT / ".windows-use" / "vm"
OWNER = "window-Stand-User/v1"


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def no_links(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("虚拟机目录不能包含符号链接或目录联接。")
    return path.resolve()


def parse_info(text):
    result = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip('"')] = value.strip().strip('"').replace('\\"', '"')
    return result


def find_vbox():
    candidates = [shutil.which("VBoxManage.exe")]
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Oracle\VirtualBox") as key:
                candidates.append(
                    str(Path(winreg.QueryValueEx(key, "InstallDir")[0]) / "VBoxManage.exe")
                )
        except OSError:
            pass
    candidates += [
        str(Path(ROOT.anchor) / "StandUser-VirtualBox" / "VBoxManage.exe"),
        r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe",
    ]
    return next((Path(p).resolve() for p in candidates if p and Path(p).is_file()), None)


class Manager:
    def __init__(self, home=DEFAULT_HOME, binary=None):
        self.home = no_links(home)
        self.manifest = self.home / "machine.json"
        self.binary = Path(binary) if binary else find_vbox()

    def command(self, *args, timeout=90):
        if not self.binary:
            raise RuntimeError("尚未安装 VirtualBox，请先点击安装运行环境。")
        completed = subprocess.run(
            [str(self.binary), *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            # Arguments may include guest provisioning data; never log the command line.
            raise RuntimeError(
                (completed.stderr or completed.stdout or "VirtualBox 操作失败")[-3000:]
            )
        return completed.stdout

    def protect(self):
        # Provisioning media contains a disposable guest password. Restrict THIS project folder.
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        sid = next(csv.reader([result.stdout.strip()]))[1]
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise RuntimeError("无法确定当前用户，未创建客户机账号。")
        # Existing parent may be owned by a setup account. Restrict newly-owned subfolders,
        # never take ownership of the checkout or rewrite its parent permissions.
        for name in ("machines", "guest", "exchange"):
            folder = no_links(self.home / name)
            folder.mkdir(exist_ok=True)
            subprocess.run(
                [
                    "icacls.exe",
                    str(folder),
                    "/inheritance:r",
                    "/grant:r",
                    f"*{sid}:(OI)(CI)F",
                    "*S-1-5-18:(OI)(CI)F",
                    "*S-1-5-32-544:(OI)(CI)F",
                ],
                capture_output=True,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    @contextlib.contextmanager
    def exclusive(self):
        self.home.mkdir(parents=True, exist_ok=True)
        path = self.home / "operation.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RuntimeError(
                "另一个虚拟机操作尚未结束。异常中断后请使用诊断中的锁恢复功能。"
            ) from error
        try:
            os.write(fd, str(os.getpid()).encode())
            yield
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)

    def load(self):
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        if data.get("owner") != OWNER:
            raise ValueError("虚拟机归属信息不匹配，拒绝操作。")
        ident = str(uuid.UUID(data["id"]))
        expected = self.home / "machines" / ("StandUser-" + ident)
        if no_links(data["directory"]) != no_links(expected):
            raise ValueError("虚拟机目录超出本项目范围。")
        return data

    def info(self, data=None):
        data = data or self.load()
        info = parse_info(self.command("showvminfo", data["id"], "--machinereadable"))
        expected = Path(data["directory"]) / (data["name"] + ".vbox")
        if no_links(info.get("CfgFile", "")) != no_links(expected):
            raise ValueError("注册的虚拟机路径与本项目记录不同，拒绝操作。")
        tag = self.command("getextradata", data["id"], "StandUser/Owner").strip()
        if tag != "Value: " + data["id"]:
            raise ValueError("虚拟机缺少本项目归属标记，拒绝操作。")
        return info

    def diagnose(self):
        import psutil

        usage = shutil.disk_usage(self.home if self.home.exists() else ROOT)
        result = {
            "virtualbox": str(self.binary) if self.binary else None,
            "memory_gib": round(psutil.virtual_memory().total / 2**30, 1),
            "available_memory_gib": round(psutil.virtual_memory().available / 2**30, 1),
            "free_gib": round(usage.free / 2**30, 1),
            "directory": str(self.home),
            "recommended": {"cpus": 2, "memory_mb": 4096, "disk_gb": 80},
            "state": "not_created",
            "guest_ready": False,
        }
        resource_status = self.home / "resource-status.json"
        if resource_status.is_file() and resource_status.stat().st_size < 16384:
            result["resources"] = json.loads(resource_status.read_text(encoding="utf-8"))
        if self.manifest.exists():
            data = self.load()
            result.update(id=data["id"], phase=data["phase"])
            try:
                result["state"] = self.info(data).get("VMState", "unknown")
            except RuntimeError as error:
                result["error"] = str(error)
            heartbeat = self.home / "exchange" / "output" / "heartbeat.json"
            if heartbeat.is_file() and heartbeat.stat().st_size < 4096:
                import time

                beat = json.loads(heartbeat.read_text(encoding="utf-8"))
                result["guest_ready"] = (
                    result["state"] == "running"
                    and beat.get("vm_id") == data["id"]
                    and 0 <= time.time() - beat.get("time", 0) < 15
                )
        return result

    def _no_task(self):
        if (self.home / "task.lock").exists():
            raise RuntimeError("虚拟机正被 Agent 使用，请先停止任务，再管理虚拟机。")

    def revoke_task(self, pid):
        from windows_use.vm.protocol import read_message, write_message

        path = self.home / "exchange" / "input" / "session.json"
        if path.is_file():
            session = read_message(path)
            if session.get("pid") == pid:
                write_message(path.with_name("revoked.json"), {"session": session["id"]})

    def recover_locks(self):
        import psutil

        for name in ("operation.lock", "task.lock"):
            path = self.home / name
            if path.is_file():
                no_links(path)
                pid = int(path.read_text(encoding="ascii"))
                if psutil.pid_exists(pid):
                    raise RuntimeError("锁对应的进程仍在运行，拒绝恢复。请先结束相关操作。")
                if name == "task.lock":
                    self.revoke_task(pid)
                path.unlink()
        return "已恢复过期锁；不会重放上次未知结果的动作。"

    def cleanup(self):
        with self.exclusive():
            self._no_task()
            if self.manifest.exists():
                raise RuntimeError("请先删除虚拟机，再清理执行包。")
            for relative in ("guest", "exchange/input"):
                path = no_links(self.home / relative)
                if not path.is_relative_to(self.home):
                    raise ValueError("清理路径超出项目目录。")
                if path.exists():
                    for item in path.rglob("*"):
                        no_links(item)
                    shutil.rmtree(path)
        return "已清理客户机代码与请求文件；输出成果和安装恢复记录保留。"

    def create(self, iso=None, memory_mb=4096, cpus=2, disk_gb=80, image_index=1, unattended=True):
        import psutil

        iso = Path(iso).resolve() if iso else None
        if iso is not None and (not iso.is_file() or iso.suffix.lower() != ".iso"):
            raise ValueError("请选择微软 Windows 11 x64 ISO 安装镜像。")
        if iso is not None and iso.is_relative_to(self.home):
            raise ValueError("请将原始 ISO 放在虚拟机数据目录以外，以便删除虚拟机时保留镜像。")
        if not 4096 <= memory_mb <= min(16384, psutil.virtual_memory().total // 2 // 2**20):
            raise ValueError("内存至少 4096 MB，且不超过主机总内存的一半。")
        if not 2 <= cpus <= min(8, max(2, (os.cpu_count() or 2) - 2)):
            raise ValueError("CPU 配置超出范围，请给主机保留处理能力。")
        if not 64 <= disk_gb <= 256 or not 1 <= image_index <= 99:
            raise ValueError("磁盘或镜像版本索引超出范围。")
        if shutil.disk_usage(ROOT).free < 35 * 2**30:
            raise ValueError("所在盘至少需要约 35 GiB 可用空间，安装与快照还会继续增长。")
        if not self.binary or not self.binary.is_file():
            raise RuntimeError("请先安装 VirtualBox。")
        with self.exclusive():
            if self.manifest.exists():
                raise RuntimeError("已有本项目虚拟机或未完成配置。请先查看状态或清理；不会覆盖。")
            self.protect()
            ident = str(uuid.uuid4())
            name = "StandUser-" + ident
            directory = self.home / "machines" / name
            directory.parent.mkdir(parents=True, exist_ok=True)
            if directory.exists():
                raise RuntimeError("目标目录已存在，拒绝覆盖。")
            data = {
                "owner": OWNER,
                "id": ident,
                "name": name,
                "directory": str(directory),
                "phase": "creating",
                "memory_mb": memory_mb,
                "cpus": cpus,
                "disk_gb": disk_gb,
                "iso": str(iso) if iso else None,
                "image_index": image_index,
            }
            write_json(self.manifest, data)
            self.command(
                "createvm",
                "--name",
                name,
                "--uuid",
                ident,
                "--ostype",
                "Windows11_64",
                "--basefolder",
                str(directory.parent),
                "--register",
            )
            self.command("setextradata", ident, "StandUser/Owner", ident)
            self.info(data)
            self.command(
                "modifyvm",
                ident,
                "--memory",
                memory_mb,
                "--cpus",
                cpus,
                "--firmware",
                "efi",
                "--tpm-type",
                "2.0",
                "--ioapic",
                "on",
                "--graphicscontroller",
                "vboxsvga",
                "--vram",
                "128",
                "--accelerate-3d",
                "off",
                "--clipboard-mode",
                "disabled",
                "--drag-and-drop",
                "disabled",
                "--nic1",
                "nat",
                "--audio-enabled",
                "off",
                "--usb-ohci",
                "off",
                "--usb-ehci",
                "off",
                "--usb-xhci",
                "off",
                "--boot1",
                "disk",
                "--boot2",
                "dvd",
            )
            disk = Path(data["directory"]) / "system.vdi"
            self.command(
                "createmedium",
                "disk",
                "--filename",
                disk,
                "--size",
                disk_gb * 1024,
                "--format",
                "VDI",
                "--variant",
                "Standard",
                timeout=180,
            )
            self.command(
                "storagectl", ident, "--name", "SATA", "--add", "sata", "--controller", "IntelAhci"
            )
            self.command(
                "storageattach",
                ident,
                "--storagectl",
                "SATA",
                "--port",
                "0",
                "--device",
                "0",
                "--type",
                "hdd",
                "--medium",
                disk,
            )
            self.command(
                "storageattach",
                ident,
                "--storagectl",
                "SATA",
                "--port",
                "1",
                "--device",
                "0",
                "--type",
                "dvddrive",
                "--medium",
                iso if iso else "emptydrive",
            )
            self.prepare_bundle(ident)
            if unattended and iso:
                self._unattended(data)
            data["phase"] = "prepared" if iso else "awaiting_iso"
            write_json(self.manifest, data)
            return data

    def finish_setup(self, iso, image_index=1, unattended=True):
        iso = Path(iso).resolve()
        if not iso.is_file() or iso.suffix.lower() != ".iso" or not 1 <= image_index <= 99:
            raise ValueError("请选择有效 ISO 和版本索引。")
        if iso.is_relative_to(self.home):
            raise ValueError("请将原始 ISO 放在虚拟机数据目录以外。")
        with self.exclusive():
            self._no_task()
            data = self.load()
            info = self.info(data)
            if data["phase"] != "awaiting_iso" or info.get("VMState") != "poweroff":
                raise RuntimeError(
                    "只有等待镜像且已关机的新虚拟机可以继续安装；不会重装已有客户机。"
                )
            disk = Path(data["directory"]) / "system.vdi"
            if no_links(info.get("SATA-0-0", "")) != no_links(disk):
                raise ValueError("系统盘配置已被外部修改，拒绝安装。")
            data.update(iso=str(iso), image_index=image_index)
            self.copy_bundle(data["id"])
            self.command(
                "storageattach",
                data["id"],
                "--storagectl",
                "SATA",
                "--port",
                "1",
                "--device",
                "0",
                "--type",
                "dvddrive",
                "--medium",
                iso,
            )
            if unattended:
                self._unattended(data)
            data["phase"] = "prepared"
            write_json(self.manifest, data)
            return data

    def copy_bundle(self, ident):
        bundle = self.home / "guest"
        bundle.mkdir(exist_ok=True)
        # Copy code only; never export desktop.json, .env, memory, user profiles or model keys.
        shutil.copytree(
            ROOT / "windows_use",
            bundle / "windows_use",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for name in ("bootstrap.py", "setup-guest.ps1"):
            shutil.copy2(ROOT / "vm" / "guest" / name, bundle / name)
        write_json(bundle / "identity.json", {"vm_id": ident})
        runtime = bundle / "runtime"
        runtime.mkdir(exist_ok=True)
        base = Path(sys.base_prefix)
        for name in ("python.exe", "pythonw.exe", "LICENSE.txt"):
            if (base / name).is_file():
                shutil.copy2(base / name, runtime / name)
        for pattern in ("*.dll", "python*.zip"):
            for source in base.glob(pattern):
                shutil.copy2(source, runtime / source.name)
        for name in ("Lib", "DLLs"):
            shutil.copytree(
                base / name,
                runtime / name,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    "site-packages",
                    "__pycache__",
                    "*.pyc",
                    "test",
                    "tests",
                    "idlelib",
                    "turtledemo",
                ),
            )
        return bundle

    def refresh_bundle(self):
        with self.exclusive():
            self._no_task()
            data = self.load()
            if self.info(data).get("VMState") != "poweroff":
                raise RuntimeError("更新执行器前请正常关闭虚拟机。")
            bundle = self.copy_bundle(data["id"])
            self.command("sharedfolder", "remove", data["id"], "--name", "StandUserRuntime")
            self.command(
                "sharedfolder",
                "add",
                data["id"],
                "--name",
                "StandUserRuntime",
                "--hostpath",
                bundle / "runtime",
                "--readonly",
            )
            return "已更新执行器和本地运行时副本；依赖目录仅作只读共享。"

    def prepare_bundle(self, ident):
        bundle = self.copy_bundle(ident)
        for name in ("input", "output"):
            (self.home / "exchange" / name).mkdir(parents=True, exist_ok=True)
        dependencies = Path(sys.prefix) / "Lib" / "site-packages"
        for name, path, readonly in (
            ("StandUserCode", bundle, True),
            ("StandUserRuntime", bundle / "runtime", True),
            ("StandUserLibs", dependencies, True),
            ("StandUserInput", self.home / "exchange" / "input", True),
            ("StandUserOutput", self.home / "exchange" / "output", False),
        ):
            if not path.is_dir():
                raise RuntimeError("找不到可复用的 Python 运行环境。")
            self.command(
                "sharedfolder",
                "add",
                ident,
                "--name",
                name,
                "--hostpath",
                str(path),
                *(["--readonly"] if readonly else []),
            )

    def _unattended(self, data):
        additions = self.binary.parent / "VBoxGuestAdditions.iso"
        if not additions.is_file():
            raise RuntimeError("缺少 Guest Additions ISO，请在 VirtualBox 安装程序中修复主程序。")
        password = "Su!" + secrets.token_urlsafe(24)
        from windows_use.vm.credentials import protect

        data["guest_password_protected"] = protect(password)
        write_json(self.manifest, data)
        write_json(
            self.home / "guest" / "login.json", {"username": "StandUser", "password": password}
        )
        password_file = Path(data["directory"]) / "guest-password.txt"
        password_file.write_text(password, encoding="utf-8")
        # Embed local setup in installation media: shared folders may only become available
        # after the Guest Additions reboot. The startup launcher waits for them at logon.
        setup = (ROOT / "vm" / "guest" / "setup-guest.ps1").read_text(encoding="utf-8")
        setup = "$StandUserGuestPassword = '" + password + "'\n" + setup
        encoded_setup = base64.b64encode(setup.encode("utf-16le")).decode("ascii")
        # A separate guest-only credential. Never reuse Windows host or model credentials.
        try:
            self.command(
                "unattended",
                "install",
                data["id"],
                "--iso",
                data["iso"],
                "--user",
                "StandUser",
                "--user-password-file",
                password_file,
                "--admin-password-file",
                password_file,
                "--full-user-name",
                "Stand User",
                "--hostname",
                "stand-user.local",
                "--image-index",
                data["image_index"],
                "--install-additions",
                "--additions-iso",
                additions,
                "--auxiliary-base-path",
                str(Path(data["directory"]) / "unattended-"),
                "--post-install-command",
                "powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand "
                + encoded_setup,
                timeout=180,
            )
        finally:
            password_file.unlink(missing_ok=True)

    def start(self, headless=True, auto_owner=None):
        with self.exclusive():
            self._no_task()
            data = self.load()
            info = self.info(data)
            if data["phase"] != "prepared":
                raise RuntimeError(
                    "配置未完成，不能启动；请选择 Windows ISO 继续安装，或清理失败的配置。"
                )
            if info.get("VMState") == "running":
                if auto_owner is None:
                    (self.home / "auto-run.json").unlink(missing_ok=True)
                if not headless:
                    subprocess.Popen(
                        [
                            str(self.binary.with_name("VirtualBoxVM.exe")),
                            "--startvm",
                            data["id"],
                            "--separate",
                        ],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                return "虚拟机已在运行。"
            import psutil

            if psutil.virtual_memory().available < (data["memory_mb"] + 1024) * 2**20:
                raise RuntimeError(
                    "当前可用内存不足；需在客户机内存之外至少给主机保留 1 GB，请关闭暂时不用的程序后重试。"
                )
            if auto_owner:
                write_json(self.home / "auto-run.json", {**auto_owner, "vm_id": data["id"]})
            else:
                (self.home / "auto-run.json").unlink(missing_ok=True)
            self.command(
                "startvm", data["id"], "--type", "headless" if headless else "separate", timeout=120
            )
            return "已启动独立桌面；尚未登录或安装完成时不会执行 Agent 动作。"

    def control(self, action):
        commands = {
            "shutdown": "acpipowerbutton",
            "save": "savestate",
            "pause": "pause",
            "resume": "resume",
        }
        if action not in commands:
            raise ValueError("不支持的控制操作。")
        with self.exclusive():
            self._no_task()
            data = self.load()
            self.info(data)
            (self.home / "auto-run.json").unlink(missing_ok=True)
            self.command("controlvm", data["id"], commands[action], timeout=180)
            return "已发送虚拟机控制请求；请刷新状态确认。"

    def snapshot(self, restore=False):
        with self.exclusive():
            self._no_task()
            data = self.load()
            if self.info(data).get("VMState") != "poweroff":
                raise RuntimeError("请先正常关闭虚拟机，再创建或恢复基线。")
            if restore:
                if not data.get("baseline"):
                    raise RuntimeError("尚未创建基线快照。")
                self.command("snapshot", data["id"], "restore", data["baseline"], timeout=300)
            else:
                name = "StandUser-Clean-" + secrets.token_hex(4)
                self.command("snapshot", data["id"], "take", name, timeout=300)
                data["baseline"] = name
                write_json(self.manifest, data)
            return "快照操作完成。共享输出目录不随快照回退。"

    def remove(self, confirmation):
        with self.exclusive():
            self._no_task()
            data = self.load()
            if confirmation != data["id"]:
                raise ValueError("删除确认与当前虚拟机不匹配。")
            registered = re.findall(r"\{([0-9a-fA-F-]{36})\}", self.command("list", "vms"))
            state = None
            if data["id"] in registered:
                state = self.info(data).get("VMState")
                if state not in {"poweroff", "aborted", "saved"}:
                    raise RuntimeError("请正常关机后再删除；不会强制丢弃运行或保存中的状态。")
            elif data["phase"] not in {"creating", "unregistered"}:
                raise RuntimeError("注册信息丢失，无法确定磁盘归属；请先核对 VirtualBox 中的状态。")
            directory = no_links(data["directory"])
            for ident in registered:
                if ident == data["id"]:
                    continue
                other = parse_info(self.command("showvminfo", ident, "--machinereadable"))
                if any(
                    Path(value).is_absolute() and Path(value).resolve().is_relative_to(directory)
                    for value in other.values()
                ):
                    raise RuntimeError("其他虚拟机引用了本项目目录，拒绝删除。")
            for entry in directory.rglob("*"):
                no_links(entry)
            disks = list(directory.rglob("*.vdi"))
            if state == "saved":
                # Validate all references before discarding the confirmed VM's saved RAM.
                self.command("discardstate", data["id"])
            # No --delete: VirtualBox must not decide which host files to erase.
            if data["id"] in registered:
                self.command("unregistervm", data["id"])
            data["phase"] = "unregistered"
            write_json(self.manifest, data)
            # VirtualBox retains media registrations; forget children before parents.
            pending = list(disks)
            while pending:
                progress = False
                registered_media = self.command("list", "hdds")
                for disk in pending[:]:
                    if str(disk).casefold() not in registered_media.casefold():
                        pending.remove(disk)
                        progress = True
                        continue
                    try:
                        self.command("closemedium", "disk", disk)
                    except RuntimeError:
                        continue
                    pending.remove(disk)
                    progress = True
                if not progress:
                    raise RuntimeError("介质仍被引用，已停止清理。数据保留，可解除引用后再次删除。")
            if directory.exists():
                shutil.rmtree(directory)
            self.manifest.unlink()
            return "已删除本项目虚拟机。ISO、共享输出与宿主模型设置保留；运行环境可单独卸载。"
