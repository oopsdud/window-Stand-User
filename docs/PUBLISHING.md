# Public repository contents / 公开仓库内容分类

| Category / 分类 | Included / 上传内容 |
| --- | --- |
| Runtime source / 程序源码 | `windows_use/`, `frontend/desktop_app/`, entry points |
| Setup & maintenance / 安装管理 | `scripts/`, `vm/`, `Start.vbs`, `Maintenance.vbs` |
| Dependencies / 依赖 | `pyproject.toml`, `uv.lock`, `.python-version` |
| Tests / 测试 | `tests/`, `frontend/tests/`, GitHub CI |
| Documentation / 文档 | Bilingual READMEs, user guides, architecture notes |
| Visual assets / 展示素材 | Bundled companion artwork and fixture screenshots in `docs/images/` |
| Notices / 声明 | `LICENSE`, `docs/third-party/`, upstream README, security policy |

## Local only / 仅本地保留

- `.windows-use/`: model credentials, settings, history and timer databases, imported pictures, VM disks and exchange data.
- `.local/`, `.venv/`: caches, downloaded installers, managed Python, test output and runtime dependencies.
- Environment files, private keys, databases, VM images, logs and local shortcuts.
- `AGENTS.md`, `DEVELOPMENT_PLAN.md` and selected local diagnostic/update/validation notes: local development context, not release documentation.
- Bytecode, editor metadata, test/lint caches and build outputs.

See [`.gitignore`](../.gitignore) for concrete exclusions. Screenshots come from `scripts/capture_readme.py` with fixture data. Do not replace them with unreviewed captures of a personal desktop.

This is a new repository; the deleted original Git history is not reconstructed. Upstream attribution remains in the source, README and license.
