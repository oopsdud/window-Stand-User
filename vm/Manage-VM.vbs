Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
project = fso.GetParentFolderName(folder)
python = fso.BuildPath(project, ".venv\Scripts\pythonw.exe")
entry = fso.BuildPath(folder, "manage.pyw")
shell.CurrentDirectory = project
shell.Run """" & python & """ """ & entry & """", 0, False
