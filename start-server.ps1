param([string]$RuntimeRoot)
$ErrorActionPreference = 'Stop'
if (!$RuntimeRoot) {
    $RuntimeRoot=if (Test-Path (Join-Path $PSScriptRoot 'artifacts')) { $PSScriptRoot } else { Join-Path $PSScriptRoot '../zhime-local' }
}
Set-Location -LiteralPath $RuntimeRoot
$RuntimeRoot=(Get-Location).Path
if (!(Test-Path '.venv\Scripts\python.exe')) { throw 'Create and configure .venv first; see README.' }
if (!(Test-Path 'artifacts\model.pt')) { throw 'Missing artifacts\model.pt.' }
$listener = Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    $heartbeat = Get-Item 'spool\ready' -ErrorAction SilentlyContinue
    if ($owner -and $owner.CommandLine -match 'zhime\.server' -and $heartbeat -and ((Get-Date) - $heartbeat.LastWriteTime).TotalSeconds -lt 5) {
        Write-Host "ZhIME is already running (PID $($listener.OwningProcess)). Switch Windows input method to Weasel and select Luna Pinyin."
        exit 0
    }
    throw "Port 18765 is occupied, or ZhIME's spool heartbeat is stale. Check the running process before restarting."
}
New-Item -ItemType Directory -Force -Path 'spool' | Out-Null
$stdoutLog = Join-Path $RuntimeRoot 'spool\service.stdout.log'
$stderrLog = Join-Path $RuntimeRoot 'spool\service.stderr.log'
$serverArgs = @('-u','-m','zhime.server','--checkpoint','artifacts/model.pt','--tokenizer','artifacts/tokenizer.model','--alpha','0.5','--length-exponent','0','--preserve-length','--spool','spool')
$process = Start-Process -FilePath (Join-Path $RuntimeRoot '.venv\Scripts\python.exe') -ArgumentList $serverArgs -WorkingDirectory $RuntimeRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
$deadline = (Get-Date).AddSeconds(45)
do {
    Start-Sleep -Milliseconds 400
    $listener = Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $heartbeat = Get-Item 'spool\ready' -ErrorAction SilentlyContinue
    if ($listener -and $heartbeat -and ((Get-Date) - $heartbeat.LastWriteTime).TotalSeconds -lt 5) {
        Write-Host "ZhIME started in the background (PID $($listener.OwningProcess)). You may close this window."
        exit 0
    }
    $process.Refresh()
    if ($process.HasExited) { throw "ZhIME exited during startup. See $stderrLog" }
} while ((Get-Date) -lt $deadline)
throw "ZhIME did not become ready within 45 seconds. See $stderrLog"
