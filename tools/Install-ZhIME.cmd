@echo off
setlocal
set "ZHIME_SETUP=%~dp0install-native.ps1"
powershell.exe -NoProfile -Command "try { $p=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File '+[char]34+$env:ZHIME_SETUP+[char]34); exit $p.ExitCode } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (echo Installation failed or was cancelled.) else (echo Installed. Use Win+Space to select ZhIME.)
pause
