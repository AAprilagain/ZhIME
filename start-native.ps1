param([switch]$Legacy, [switch]$Restart, [ValidateRange(1,65535)][int]$Port=18770, [string]$Deployment, [string]$RuntimeRoot)
$ErrorActionPreference='Stop'
if ($Deployment -and (Test-Path -LiteralPath $Deployment -PathType Leaf)) {
    $Deployment=(Resolve-Path -LiteralPath $Deployment).Path
}
if (!$RuntimeRoot) {
    $RuntimeRoot=if (Test-Path (Join-Path $PSScriptRoot 'artifacts')) { $PSScriptRoot } else { Join-Path $PSScriptRoot '../zhime-local' }
}
Set-Location -LiteralPath $RuntimeRoot
$RuntimeRoot=(Get-Location).Path
$baseUrl="http://127.0.0.1:$Port"
$automatic=$Port -eq 18770 -and !$Legacy -and !$Deployment -and (Test-Path 'automatic-start.enabled')
$config='artifacts/deployment.json'
if ($Deployment) { $config=$Deployment }
elseif (!$Legacy -and (Test-Path 'artifacts/deployment-local-gpu.json')) {
    $config='artifacts/deployment-local-gpu.json'
}
$profile=Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
$device=if ($profile.device) { $profile.device } else { 'cpu' }
$checkpoint=if ($profile.checkpoint) { $profile.checkpoint } else { 'artifacts/model.pt' }
$python=if ($device -like 'cuda*') { '.venv-cuda/Scripts/python.exe' } else { '.venv/Scripts/python.exe' }
$health=$null
try {
    $health=Invoke-RestMethod "$baseUrl/health" -TimeoutSec 2
} catch {}
if ($health.ready -and $health.protocol -eq 1) {
    $runningDevice=if ($health.configuration.device) { $health.configuration.device } else { 'cpu' }
    if (!$Restart) {
        if ($health.configuration.model_sha256 -ne $profile.model_sha256 -or $runningDevice -ne $device) {
            throw 'A different ZhIME deployment is listening on this port. Use -Restart to switch profiles.'
        }
        Write-Host 'Independent ZhIME engine is already running.'
        exit 0
    }
    $listener=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen
    $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($process.CommandLine -notmatch '\s-m\s+zhime\.(independent_server|autostart)(\s|$)') {
        throw 'The listener is not the ZhIME engine.'
    }
    Stop-Process -Id $process.ProcessId -ErrorAction Stop
    Wait-Process -Id $process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
}
if ($automatic) {
    Write-Host 'Foreground automatic startup is enabled. Select ZhIME with Win+Space.'
    exit 0
}
if (!(Test-Path 'artifacts\lexicon\lexicon.sqlite')) { throw 'Compile the shared lexicon first; see README-native.md.' }
New-Item -ItemType Directory -Force -Path runtime | Out-Null
$logPrefix=if ($Port -eq 18770) { 'runtime/engine' } else { "runtime/engine-$Port" }
$arguments=@('-u','-m','zhime.independent_server','--port',$Port,'--config',('"' + $config + '"'),'--checkpoint',('"' + $checkpoint + '"'))
$p=Start-Process -FilePath (Join-Path $RuntimeRoot $python) -ArgumentList $arguments -WorkingDirectory $RuntimeRoot -WindowStyle Hidden -RedirectStandardOutput "$logPrefix.stdout.log" -RedirectStandardError "$logPrefix.stderr.log" -PassThru
$deadline=(Get-Date).AddSeconds(90)
do {
    Start-Sleep -Milliseconds 500
    try { $health=Invoke-RestMethod "$baseUrl/health" -TimeoutSec 2; if ($health.ready -and $health.configuration.model_sha256 -eq $profile.model_sha256 -and ($device -eq 'cpu' -or $health.model)) { Write-Host "Independent ZhIME engine is ready in the background ($device)."; exit 0 } } catch {}
    $p.Refresh(); if ($p.HasExited) { throw "Engine startup failed. See $logPrefix.stderr.log." }
} while ((Get-Date) -lt $deadline)
throw "Engine startup timed out. See $logPrefix.stderr.log."
