param(
    [switch]$Gui,
    [switch]$Repair,
    [ValidateSet('Install','Check','Launch')][string]$Action = 'Install'
)
# Windows PowerShell 5.1; native first-run UI works without Python or Git installed.
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$local = Join-Path $project '.local'
$python = Join-Path $project '.venv\Scripts\python.exe'
$receipt = Join-Path $local 'setup.json'
$log = Join-Path $local 'setup.log'
$uvVersion = '0.10.9'
$uvHash = 'f58dc40896000229db7c52b8bdd931394040ef2ad59abd1eda841f6d70b13d7a'
$utf8 = New-Object Text.UTF8Encoding($false)

function Get-Fingerprint {
    $parts = @('pyproject.toml','uv.lock','scripts\Setup.ps1') | ForEach-Object {
        (Get-FileHash -LiteralPath (Join-Path $project $_) -Algorithm SHA256).Hash
    }
    return $parts -join ':'
}
function Test-Ready {
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $receipt)) { return $false }
    try {
        $record = Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json
        return $record.root -eq $project -and $record.fingerprint -eq (Get-Fingerprint)
    } catch { return $false }
}
function Start-Companion {
    if (-not (Test-Ready)) { throw 'Run setup before launching.' }
    Start-Process -FilePath (Join-Path $project '.venv\Scripts\pythonw.exe') -WorkingDirectory $project -WindowStyle Hidden -ArgumentList ('"{0}"' -f (Join-Path $project 'frontend\start-desktop.pyw')) | Out-Null
}
function Write-SetupLog([string]$message) {
    [IO.File]::AppendAllText($log, ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $message) + [Environment]::NewLine, $utf8)
}
function Invoke-SetupTool([string]$Executable, [string[]]$ToolArgs) {
    # PowerShell 5.1 wraps normal native stderr (uv progress) as ErrorRecord.
    $ErrorActionPreference = 'Continue'
    & $Executable @ToolArgs 2>&1 | ForEach-Object { Write-SetupLog "$_" }
    if ($LASTEXITCODE -ne 0) { throw "Setup command failed ($LASTEXITCODE). Read setup.log and retry." }
}
function Assert-LocalCheckout {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { throw 'This installer supports Windows x64 only.' }
    if ($project.StartsWith('\\')) { throw 'Extract the project to a local disk, not a network share.' }
    $cursor = Get-Item -LiteralPath $project
    while ($null -ne $cursor) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked checkout directories are not supported.' }
        $cursor = $cursor.Parent
    }
    foreach ($relative in @('.local','.local\tools','.local\python','.local\cache','.local\tmp','.venv')) {
        $path = Join-Path $project $relative
        if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Linked runtime directory: $path" }
    }
}
function Install-LocalRuntime {
    Assert-LocalCheckout
    New-Item -ItemType Directory -Force -Path $local | Out-Null
    # File handle lock is released by Windows after a crash; retries do not need lock deletion.
    $guard = [IO.File]::Open((Join-Path $local 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        Write-SetupLog 'Preparing local Python and desktop dependencies. No VM or model calls will start.'
        $disk = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($project))
        if ($disk.AvailableFreeSpace -lt 5GB) { throw 'At least 5 GB free disk space is required for runtime setup.' }
        # Never rewrite dependencies while a checkout's Python process or its VM uses them.
        $busy = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith((Join-Path $project '.venv\'), [StringComparison]::OrdinalIgnoreCase)
        }
        if ($busy) { throw 'Close this checkout desktop/Agent/Python processes before installing or updating.' }
        $machine = Join-Path $project '.windows-use\vm\machine.json'
        if (Test-Path -LiteralPath $machine) {
            if (-not (Test-Path -LiteralPath $python)) { throw 'Existing VM found without its runtime. Restore the original checkout before updating.' }
            $status = & $python -m windows_use.vm.cli status
            if ($LASTEXITCODE -ne 0) { throw 'Cannot verify existing VM state; update stopped.' }
            $state = ($status -join "`n" | ConvertFrom-Json).result
            if ($state.error -or $state.state -ne 'poweroff') { throw 'Shut down the existing VM completely before updating dependencies (saved state is not enough).' }
        }
        # Do not leave an old success receipt after a failed dependency update.
        if (Test-Path -LiteralPath $receipt) { Remove-Item -LiteralPath $receipt }
        $env:UV_CACHE_DIR = Join-Path $local 'cache\uv'
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $local 'python'
        $env:UV_PYTHON_BIN_DIR = Join-Path $local 'python-bin'
        $env:UV_PYTHON_INSTALL_REGISTRY = 'false'
        $env:UV_PYTHON_NO_REGISTRY = 'true'
        $env:UV_NO_MODIFY_PATH = '1'
        $env:UV_NO_CONFIG = '1'
        $env:UV_PYTHON_DOWNLOADS = 'automatic'
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $project '.venv'
        $env:UV_LINK_MODE = 'copy'
        $env:PYTHONUTF8 = '1'
        $env:TEMP = Join-Path $local 'tmp'
        $env:TMP = $env:TEMP
        New-Item -ItemType Directory -Force -Path $env:TEMP,(Join-Path $local 'tools') | Out-Null
        $zip = Join-Path $local 'tools\uv-0.10.9.zip'
        $uvDir = Join-Path $local 'tools\uv-0.10.9'
        $uv = Join-Path $uvDir 'uv.exe'
        if (-not (Test-Path -LiteralPath $zip)) {
            Write-SetupLog 'Downloading pinned uv from github.com/astral-sh/uv...'
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile "$zip.partial"
            if ((Get-FileHash -LiteralPath "$zip.partial" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $uvHash) { throw 'uv download checksum mismatch.' }
            Move-Item -LiteralPath "$zip.partial" -Destination $zip
        }
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $uvHash) { throw 'uv archive checksum mismatch.' }
        Expand-Archive -LiteralPath $zip -DestinationPath $uvDir -Force
        Write-SetupLog 'Installing project-local Python 3.12 (no PATH or registry registration)...'
        Invoke-SetupTool $uv @('python','install','3.12','--no-bin','--no-registry')
        Write-SetupLog 'Installing locked desktop dependencies (no development extra)...'
        Invoke-SetupTool $uv @('sync','--project',$project,'--locked','--extra','desktop','--no-dev','--python','3.12','--managed-python')
        Invoke-SetupTool $python @('-c', "import sys; from pathlib import Path; import PySide6, psutil, comtypes; from windows_use.vm.manager import Manager; assert Path(sys.base_prefix).resolve().is_relative_to(Path('.local/python').resolve()); print('Runtime import check passed')")
        if (Test-Path -LiteralPath $machine) {
            Invoke-SetupTool $python @('-m','windows_use.vm.cli','update')
        }
        $record = @{root=$project;fingerprint=(Get-Fingerprint);uv=$uvVersion;python='3.12';completed=[DateTime]::UtcNow.ToString('o')}
        [IO.File]::WriteAllText("$receipt.tmp", ($record | ConvertTo-Json), $utf8)
        Move-Item -LiteralPath "$receipt.tmp" -Destination $receipt -Force
        Write-SetupLog 'Ready. Start the companion, then configure your model in Settings. VM installation is optional.'
    } finally { $guard.Dispose() }
}

Push-Location -LiteralPath $project
try {
    if ($Gui) {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        [Windows.Forms.Application]::EnableVisualStyles()
        if ((Test-Ready) -and -not $Repair) { Start-Companion; exit 0 }
        $script:repairPending=[bool]$Repair
        $form = New-Object Windows.Forms.Form
        $form.Text = '白金之星 · 首次准备'
        $form.Size = New-Object Drawing.Size(720,520)
        $form.MinimumSize = $form.Size
        $form.StartPosition = 'CenterScreen'
        $form.AutoScaleMode = 'Dpi'
        $layout = New-Object Windows.Forms.TableLayoutPanel
        $layout.Dock='Fill'; $layout.ColumnCount=1; $layout.RowCount=3
        $layout.RowStyles.Add((New-Object Windows.Forms.RowStyle('Absolute',100))) | Out-Null
        $layout.RowStyles.Add((New-Object Windows.Forms.RowStyle('Percent',100))) | Out-Null
        $layout.RowStyles.Add((New-Object Windows.Forms.RowStyle('Absolute',50))) | Out-Null
        $intro = New-Object Windows.Forms.Label
        $intro.Dock='Fill'; $intro.Padding=New-Object Windows.Forms.Padding(12)
        $intro.Text="自动准备项目专用 Python 和桌宠依赖，均放在当前目录。`r`n不改全局 PATH，不添加开机自启；本步骤不安装虚拟机、不下载 Windows。`r`n首次需要联网及至少 5 GB 磁盘空间，已有模型配置和历史保留。`r`n位置：$project"
        $view = New-Object Windows.Forms.TextBox
        $view.Dock='Fill'; $view.Multiline=$true; $view.ReadOnly=$true; $view.ScrollBars='Both'
        $button = New-Object Windows.Forms.Button
        $button.Dock='Fill'; $button.Text='开始准备 / 重试'
        $layout.Controls.Add($intro,0,0); $layout.Controls.Add($view,0,1); $layout.Controls.Add($button,0,2)
        $form.Controls.Add($layout)
        $script:setupProcess=$null
        $timer=New-Object Windows.Forms.Timer
        $timer.Interval=500
        $button.Add_Click({
            if ((Test-Ready) -and -not $script:repairPending) { Start-Companion; $form.Close(); return }
            $info=New-Object Diagnostics.ProcessStartInfo
            $info.FileName='powershell.exe'
            $info.Arguments='-NoProfile -ExecutionPolicy Bypass -File "{0}" -Action Install' -f $PSCommandPath
            $info.UseShellExecute=$false; $info.CreateNoWindow=$true; $info.WorkingDirectory=$project
            $script:setupProcess=[Diagnostics.Process]::Start($info)
            $button.Enabled=$false; $button.Text='正在准备，请稍候…'; $timer.Start()
        })
        $timer.Add_Tick({
            if (Test-Path -LiteralPath $log) { $view.Text=(Get-Content -LiteralPath $log -Encoding UTF8 -Tail 70) -join "`r`n"; $view.SelectionStart=$view.Text.Length; $view.ScrollToCaret() }
            if ($script:setupProcess.HasExited) {
                $timer.Stop(); $button.Enabled=$true
                $script:repairPending=$script:setupProcess.ExitCode -ne 0 -or -not (Test-Ready)
                $button.Text=if (-not $script:repairPending) { '启动白金之星' } else { '准备失败 · 查看上方记录后重试' }
                $script:setupProcess.Dispose(); $script:setupProcess=$null
            }
        })
        $form.Add_FormClosing({ param($sender,$eventArgs)
            if ($script:setupProcess -and -not $script:setupProcess.HasExited) { $eventArgs.Cancel=$true; [Windows.Forms.MessageBox]::Show('依赖准备尚在进行，请等待完成或失败后关闭。') | Out-Null }
        })
        [void]$form.ShowDialog()
        $timer.Dispose(); $form.Dispose()
    } elseif ($Action -eq 'Check') {
        @{ready=(Test-Ready);root=$project;python=$python;fingerprint=(Get-Fingerprint)} | ConvertTo-Json
    } elseif ($Action -eq 'Launch') { Start-Companion }
    else { Install-LocalRuntime }
} catch {
    New-Item -ItemType Directory -Force -Path $local | Out-Null
    Write-SetupLog "ERROR: $($_.Exception.Message)"
    if ($Gui) { [Windows.Forms.MessageBox]::Show("Setup failed. See $log`r`n$($_.Exception.Message)") | Out-Null }
    else { Write-Error $_ }
    exit 1
} finally { Pop-Location }
