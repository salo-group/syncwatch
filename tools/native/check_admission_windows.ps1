param([string]$DotNet = 'dotnet')

$ErrorActionPreference = 'Stop'
$admissionRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$admissionServer = Join-Path $admissionRoot 'tools/consumers/admission_fixture.py'
$admissionConfig = Join-Path ([System.IO.Path]::GetTempPath()) ('sw-admission-' + [guid]::NewGuid().ToString('N') + '.json')
$admissionVariable = 'SYNCWATCH_ADMISSION_ISOLATION_FIXTURE'
$admissionPrevious = [Environment]::GetEnvironmentVariable($admissionVariable, 'Process')
$admissionPeer = New-Object System.Diagnostics.Process
$admissionStarted = $false
$admissionFailures = New-Object 'System.Collections.Generic.List[System.Exception]'

try {
    $admissionPeer.StartInfo.FileName = (Get-Command python -CommandType Application | Select-Object -First 1).Source
    $admissionPeer.StartInfo.Arguments = '"' + $admissionServer + '" --serve "' + $admissionConfig + '"'
    $admissionPeer.StartInfo.UseShellExecute = $false
    $admissionPeer.StartInfo.CreateNoWindow = $true
    $admissionPeer.StartInfo.RedirectStandardInput = $true
    if (-not $admissionPeer.Start()) { throw 'Admission fixture did not start' }
    $admissionStarted = $true
    $admissionDeadline = [DateTime]::UtcNow.AddSeconds(5)
    while (-not (Test-Path -LiteralPath $admissionConfig)) {
        if ($admissionPeer.HasExited -or [DateTime]::UtcNow -ge $admissionDeadline) {
            throw 'Admission fixture did not publish its configuration'
        }
        Start-Sleep -Milliseconds 10
    }
    $admissionData = Get-Content -LiteralPath $admissionConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    $admissionAddress = [uri]$admissionData.service
    if ($admissionPeer.HasExited -or $admissionData.ownerPid -ne $admissionPeer.Id -or $admissionAddress.Host -ne '127.0.0.1' -or $admissionAddress.Scheme -ne 'http') {
        throw 'Admission fixture ownership or address changed'
    }
    [Environment]::SetEnvironmentVariable($admissionVariable, $admissionConfig, 'Process')
    & $DotNet run --project (Join-Path $admissionRoot 'apps/windows/SyncWatch.Tests') --no-restore --no-build --configuration Debug
    if ($LASTEXITCODE -ne 0) { throw 'Native admission isolation or application checks failed' }
} catch {
    $admissionFailures.Add($_.Exception)
} finally {
    [Environment]::SetEnvironmentVariable($admissionVariable, $admissionPrevious, 'Process')
    try {
        if ($admissionStarted) {
            # The worker owns threads only. EOF also stops it if this driver exits unexpectedly.
            $admissionPeer.StandardInput.Close()
            if (-not $admissionPeer.WaitForExit(10000)) {
                $admissionPeer.Kill()
                if (-not $admissionPeer.WaitForExit(5000)) { throw 'Admission fixture did not stop' }
                throw 'Admission fixture required forced cleanup'
            }
            if ($admissionPeer.ExitCode -ne 0) { throw 'Admission fixture cleanup failed' }
        }
    } catch {
        $admissionFailures.Add($_.Exception)
    } finally {
        $admissionPeer.Dispose()
        foreach ($admissionFile in @($admissionConfig, ($admissionConfig + '.pending'))) {
            if (Test-Path -LiteralPath $admissionFile) { Remove-Item -LiteralPath $admissionFile -Force }
        }
    }
}
if ($admissionFailures.Count -eq 1) { throw $admissionFailures[0] }
if ($admissionFailures.Count -gt 1) { throw [System.AggregateException]::new('Native checks and fixture cleanup failed', $admissionFailures.ToArray()) }
