"""Local desktop preferences, Windows-protected credentials, and bounded image imports."""

import base64
import copy
import ctypes
import hashlib
import io
import json
import os
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageOps

from desktop_app.brand import DEFAULT_ART, PET_NAME
from desktop_app.runtime import DATA

PROVIDERS = [
    ("OpenAI / 兼容接口", "openai"),
    ("Anthropic", "anthropic"),
    ("Google Gemini", "google"),
    ("Ollama 本地模型", "ollama"),
    ("DeepSeek", "deepseek"),
    ("OpenRouter", "open_router"),
    ("Groq", "groq"),
    ("Mistral", "mistral"),
    ("Atlas Cloud", "atlas_cloud"),
    ("Cheaper Inference", "cheaper_inference"),
    ("Cerebras", "cerebras"),
    ("NVIDIA", "nvidia"),
    ("Azure OpenAI", "azure_openai"),
    ("LiteLLM", "litellm"),
]
STATES = [
    ("默认 / 待机", "idle"),
    ("思考中", "thinking"),
    ("执行中", "working"),
    ("已完成", "done"),
    ("遇到问题", "error"),
]
DEFAULTS = {
    "provider": "openai",
    "model": "",
    "base_url": "",
    "api_key": "",
    "api_version": "2024-02-01",
    "browser": "EDGE",
    "max_steps": 25,
    "use_vision": False,
    "experimental": False,
    "sandbox_network": False,
    "execution_environment": "host",
    "vm_autostart": True,
    "vm_auto_save": True,
    "allowed_risks": [],
    "instructions": "",
    "pet_name": PET_NAME,
    "pet_size": 160,
    "motion": "float",
    "reduce_motion": False,
    "auto_remove_white": True,
    "assets": {"idle": DEFAULT_ART},
    "show_observer": True,
    "position": None,
    "memory_enabled": True,
    "reuse_workflows": True,
    "planning_interval": 8,
    "max_plans": 6,
    "planner": {
        "enabled": False,
        "provider": "openai",
        "model": "",
        "base_url": "",
        "api_key": "",
        "api_version": "2024-02-01",
    },
    "vision_model": {
        "enabled": False,
        "provider": "openai",
        "model": "",
        "base_url": "",
        "api_key": "",
        "api_version": "2024-02-01",
    },
}

APPEARANCE_KEYS = ("pet_name", "pet_size", "motion", "reduce_motion", "auto_remove_white", "assets")


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, decrypt=False) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(target.data)


def validate_model(config, check_roles=True):
    if not config.get("model", "").strip():
        raise ValueError("请填写模型名称，与模型服务中的 Model ID 保持一致。")
    if config.get("provider") not in {key for _, key in PROVIDERS}:
        raise ValueError("请选择支持的模型服务商。")
    address = config.get("base_url", "").strip()
    if address:
        parsed = urlsplit(address)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("接口地址需要以 http:// 或 https:// 开头，且不应包含账号密码。")
    if check_roles:
        for role, title in (("planner", "规划大脑"), ("vision_model", "视觉模型")):
            profile = config.get(role, {})
            if profile.get("enabled"):
                try:
                    validate_model(profile, check_roles=False)
                except ValueError as error:
                    raise ValueError(f"{title}：{error}") from error


def model_secrets(config):
    return [config.get("api_key", "")] + [
        config.get(role, {}).get("api_key", "") for role in ("planner", "vision_model")
    ]


