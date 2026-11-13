param(
    [ValidateSet('Cache','Runtime')][string]$Action='Cache',
    [switch]$Apply,
    [switch]$Gui
)
$ErrorActionPreference='Stop'
$project=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))

function Get-Targets([string]$Kind) {
    $items=@('.local\cache','.local\vm-downloads')
    if ($Kind -eq 'Runtime') { $items+=@('.venv','.local\python','.local\python-bin','.local\tools','.local\tmp','.local\setup.json') }
    return $items | ForEach-Object { Join-Path $project $_ }
}
function Remove-ProjectRuntime([string]$Kind) {
    if ($Kind -eq 'Runtime' -and (Test-Path -LiteralPath (Join-Path $project '.windows-use\vm\machine.json'))) {
        throw '请先在虚拟机管理中“停用并恢复主机”；客户机仍依赖此运行时，未清理。'
    }
    $processes=Get-CimInstance Win32_Process -ErrorAction Stop
    if ($processes | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith((Join-Path $project '.venv\'),[StringComparison]::OrdinalIgnoreCase)) -or
        $_.Name -like 'VirtualBox-*-Win.exe'
    }) { throw '请先从托盘退出本项目桌宠，并等待任务、资源释放及安装进程结束，再清理。' }
    $targets=@(Get-Targets $Kind)
    $pythonAliases=@()
    # Preflight every path before deleting anything. Never cross a junction or leave this checkout.
    foreach ($target in $targets) {
        $absolute=[IO.Path]::GetFullPath($target)
        if (-not $absolute.StartsWith($project+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '清理路径超出项目目录。' }
        $cursor=$absolute
        while ($cursor) {
            if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "拒绝清理链接路径：$cursor" }
            $cursor=Split-Path -Parent $cursor
        }
        if (Test-Path -LiteralPath $target) {
            $resolved=(Resolve-Path -LiteralPath $target).Path
            if ($resolved -ne $absolute) { throw '路径解析不一致。' }
            $links=@(Get-ChildItem -LiteralPath $target -Force -Recurse | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
            foreach ($entry in $links) {
                # uv maintains one minor-version alias. Unlink this junction only; never recurse through it.
                $pythonRoot=Join-Path $project '.local\python'
                $destination=@($entry.Target)[0]
                if ($Kind -ne 'Runtime' -or $entry.Parent.FullName -ne $pythonRoot -or $entry.Name -ne 'cpython-3.12-windows-x86_64-none' -or $entry.LinkType -ne 'Junction' -or -not $destination) { throw "目录包含非托管链接，未清理：$($entry.FullName)" }
                $destination=[IO.Path]::GetFullPath($destination)
                if ((Split-Path -Parent $destination) -ne $pythonRoot -or (Split-Path -Leaf $destination) -notmatch '^cpython-3\.12\.\d+-windows-x86_64-none$') { throw 'Python 别名目标超出托管目录。' }
                if (-not (Test-Path -LiteralPath $destination) -or ((Get-Item -LiteralPath $destination).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Python 别名目标无效。' }
                $pythonAliases += $entry.FullName
            }
        }
    }
    $local=Join-Path $project '.local'
    New-Item -ItemType Directory -Force -Path $local | Out-Null
    $guard=[IO.File]::Open((Join-Path $local 'setup.lock'),'OpenOrCreate','ReadWrite','None')
    try {
        foreach ($alias in $pythonAliases) { [IO.Directory]::Delete($alias) }
        foreach ($target in $targets) {
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        }
    } finally { $guard.Dispose() }
    return '清理完成。个人配置、历史、成果、原始 ISO 和源代码保留；未改系统设置。'
}
if ($Gui) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $form=New-Object Windows.Forms.Form
    $form.Text='白金之星 · 清理本地运行环境'; $form.Size=New-Object Drawing.Size(640,380)
    $form.MinimumSize=$form.Size; $form.StartPosition='CenterScreen'; $form.AutoScaleMode='Dpi'
    $flow=New-Object Windows.Forms.FlowLayoutPanel
    $flow.Dock='Fill'; $flow.FlowDirection='TopDown'; $flow.WrapContents=$false; $flow.AutoScroll=$true
    $note=New-Object Windows.Forms.Label
    $note.Size=New-Object Drawing.Size(590,110)
    $note.Text="先从托盘退出桌宠；任务自动启动的虚拟机约 21 秒后保存停止。`r`n要彻底移除虚拟机，请先进入其管理界面「停用并恢复主机」。`r`n这里仅清理当前源码目录下的缓存或 Python 依赖，不删除配置、历史和成果。`r`n项目位置：$project"
    $flow.Controls.Add($note)
    $repair=New-Object Windows.Forms.Button
    $repair.Text='修复 / 更新本地依赖'; $repair.Size=New-Object Drawing.Size(590,42)
    $repair.Add_Click({
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList ('-NoProfile -STA -ExecutionPolicy Bypass -File "{0}" -Gui -Repair' -f (Join-Path $PSScriptRoot 'Setup.ps1'))
        $form.Close()
    })
    $flow.Controls.Add($repair)
    foreach ($choice in @(@('仅清理下载缓存','Cache'),@('删除本地 Python 和依赖（下次启动重新准备）','Runtime'))) {
        $button=New-Object Windows.Forms.Button
        $button.Text=$choice[0]; $button.Tag=$choice[1]; $button.Size=New-Object Drawing.Size(590,42)
        $button.Add_Click({
            param($sender,$eventArgs)
            if ([Windows.Forms.MessageBox]::Show('确认清理当前项目的所选内容？个人配置与成果保留。','确认清理','YesNo') -ne 'Yes') { return }
            try { $message=Remove-ProjectRuntime $sender.Tag }
            catch { $message=$_.Exception.Message }
            [Windows.Forms.MessageBox]::Show($message) | Out-Null
        })
        $flow.Controls.Add($button)
    }
    $form.Controls.Add($flow); [void]$form.ShowDialog(); $form.Dispose()
} elseif ($Apply) { Remove-ProjectRuntime $Action }
else { @{action=$Action;delete=@(Get-Targets $Action);preserve='Source, model settings, history, outputs, original ISO';apply=$false} | ConvertTo-Json }
