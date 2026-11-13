import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from desktop_app.settings import DEFAULTS
from desktop_app.vm_ui import VMPanel
from desktop_app.widgets import DeskWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_vm_mode_persists_without_modifying_model_profiles(app):
    config = copy.deepcopy(DEFAULTS)
    config["execution_environment"] = "vm"
    config["vm_autostart"] = False
    config["vm_auto_save"] = False
    config["planner"].update(model="saved-brain", enabled=True)
    window = DeskWindow(config)
    try:
        assert window.execution_panel.environment.currentData() == "vm"
        assert not window.execution_panel.vm_autostart.isChecked()
        assert window.values()["planner"] == config["planner"]
        assert window.values()["execution_environment"] == "vm"
        assert not window.values()["vm_autostart"]
        assert not window.values()["vm_auto_save"]
    finally:
        window._quitting = True
        window.close()


def test_vm_creation_does_not_bypass_windows_license_choice(app, monkeypatch):
    panel = VMPanel()
    called = []
    monkeypatch.setattr(panel, "run", lambda operation: called.append(operation))
    panel.iso.setText("Windows.iso")
    panel.create()
    assert not called and "许可" in panel.status.text()
    panel.close()


def test_vm_status_is_readable_and_reports_missing_guest(app):
    import json

    panel = VMPanel()
    panel.result(
        True,
        json.dumps(
            {
                "state": "poweroff",
                "phase": "awaiting_iso",
                "virtualbox": "F:/VirtualBox",
                "guest_ready": False,
                "available_memory_gib": 4.2,
                "free_gib": 100,
                "directory": "F:/vm",
            }
        ),
    )
    assert "等待选择 Windows ISO" in panel.status.text()
    assert "未连接" in panel.status.text()
    panel.close()


def test_vm_sections_collapse_maintenance_without_hiding_daily_controls(app):
    panel = VMPanel()
    assert set(panel.sections) == {
        "运行状态",
        "首次准备",
        "调整安装参数",
        "日常使用",
        "备份与恢复",
        "维护与排障",
        "停用与清理",
    }
    assert not panel.sections["日常使用"][1].isHidden()
    for title in ("调整安装参数", "备份与恢复", "维护与排障", "停用与清理"):
        header, body = panel.sections[title]
        assert body.isHidden()
        header.click()
        assert not body.isHidden()
    panel.close()


def test_timer_panel_cancels_pending_job(app, tmp_path):
    from desktop_app.timer_ui import TimerPanel
    from windows_use.timers import TimerStore

    panel = TimerPanel()
    panel.path = tmp_path / "timers.sqlite3"
    store = TimerStore(panel.path)
    store.create("查看网页", "host", delay_seconds=60)
    panel.refresh()
    panel.entries.setCurrentRow(0)
    panel.cancel()
    assert store.list()[0]["status"] == "cancelled"
    panel.close()
