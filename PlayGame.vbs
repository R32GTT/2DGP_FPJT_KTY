Option Explicit

Dim files, shell, root, python, command
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = root & "\.venv\Scripts\pythonw.exe"

If Not files.FileExists(python) Then
    MsgBox "Game environment is missing. Run SetupEnvironment.bat first.", vbExclamation, "REVENANT : L.O.S.T"
    WScript.Quit 1
End If

shell.CurrentDirectory = root
command = Chr(34) & python & Chr(34) & " -B -X utf8 -m prototype.game_entry"
shell.Run command, 1, False
