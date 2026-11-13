"""No desktop/model access: exercises the actual process transport used by the GUI."""

import json
import sys
import time

request = json.loads(sys.stdin.readline())
if request.get("task") == "block":
    time.sleep(60)
else:
    print(json.dumps({"type": "capture_start", "data": {}}), flush=True)
    assert json.loads(sys.stdin.readline())["command"] == "capture_ready"
    print(json.dumps({"type": "capture_end", "data": {}}), flush=True)
    payload = (
        json.dumps(
            {"type": "result", "data": {"is_done": True, "content": "中文结果 ✓"}},
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    # Split inside a multibyte sequence; the receiver must buffer bytes before decoding.
    middle = payload.index("中".encode()) + 1
    sys.stdout.buffer.write(payload[:middle])
    sys.stdout.buffer.flush()
    time.sleep(0.05)
    sys.stdout.buffer.write(payload[middle:])
    sys.stdout.buffer.flush()
