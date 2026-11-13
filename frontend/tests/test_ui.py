import copy
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYTHONIOENCODING"] = "utf-8"

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_app.process import WorkerProcess
from desktop_app.settings import DEFAULTS
from desktop_app.widgets import DeskWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate()


def test_settings_edit_and_state_preview(app):
    window = DeskWindow(copy.deepcopy(DEFAULTS))
    window.model.setText("custom-model")
    window.pet_name.setText("小团子")
    window.art_state.setCurrentIndex(2)
    window.reduced.setChecked(True)
    assert window.values()["model"] == "custom-model"
    assert window.preview.state == "working"
    assert window.preview.config["reduce_motion"] is True
    assert window.preview_title.text() == "小团子"
    saved = []
    window.save_requested.connect(saved.append)
    window.save()
    assert saved[0]["pet_name"] == "小团子"
    assert saved[0]["allowed_risks"] == []
    window.risk_checks["send"].setChecked(True)
    assert window.values()["allowed_risks"] == ["send"]
    window.set_busy(True, paused=True)
    assert window.pause.text() == "继续任务"
    assert not window.run.isEnabled()
    assert not window.tabs.isTabEnabled(1)
    window._quitting = True
    window.close()


def test_process_capture_handshake_and_unicode(app):
    process = WorkerProcess()
    events = []
    exited = []

    def receive(kind, data):
        events.append((kind, data))
        if kind == "capture_start":
            process.command("capture_ready")

    process.event.connect(receive)
    process.exited.connect(exited.append)
    try:
        process.start({}, script=Path(__file__).with_name("fixture_worker.py"))
        wait_until(lambda: bool(exited))
        assert [kind for kind, _ in events] == ["capture_start", "capture_end", "result"]
        assert events[-1][1]["content"] == "中文结果 ✓"
        assert exited == [0]
    finally:
        process.stop()
        process.process.waitForFinished(1000)


def test_stop_terminates_blocked_worker(app):
    process = WorkerProcess()
    exited = []
    process.exited.connect(exited.append)
    process.start({}, task="block", script=Path(__file__).with_name("fixture_worker.py"))
    assert process.process.waitForStarted(3000)
    started = time.monotonic()
    process.stop()
    wait_until(lambda: bool(exited), timeout=3)
    assert time.monotonic() - started < 3
    assert not process.busy


def test_custom_art_can_return_to_default(app, tmp_path, monkeypatch):
    from PIL import Image

    import desktop_app.pet as pet_module

    path = tmp_path / "custom.png"
    Image.new("RGBA", (40, 40), "red").save(path)
    monkeypatch.setattr(pet_module, "asset_path", lambda value: path if value else None)
    canvas = pet_module.PetCanvas()
    config = copy.deepcopy(DEFAULTS)
    config["assets"]["idle"] = "custom.png"
    canvas.configure(config)
    assert len(canvas.frames) == 1
    canvas.configure(copy.deepcopy(DEFAULTS))
    assert canvas.art == DEFAULTS["assets"]["idle"]
    assert len(canvas.frames) == 1


def test_window_cross_hides_to_tray_but_explicit_exit_is_separate(app):
    window = DeskWindow(copy.deepcopy(DEFAULTS))
    actions = []
    window.background_requested.connect(lambda: actions.append("background"))
    window.collapse_requested.connect(lambda: actions.append("pet"))
    window.quit_requested.connect(lambda: actions.append("quit"))
    window.show()
    window.close()
    assert actions == ["background"]
    window.quit_button.click()
    assert actions == ["background", "quit"]
    window._quitting = True
    window.close()


