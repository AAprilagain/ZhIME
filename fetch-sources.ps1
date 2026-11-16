param([string]$Destination = (Join-Path $PSScriptRoot '../zhime-local/third_party'))
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$sources=@(
    @{path=(Join-Path $Destination 'rime-ice'); url='https://github.com/iDvel/rime-ice.git'; commit='99662ba01e3affa6a0f009174ca530b887bac3b6'},
    @{path=(Join-Path $Destination 'VimeML'); url='https://github.com/Voltline/VimeML.git'; commit='1357e179a0393af954fe3d415ea2a12e14042264'}
)
foreach ($source in $sources) {
    if (!(Test-Path (Join-Path $source.path '.git'))) {
        git init $source.path
        git -C $source.path remote add origin $source.url
        git -C $source.path fetch --depth 1 origin $source.commit
        if ($LASTEXITCODE -ne 0) { throw "Cannot fetch pinned source $($source.url)" }
        git -C $source.path checkout --detach FETCH_HEAD
    }
    $actual=git -C $source.path rev-parse HEAD
    if ($actual -ne $source.commit) { throw "Source revision mismatch at $($source.path); existing files were not overwritten." }
}
Write-Host 'Pinned source identities verified. See THIRD_PARTY.md and retained license notices.'
