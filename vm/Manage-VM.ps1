param(
    [ValidateSet('status','prepare','update','create','start','show','shutdown','save','pause','resume','snapshot','restore','delete','recover','cleanup')]
    [string]$Action = 'status',
    [string]$Iso,
    [int]$MemoryMB = 4096,
    [int]$CPUs = 2,
    [int]$DiskGB = 80,
    [int]$ImageIndex = 1,
    [string]$ConfirmId,
    [switch]$ManualInstall,
    [switch]$AcceptWindowsLicense
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python environment is missing.' }
$arguments = @('-m','windows_use.vm.cli',$Action,'--memory',"$MemoryMB",'--cpus',"$CPUs",'--disk',"$DiskGB",'--image-index',"$ImageIndex")
if ($Iso) { $arguments += @('--iso',$Iso) }
if ($ConfirmId) { $arguments += @('--confirm-id',$ConfirmId) }
if ($ManualInstall) { $arguments += '--manual-install' }
if ($AcceptWindowsLicense) { $arguments += '--accept-windows-license' }
$previousEncoding = $env:PYTHONUTF8
Push-Location -LiteralPath $project
try {
    $env:PYTHONUTF8 = '1'
    & $python @arguments
    $result = $LASTEXITCODE
} finally {
    Pop-Location
    $env:PYTHONUTF8 = $previousEncoding
}
exit $result
