# ruff: noqa: E402
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / "frontend")]
from desktop_app.runtime import configure

configure()
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea

from desktop_app.vm_ui import VMPanel
from desktop_app.widgets import STYLE, ensure_fonts


class Window(QScrollArea):
    def closeEvent(self, event):
        if self.widget().busy:
            QMessageBox.information(self, "操作进行中", "请等待当前虚拟机管理操作完成后再关闭。")
            event.ignore()
        else:
            super().closeEvent(event)


app = QApplication(sys.argv)
ensure_fonts()
window = Window()
window.setObjectName("desk")
window.setStyleSheet(STYLE)
window.setWindowTitle("白金之星 · 虚拟机管理")
window.setWidgetResizable(True)
window.setWidget(VMPanel())
window.resize(640, 760)
window.show()
sys.exit(app.exec())
