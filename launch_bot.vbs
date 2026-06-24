' Launch the Discord CC Bot silently in the background (no console window).
' Keep this file in the SAME folder as discord_bot.py.
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Run from this script's own folder so relative paths resolve correctly.
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = scriptDir

' Prefer the project virtualenv (.venv) if it exists, else fall back to "python" on PATH.
venvPy = scriptDir & "\.venv\Scripts\python.exe"
If fso.FileExists(venvPy) Then
  pyExe = """" & venvPy & """"
Else
  pyExe = "python"
End If

' 0 = hidden window, False = do not wait. Output is logged to discord_bot.log.
sh.Run "cmd /c " & pyExe & " discord_bot.py > discord_bot.log 2>&1", 0, False
