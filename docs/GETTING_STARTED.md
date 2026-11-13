# 从 GitHub 到开始使用

适用于新用户的 Windows x64 电脑。无需复制开发者的 Python、用户目录、配置或虚拟机。当前仓库地址尚未确定，因此这里不使用上游仓库地址冒充本项目地址；发布后从本项目仓库克隆或 Download ZIP 即可。

## 1. 获取与首次准备

1. 克隆源码，或下载 ZIP 并**完整解压**到本地可写目录。希望放在非系统盘，就先放到该盘；路径可包含中文和空格。不要直接在 ZIP、网络共享或目录联接中运行。
2. 双击根目录 **Start.vbs** →「开始准备」。Windows PowerShell 自带的图形向导可在没有 Python 时运行，进度写在窗口和 `.local/setup.log`。
3. 向导校验固定 uv 下载包，下载项目专用 Python 3.12，并按 `uv.lock` 安装 `desktop` 依赖。全部位于 `.local` 和 `.venv`，不修改全局 PATH，不注册 Python，不添加启动项、服务或计划任务。首次需联网，保守预留至少 5 GB 磁盘空间；下载大小以实际依赖为准。
4. 准备完成，点击「启动白金之星」。以后双击同一个 `Start.vbs`，配置指纹一致时直接启动，不检查网络更新、不重复下载。
5. 在桌宠设置填写自己的模型接口、模型名与 API Key。大脑、视觉模型按需启用；源码不提供预置密钥。保存后配置留在 `.windows-use`，不会被重装依赖覆盖。

首次准备不下载 ISO、不安装 VirtualBox、不启动 Agent 或模型请求。用户只用本机后台/逐次授权模式时，到这里即可使用。环境失败可重试，已完成的下载会复用；依赖锁不一致会停止，不悄悄升级第三方包。已有依赖损坏时，从 `Maintenance.vbs` 点击「修复 / 更新本地依赖」重新准备；失败的更新不会留下可跳过检查的旧成功标记。

脚本固定 uv 0.10.9 的官方 ZIP 与 SHA256；Python 补丁版本由该 uv 版本的发行元数据确定。运行库锁在仓库，开发、打包与 Notebook 工具仅在 `dev` extra，不随普通桌宠安装。

## 2. 需要不抢主机鼠标时

在「设置 → 执行方式」：

1. 点击「微软官方下载」，选择 Windows 11 x64 ISO，存到虚拟机数据目录以外。
2. 选择 ISO，确认对应 Windows 许可，点击 **一键安装并启动**。程序复用既有 VirtualBox，缺少才安装到项目所在盘；Windows UAC 的管理员授权不能代按。虚拟机磁盘和交换目录均在项目下。
3. 等待系统安装、Guest Additions 与客户机执行器上线；必要时手工处理安装、激活、登录或应用安装。程序不会复制主机软件和账号登录态。
4. 点击 **完成准备并启用按需运行**。程序确认执行器在线后保存虚拟机，保存执行设置；之后在桌宠输入任务即可按需启动。

需要人工选择的内容是模型凭据、Windows 镜像/许可、UAC 和账号登录。磁盘创建、运行环境安装、客户机启动配置、资源检测和日常启停尽量自动完成。不是完全无人参与，也不绕过 Windows 授权。

默认 2 核、4 GB 内存、80 GB 动态磁盘；Windows 与软件依然需要实际磁盘空间。运行时至少额外给主机留 1 GB 可用内存，创建时检查至少 35 GiB 空余磁盘。推荐 16 GB 或以上主机内存；负载较高时仍需释放资源。详情见[虚拟机指南](../vm/README.md)。

## 3. 不使用时会留下什么

| 状态 | 行为与资源 |
| --- | --- |
| 只拉取源码 | 无后台进程、无虚拟机、无系统安装 |
| 首次准备完成但没启动 | Python/依赖占磁盘；没有常驻进程 |
| 桌宠待命 | 桌宠自身有内存和动画开销；没有任务模型进程，不自动启动虚拟机 |
| 执行虚拟机任务 | 客户机占配置的 RAM/CPU；一个临时监测进程等待任务结束 |
| 自动启动的任务结束 | 约 21 秒后保存客户机状态并停止，释放运行 RAM/CPU；磁盘与保存文件保留 |
| 手动启动或打开客户机画面 | 用户接管，保持运行；自行保存停止或关机 |
| 托盘退出 | 桌宠退出；自动启动的任务停止后，临时进程完成资源释放再退出 |
| 长期不用 | 停用虚拟机，再清理本地运行环境；个人数据默认保留 |

