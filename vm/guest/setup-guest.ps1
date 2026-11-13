# Runs INSIDE the guest only, after Guest Additions installation.
$ErrorActionPreference = 'Stop'
$code = '\\vboxsvr\StandUserCode'
$bios = Get-ItemProperty 'HKLM:\HARDWARE\DESCRIPTION\System\BIOS'
if ($bios.SystemProductName -notmatch 'VirtualBox') { throw 'Guest setup is restricted to VirtualBox. Do not run it on the host.' }
$startup = [Environment]::GetFolderPath('CommonStartup')
$launcher = Join-Path $startup 'StandUserGuest.vbs'
$vbs = @'
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
For attempt = 1 To 60
    If fs.FileExists("\\vboxsvr\StandUserRuntime\pythonw.exe") Then Exit For
    WScript.Sleep 2000
Next
If Not fs.FileExists("\\vboxsvr\StandUserRuntime\pythonw.exe") Then WScript.Quit 1
shell.Run """\\vboxsvr\StandUserRuntime\pythonw.exe"" ""\\vboxsvr\StandUserCode\bootstrap.py""", 0, False
'@
Set-Content -LiteralPath $launcher -Value $vbs -Encoding ASCII
if ($StandUserGuestPassword) {
    $login = @{username='StandUser';password=$StandUserGuestPassword}
} elseif (Test-Path -LiteralPath "$code\login.json") {
$login = Get-Content -LiteralPath "$code\login.json" -Raw -Encoding UTF8 | ConvertFrom-Json
}
if ($login) {
if ($login.username -ne 'StandUser') { throw 'Unexpected guest account.' }
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -LiteralPath $winlogon -Name 'AutoAdminLogon' -Value '1'
Set-ItemProperty -LiteralPath $winlogon -Name 'DefaultUserName' -Value $login.username
Set-ItemProperty -LiteralPath $winlogon -Name 'DefaultDomainName' -Value $env:COMPUTERNAME
Set-ItemProperty -LiteralPath $winlogon -Name 'DefaultPassword' -Value $login.password
Remove-ItemProperty -LiteralPath $winlogon -Name 'AutoLogonCount' -ErrorAction SilentlyContinue
}
# Only the guest power policy is changed; no host power settings, firewall or service changes.
& powercfg.exe /change standby-timeout-ac 0
& powercfg.exe /change monitor-timeout-ac 0
Start-Process -FilePath 'wscript.exe' -ArgumentList @('//B', ('"{0}"' -f $launcher)) -WindowStyle Hidden
Write-Output 'Guest actuator configured. Sign in to StandUser before running tasks.'