def test_background_stays_hidden_on_events_and_tray_size_persists(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QAbstractNativeEventFilter
    from PySide6.QtWidgets import QPushButton

    import desktop_app.app as app_module
    from desktop_app.settings import SettingsStore

    class NoHotkeys(QAbstractNativeEventFilter):
        registered = [1, 2]

        def __init__(self, *_):
            super().__init__()

        def nativeEventFilter(self, *_):
            return False, 0

        def close(self):
            pass

    monkeypatch.setattr(app_module, "Hotkeys", NoHotkeys)
    companion = app_module.Companion(app, SettingsStore(tmp_path))
    try:
        assert not companion.pet.findChildren(QPushButton)
        companion.desk.close()
        assert companion.hidden_to_tray
        assert not companion.pet.isVisible()
        assert not companion.desk.isVisible()
        companion.on_event("capture_end", {})
        companion.on_event("paused", {})
        companion.outcome = "done"
        companion.on_exit(0)
        assert companion.hidden_to_tray
        assert not companion.pet.isVisible()
        assert not companion.desk.isVisible()
        # Sizing from the tray must neither reveal the app nor save unrelated draft edits.
        companion.desk.model.setText("unsaved-draft")
        companion.size_actions[220].trigger()
        assert companion.pet.width() == 236
        assert companion.store.load()["pet_size"] == 220
        assert companion.store.load()["model"] == ""
        assert not companion.pet.isVisible()
        companion.wake()
        assert companion.pet.isVisible()
        assert not companion.desk.isVisible()
        assert not companion.hidden_to_tray
        companion.pet.canvas.clicked.emit()
        assert companion.actions.isVisible()
        assert not companion.quick.isVisible()
        companion.actions.task.click()
        assert companion.quick.isVisible()
        assert not companion.desk.isVisible()
        companion.quick.input.setText("draft task")
        companion.on_event("capture_start", {})
        assert not companion.quick.isVisible()
        companion.on_event("capture_end", {})
        assert not companion.quick.isVisible()
        companion.open_quick()
        assert companion.quick.input.text() == "draft task"
        companion.hide_to_tray()
        assert not companion.quick.isVisible()
        from types import SimpleNamespace

        original_worker = companion.worker
        commands = []
        companion.worker = SimpleNamespace(busy=True, command=commands.append)
        companion.mode = "run"
        companion.open_quick()
        assert commands == ["pause"]
        assert not companion.quick.isVisible()
        companion.on_event("paused", {})
        assert companion.quick.isVisible()
        assert not companion.desk.isVisible()
        assert not companion.quick.input.isEnabled()
        companion.pause_or_resume()
        assert commands == ["pause", "resume"]
        assert not companion.quick.isVisible()
        companion.worker = original_worker
        companion.desk.set_busy(False)
        companion.desk.model.setText("model-draft-must-not-be-saved")
        companion.desk.pet_name.setText("New pet name")
        companion.desk.save_appearance_button.click()
        assert companion.store.load()["pet_name"] == "New pet name"
        assert companion.store.load()["model"] == ""
        companion.paused = False
        companion.on_event("thought", {"step": 0, "thought": "First step"})
        companion.on_event("tool_call", {"step": 0, "tool_name": "click_tool", "tool_params": {}})
        assert companion.observer.output.toPlainText() == "{}"
        companion.on_event("capture_start", {})
        companion.on_event("capture_end", {})
        assert companion.observer.isVisible()
        assert companion.observer.output.toPlainText() == "{}"
        companion.on_event("thought", {"step": 1, "thought": "Next step"})
        assert companion.observer.output.toPlainText() == "Next step"
        companion.on_event("result", {"is_done": True, "content": "Finished"})
        companion.on_exit(0)
        assert companion.observer.isVisible()
        assert companion.observer.output.toPlainText() == "Finished"
        companion.pet.setProperty("captureExcluded", True)
        companion.observer.setProperty("captureExcluded", True)
        monkeypatch.setattr(app_module, "action_passthrough", lambda *_: True)
        monkeypatch.setattr(app_module, "overlaps_action", lambda *_: False)
        companion.on_event(
            "progress", {"phase": "observing", "step": 2, "content": "Reading browser"}
        )
        companion.on_event("capture_start", {"purpose": "observe", "filtered": True})
        assert companion.pet.isVisible()
        assert companion.observer.isVisible()
        companion.on_event("capture_end", {})
        companion.on_event(
            "progress", {"phase": "thinking", "step": 2, "content": "Waiting for model"}
        )
        assert companion.observer.phase_text == "思考中"
        companion.observer.update_elapsed()
        assert "第 3 步" in companion.observer.title.text()
        assert companion.observer.output.toPlainText() == "Waiting for model"
    finally:
        companion.tray.hide()
        companion.desk._quitting = True
        companion.desk.close()
        companion.pet.close()
        companion.observer.close()
        companion.quick.close()
        companion.actions.close()
        app.removeNativeEventFilter(companion.hotkeys)


def test_quick_input_submission_and_running_pet_drag(app):
    from PySide6.QtCore import QPoint, Qt

    from desktop_app.widgets import PetOverlay, QuickInput

    quick = QuickInput()
    tasks = []
    quick.run_requested.connect(tasks.append)
    quick.show()
    quick.input.setText("  search a mouse  ")
    QTest.keyClick(quick.input, Qt.Key.Key_Return)
    assert tasks == ["search a mouse"]
    quick.set_busy(True, paused=True)
    quick.submit()
    assert len(tasks) == 1
    assert not quick.send.isEnabled()
    from PySide6.QtWidgets import QPushButton

    assert {b.text() for b in quick.findChildren(QPushButton)} == {"发送", "×"}
    pet = PetOverlay(copy.deepcopy(DEFAULTS))
    pet.set_passive(True)
    pet.show()
    pet.update_hit_region()
    assert not pet.mask().contains(QPoint(0, 0))
    assert pet.mask().contains(pet.rect().center())
    assert not pet.windowFlags() & Qt.WindowType.WindowTransparentForInput
    start = pet.pos()
    pet.canvas.dragged.emit(QPoint(25, 30))
    assert pet.pos() == start + QPoint(25, 30)
    pet.close()
    quick.close()


def test_appearance_controls_keep_height_and_save_stays_visible_at_minimum_size(app):
    from PySide6.QtCore import QPoint, QRect

    window = DeskWindow(copy.deepcopy(DEFAULTS))
    window.resize(700, 610)
    window.tabs.setCurrentIndex(2)
    window.show()
    QTest.qWait(30)
    try:
        viewport = window.appearance_scroll.viewport()
        for widget in (window.pet_name, window.art_state, window.motion):
            window.appearance_scroll.ensureWidgetVisible(widget, 0, 0)
            QTest.qWait(10)
            rect = QRect(widget.mapTo(viewport, QPoint()), widget.size())
            assert widget.height() >= 38
            assert viewport.rect().contains(rect)
        save = window.save_appearance_button
        assert window.rect().contains(QRect(save.mapTo(window, QPoint()), save.size()))
        window.size_slider.setValue(220)
        assert window.preview.width() == 220
        assert window.preview.height() == 220
        assert not window.reset_art.isEnabled()
        assert not window.clean_art.isEnabled()
    finally:
        window._quitting = True
        window.close()


def test_changing_pet_preferences_reuses_decoded_art(app, tmp_path, monkeypatch):
    from PIL import Image

    import desktop_app.pet as pet_module

    path = tmp_path / "art.png"
    Image.new("RGBA", (400, 400), "red").save(path)
    monkeypatch.setattr(pet_module, "asset_path", lambda value: path if value else None)
    canvas = pet_module.PetCanvas()
    config = copy.deepcopy(DEFAULTS)
    config["assets"]["idle"] = "art.png"
    canvas.configure(config)
    frame = canvas.frames[0]
    config["pet_name"] = "renamed"
    config["motion"] = "bounce"
    canvas.configure(config)
    assert canvas.frames[0] is frame
    canvas.configure(copy.deepcopy(DEFAULTS))
    assert canvas.art == DEFAULTS["assets"]["idle"]
    assert len(canvas.frames) == 1
    canvas.close()


def test_long_feedback_is_complete_and_auto_scrolls(app, monkeypatch):
    from PySide6.QtCore import Qt

    from desktop_app.widgets import Observer

    observer = Observer()
    text = "\n".join(f"line {i}: full response" for i in range(150))
    observer.append("任务结果", text)
    observer.show()
    QTest.qWait(30)
    try:
        assert observer.output.toPlainText() == text
        assert not observer.windowFlags() & Qt.WindowType.WindowTransparentForInput
        bar = observer.output.verticalScrollBar()
        assert bar.maximum() > 0
        monkeypatch.setattr(observer, "underMouse", lambda: False)
        observer.scroll_after = 0
        observer.scroll_tick()
        assert bar.value() > 0
        observer.pause_scroll()
        before = bar.value()
        observer.scroll_tick()
        assert bar.value() == before
    finally:
        observer.close()


def test_actions_hide_when_an_outside_native_click_starts(app, monkeypatch):
    from PySide6.QtCore import QPoint

    import desktop_app.widgets as module

    down = [False]
    monkeypatch.setattr(module, "mouse_buttons_down", lambda: down[0])
    actions = module.PetActions()
    actions.move(100, 100)
    actions.show()
    monkeypatch.setattr(module.QCursor, "pos", lambda: QPoint(500, 500))
    down[0] = True
    actions.check_outside_click()
    assert not actions.isVisible()
    actions.close()


def test_consent_card_has_no_focus_and_answers_only_once(app):
    from PySide6.QtCore import Qt

    from desktop_app.consent_ui import ConsentCard

    card = ConsentCard()
    answers = []
    card.answered.connect(answers.append)
    data = {
        "request_id": "a",
        "target": "Editor",
        "tool": "click_tool",
        "params": {"loc": [10, 20]},
    }
    card.present(data)
    assert card.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert card.details.isReadOnly()
    card.reply(True)
    card.reply(True)
    assert answers == [{"command": "consent", "request_id": "a", "approved": True}]
    card.present({**data, "request_id": "b"})
    card.close()
    assert answers[-1]["approved"] is False
    card.present({**data, "request_id": "c"})
    card.timer.timeout.emit()
    assert answers[-1] == {"command": "consent", "request_id": "c", "approved": False}
    card.present({**data, "request_id": "hidden"}, visible=False)
    assert not card.isVisible() and card.request_id == "hidden"
    assert card.timer.isActive()
    card.dismiss()


def test_execution_settings_roundtrip_and_unavailable_sandbox(app, monkeypatch):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QPushButton

    import desktop_app.execution_ui as module

    monkeypatch.setattr(
        module, "availability", lambda: {"available": False, "reason": "家庭版不可用"}
    )
    config = copy.deepcopy(DEFAULTS)
    config["sandbox_network"] = True
    window = DeskWindow(config)
    try:
        window.resize(700, 610)
        window.tabs.setCurrentIndex(5)
        window.show()
        QTest.qWait(30)
        save = next(
            button
            for button in window.execution_panel.findChildren(QPushButton)
            if button.text() == "保存执行设置"
        )
        assert window.rect().contains(QRect(save.mapTo(window, QPoint()), save.size()))
        assert window.execution_panel.network.isChecked()
        assert not window.execution_panel.start.isEnabled()
        window.execution_panel.network.setChecked(False)
        assert window.values()["sandbox_network"] is False
        window.set_busy(True)
        assert not window.tabs.isTabEnabled(5)
        window.set_settings_note("已保存")
        assert window.execution_panel.note.text() == "已保存"
    finally:
        window._quitting = True
        window.close()