保存状态不是关机，会暂停客户机里的下载、视频和其他后台工作。需长期运行时关闭自动保存。断电或强制终止整个进程树时不能保证自动清理完成；管理页显示实际状态与最近资源释放结果。

## 4. 清理与卸载

- **仅释放运行资源**：保存虚拟机或正常关机，从托盘退出桌宠。下次可继续使用。
- **移除虚拟机**：在管理界面点击「停用并恢复主机」，确认丢弃客户机磁盘前先导出重要成果。仅删除本项目 VM；若 VirtualBox 是本项目安装且没有其他虚拟机依赖，调用官方卸载。既有安装保留。
- **清缓存或 Python**：退出桌宠并等待任务收尾，双击 **Maintenance.vbs**，选择「仅清理下载缓存」或「删除本地 Python 和依赖」。删除运行时前必须先移除本项目虚拟机，避免破坏其只读共享依赖。
- **彻底不保留个人数据**：先完成前述停用流程，自己备份所需文件后删除整个项目目录。程序不会自动替用户删除模型配置、历史和成果。

清理脚本默认只显示计划，只有图形确认或 `-Apply` 才删除固定项目目录。拒绝越界、未知链接和仍在使用的运行环境；uv 创建且目标位于同一 Python 目录内的版本别名仅解除链接，不递归穿过链接。不会删除其他 Python、其他虚拟机、其他目录中的 ISO。清缓存后再更新需重新下载部分依赖。

VirtualBox 是系统级软件，必需驱动、安装注册和 Windows 缓存/日志不可能全部装在项目盘，也无法承诺卸载后系统完全无痕。程序不改 Hyper-V、安全功能、BCD、宿主电源策略或系统防火墙。没有开机自启和常驻管理服务。

## 5. 更新、恢复和脚本接口

更新源码前退出桌宠、**正常关闭**虚拟机（不要仅保存状态），再 Git pull 或解压新版。双击 Start.vbs 会检测项目依赖/安装脚本指纹，必要时重做本地同步并刷新客户机执行器；设置和历史保留。代码更新涉及客户机但指纹未变化时，在管理页执行「更新客户机执行器」。

不要在已配置后直接搬动项目目录：虚拟环境、共享文件夹和 VM 配置引用绝对路径。迁移应导出成果、停用旧 VM，在新位置拉取源码并重新准备。移动后准备失败不会自动删除旧数据或冒险修复 VM 归属。

脚本供批量准备/排障，普通用户使用两个 VBS 入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup.ps1 -Action Check
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup.ps1 -Action Install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup.ps1 -Action Launch
# 先查看计划
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Cleanup.ps1 -Action Cache
# 明确执行，仅在本项目目录中清理
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Cleanup.ps1 -Action Runtime -Apply
```

PowerShell 执行策略仅对该进程生效。如果企业策略禁用 VBS/PowerShell，应按组织规则处理，不绕过安全策略。公开仓库包含源码、脚本与 uv.lock，不包含 `.local`、`.venv`、`.windows-use`、`.env`；完整分类见[公开仓库内容](PUBLISHING.md)。

## 下载依据与验证边界

- [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)和[Python 管理说明](https://docs.astral.sh/uv/guides/install-python/)；入口使用项目专用安装位置及不注册/不添加 PATH 的参数。
- [固定 uv 发布](https://github.com/astral-sh/uv/releases/tag/0.10.9)及其官方校验文件；依赖来自锁文件记录的包源。
- [Oracle 安装说明](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html)及[微软 ISO](https://www.microsoft.com/software-download/windows11)。

开发验收使用了没有虚拟环境和个人配置的独立源码副本，路径包含中文与空格，实际完成 Python 下载、锁定依赖安装与导入验证。它验证首次准备流程，不等同于所有 Windows 电脑、网络策略或 Windows ISO 已兼容；真实客户机系统安装和模型执行的端到端验收仍待完成。