class SettingsStore:
    def __init__(self, directory=DATA):
        self.directory = Path(directory)
        self.path = self.directory / "desktop.json"
        self.warning = ""

    def load(self):
        self.warning = ""
        config = copy.deepcopy(DEFAULTS)
        if not self.path.exists():
            self._import_cli(config)
            return config
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("无效配置格式")
            for key, default in DEFAULTS.items():
                if key != "api_key" and key in raw:
                    value = raw[key]
                    if default is None or type(value) is type(default):
                        config[key] = value
            if raw.get("api_key_protected"):
                try:
                    config["api_key"] = _dpapi(
                        base64.b64decode(raw["api_key_protected"]), decrypt=True
                    ).decode("utf-8")
                except (OSError, ValueError):
                    self.warning = "当前 Windows 账号无法解密已保存的 Key，请在设置中重新填写。"
            for role in ("planner", "vision_model"):
                profile = copy.deepcopy(DEFAULTS[role])
                saved = raw.get(role, {})
                if not isinstance(saved, dict):
                    saved = {}
                for key, default in profile.items():
                    value = saved.get(key)
                    if key != "api_key" and type(value) is type(default):
                        profile[key] = value
                encrypted = saved.get("api_key_protected")
                if encrypted:
                    try:
                        profile["api_key"] = _dpapi(
                            base64.b64decode(encrypted), decrypt=True
                        ).decode("utf-8")
                    except (OSError, ValueError):
                        self.warning = "某个角色的 Key 无法解密，请到大脑与视觉设置中重新填写。"
                config[role] = profile
            config["planning_interval"] = min(30, max(2, config["planning_interval"]))
            config["max_plans"] = min(20, max(1, config["max_plans"]))
            config["pet_size"] = min(220, max(100, config["pet_size"]))
            config["max_steps"] = min(200, max(1, config["max_steps"]))
            if config["browser"] not in {"EDGE", "CHROME", "FIREFOX"}:
                config["browser"] = "EDGE"
            if config["motion"] not in {"float", "breathe", "bounce"}:
                config["motion"] = "float"
            pos = config["position"]
            if not (isinstance(pos, list) and len(pos) == 2 and all(type(x) is int for x in pos)):
                config["position"] = None
            config["assets"] = {
                k: v
                for k, v in config["assets"].items()
                if k in dict(STATES).values() and isinstance(v, str)
            }
            if config["pet_name"] == "芽芽":
                config["pet_name"] = PET_NAME
        except (OSError, ValueError, TypeError):
            self.warning = "桌宠配置无法读取，已载入默认值。原文件保留在本地目录。"
        return config

    def _import_cli(self, config):
        """One-time import of the existing CLI profile, without loading the Agent package."""
        if not (self.directory / "config.json").is_file():
            return
        try:
            profiles = json.loads((self.directory / "config.json").read_text(encoding="utf-8"))
            active = next((p for p in profiles if p.get("active")), None)
            if not active:
                return
            config.update(
                provider=active["provider"],
                model=active["llm"],
                base_url=active.get("base_url") or "",
            )
            if active.get("api_key_encrypted"):
                from cryptography.fernet import Fernet
                from cryptography.hazmat.primitives import hashes
                from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=b"windows-use-cli-v1",
                    iterations=480_000,
                )
                key = base64.urlsafe_b64encode(kdf.derive(Path.home().as_posix().encode()))
                config["api_key"] = (
                    Fernet(key).decrypt(active["api_key_encrypted"].encode()).decode()
                )
        except Exception:
            self.warning = "已有终端配置未能完整导入，请检查模型设置。"

    def save(self, config):
        self.directory.mkdir(parents=True, exist_ok=True)
        raw = {
            key: copy.deepcopy(config.get(key, value))
            for key, value in DEFAULTS.items()
            if key != "api_key"
        }
        key = config.get("api_key", "")
        raw["api_key_protected"] = base64.b64encode(_dpapi(key.encode())).decode() if key else ""
        for role in ("planner", "vision_model"):
            key = raw[role].pop("api_key", "")
            raw[role]["api_key_protected"] = (
                base64.b64encode(_dpapi(key.encode())).decode() if key else ""
            )
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def save_layout(self, changes):
        """Layout autosaves must never replace persisted model settings with stale memory."""
        if set(changes) - {"position", "pet_size"}:
            raise ValueError("布局保存仅接受位置和大小。")
        current = self.load()
        if self.warning:
            raise OSError("配置读取异常，已停止自动覆盖：" + self.warning)
        current.update(copy.deepcopy(changes))
        self.save(current)


