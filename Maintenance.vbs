Option Explicit
Dim files, shell, root
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.Run "powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File " & Chr(34) & files.BuildPath(root, "scripts\Cleanup.ps1") & Chr(34) & " -Gui", 0, False
