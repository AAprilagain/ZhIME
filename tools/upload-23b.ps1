param(
    [Parameter(Mandatory)][string]$Corpus,
    [Parameter(Mandatory)][string]$Inventory,
    [Parameter(Mandatory)][string]$Server,
    [Parameter(Mandatory)][string]$Stage,
    [int]$WaitForUploader = 0
)
$ErrorActionPreference = 'Stop'
if ($Stage -notmatch '^/tmp/[a-zA-Z0-9_-]+$' -or $Server -notmatch '^[a-zA-Z0-9_.-]+$') {
    throw 'Invalid staging path or host'
}
if ($WaitForUploader -gt 0) {
    $running = Get-Process -Id $WaitForUploader -ErrorAction SilentlyContinue
    if ($running) { $running.WaitForExit() }
}
$files = Get-Content -LiteralPath $Inventory -Raw | ConvertFrom-Json
foreach ($item in $files) {
    $name = $item.Name
    if ($name -notmatch '^[a-z0-9_-]+\.parquet$') { throw "Invalid shard name $name" }
    $local = Get-Item -LiteralPath (Join-Path $Corpus $name)
    if ($local.Length -ne $item.Length) { throw "Local size mismatch for $name" }
    $remote = "$Stage/parquet/$name"
    $complete = $false
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        ssh $Server "test -f $remote && test `$(stat -c %s $remote) -eq $($item.Length)"
        if ($LASTEXITCODE -eq 0) { $complete = $true; break }
        scp $local.FullName "${Server}:${remote}.partial"
        if ($LASTEXITCODE -eq 0) {
            ssh $Server "test `$(stat -c %s $remote.partial) -eq $($item.Length) && mv -- $remote.partial $remote"
            if ($LASTEXITCODE -eq 0) { $complete = $true; break }
        }
        Start-Sleep -Seconds (5 * $attempt)
    }
    if (!$complete) { throw "Could not upload $name" }
    Write-Output $name
}
Write-Output 'All allowed shards staged.'
