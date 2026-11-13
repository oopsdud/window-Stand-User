import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_app.cognitive_ui import MemoryPanel
from desktop_app.settings import DEFAULTS, SettingsStore, validate_model
from desktop_app.widgets import DeskWindow
from windows_use.memory import MemoryStore


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_role_configuration_and_minimum_window_layout(app):
    window = DeskWindow(copy.deepcopy(DEFAULTS))
    window.resize(700, 610)
    window.tabs.setCurrentIndex(3)
    brain = window.cognitive_settings.profiles["planner"]
    brain.enabled.setChecked(True)
    brain.fields["model"].setText("large-planner")
    brain.fields["api_key"].setText("planner-key")
    vision = window.cognitive_settings.profiles["vision_model"]
    vision.enabled.setChecked(True)
    vision.fields["model"].setText("vision-reader")
    window.show()
    QTest.qWait(40)
    values = window.values()
    assert values["planner"]["model"] == "large-planner"
    assert values["vision_model"]["model"] == "vision-reader"
    assert brain.fields["model"].height() >= 36
    assert brain.fields["model"].width() > 150
    assert "未保存" in window.cognitive_settings.note.text()
    window.set_settings_note("验证成功")
    assert window.cognitive_settings.note.text() == "验证成功"
    assert window.settings_note.text() == "验证成功"
    window._quitting = True
    window.close()


def test_memory_ui_manages_preferences_and_reads_paged_history(app, tmp_path):
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    task = store.start("整理网页")
    for i in range(34):
        store.append(task, i, "observe", {"content": f"第 {i} 步"})
    store.put_preference("语言", "中文", quote="中文")
    store.close()
    panel = MemoryPanel(path)
    panel.refresh()
    assert panel.items.count() == 1
    assert panel.current_after == 30
    panel.more()
    assert panel.current_after == 34
    continued = []
    panel.continue_requested.connect(continued.append)
    panel.continue_task()
    assert task in continued[0] and "重新观察" in continued[0]
    panel.kind.setCurrentIndex(1)
    assert "待确认" in panel.items.currentText()
    panel.confirm()
    store = MemoryStore(path)
    assert store.preferences()[0]["value"] == "中文"
    store.close()
    panel.remove()
    assert panel.items.count() == 0


def test_role_keys_are_all_protected_and_legacy_config_loads(tmp_path, monkeypatch):
    import desktop_app.settings as settings

    # Check all credential plumbing deterministically; the separate native test exercises DPAPI.
    monkeypatch.setattr(settings, "_dpapi", lambda data, decrypt=False: data[::-1])
    store = SettingsStore(tmp_path)
    config = copy.deepcopy(DEFAULTS)
    config["api_key"] = "actor-secret"
    config["planner"].update(model="brain", api_key="brain-secret", enabled=True)
    config["vision_model"].update(model="eyes", api_key="eye-secret", enabled=True)
    store.save(config)
    raw = store.path.read_text(encoding="utf-8")
    assert all(secret not in raw for secret in ("actor-secret", "brain-secret", "eye-secret"))
    assert store.load() == config
    store.path.write_text(
        json.dumps({"model": "old-model", "planner": None, "vision_model": []}), encoding="utf-8"
    )
    loaded = store.load()
    assert loaded["model"] == "old-model" and not loaded["planner"]["enabled"]


def test_enabled_role_requires_its_own_model():
    config = copy.deepcopy(DEFAULTS)
    config["model"] = "actor"
    config["planner"]["enabled"] = True
    with pytest.raises(ValueError, match="规划大脑"):
        validate_model(config)


def test_save_button_persists_roles_across_new_store_and_window(app, tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    import desktop_app.settings as settings
    from desktop_app.app import Companion

    monkeypatch.setattr(settings, "_dpapi", lambda data, decrypt=False: data[::-1])
    store = SettingsStore(tmp_path)
    config = copy.deepcopy(DEFAULTS)
    window = DeskWindow(config)
    owner = NS(config=config, desk=window)

    def save(values):
        store.save(values)
        owner.config = copy.deepcopy(values)
        window.config = copy.deepcopy(values)
        return True

    owner.save = save
    window.save_requested.connect(lambda values: Companion.save_model_settings(owner, values))
    for role in ("planner", "vision_model"):
        profile = window.cognitive_settings.profiles[role]
        profile.enabled.setChecked(True)
        profile.fields["model"].setText(role + "-model")
        profile.fields["api_key"].setText(role + "-secret")
        profile.fields["base_url"].setText("https://example.test/v1")
    window.cognitive_settings.save_requested.emit()
    # Saving unrelated appearance/position afterward must retain both model profiles.
    owner.config["position"] = [-1000, 200]
    store.save(owner.config)
    restarted = DeskWindow(SettingsStore(tmp_path).load())
    for role in ("planner", "vision_model"):
        assert restarted.values()[role] == window.values()[role]
        assert role + "-secret" not in store.path.read_text(encoding="utf-8")
    assert not restarted.cognitive_settings.note.text()
    for widget in (window, restarted):
        widget._quitting = True
        widget.close()


def test_role_connection_checks_use_real_contracts_without_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDOWS_USE_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from unittest.mock import MagicMock

    from desktop_app import worker
    from windows_use.messages import ImageMessage
    from windows_use.providers.events import LLMEvent, LLMEventType, ToolCall
    from windows_use.providers.views import TokenUsage

    llm = MagicMock()
    monkeypatch.setattr(worker, "create_model", lambda _: llm)
    emitted = []
    llm.invoke.return_value = LLMEvent(
        type=LLMEventType.TOOL_CALL,
        usage=TokenUsage(),
        tool_call=ToolCall(
            id="test",
            name="submit_plan",
            params={"summary": "ok", "success_criteria": "ok", "clicks": []},
        ),
    )
    worker.test_connection(
        {"_test_role": "planner"}, lambda kind, data: emitted.append((kind, data))
    )
    assert emitted[-1][1]["ok"]
    assert llm.invoke.call_args.kwargs["tools"][0].name == "submit_plan"
    llm.invoke.return_value = LLMEvent(
        type=LLMEventType.TEXT, content="VISION TEST 42", usage=TokenUsage()
    )
    worker.test_connection(
        {"_test_role": "vision_model"}, lambda kind, data: emitted.append((kind, data))
    )
    assert isinstance(llm.invoke.call_args.kwargs["messages"][0], ImageMessage)
    assert emitted[-1][1]["ok"]
    llm.invoke.return_value = LLMEvent(
        type=LLMEventType.TEXT, content="cannot see", usage=TokenUsage()
    )
    with pytest.raises(RuntimeError, match="42"):
        worker.test_connection({"_test_role": "vision_model"}, lambda *_: None)
