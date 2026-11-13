param([switch]$DownloadOnly)
# Pinned Oracle distribution. No Extension Pack, bridged/host-only networking, USB or Python API.
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$data = Join-Path $project '.windows-use\vm'
$cache = Join-Path $project '.local\vm-downloads'
$target = Join-Path ([IO.Path]::GetPathRoot($project)) 'StandUser-VirtualBox'
$name = 'VirtualBox-7.2.22-175245-Win.exe'
$hash = 'afd0ff1656cbe04ed88ce619594ae021419d97cce89fa9bdc443e9a963f1713f'
$installer = Join-Path $cache $name
$journal = Join-Path $data 'host-install.json'
New-Item -ItemType Directory -Path $data,$cache -Force | Out-Null
if (-not (Test-Path -LiteralPath $installer)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri "https://download.virtualbox.org/virtualbox/7.2.22/$name" -OutFile "$installer.partial"
    if ((Get-FileHash -LiteralPath "$installer.partial" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) {
        throw 'Download SHA256 mismatch. Partial file retained for diagnosis; nothing executed.'
    }
    Move-Item -LiteralPath "$installer.partial" -Destination $installer
}
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) { throw 'Installer SHA256 mismatch.' }
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Oracle') { throw 'Oracle installer signature is not valid.' }
if ($DownloadOnly) { Write-Output "Verified installer ready: $installer"; exit 0 }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $process = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -PassThru -Wait -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"{0}"' -f $PSCommandPath))
    exit $process.ExitCode
}
if (Test-Path 'HKLM:\SOFTWARE\Oracle\VirtualBox') { throw 'Existing VirtualBox detected. Reuse it; this script does not replace or claim it.' }
if (Test-Path -LiteralPath $target) { throw 'Target directory already exists. Nothing overwritten. Inspect it before retrying.' }
$record = @{owner='window-Stand-User/v1';target=$target;phase='prepared';version='7.2.22';created=(Get-Date).ToUniversalTime().ToString('o');sha256=$hash}
$record | ConvertTo-Json | Set-Content -LiteralPath $journal -Encoding UTF8
New-Item -ItemType Directory -Path $target | Out-Null
# Only a NEW dedicated top-level folder is secured. Never change the checkout or parent ACLs.
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
foreach ($entry in @(@('S-1-5-18','FullControl'),@('S-1-5-32-544','FullControl'),@('S-1-5-32-545','ReadAndExecute'))) {
    $sid = New-Object Security.Principal.SecurityIdentifier($entry[0])
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid,$entry[1],'ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $target -AclObject $acl
$arguments = @('--silent','--msiparams','ADDLOCAL=VBoxApplication',('INSTALLDIR="{0}"' -f $target),'VBOX_INSTALLDESKTOPSHORTCUT=0','VBOX_INSTALLQUICKLAUNCHSHORTCUT=0','VBOX_REGISTERFILEEXTENSIONS=0','VBOX_START=0','REBOOT=ReallySuppress')
$process = Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
$record.exit_code = $process.ExitCode
$record.phase = 'install_failed'
if ($process.ExitCode -in @(0,3010) -and (Test-Path -LiteralPath (Join-Path $target 'VBoxManage.exe'))) { $record.phase = 'installed' }
$record | ConvertTo-Json | Set-Content -LiteralPath $journal -Encoding UTF8
if ($record.phase -ne 'installed') { throw "Installation failed ($($process.ExitCode)). Restore script can inspect the recorded state." }
if ($process.ExitCode -eq 3010) { Write-Output 'Installed. Windows requests a restart; no restart was performed.' }
else { Write-Output 'Installed. Host Windows features, BCD, firewall and power settings were not changed.' }
