# window-Stand User · 白金之星前端

桌宠相关源码、启动入口、使用说明和测试集中在本目录，便于单独审阅和上传。

```text
frontend/
├── desktop_app/        # 界面、动画、托盘、设置及 Agent 进程桥接
├── assets/             # 内置白金之星角色图片
├── tests/              # 前端及进程通信测试
├── start-desktop.vbs   # 双击启动，不显示终端
├── start-desktop.pyw   # Python 入口及子进程入口
├── DESKTOP_GUIDE.md    # 使用指南
└── README.md
```

新用户先双击项目根目录 `Start.vbs`，自动准备 Python 和依赖后启动；详见[从 GitHub 到开始使用](../docs/GETTING_STARTED.md)。本目录 `start-desktop.vbs` 兼容已准备的环境，缺少 Python 时转到首次准备。快捷方式绑定本机路径，不纳入上传源码。

根目录保留一个很小的 `start-desktop.pyw` 转发入口，兼容目录整理前已启动的旧进程和旧快捷方式；实际界面实现仍全部在本目录。更新后需从托盘退出旧桌宠再启动，才能加载新版界面。

## 与核心的关系

前端通过独立进程调用项目根目录 `windows_use/` 中的 Agent；本目录不是可独立运行的安装包。依赖统一声明在根目录 `pyproject.toml`，版本锁定在 `uv.lock`，使用根目录 `.venv/` 中的 Python。移动目录不迁移现有配置：设置和导入图片仍在根目录 `.windows-use/`，缓存仍在 `.local/`。

## 上传与验证

- 仅分享界面实现：上传本目录源码，排除 `__pycache__/`、`.pytest_cache/` 和 `*.lnk`。
- 分享可搭建的完整项目：还需根目录核心源码、`pyproject.toml`、`uv.lock` 等项目文件；不要包含 `.venv/`、`.local/`、`.windows-use/` 或 `.env`，它们含依赖、缓存或个人配置。
- 在项目根目录运行测试：`.venv\Scripts\python.exe -m pytest frontend/tests`。

详细操作见 [桌宠使用指南](DESKTOP_GUIDE.md)。

操作安全策略位于核心 `windows_use/safety.py`，前端只负责显示和传递用户授权。详见 [安全迁移说明](../docs/SAFETY_MIGRATION.md)。

新增 `consent_ui.py` 单次桌面批准卡、`execution_ui.py` 执行环境页；worker 将批准绑定到具体工具参数，并在批准后重查目标。后台文件与控件工具、默认路由和可选沙箱边界见 [混合执行状态](../docs/HYBRID_EXECUTION_STATUS.md)。本轮不安装或运行沙箱客户机 Agent。

记忆和模型分工由核心 `windows_use/memory.py`、`windows_use/cognition.py` 实现；前端 `cognitive_ui.py` 负责角色设置与历史管理，`worker.py` 负责接入执行生命周期。新增“大脑与视觉”“历史与记忆”两个页面。详见 [架构说明](../docs/COGNITIVE_ARCHITECTURE.md)。
