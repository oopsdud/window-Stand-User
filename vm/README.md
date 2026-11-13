# 白金之星 · 可选虚拟机

更新：2026-11-12。虚拟机使用 VirtualBox 7.2，Windows 11 客户机。主机仍运行桌宠、模型与历史；客户机运行截图、UIA 和输入执行器。虚拟机连接失败会结束相关任务，**不会转去控制主机**。

## 新用户默认行为

- GitHub 源码不包含虚拟环境、个人配置、虚拟磁盘或 Windows。先用根目录 `Start.vbs` 自动准备 Python 与桌宠依赖，见[首次使用](../docs/GETTING_STARTED.md)。
- 可复用既有 VirtualBox；没有时按需安装固定版本 7.2.22，仅主程序及必需驱动，位置为项目所在盘根目录的 `StandUser-VirtualBox`。
- 创建本项目专属虚拟机，默认 2 核、4096 MB、80 GB 动态 VDI，UEFI + TPM 2.0；不会在首次打开桌宠时自动创建或启动。
- 数据位于 `.windows-use/vm`，源码/运行时副本/交换目录均在项目所在盘。80 GB 是容量上限，创建时不预先占满。
- 不安装 Extension Pack、USB、桥接或 Host-only 网络组件，不改 Hyper-V、内存完整性、BCD、宿主防火墙或宿主电源计划，不自动重启主机。
- 默认 NAT 联网，无入站端口转发；关闭共享剪贴板、拖放、USB 和音频。NAT 不等于禁止访问主机/局域网服务。
- 开发机已验证空机固件与管理流程；真实 Windows 安装和客户机 Agent 仍待验收。这不表示从 GitHub 获取的源码已经包含配好的系统。

正式启动 4 GB 客户机时，程序还要求给主机留至少 1 GB 可用内存；内存不足会提示释放资源，不会强行启动。实际要求按当前机器检测，不使用开发机的路径或硬件结论。

## 最少操作启用

