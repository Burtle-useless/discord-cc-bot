' Launch the Discord CC Bot silently in the background (no console window).
' Keep this file in the SAME folder as discord_bot.py.
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Run from this script's own folder so relative paths resolve correctly.
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = scriptDir

' Find a Python to run with. Order: .venv -> venv (no dot) -> "py" launcher.
' (A bare "python" can resolve to the Microsoft Store stub on Windows, which runs
'  nothing and leaves an EMPTY log, so we fall back to the "py" launcher instead.)
dotVenv = scriptDir & "\.venv\Scripts\python.exe"
venvDir = scriptDir & "\venv\Scripts\python.exe"
If fso.FileExists(dotVenv) Then
  pyExe = """" & dotVenv & """"
ElseIf fso.FileExists(venvDir) Then
  pyExe = """" & venvDir & """"
Else
  pyExe = "py"
End If

' 0 = hidden window, False = do not wait. Output is logged to discord_bot.log.
sh.Run "cmd /c " & pyExe & " discord_bot.py > discord_bot.log 2>&1", 0, False
