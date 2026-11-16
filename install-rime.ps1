param([string]$RimeUserDir = (Join-Path $env:APPDATA 'Rime'), [string]$RuntimeRoot)
$ErrorActionPreference = 'Stop'
if (!$RuntimeRoot) {
    $RuntimeRoot=if (Test-Path (Join-Path $PSScriptRoot 'artifacts')) { $PSScriptRoot } else { Join-Path $PSScriptRoot '../zhime-local' }
}
$RuntimeRoot=(Resolve-Path -LiteralPath $RuntimeRoot).Path
$custom = Join-Path $RimeUserDir 'luna_pinyin.custom.yaml'
if (Test-Path -LiteralPath $custom) {
    throw "Already exists: $custom. Merge the patch manually; see README."
}
$lua = Join-Path $RimeUserDir 'lua'
New-Item -ItemType Directory -Force -Path $lua | Out-Null
foreach ($name in @('zhime.lua', 'zhime_clear.lua')) {
    $dest = Join-Path $lua $name
    if (Test-Path -LiteralPath $dest) { throw "Already exists: $dest. Nothing overwritten." }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'rime\zhime.lua') -Destination $lua
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'rime\zhime_clear.lua') -Destination $lua
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'rime\luna_pinyin.custom.yaml') -Destination $custom
$spool = Join-Path $RuntimeRoot 'spool'
New-Item -ItemType Directory -Force -Path $spool | Out-Null
[System.IO.File]::WriteAllText((Join-Path $RimeUserDir 'zhime-spool-path.txt'), $spool, (New-Object System.Text.UTF8Encoding $false))
Write-Host 'Configuration copied. Redeploy using the Weasel menu.'
