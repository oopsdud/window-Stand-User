"""Desktop application lifecycle and non-interfering observation coordination."""

import copy
import ctypes
import hashlib
import sys
import time
from ctypes import wintypes

from PySide6.QtCore import QTimer
from PySide6.QtGui import QActionGroup
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from desktop_app.platform import (
    Hotkeys,
    action_passthrough,
    overlaps_action,
    protect_overlay,
    release_agent_modifiers,
)
from desktop_app.process import WorkerProcess
from desktop_app.runtime import ROOT
from desktop_app.settings import APPEARANCE_KEYS, SettingsStore, validate_model
from desktop_app.widgets import DeskWindow, Observer, PetActions, PetOverlay, QuickInput, summarize


class Companion:
    def __init__(self, application, store=None):
        self.application = application
        self.store = store or SettingsStore()
        self.config = self.store.load()
        self.desk = DeskWindow(self.config)
        self.desk.execution_panel.vm_panel.deactivated.connect(self.vm_deactivated)
        self.desk.memory_panel.path = self.store.directory / "memory.sqlite3"
        self.task_id = None
        if self.desk.memory_panel.path.exists():
            self.desk.memory_panel.use_store(lambda s: s.recover_interrupted())
        self.pet = PetOverlay(self.config)
        self.observer = Observer()
        self.quick = QuickInput()
        self.actions = PetActions()
        self.actions.settings_requested.connect(lambda: self.open_panel(1))
        self.actions.task_requested.connect(self.open_quick)
        self.quick.run_requested.connect(self.run_quick)
        self.quick.dismissed.connect(self.dismiss_quick)
        self.worker = WorkerProcess()
        from desktop_app.consent_ui import ConsentCard

        self.consent_card = ConsentCard()
        self.consent_card.answered.connect(self.worker.command)
        self.worker.event.connect(self.on_event)
        self.worker.exited.connect(self.on_exit)
        self.worker.diagnostic.connect(self.on_diagnostic)
        self.desk.run_requested.connect(self.run)
        self.desk.pause_requested.connect(self.pause_or_resume)
        self.desk.stop_requested.connect(self.stop)
        self.desk.save_requested.connect(self.save_model_settings)
        self.desk.appearance_requested.connect(self.save_appearance)
        self.desk.test_requested.connect(self.test)
        self.desk.collapse_requested.connect(self.collapse)
        self.desk.background_requested.connect(self.hide_to_tray)
        self.desk.quit_requested.connect(self.quit)
        self.pet.open_requested.connect(self.open_panel)
        self.pet.input_requested.connect(self.toggle_actions)
        self.pet.background_requested.connect(self.hide_to_tray)
        self.pet.moved.connect(self.remember_position)
        self.pet.canvas.dragged.connect(lambda _: self.position_observer())
        self.mode = None
        self.paused = False
        self.pause_pending = False
        self.capturing = False
        self.outcome = None
        self.requested_tab = 0
        self.requested_view = "panel"
        self.foreground = None
        self.error_summary = ""
        self.hidden_to_tray = False
        self.hotkeys = Hotkeys(self.open_quick, self.stop)
        application.installNativeEventFilter(self.hotkeys)
        self.tray = QSystemTrayIcon(self.pet.icon(), application)
        self.tray.setToolTip("window-Stand User · 桌宠")
        self.desk.setWindowIcon(self.tray.icon())
        menu = QMenu()
        menu.addAction("唤醒桌宠", self.wake)
        menu.addAction("快捷输入", self.open_quick)
        menu.addAction("打开面板 / 暂停查看", lambda: self.open_panel(0))
        menu.addAction("模型与执行", lambda: self.open_panel(1))
        menu.addAction("桌宠外观与更多设置", lambda: self.open_panel(2))
        menu.addAction("大脑与视觉模型", lambda: self.open_panel(3))
        menu.addAction("历史与记忆", lambda: self.open_panel(4))
        size_menu = menu.addMenu("桌宠大小")
        self.size_group = QActionGroup(size_menu)
        self.size_actions = {}
        for title, size in (
            ("小 · 100", 100),
            ("较小 · 130", 130),
            ("标准 · 160", 160),
            ("较大 · 190", 190),
            ("大 · 220", 220),
        ):
            action = size_menu.addAction(title)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=size: self.set_pet_size(value))
            self.size_group.addAction(action)
            self.size_actions[size] = action
        size_menu.addSeparator()
        size_menu.addAction("自定义大小…", lambda: self.open_panel(2))
        self._sync_size_menu()
        menu.addSeparator()
        menu.addAction("停止任务  Ctrl+Alt+Esc", self.stop)
        menu.addAction("让桌宠回到屏幕右下角", self.reset_position)
        menu.addAction("隐藏到系统托盘", self.hide_to_tray)
        menu.addSeparator()
        menu.addAction("退出桌宠", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()
        self.reset_position()
        if self.config.get("position"):
            self.pet.move(*self.config["position"])
            self.clamp_position()
        self.pet.show()
        protect_overlay(self.pet, False)
        if self.store.warning:
            self.desk.set_settings_note(self.store.warning)
        if len(self.hotkeys.registered) < 2:
            self.desk.run_note.setText(
                "部分快捷键已被其他应用占用；可用系统托盘菜单呼出面板或停止任务。"
            )
        if not self.config.get("model"):
            self.open_panel(1)
        from desktop_app.timer_ui import TimerDispatcher
        from windows_use.timers import TimerStore

        self.desk.timer_panel.path = self.store.directory / "timers.sqlite3"
        self.timer_dispatcher = TimerDispatcher(
            TimerStore(self.desk.timer_panel.path), self.run_scheduled
        )
        self.schedule_clock = QTimer(application)
        self.schedule_clock.timeout.connect(self.poll_timers)
        self.schedule_clock.start(1000)

    def poll_timers(self):
        try:
            self.timer_dispatcher.tick(self.worker.busy or self.desk.execution_panel.vm_panel.busy)
        except Exception as error:
            self.schedule_clock.stop()
            self.on_diagnostic(f"定时调度已停止，请检查本地数据库后重启桌宠：{error}")

    def run_scheduled(self, job):
        self.desk.append("定时任务到期", job["task"])
        self.tray.showMessage("白金之星 · 定时任务", job["task"][:180])
        return self.run(job["task"], scheduled=job)

    def tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.wake()

    def vm_deactivated(self):
        self.config["execution_environment"] = "host"
        self.desk.config["execution_environment"] = "host"

    def hide_to_tray(self):
        self.hidden_to_tray = True
        self.consent_card.hide()
        self.desk.hide()
        self.quick.hide()
        self.actions.hide()
        self.pet.menu.hide()
        self.pet.hide()
        self.observer.hide()
        self.tray.setToolTip("window-Stand User · 后台运行，点击唤醒桌宠")

    def wake(self):
        self.hidden_to_tray = False
        self.tray.setToolTip("window-Stand User · 点击唤醒桌宠，右键打开设置")
        self._show_overlays()
        if self.consent_card.request_id:
            self.consent_card.show()

    def _sync_size_menu(self):
        # An arbitrary slider value may not correspond to a preset.
        self.size_group.setExclusive(False)
        for size, action in self.size_actions.items():
            action.setChecked(size == self.config["pet_size"])
        self.size_group.setExclusive(True)

    def set_pet_size(self, size):
        config = copy.deepcopy(self.config)
        config["pet_size"] = size
        try:
            self.store.save_layout({"pet_size": size})
        except Exception as error:
            self.desk.set_settings_note(f"桌宠大小保存失败：{error}")
            self._sync_size_menu()
            return
        self.config = config
        self.desk.config["pet_size"] = size
        self.desk.size_slider.setValue(size)
        self.pet.configure(config)
        self.clamp_position()
        self._sync_size_menu()

    def reset_position(self):
        screen = self.application.primaryScreen().availableGeometry()
        self.pet.move(
            screen.right() - self.pet.width() - 22, screen.bottom() - self.pet.height() - 18
        )
        self.position_observer()

    def clamp_position(self):
        screen = (
            self.application.screenAt(self.pet.frameGeometry().center())
            or self.application.primaryScreen()
        )
        area = screen.availableGeometry()
        self.pet.move(
            max(area.left(), min(self.pet.x(), area.right() - self.pet.width())),
            max(area.top(), min(self.pet.y(), area.bottom() - self.pet.height())),
        )
        self.position_observer()

    def position_observer(self):
        screen = self.application.screenAt(self.pet.pos()) or self.application.primaryScreen()
        area = screen.availableGeometry()
        x = min(self.pet.x(), area.right() - self.observer.width() - 8)
        y = self.pet.y() - self.observer.height() - 6
        if y < area.top():
            x = self.pet.x() - self.observer.width() - 8
            y = self.pet.y()
        self.observer.move(
            max(area.left(), x), max(area.top(), min(y, area.bottom() - self.observer.height()))
        )
        self.quick.adjustSize()
        quick_x = self.pet.x() + (self.pet.width() - self.quick.width()) // 2
        quick_y = self.pet.y() - self.quick.height() - 4
        if quick_y < area.top():
            quick_y = self.pet.y() + self.pet.height() + 4
        self.quick.move(
            max(area.left(), min(quick_x, area.right() - self.quick.width() + 1)),
            max(area.top(), min(quick_y, area.bottom() - self.quick.height() + 1)),
        )
        self.actions.move(
            max(
                area.left(),
                min(
                    self.pet.x() + (self.pet.width() - self.actions.width()) // 2,
                    area.right() - self.actions.width() + 1,
                ),
            ),
            max(
                area.top(),
                min(
                    self.pet.y() + self.pet.height() - 6, area.bottom() - self.actions.height() + 1
                ),
            ),
        )

    def remember_position(self):
        self.clamp_position()
        self.config["position"] = [self.pet.x(), self.pet.y()]
        self.desk.config["position"] = self.config["position"]
        try:
            self.store.save_layout({"position": self.config["position"]})
        except OSError:
            self.desk.append("位置未保存", "本地配置暂时无法写入。", "error")

    def _remember_foreground(self):
        getter = ctypes.windll.user32.GetForegroundWindow
        getter.restype = wintypes.HWND
        target = getter()
        own = {
            int(w.winId()) for w in (self.pet, self.desk, self.observer, self.quick, self.actions)
        }
        if target and target not in own:
            self.foreground = target

    def _restore_foreground(self):
        if self.foreground:
            setter = ctypes.windll.user32.SetForegroundWindow
            setter.argtypes = [wintypes.HWND]
            setter(self.foreground)

    def open_panel(self, tab=0):
        self.actions.hide()
        self.requested_view = "panel"
        self.quick.hide()
        self.wake()
        if self.worker.busy and self.mode == "run" and not self.paused:
            self.requested_tab = tab
            self.pause_pending = True
            self.worker.command("pause")
            self.pet.set_state(
                "thinking", "正在暂停，当前调用结束后打开面板。也可用停止快捷键结束任务。"
            )
            return
        self._remember_foreground()
        self.desk.tabs.setCurrentIndex(0 if self.worker.busy else tab)
        self.desk.show()
        self.desk.raise_()
        self.desk.activateWindow()
        self.observer.hide()

    def open_quick(self):
        self.actions.hide()
        self.requested_view = "quick"
        self.wake()
        if self.worker.busy and self.mode == "run" and not self.paused:
            self.pause_pending = True
            self.worker.command("pause")
            return
        self._remember_foreground()
        self.desk.hide()
        self.observer.hide()
        self.quick.set_busy(self.worker.busy, self.paused)
        self.position_observer()
        self.quick.show()
        self.quick.raise_()
        self.quick.activateWindow()
        self.quick.input.setFocus()

    def run_quick(self, task):
        if self.run(task):
            self.quick.input.clear()
        elif not self.worker.busy:
            self.quick.note.setText(self.desk.settings_note.text())
            self.open_panel(1)

    def toggle_actions(self):
        if time.monotonic() - self.actions.dismissed_at < 0.3:
            return
        if self.actions.isVisible():
            self.actions.hide()
        elif not self.capturing and not self.hidden_to_tray:
            screen = self.application.screenAt(self.pet.pos()) or self.application.primaryScreen()
            bottom = screen.availableGeometry().bottom()
            if self.pet.y() + self.pet.height() + self.actions.height() > bottom:
                self.pet.move(self.pet.x(), bottom - self.pet.height() - self.actions.height() - 4)
                self.remember_position()
            self.position_observer()
            self.actions.show()

    def dismiss_quick(self):
        if self.paused and self.mode == "run":
            self.pause_or_resume()
        else:
            self._show_overlays()

    def collapse(self):
        self.desk.hide()
        self.quick.hide()
        self.wake()

    def save(self, config):
        if self.worker.busy:
            self.desk.set_settings_note("请结束当前任务后保存设置。")
            return False
        try:
            self.store.save(config)
            persisted = self.store.load()
            if self.store.warning:
                raise OSError(self.store.warning)
            for role in ("planner", "vision_model"):
                if persisted[role] != config[role]:
                    raise OSError("模型配置写入后读取不一致，请重新保存。")
        except Exception as error:
            self.desk.set_settings_note(f"设置保存失败：{error}")
            self.desk.appearance_note.setText(f"设置保存失败：{error}")
            return False
        self.config = copy.deepcopy(config)
        self.desk.config = copy.deepcopy(config)
        self.desk.brand_pet.configure(config)
        self.desk.setWindowTitle(f"{config['pet_name']} · window-Stand User")
        self.desk.hero.setText(f"你的桌面，交给{config['pet_name']}。")
        self.pet.configure(config)
        self.pet.set_state("idle", "点我输入任务，或拖动我换个位置。")
        self.clamp_position()
        self.desk.set_settings_note(
            f"设置已保存并重新读取验证，重启后保留。位置：{self.store.path}"
        )
        self.desk.appearance_note.setText("外观已保存，桌宠已更新。")
        self.tray.setIcon(self.pet.icon())
        self._sync_size_menu()
        return True

    def save_appearance(self, changes):
        config = copy.deepcopy(self.config)
        config.update(
            {key: copy.deepcopy(changes[key]) for key in APPEARANCE_KEYS if key in changes}
        )
        return self.save(config)

    def save_model_settings(self, changes):
        config = copy.deepcopy(self.config)
        config.update(
            {
                key: copy.deepcopy(value)
                for key, value in changes.items()
                if key not in APPEARANCE_KEYS
            }
        )
        # Preserve unsaved appearance assets when saving model settings.
        assets_draft = copy.deepcopy(self.desk.config.get("assets", {}))
        saved = self.save(config)
        if saved:
            self.desk.config["assets"] = assets_draft
        return saved

    def _validate(self, config):
        try:
            validate_model(config)
            return True
        except ValueError as error:
            self.desk.tabs.setCurrentIndex(1)
            self.desk.set_settings_note(str(error))
            return False

    def run(self, task, *, scheduled=None):
        task = task.strip()
        if not task:
            self.desk.run_note.setText("先写下你希望完成的任务。")
            self.desk.task.setFocus()
            return
        if self.worker.busy:
            return
        config = copy.deepcopy(self.config) if scheduled else self.desk.values()
        if scheduled:
            config["execution_environment"] = scheduled["environment"]
            config["allowed_risks"] = sorted(
                set(config.get("allowed_risks", [])) & set(scheduled["risks"])
            )
            config["_scheduled_timer"] = scheduled["id"]
        if not self._validate(config) or (not scheduled and not self.save(config)):
            return
        self._begin("run")
        self.desk.append("你的任务", task)
        self.observer.output.clear()
        self.observer.has_content = False
        self.observer.last_step = None
        self.desk.hide()
        self.quick.hide()
        if not scheduled:
            self._restore_foreground()
        self.pet.set_passive(True)
        self.pet.set_state("thinking", "正在准备。等待模型时可拖动，点击可暂停并展开输入框。")
        self._show_overlays()
        self.worker.start(config, task)
        return True

    def test(self, config):
        if self.worker.busy or not self._validate(config):
            return
        self._begin("test")
        self.desk.set_settings_note("正在验证连接与工具调用，可以使用停止快捷键取消…")
        self.desk.stop.setEnabled(True)
        self.worker.start(config, operation="test")

    def _begin(self, mode):
        self.mode = mode
        self.task_id = None
        self.paused = self.pause_pending = self.capturing = False
        self.outcome = None
        self.error_summary = ""
        self.desk.set_busy(True, testing=mode == "test")
        self.desk.status.setText("● 正在连接" if mode == "test" else "● 准备执行")

    def pause_or_resume(self):
        if not self.worker.busy or self.mode != "run":
            return
        if self.paused:
            self.desk.hide()
            self.quick.hide()
            self._restore_foreground()
            self.paused = False
            self.pause_pending = False
            self.pet.set_passive(True)
            self.worker.command("resume")
            self.desk.set_busy(True)
            self._show_overlays()
        else:
            self.open_panel(0)

    def stop(self):
        if not self.worker.busy:
            return
        self.outcome = "cancelled"
        self.desk.status.setText("● 正在停止")
        self.worker.stop()
        if self.mode == "run" and not getattr(self.worker, "vm_mode", False):
            release_agent_modifiers()

    def _show_overlays(self):
        if self.capturing or self.hidden_to_tray:
            return
        self.pet.show()
        self.position_observer()
        if (
            self.observer.has_content
            and not self.paused
            and self.config["show_observer"]
            and not self.quick.isVisible()
            and not self.desk.isVisible()
        ):
            self.observer.show()
        else:
            self.observer.hide()

    def on_event(self, kind, data):
        if kind == "consent_requested":
            self.consent_card.present(data, visible=not self.hidden_to_tray)
            if self.hidden_to_tray:
                self.tray.setToolTip("白金之星 · 等待桌面授权，点击查看；超时不会执行")
                if getattr(getattr(self, "timer_dispatcher", None), "active", None):
                    self.tray.showMessage(
                        "定时任务需要桌面授权", "点击托盘唤醒桌宠并查看操作请求；未批准不会执行。"
                    )
            self.pet.set_state("paused", "需要你的同意，才会使用鼠标或键盘。")
            self.desk.status.setText("● 等待桌面授权")
            self.desk.append("等待桌面授权", "已提出单次动作请求；批准前不会执行。")
            return
        if kind == "consent_closed":
            if self.consent_card.request_id == data.get("request_id"):
                self.consent_card.dismiss()
            if self.hidden_to_tray:
                self.tray.setToolTip("window-Stand User · 后台运行，点击唤醒桌宠")
            return
        if kind == "task_started":
            self.task_id = data["task_id"]
            return
        if kind == "cognitive":
            self.desk.append(data.get("title", "任务规划"), data.get("content", ""))
            self.observer.append(
                data.get("title", "任务规划"),
                data.get("content", ""),
                step=data.get("step"),
                force=True,
            )
            self._show_overlays()
            return
        if kind == "progress":
            phase = data.get("phase")
            title = {
                "observing": "观察中",
                "thinking": "思考中",
                "executing": "执行中",
                "planning": "大脑整理与规划",
                "vision": "视觉识别中",
                "acting": "行动者思考中",
                "reusing": "复用已验证流程",
            }.get(phase, "处理中")
            content = data.get("content", "")
            if phase == "executing":
                action, detail = summarize("tool_call", data)
                content = f"{action}\n{detail}"
            self.observer.append(title, content, step=data.get("step"), force=True)
            self._show_overlays()
            return
        if kind == "capture_start":
            self.capture_depth = getattr(self, "capture_depth", 0) + 1
            self.capturing = True
            self.desk.hide()
            self.quick.hide()
            self.actions.hide()
            self.pet.menu.hide()
            purpose = data.get("purpose")
            passthrough = action_passthrough(self.pet, True)
            passthrough = action_passthrough(self.observer, True) and passthrough
            stable = passthrough and (
                purpose == "action"
                or (
                    purpose == "observe"
                    and data.get("filtered")
                    and self.pet.property("captureExcluded")
                    and self.observer.property("captureExcluded")
                )
            )
            for overlay in (self.pet, self.observer):
                if not stable or (
                    purpose == "action" and overlaps_action(overlay, data.get("tool_params", {}))
                ):
                    overlay.hide()
            # Fallback retains the old hide handshake if native exclusion isn't available.
            QTimer.singleShot(20 if stable else 100, lambda: self.worker.command("capture_ready"))
            return
        if kind == "capture_end":
            self.capture_depth = max(0, getattr(self, "capture_depth", 1) - 1)
            if self.capture_depth:
                return
            self.capturing = False
            action_passthrough(self.pet, False)
            action_passthrough(self.observer, False)
            self._show_overlays()
            return
        if kind == "paused":
            self.paused = True
            self.pause_pending = False
            self.pet.set_passive(False)
            self.pet.set_state("paused", "可以查看记录。点击面板的「继续任务」恢复。")
            self.desk.set_busy(True, paused=True)
            self.desk.status.setText("● 已暂停")
            self.observer.append("已暂停", "等待你继续任务；不是程序卡住。", force=True)
            self.desk.append("已暂停", "当前操作已结束，可以查看记录。恢复后会重新观察桌面。")
            if not self.hidden_to_tray:
                if self.requested_view == "quick":
                    self.open_quick()
                else:
                    self.open_panel(self.requested_tab)
            return
        if kind == "resumed":
            self.pet.set_state("thinking", "继续执行，正在重新确认桌面。")
            self.observer.append("观察中", "已恢复，正在重新确认桌面…", force=True)
            return
        if kind in {"loading", "ready"}:
            if self.mode == "run":
                self.pet.set_state("thinking", data.get("content", "已就绪，正在读取桌面。"))
                self.observer.append("准备中", data.get("content", "正在启动桌面观察…"), force=True)
                self._show_overlays()
            return
        if kind == "connection":
            self.outcome = "done"
            self.desk.set_settings_note(data["content"] + " 未保存的设置请点击保存。")
            return
        if kind == "cancelled":
            self.outcome = "cancelled"
            return
        if kind == "fatal":
            self.outcome = "error"
            self.error_summary = data.get("content", "运行失败")
            self.desk.append("运行失败", self.error_summary, "error")
            self.observer.append("运行失败", self.error_summary, force=True)
            self.pet.set_state("error", self.error_summary)
            self.desk.set_settings_note(self.error_summary)
            return
        title, content = summarize(kind, data)
        if kind == "done":
            # The final result event is authoritative; don't append the answer twice.
            return
        if kind == "result":
            self.outcome = "done" if data.get("is_done") else "error"
            self.error_summary = str(data.get("error") or "")
        self.desk.append(title, content, "error" if kind == "error" else "normal")
        if kind in {"thought", "tool_call", "tool_result", "result", "error"} and content.strip():
            phase_title = {
                "thought": "思考中",
                "tool_call": "执行中",
                "tool_result": "等待下一步",
            }.get(kind, title)
            self.observer.append(phase_title, content, step=data.get("step"), force=True)
            self._show_overlays()
        state = "working" if kind == "tool_call" else "thinking"
        if kind == "result":
            state = self.outcome
        self.pet.set_state(state, f"{title} · {content}"[:100])
        if "step" in data:
            self.desk.status.setText(
                f"● 第 {int(data['step']) + 1} / {self.config['max_steps']} 步"
            )

    def on_diagnostic(self, message):
        # SDK warnings and stderr are visible in details, not mixed into the event protocol.
        self.desk.append("运行诊断", message)

    def on_exit(self, code):
        if hasattr(self, "timer_dispatcher"):
            status = self.outcome if self.outcome in {"done", "cancelled"} else "failed"
            self.timer_dispatcher.finish(status, self.error_summary)
        self.consent_card.dismiss()
        if self.task_id:
            status = "cancelled" if self.outcome == "cancelled" else "interrupted"
            self.desk.memory_panel.use_store(
                lambda s: s.finish(
                    self.task_id,
                    status,
                    self.error_summary or "执行进程结束；请读取检查点并重新观察后续接。",
                )
            )
            self.task_id = None
        action_passthrough(self.pet, False)
        action_passthrough(self.observer, False)
        self.capturing = self.paused = self.pause_pending = False
        self.capture_depth = 0
        testing = self.mode == "test"
        self.mode = None
        self.desk.set_busy(False)
        self.quick.set_busy(False)
        self.pet.set_passive(False)
        self.observer.hide()
        if self.outcome == "cancelled":
            if not testing and not getattr(self.worker, "vm_mode", False):
                release_agent_modifiers()
            text = "任务已停止。已启动的外部程序或命令可能仍在运行。"
            self.desk.append("已停止", text)
            self.observer.append("已停止", text, force=True)
            self.pet.set_state("idle", text)
            self.desk.status.setText("● 已停止")
            if testing:
                self.desk.set_settings_note("连接测试已取消。")
        elif self.outcome == "done":
            self.pet.set_state(
                "done", "连接验证通过。" if testing else "任务完成，点击我查看完整结果。"
            )
            self.desk.status.setText("● 已完成")
        else:
            text = self.error_summary or f"执行进程意外结束（退出码 {code}），请查看运行记录。"
            self.pet.set_state("error", text)
            self.desk.status.setText("● 未完成")
            if self.outcome is None:
                self.desk.append("任务未完成", text, "error")
                self.observer.append("任务未完成", text, force=True)
            if testing:
                self.desk.set_settings_note(text)
        self._show_overlays()

    def quit(self):
        if self.desk.execution_panel.vm_panel.busy:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                self.desk, "虚拟机操作进行中", "请等待当前虚拟机管理操作完成后再退出。"
            )
            return
        self.consent_card.dismiss()
        self.schedule_clock.stop()
        if self.worker.busy:
            self.stop()
            self.worker.process.waitForFinished(1500)
        self.hotkeys.close()
        self.tray.hide()
        self.desk._quitting = True
        self.desk.close()
        self.pet.close()
        self.observer.close()
        self.quick.close()
        self.actions.close()
        self.application.quit()


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("window-Stand User")
    application.setQuitOnLastWindowClosed(False)
    identity = "windows-use-pet-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    probe = QLocalSocket()
    probe.connectToServer(identity)
    if probe.waitForConnected(400):
        probe.write(b"show\n")
        probe.waitForBytesWritten(400)
        return
    server = QLocalServer(application)
    QLocalServer.removeServer(identity)
    if not server.listen(identity):
        raise RuntimeError("无法创建本地桌宠实例。")
    companion = Companion(application)
    if "--appearance" in sys.argv:
        companion.open_panel(2)
    elif "--cognition" in sys.argv:
        companion.open_panel(3)

    def activate():
        socket = server.nextPendingConnection()
        if socket:
            companion.open_quick()
            socket.disconnectFromServer()
            socket.deleteLater()

    server.newConnection.connect(activate)
    application.aboutToQuit.connect(companion.hotkeys.close)
    application.exec()
