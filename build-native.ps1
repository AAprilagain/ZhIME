param([string]$BuildDir = (Join-Path $PSScriptRoot '../zhime-local/build-dev'))
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
cmake -S native -B $BuildDir -G 'Visual Studio 16 2019' -A x64
if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed.' }
cmake --build $BuildDir --config Release
if ($LASTEXITCODE -ne 0) { throw 'Native build failed.' }
