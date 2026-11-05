param([switch]$WithIdentity, [switch]$WithMonitoring, [switch]$WithSinging)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
function Assert-Exit { if ($LASTEXITCODE -ne 0) { throw "Startup failed: $LASTEXITCODE" } }
$compose = @('compose','--env-file','.env','-f','infra/docker-compose.yml','-f','infra/compose.app.yml')
if ($WithIdentity) { $compose += @('-f','infra/compose.identity.yml') }
if ($WithMonitoring) {
  & .venv/Scripts/python.exe scripts/prepare_monitoring.py; Assert-Exit
  $compose += @('-f','infra/compose.monitoring.yml')
}
if ($WithSinging) {
  $enginePython = Join-Path $projectRoot 'runtime/singing-engine/.venv/Scripts/python.exe'
  if (-not (Test-Path -LiteralPath $enginePython)) { throw 'Run scripts/setup_singing.ps1 first' }
  if (-not (Test-Path -LiteralPath 'runtime/models/ace-step-manifest.json')) { throw 'Model preparation is incomplete' }
  $listener = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
  if (-not $listener) {
    $process = Start-Process -FilePath $enginePython -ArgumentList 'apps/singing/server.py' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput 'runtime/singing-service.stdout.log' -RedirectStandardError 'runtime/singing-service.stderr.log'
    $process.Id | Set-Content -LiteralPath 'runtime/singing-service.pid'
  }
}
& docker @compose up -d --wait --wait-timeout 180; Assert-Exit
Write-Output 'DeepVocal: http://127.0.0.1:8080'
