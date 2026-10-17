param([string]$DotNet = 'dotnet')

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location $repositoryRoot
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_NOLOGO = '1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE = 'true'
$env:SYNCWATCH_CONTRACT_FIXTURES = Join-Path $repositoryRoot 'contracts/fixtures/acceptance.json'
$toolchain = Get-Content (Join-Path $PSScriptRoot 'toolchains.json') -Raw | ConvertFrom-Json

if ([System.Environment]::OSVersion.Platform -ne 'Win32NT' -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'The initial Windows acceptance lane requires Windows x64'
}

function Invoke-DotNet {
    & $DotNet @args
    if ($LASTEXITCODE -ne 0) {
        throw "dotnet command failed with exit code $LASTEXITCODE"
    }
}

$version = (& $DotNet --version).Trim()
if ($LASTEXITCODE -ne 0 -or $version -ne $toolchain.windows.dotnetSdk) {
    throw 'The installed .NET SDK must match the exact repository pin'
}

Invoke-DotNet --info
Invoke-DotNet restore SyncWatch.slnx --configfile NuGet.Config
Invoke-DotNet format SyncWatch.slnx --no-restore --verify-no-changes
Invoke-DotNet build SyncWatch.slnx --no-restore --configuration Debug
Invoke-DotNet run --project packages/windows-domain/SyncWatch.Domain.Tests --no-restore --no-build --configuration Debug
Write-Output 'Built apps/windows/SyncWatch/bin/Debug/net10.0-windows/SyncWatch.exe; interactive launch/close is a separate acceptance step'
