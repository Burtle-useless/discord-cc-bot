' Open the CC Bot control panel (a small GUI window; the PowerShell console stays hidden).
' Keep this file in the SAME folder as control-panel.ps1 and discord_bot.py.
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & scriptDir & "\control-panel.ps1""", 0, False
