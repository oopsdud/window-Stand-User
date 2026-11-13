import threading

import pytest

from desktop_app.worker import Cancelled, Control


def test_capture_waits_for_frontend_and_announces_end():
    events = []
    captured = threading.Event()
    requested = threading.Event()

    def emit(kind, data):
        events.append(kind)
        if kind == "capture_start":
            requested.set()

    control = Control(emit)
    thread = threading.Thread(target=lambda: control.observe(captured.set))
    thread.start()
    assert requested.wait(1)
    assert not captured.is_set()
    control.receive({"command": "capture_ready"})
    thread.join(2)
    assert captured.is_set()
    assert events == ["capture_start", "capture_end"]


def test_pause_gate_blocks_until_resume():
    paused = threading.Event()
    results = []
    control = Control(lambda kind, _: paused.set() if kind == "paused" else None)
    control.receive({"command": "pause"})
    thread = threading.Thread(target=lambda: results.append(control.checkpoint()))
    thread.start()
    assert paused.wait(1)
    assert not results
    control.receive({"command": "resume"})
    thread.join(2)
    assert results == [True]  # Caller can invalidate pre-pause coordinates.


def test_cancel_prevents_new_action():
    control = Control(lambda *_: None)
    control.receive({"command": "cancel"})
    with pytest.raises(Cancelled):
        control.checkpoint()


def test_capture_end_sent_even_when_capture_fails():
    events = []

    def emit(kind, _):
        events.append(kind)
        if kind == "capture_start":
            control.receive({"command": "capture_ready"})

    control = Control(emit)

    def fail():
        raise ValueError("capture error")

    with pytest.raises(ValueError):
        control.observe(fail)
    assert events[-1] == "capture_end"


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_control_revokes_pending_approval(command):
    requested = threading.Event()
    result = []
    control = Control(lambda kind, _: requested.set() if kind == "consent_requested" else None)
    thread = threading.Thread(
        target=lambda: result.append(control.consent.request("click_tool", {}, "Editor"))
    )
    thread.start()
    assert requested.wait(1)
    control.receive({"command": command})
    thread.join(2)
    assert result == [False]


def test_nested_observation_keeps_overlay_hidden_until_outer_action_finishes():
    from types import SimpleNamespace

    from desktop_app.app import Companion

    state = SimpleNamespace(capture_depth=2, capturing=True)
    Companion.on_event(state, "capture_end", {})
    assert state.capture_depth == 1 and state.capturing
