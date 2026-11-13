param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$localDir = Join-Path $PSScriptRoot '.local'
$directories = @('cache/uv', 'cache/pip', 'cache/huggingface', 'cache/tiktoken', 'cache/xdg', 'tmp')
foreach ($directory in $directories) {
    New-Item -ItemType Directory -Force -Path (Join-Path $localDir $directory) | Out-Null
}
$env:UV_CACHE_DIR = Join-Path $localDir 'cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $localDir 'python'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:PIP_CACHE_DIR = Join-Path $localDir 'cache/pip'
$env:HF_HOME = Join-Path $localDir 'cache/huggingface'
$env:TIKTOKEN_CACHE_DIR = Join-Path $localDir 'cache/tiktoken'
$env:XDG_CACHE_HOME = Join-Path $localDir 'cache/xdg'
$env:TEMP = Join-Path $localDir 'tmp'
$env:TMP = $env:TEMP
$env:WINDOWS_USE_HOME = Join-Path $PSScriptRoot '.windows-use'
$env:ANONYMIZED_TELEMETRY = 'false'
$env:LITELLM_LOCAL_MODEL_COST_MAP = 'True'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$pythonExe = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Local environment missing. Install dependencies into .venv first.'
}
& $pythonExe (Join-Path $PSScriptRoot 'main.py') @CliArgs
exit $LASTEXITCODE