def remove_white_background(frame):
    """Remove neutral near-white pixels connected to the image edge, not enclosed details."""
    frame = frame.convert("RGBA")
    pixels = list(frame.getdata())
    mask = Image.new("L", frame.size)
    mask.putdata(
        [
            255 if a == 0 or (min(r, g, b) >= 225 and max(r, g, b) - min(r, g, b) <= 22) else 0
            for r, g, b, a in pixels
        ]
    )
    access = mask.load()
    width, height = frame.size
    border = [(x, y) for x in range(width) for y in (0, height - 1)]
    border += [(x, y) for y in range(height) for x in (0, width - 1)]
    for point in border:
        if access[point] == 255:
            ImageDraw.floodfill(mask, point, 128)
    output = []
    for (r, g, b, a), connected in zip(pixels, mask.getdata()):
        if connected == 128:
            # Preserve a narrow antialiased edge instead of leaving a white halo.
            alpha = min(1.0, max(0.0, (248 - min(r, g, b)) / 23))
            if alpha:
                r, g, b = (
                    max(0, min(255, round((c - 255 * (1 - alpha)) / alpha))) for c in (r, g, b)
                )
            a = round(a * alpha)
        output.append((r, g, b, a))
    frame.putdata(output)
    return frame


def import_art(source: str, directory=DATA, remove_white=True) -> str:
    """Normalize imported art; bound decoding work, retain alpha and animated durations."""
    path = Path(source)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("图片请控制在 20 MB 以内。")
    frames, durations = [], []
    with Image.open(path) as image:
        if image.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
            raise ValueError("支持 PNG、JPG、WebP 和 GIF 图片。")
        if image.width * image.height > 16_000_000:
            raise ValueError("图片分辨率过大，请缩小到 1600 万像素以内。")
        count = getattr(image, "n_frames", 1)
        if count > 120:
            raise ValueError("动画最多支持 120 帧，请先缩短或降低帧率。")
        if image.width * image.height * count > 128_000_000:
            raise ValueError("动画总像素量过大，请降低分辨率或帧数。")
        for index in range(count):
            image.seek(index)
            if image.width * image.height > 16_000_000:
                raise ValueError("动画帧分辨率过大。")
            frame = ImageOps.exif_transpose(image).convert("RGBA")
            limit = 1024 if count == 1 else 512
            frame.thumbnail((limit, limit), Image.Resampling.LANCZOS)
            if remove_white:
                frame = remove_white_background(frame)
            frames.append(frame.copy())
            durations.append(min(2000, max(40, int(image.info.get("duration", 100)))))
    # Use one shared crop for animations to avoid frame-to-frame size jitter.
    boxes = [frame.getchannel("A").getbbox() for frame in frames]
    boxes = [box for box in boxes if box]
    if not boxes:
        raise ValueError("去白底后图片完全透明，请关闭自动去白底后重新导入。")
    crop = (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
    frames = [frame.crop(crop) for frame in frames]
    # APNG preserves transparency better than re-encoding GIF palettes. The renderer
    # uses Pillow for all frames, so playback is independent of Qt image plugins.
    output = io.BytesIO()
    frames[0].save(
        output,
        format="PNG",
        save_all=len(frames) > 1,
        append_images=frames[1:],
        duration=durations,
        loop=0,
    )
    content = output.getvalue()
    relative = Path("desktop-assets") / (hashlib.sha256(content).hexdigest()[:24] + ".png")
    destination = Path(directory) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return relative.as_posix()


def asset_path(relative, directory=DATA):
    if not relative:
        return None
    if relative == DEFAULT_ART:
        return Path(__file__).resolve().parents[1] / "assets" / "star-platinum.png"
    root = (Path(directory) / "desktop-assets").resolve()
    path = (Path(directory) / relative).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None
