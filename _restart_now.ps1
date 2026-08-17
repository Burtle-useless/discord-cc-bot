# Restart cc-bot (kill -> wait for port release -> roll log -> start -> verify).
#
# Pure ASCII on purpose: PowerShell 5.1 reads a BOM-less file as CP950 and
# silently mangles non-ASCII text.
param([switch]$Detached)

$ErrorActionPreference = 'Continue'

$bot = $PSScriptRoot
$vbs = "$bot\launch_bot.vbs"
$applog = "$bot\discord_bot.log"
$log = "$bot\restart.log"
$port = 47361
$marker = 'discord_bot.py'

function W($m) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Add-Content -Path $log -Encoding utf8
}

# Self-detach unconditionally.
#
# A Claude Code session can be running as a CHILD of discord_bot.py -- that is
# the normal case, since the bot spawns CC to answer messages. The taskkill /F /T
# below then tears down the session's own process tree, which reaps this script
# too: the bot gets killed and never restarted, and restart.log just stops.
# The rule had only ever been a comment describing what the CALLER was expected
# to do -- nothing enforced it. A rule that matters must live in code, so the
# script now re-launches itself through WMI (parent: WmiPrvSE, outside every
# job) no matter how it was invoked.
if (-not $Detached) {
    $me = $MyInvocation.MyCommand.Path
    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$me`" -Detached"
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$cmd}
    if ($r.ReturnValue -eq 0) {
        Write-Output "restart handed off to detached pid $($r.ProcessId); watch restart.log"
        exit 0
    }
    # WMI refused (should not happen for the same user) -- carry on inline,
    # which is the old risky behaviour, but better than doing nothing.
    Write-Output "WMI detach failed (rv=$($r.ReturnValue)); continuing inline"
}

function Listener {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

# Matched on the command line, not the process tree: precise, and it cannot
# wander into unrelated python or cmd processes.
#
# A healthy bot is THREE processes, not two: the two python ones plus the
# cmd.exe from launch_bot.vbs's "cmd /c ... > discord_bot.log". That cmd owns
# the redirect handle on the log. With only python.exe matched, the wait loop
# below declares everything dead while it is still alive holding the file; the
# roll then fails and the next launch's redirect cannot open its target, so
# python is never executed and nothing is written anywhere -- a restart that
# leaves no trace at all. Truncating with a single '>' needs stricter access
# than appending, which makes this failure mode easy to hit.
function BotProcs {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$marker*" }
}

W '--- restart requested ---'

# Give the caller time to finish its last reply before we pull the rug.
Start-Sleep -Seconds 8

$procs = @(BotProcs)
if ($procs.Count -gt 0) {
    W ("stopping " + $procs.Count + " bot process(es): " + (($procs | ForEach-Object { $_.ProcessId }) -join ','))
    foreach ($p in $procs) {
        # /T because the bot spawns CC child processes that hold the port's
        # sockets open; killing only the parent leaves them behind.
        taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null
    }
} else {
    W 'no bot process found'
    $conn = Listener
    if ($conn) {
        W "stopping port holder pid=$($conn.OwningProcess)"
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

# Wait for BOTH the processes to die and the port to be released (up to 15s).
# The port matters most: the single-instance lock makes a new bot exit on
# startup if 47361 is still taken, and that exit is invisible.
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if (-not (Listener) -and @(BotProcs).Count -eq 0) { break }
}
if (Listener) { W 'WARNING: port still held after 15s' } else { W 'port released' }

# Roll the app log BEFORE relaunching.
#
# Every launch path redirects with a single '>', so starting the bot truncates
# discord_bot.log -- destroying the crash output from the run that just died,
# which is the one thing worth reading at that moment. Rolling it here fixes all
# of them at once (the '>' then simply creates a fresh file) without touching
# the encoding-sensitive .vbs files.
# Do NOT swallow the failure: a silent one puts us right back to losing the log.
# Retried rather than attempted once: a handle can outlive the process that owns
# it by a moment, and a successful roll doubles as proof that nobody is holding
# the file -- which is exactly the precondition the vbs's redirect needs.
if (Test-Path $applog) {
    $rolled = $false
    $err = ''
    for ($i = 0; $i -lt 20; $i++) {
        try {
            Move-Item -Path $applog -Destination "$bot\discord_bot.log.1" -Force -ErrorAction Stop
            $rolled = $true
            break
        } catch {
            $err = $_.Exception.Message
            Start-Sleep -Milliseconds 500
        }
    }
    if ($rolled) {
        W 'rolled discord_bot.log -> discord_bot.log.1'
    } else {
        W "WARNING: could not roll discord_bot.log after 10s: $err"
    }
}

# $waitSec: how long to wait for the port before giving up on this attempt.
# The first attempt gets a short leash: the bot imports torch (drive mode's GPU
# model), so 60s is the honest cold-start budget, but a launch that has not
# bound the port by then is dead rather than slow. The retry keeps a long one.
function Launch($waitSec) {
    Start-Process -FilePath 'wscript.exe' -ArgumentList "`"$vbs`"" -WindowStyle Hidden
    W "launched launch_bot.vbs (waiting up to ${waitSec}s)"
    for ($i = 0; $i -lt $waitSec; $i++) {
        Start-Sleep -Seconds 1
        if (Listener) { return $true }
    }
    return $false
}

if (-not (Test-Path $vbs)) {
    W "ERROR: vbs not found at $vbs"
    W '--- done ---'
    return
}

$ok = Launch 60
if (-not $ok) {
    W 'first launch did not come up; retrying once'
    Start-Sleep -Seconds 2
    $ok = Launch 120
}

if ($ok) {
    $c = Listener
    W "listening again on $($c.LocalAddress):$port  pid=$($c.OwningProcess)"
} else {
    W 'ERROR: cc-bot did not come back after two attempts'
}
W '--- done ---'