1. **下载 ISO**：[微软 Windows 11 官方下载页](https://www.microsoft.com/software-download/windows11)。选择 x64 ISO、所需语言，并按微软页面核对下载。存到希望使用的磁盘普通目录，不要放进 `.windows-use/vm`。程序提供官方入口，不代选版本、许可或下载大镜像。
2. **打开管理**：双击本目录 `Manage-VM.vbs`，或在新版桌宠「设置 → 执行方式」查看虚拟机区域。
3. **选择 ISO → 一键安装并启动**：缺少 VirtualBox 时会调用安装器（Windows UAC 需要用户授权）。已有本项目空机时会接续配置，不再创建第二台。默认 2 核、4 GB、80 GB、镜像索引 1；多版本 ISO 按自己的授权调整。确认 Windows 许可条件后启动自动安装。
4. 安装器会安排 Guest Additions、专用 `StandUser` 账号自动登录和客户机执行器。安装过程中客户机会重启；主机不自动重启。完成后点击「检测 / 刷新」，检查客户机执行器是否在线。
5. 点击 **完成准备并启用按需运行**：只有客户机执行器在线才会启用，先保存虚拟机状态并释放内存，然后保存执行设置。之后从桌宠发任务即可，程序按需启动。首次连接最多等待 120 秒，已运行的客户机响应等待上限为 45 秒。若从独立管理窗口启用，重新打开已在运行的桌宠以读取新设置。

Microsoft ISO、Windows 许可和具体安装界面可能因版本不同而变化，尚不能承诺每个 ISO 都无人值守成功。不跳过 TPM 或其他安全要求、不使用非官方精简镜像或激活工具。若自动安装不兼容，可清理本项目新建客户机后勾选「手动安装系统」重建。

主机里的浏览器登录态、Office/WPS、微信等不会自动复制过去。所需软件需在客户机独立准备；不要把主机密码或整个用户目录共享给客户机。

## 平时管理与资源

| 入口 | 效果 |
| --- | --- |
| 启动后台虚拟机 | 独立桌面运行，无须打开控制台窗口 |
| 打开虚拟机画面 | 查看安装、登录和应用界面；需要你在客户机手动处理问题时使用 |
| 保存状态并停止 | 把当前客户机内存保存到 F 盘，释放客户机运行内存，下次恢复；会额外占用磁盘 |
| 正常关机 | 向客户机发送 ACPI 关机请求；保存提示、更新等可能需要人工处理 |
| 创建基线快照 | 建议装好系统、软件并正常关机后创建；不是主机备份 |
| 恢复基线 | 客户机回到基线，之后的客户机系统盘改动丢失；共享成果不回退 |
| 打开成果目录 | 打开 `.windows-use/vm/exchange/output/results` |
| 更新客户机执行器 | 项目升级后，在客户机正常关机状态更新代码及运行时副本 |
| 恢复过期锁 | 仅清理已退出进程的锁；不会结束仍在工作的进程或重放旧动作 |

默认自动保存**本任务按需启动**的客户机：任务进程正常结束、被停止或退出桌宠后，约 21 秒宽限期结束再保存状态，释放运行内存；保存本身需要额外时间。宽限期内的新任务可接管客户机，旧任务的释放程序不会停止新任务。临时释放进程结束后自行退出，不注册开机服务或计划任务。

手动启动、手动控制或打开客户机画面会取消本次自动释放归属，避免打断用户手工工作；此时自行点击「保存状态并停止」或「正常关机」。关闭「任务结束后自动保存」可保持长期任务运行。暂停仍占内存；保存状态会占用磁盘。停止任务时已经派发的单个动作可能在保存后继续恢复执行，不承诺中途回滚，也不自动重放请求。

自动释放失败会保留虚拟机与诊断，在「检测 / 刷新」中显示；断电、整个程序树被杀、系统崩溃不保证清理进程能完成，需重新打开管理检查。不能把自动释放描述为任何故障下的零资源占用。

建议 Agent 运行时保持客户机在后台；若你主动进入客户机操作，也会和 Agent 共用客户机里的输入。隔离的是主机与客户机，不是同一客户机内的多套输入。

## 不用了：停用并恢复主机

先停止桌宠任务并导出客户机中重要文件，再点击 **「停用并恢复主机」**，确认后程序会：

1. 请求正常关机，最多等待 90 秒；未能关机则停下，不强制断电。
2. 仅注销和删除本项目 UUID、目录和归属标记均匹配的客户机，包含快照/保存状态。
3. 清理客户机代码、运行时副本和请求数据，保留共享成果与恢复记录。
4. 将执行设置切回本机逐次授权，保留原模型、大脑、视觉、Key、桌宠形象与历史。
5. 若 VirtualBox 是本项目安装的、版本及目录未被替换、当前账号没有其他虚拟机，则调用官方 MSI 卸载；既有安装或其他虚拟机依赖的安装不会被自动卸载。使用前也应确认其他 Windows 账号没有依赖它。
6. 清除本项目已验证的 VirtualBox 安装包；原始 Windows ISO 保留。

也可以分别用「删除本项目虚拟机」「清理执行包」「卸载本项目运行环境」。删除需要明确确认；代码不调用 `unregistervm --delete`，先核对归属、其他 VM 引用与目录链接，再在固定项目目录中清理。

**能恢复的是本项目引入的运行环境与配置，不保证系统层完全无痕**：必要驱动、安装登记、Windows Installer 缓存、事件日志、预取记录等由 Windows/官方卸载器管理。需要重启才能卸载的部分不会强制处理。程序不通过删注册表、强删驱动或清系统日志来制造“零残留”。原先启用的 Windows 虚拟化功能保持原状。

## 脚本入口

以下命令在项目根目录执行，全部使用现有 `.venv`；没有全局 ExecutionPolicy 变更。

```powershell
# 只读检测
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Manage-VM.ps1 -Action status

# 仅下载并验证安装器，不安装
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Install-VirtualBox.ps1 -DownloadOnly

# 安装（系统 UAC 授权，通常一次；安装路径在项目所在盘根目录）
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Install-VirtualBox.ps1

# 可先准备空机；如果已有本项目虚拟机则不重复创建
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Manage-VM.ps1 -Action prepare

# 选择自己的 ISO，确认许可后接续安装配置
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Manage-VM.ps1 -Action create -Iso "F:\ISO\Windows11.iso" -AcceptWindowsLicense

# 启动 / 保存状态并停止 / 关机
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Manage-VM.ps1 -Action start
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Manage-VM.ps1 -Action save
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Manage-VM.ps1 -Action shutdown

# 查看恢复计划；不卸载
powershell -NoProfile -ExecutionPolicy Bypass -File vm/Restore-Host.ps1 -Plan
```

`snapshot` 创建基线，`restore -ConfirmId <本项目 UUID>` 恢复，`delete -ConfirmId <本项目 UUID>` 删除，`cleanup` 清执行包，`update` 更新执行器。`Restore-Host.ps1` 无 `-Plan` 才实际卸载，并检查 VM 是否已经删除。

## 数据、通信和权限边界

| 路径/资源 | 位置与清理方式 |
| --- | --- |
| VirtualBox 程序 | `<项目所在盘>:\StandUser-VirtualBox` 或复用用户既有安装；官方 MSI 卸载 |
| 安装包 | `.local/vm-downloads`；停用流程只删除匹配已校验摘要的本项目包 |
| 虚拟磁盘/快照/NVRAM | `.windows-use/vm/machines/StandUser-<UUID>`；只在确认删除时清理 |
| 客户机代码、Python 副本 | `.windows-use/vm/guest`；只读共享，停用时清理 |
| 客户机第三方库 | 本项目 `.venv/Lib/site-packages` 只读共享；不下载第二套，不复制个人模型配置 |
| 输入协议 | `exchange/input` 只读共享给客户机 |
| 输出、心跳、诊断 | `exchange/output` 是唯一可写主机共享；成果默认保留 |
| 归属/安装记录 | `machine.json`、`host-install.json`；用于匹配 UUID、目录、版本与恢复范围 |
| 主机 Windows 服务/驱动/注册 | Windows 系统目录和注册表，不可能全部迁到 F 盘；由官方安装器和卸载器处理 |

客户机不接收模型 Key。宿主只发送任务会话、风险权限、工具参数，接收有大小限制的 JSON、控件树和 PNG。没有 pickle、任意命令 RPC 或客户机请求主机批准的通道。

请求绑定会话随机 ID、具体请求 ID、观察版本和期限。旧请求/重复动作/旧画面不执行；一次观察版本只消费一次。主机每 4 秒续期，租约 20 秒；停止时另写撤销记录。通信失败不自动重放动作，已开始的单次客户机动作不保证可中途撤销，结果未知时需先核对。

专用客户机账号随机生成，主机显示密码使用 DPAPI。无人值守安装介质、客户机自动登录配置需要该客户机密码，因此不是“密码永不落明文”；相关本地子目录限制到当前用户、管理员及 SYSTEM，且只用于这台客户机。不要把它当成重要个人账号密码。

程序启用的是通用 VM 隔离，不是无条件可信执行承诺。输出目录是有意开放的写入面；NAT 联网仍可产生外部副作用；登录、验证码、UAC、安全桌面和受保护内容可能需要人工处理。真实客户机兼容性和性能待安装具体 Windows/应用后验收。

## 排障

- **无 ISO**：从上方微软入口下载，已有空机状态应为“等待选择 Windows ISO”。
- **客户机未连接**：打开虚拟机画面，确认已安装系统并登录、Guest Additions 就绪。手动安装时，在客户机以管理员运行 `\\vboxsvr\StandUserCode\setup-guest.ps1`，然后重新登录。
- **执行器报错**：查看 `exchange/output/guest-error.txt`；项目更新后正常关闭 VM，点击更新执行器再启动。启动器从只读共享读取程序，因此移动工程目录或修改依赖前先关机。
- **自动安装中断**：配置和错误保留，不覆盖已有磁盘；核对镜像版本与索引。必要时删除本项目新机后重建或选手动安装。
- **内存不足**：释放内存再启动；程序不会关闭你的其他应用，也不自动降低 Windows 11 所需内存。
- **锁屏/休眠/宿主睡眠**：客户机会暂停或观察失效；重新登录/恢复后重新观察，旧动作不能沿用。
- **已有其他 VirtualBox**：复用现有软件，不覆盖版本、不将其标为本项目拥有；停用时保留原运行环境。

## 依据

- [Oracle 官方安装说明](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/installation.html)：Windows 主机、安装组件、安装目录安全要求、MSI 卸载。
- [VBoxManage 官方命令参考](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/vboxmanage.html)：无人值守安装、EFI/TPM、共享目录、后台运行、快照与注销。
- [Oracle 7.2.22 安装包校验表](https://download.virtualbox.org/virtualbox/7.2.22/SHA256SUMS)：本项目固定 Win 安装器摘要并另验数字签名。
- [微软 Windows 11 ISO](https://www.microsoft.com/software-download/windows11)：用户自行选择系统版本、语言与镜像。

具体实现：`windows_use/vm/`、`frontend/desktop_app/vm_ui.py`、本目录脚本。此文记录实做状态；真实 Windows 安装和应用端到端测试尚未完成，不以空机验证代替。
