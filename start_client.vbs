Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptDir
objShell.Run """" & strScriptDir & "\.venv\Scripts\pythonw.exe"" -m cas_client.main", 0, False
