"""Keep desktop runtime data beside the checkout, including when launched with pythonw."""

import os
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
ROOT = FRONTEND.parent
DATA = ROOT / ".windows-use"


def configure():
    paths = {
        "WINDOWS_USE_HOME": DATA,
        "TEMP": ROOT / ".local/tmp",
        "TMP": ROOT / ".local/tmp",
        "HF_HOME": ROOT / ".local/cache/huggingface",
        "TIKTOKEN_CACHE_DIR": ROOT / ".local/cache/tiktoken",
        "XDG_CACHE_HOME": ROOT / ".local/cache/xdg",
        "PIP_CACHE_DIR": ROOT / ".local/cache/pip",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    os.environ.update(
        ANONYMIZED_TELEMETRY="false",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
