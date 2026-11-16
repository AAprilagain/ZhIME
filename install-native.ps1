param([switch]$Uninstall, [string]$RuntimeRoot, [string]$BuildDir)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (!([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Windows TSF registration requires an elevated PowerShell. Run this script as Administrator.'
}
if (!$RuntimeRoot) {
    $RuntimeRoot=if (Test-Path (Join-Path $PSScriptRoot 'artifacts')) { $PSScriptRoot } else { Join-Path $PSScriptRoot '../zhime-local' }
}
$RuntimeRoot=(Resolve-Path -LiteralPath $RuntimeRoot).Path
$dll=Join-Path $RuntimeRoot 'ZhIME.dll'
$bundled=(Test-Path -LiteralPath $dll) -and (Test-Path -LiteralPath (Join-Path $RuntimeRoot 'automatic-start.enabled'))
if (!$bundled) {
    if (!$BuildDir) {
        $BuildDir=Join-Path $RuntimeRoot 'build-dev'
        if (!(Test-Path -LiteralPath (Join-Path $BuildDir 'native-dll.txt'))) { $BuildDir=Join-Path $RuntimeRoot 'native/build' }
    }
    $dllName=(Get-Content -LiteralPath (Join-Path $BuildDir 'native-dll.txt') -Raw).Trim()
    $dll=Join-Path (Resolve-Path -LiteralPath $BuildDir).Path ('Release/'+$dllName)
}
if (!(Test-Path -LiteralPath $dll)) { throw 'Build the native DLL first using build-native.ps1.' }
$arguments=if ($Uninstall) { @('/s','/u',('"'+$dll+'"')) } else { @('/s',('"'+$dll+'"')) }
$p=Start-Process -FilePath "$env:WINDIR\System32\regsvr32.exe" -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "TSF registration failed: $($p.ExitCode). See $dll.registration.log" }
$tip='0804:{C8470792-F77D-4F13-98A4-B26C7C5E6139}{50E39D92-7271-437D-9E5C-DA51A98AC1D2}'
$languages=Get-WinUserLanguageList
$chinese=$languages | Where-Object LanguageTag -Like 'zh-Hans*' | Select-Object -First 1
if (!$chinese -and !$Uninstall) { $languages.Add('zh-CN'); $chinese=$languages | Where-Object LanguageTag -Like 'zh-Hans*' | Select-Object -First 1 }
if ($chinese) {
    if ($Uninstall) { $null=$chinese.InputMethodTips.Remove($tip) }
    elseif (!$chinese.InputMethodTips.Contains($tip)) { $chinese.InputMethodTips.Add($tip) }
    Set-WinUserLanguageList $languages -Force
}
if ($Uninstall) { Write-Host 'ZhIME unregistered. Existing input methods were retained.' }
elseif ($bundled) {
    $listener=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18770 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if ($process.CommandLine -match '\s-m\s+zhime\.independent_server(\s|$)') { Stop-Process -Id $process.ProcessId }
    }
    Write-Host 'ZhIME registered. Select ZhIME with Win+Space. Reopen applications if upgrading.'
}
else { Write-Host 'ZhIME registered. Start the engine using start-native.ps1, then select ZhIME with Win+Space. Reopen applications if necessary.' }
