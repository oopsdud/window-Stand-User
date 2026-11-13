Option Explicit
Dim files, shell, root, script
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = files.GetParentFolderName(WScript.ScriptFullName)
script = files.BuildPath(root, "scripts\Setup.ps1")
shell.Run "powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File " & Chr(34) & script & Chr(34) & " -Gui", 0, False
