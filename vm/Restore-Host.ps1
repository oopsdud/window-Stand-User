param([switch]$Plan)
# Remove only this project's installation, after all current-user VMs have been unregistered.
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$journal = Join-Path $project '.windows-use\vm\host-install.json'
$target = Join-Path ([IO.Path]::GetPathRoot($project)) 'StandUser-VirtualBox'
if (-not (Test-Path -LiteralPath $journal)) { throw 'No project installation record. Existing software will not be removed.' }
$record = Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json
if ($record.owner -ne 'window-Stand-User/v1' -or [IO.Path]::GetFullPath($record.target) -ne [IO.Path]::GetFullPath($target)) { throw 'Ownership/path mismatch.' }
if ($Plan) {
    @{owner=$record.owner;target=$target;phase=$record.phase;managed_vm_present=(Test-Path -LiteralPath (Join-Path $project '.windows-use\vm\machine.json'));action='MSI uninstall only after owned VM removal and no other registered VMs';preserved='ISO, outputs, host model settings, history, Windows features';limitations='Other-user dependencies require confirmation; OS logs and MSI caches may remain'} | ConvertTo-Json
    exit 0
}
if (Test-Path -LiteralPath (Join-Path $project '.windows-use\vm\machine.json')) { throw 'Delete the managed VM first. Guest disk may contain unexported results.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $process = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -PassThru -Wait -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"{0}"' -f $PSCommandPath))
    exit $process.ExitCode
}
$vbox = Join-Path $target 'VBoxManage.exe'
if (Test-Path -LiteralPath $vbox) {
    $version = & $vbox --version
    if (-not $version.StartsWith($record.version + 'r')) { throw 'VirtualBox version changed since project installation; no automatic uninstall.' }
    $machines = & $vbox list vms
    if ($LASTEXITCODE -ne 0 -or $machines) { throw 'Registered VMs remain (or cannot be checked). Shared VirtualBox installation will not be removed.' }
    if (Get-Process VirtualBoxVM,VBoxHeadless,VirtualBox -ErrorAction SilentlyContinue) { throw 'VirtualBox processes are still running. Close them before uninstalling.' }
}
$products = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like '*VirtualBox*' -and $_.Publisher -like '*Oracle*' }
if ($products) {
    if (@($products).Count -ne 1) { throw 'Ambiguous VirtualBox installation. Use Windows Apps settings.' }
    $installed = Get-ItemProperty 'HKLM:\SOFTWARE\Oracle\VirtualBox' -ErrorAction Stop
    if ([IO.Path]::GetFullPath($installed.InstallDir).TrimEnd('\') -ne [IO.Path]::GetFullPath($target).TrimEnd('\')) { throw 'Installation moved or replaced; no automatic uninstall.' }
    $product = $products.PSChildName
    if ($product -notmatch '^\{[0-9A-Fa-f-]{36}\}$') { throw 'Invalid MSI product identifier.' }
    $process = Start-Process msiexec.exe -ArgumentList @('/x',$product,'/qn','/norestart') -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw "Uninstall failed: $($process.ExitCode). Record retained." }
    if ($process.ExitCode -eq 3010) {
        $record.phase = 'uninstall_pending_restart'
        $record | ConvertTo-Json | Set-Content -LiteralPath $journal -Encoding UTF8
        Write-Output 'MSI uninstall requires a manual restart. Remaining files are preserved until Windows completes cleanup.'
        exit 3010
    }
}
# Never recursively remove an arbitrary path or force-delete drivers. Let MSI own its files.
if (Test-Path -LiteralPath $target) {
    $resolved = (Resolve-Path -LiteralPath $target).Path
    if ($resolved -ne [IO.Path]::GetFullPath($target)) { throw 'Unexpected resolved path.' }
    $item = Get-Item -LiteralPath $target
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing linked directory.' }
    if (-not (Get-ChildItem -LiteralPath $target -Force)) { Remove-Item -LiteralPath $target }
    else { throw 'MSI left files in its directory. Inspect them; no recursive deletion was performed.' }
}
$record.phase = 'uninstalled'
$record | ConvertTo-Json | Set-Content -LiteralPath $journal -Encoding UTF8
Write-Output 'Project installation removed. No Windows features were disabled. OS logs/MSI cache may remain; shared outputs are preserved.'
