# CC Bot control panel - start / stop / restart the Discord CC bot and view its log.
# Zero-install WinForms GUI. Launched (hidden console) by control-panel.vbs.
# Keep this file in the same folder as discord_bot.py and launch_bot.vbs.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# The panel, the bot, and its launcher all live in the same folder.
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }

$bot = @{
    Name  = 'Discord CC Bot'
    Vbs   = Join-Path $root 'launch_bot.vbs'      # how to start it (hidden, picks .venv or python)
    Log   = Join-Path $root 'discord_bot.log'     # where its output is logged
    Match = 'discord_bot.py'                      # command-line marker used to find its process
}

# Return the PIDs of the running bot (matched by interpreter name + command line).
function Get-BotPids {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and
            $_.CommandLine -like "*$($bot.Match)*"
        } | Select-Object -ExpandProperty ProcessId
}

# Start (skip if already running): launch the .vbs from the project folder.
function Start-Bot {
    if (Get-BotPids) { return }
    Start-Process -FilePath $bot.Vbs -WorkingDirectory $root
}

# Stop: kill every matching process.
function Stop-Bot {
    foreach ($procId in Get-BotPids) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

# Refresh the status label.
function Update-Status {
    if (Get-BotPids) {
        $script:statusLabel.Text = 'Running'
        $script:statusLabel.ForeColor = [Drawing.Color]::ForestGreen
    } else {
        $script:statusLabel.Text = 'Stopped'
        $script:statusLabel.ForeColor = [Drawing.Color]::Gray
    }
}

# Update the bottom operation line and repaint immediately (so "working..." shows before the wait).
function Set-Op($text, $color) {
    $script:opLabel.Text = $text
    $script:opLabel.ForeColor = $color
    $script:opLabel.Refresh()
}

function Do-Start {
    Set-Op 'Starting...' ([Drawing.Color]::DarkOrange)
    Start-Bot
    Start-Sleep -Milliseconds 2200
    Update-Status
    if (Get-BotPids) { Set-Op 'Started' ([Drawing.Color]::ForestGreen) }
    else { Set-Op 'Failed to start - click "View Log" to see why' ([Drawing.Color]::Firebrick) }
}
function Do-Stop {
    Set-Op 'Stopping...' ([Drawing.Color]::DarkOrange)
    Stop-Bot
    Start-Sleep -Milliseconds 800
    Update-Status
    if (Get-BotPids) { Set-Op 'Still running' ([Drawing.Color]::Firebrick) }
    else { Set-Op 'Stopped' ([Drawing.Color]::Gray) }
}
function Do-Restart {
    Set-Op 'Restarting...' ([Drawing.Color]::DarkOrange)
    Stop-Bot
    Start-Sleep -Milliseconds 900
    Start-Bot
    Start-Sleep -Milliseconds 2200
    Update-Status
    if (Get-BotPids) { Set-Op 'Restarted' ([Drawing.Color]::ForestGreen) }
    else { Set-Op 'Not running after restart - click "View Log"' ([Drawing.Color]::Firebrick) }
}

# ── Window ──
$form = New-Object Windows.Forms.Form
$form.Text = 'CC Bot Control Panel'
$form.Size = New-Object Drawing.Size(450, 185)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false

$font = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Bold)

$name = New-Object Windows.Forms.Label
$name.Text = $bot.Name
$name.Location = New-Object Drawing.Point(15, 15)
$name.Size = New-Object Drawing.Size(260, 24)
$name.Font = $font
$form.Controls.Add($name)

$script:statusLabel = New-Object Windows.Forms.Label
$script:statusLabel.Location = New-Object Drawing.Point(285, 15)
$script:statusLabel.Size = New-Object Drawing.Size(140, 24)
$script:statusLabel.Font = $font
$script:statusLabel.Text = 'Checking...'
$form.Controls.Add($script:statusLabel)

$bStart = New-Object Windows.Forms.Button
$bStart.Text = 'Start'
$bStart.Location = New-Object Drawing.Point(15, 50); $bStart.Size = New-Object Drawing.Size(98, 32)
$bStart.Add_Click({ Do-Start })
$form.Controls.Add($bStart)

$bStop = New-Object Windows.Forms.Button
$bStop.Text = 'Stop'
$bStop.Location = New-Object Drawing.Point(120, 50); $bStop.Size = New-Object Drawing.Size(98, 32)
$bStop.Add_Click({ Do-Stop })
$form.Controls.Add($bStop)

$bRestart = New-Object Windows.Forms.Button
$bRestart.Text = 'Restart'
$bRestart.Location = New-Object Drawing.Point(225, 50); $bRestart.Size = New-Object Drawing.Size(98, 32)
$bRestart.Add_Click({ Do-Restart })
$form.Controls.Add($bRestart)

$bLog = New-Object Windows.Forms.Button
$bLog.Text = 'View Log'
$bLog.Location = New-Object Drawing.Point(330, 50); $bLog.Size = New-Object Drawing.Size(98, 32)
$bLog.Add_Click({
    if (Test-Path $bot.Log -PathType Leaf) { Start-Process notepad.exe $bot.Log }
    else { Set-Op '(No log file yet)' ([Drawing.Color]::Gray) }
})
$form.Controls.Add($bLog)

# Bottom operation status line.
$script:opLabel = New-Object Windows.Forms.Label
$script:opLabel.Location = New-Object Drawing.Point(15, 95)
$script:opLabel.Size = New-Object Drawing.Size(415, 24)
$script:opLabel.Font = New-Object Drawing.Font('Segoe UI', 9)
$script:opLabel.Text = 'Ready'
$script:opLabel.ForeColor = [Drawing.Color]::Gray
$form.Controls.Add($script:opLabel)

# Auto-refresh the status every 2.5s.
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 2500
$timer.Add_Tick({ Update-Status })
$timer.Start()

Update-Status
[void]$form.ShowDialog()
$timer.Stop()
