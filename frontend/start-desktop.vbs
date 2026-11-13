Option Explicit
Dim files, shell, folder, root, executable, entry
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = files.GetParentFolderName(WScript.ScriptFullName)
root = files.GetParentFolderName(folder)
executable = files.BuildPath(root, ".venv\Scripts\pythonw.exe")
entry = files.BuildPath(folder, "start-desktop.pyw")
If Not files.FileExists(executable) Then
    shell.Run "wscript.exe " & Chr(34) & files.BuildPath(root, "Start.vbs") & Chr(34), 0, False
    WScript.Quit 0
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & executable & Chr(34) & " " & Chr(34) & entry & Chr(34), 1, False
