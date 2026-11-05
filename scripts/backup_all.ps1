param([string]$Destination = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $Destination) { $Destination = Join-Path $projectRoot ('backups\full-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$target = [IO.Path]::GetFullPath($Destination)
if (-not $target.StartsWith($projectRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must stay in project' }
if (Test-Path -LiteralPath $target) { throw 'Use a new backup directory' }
New-Item -ItemType Directory -Path $target | Out-Null
function Assert-Exit { if ($LASTEXITCODE -ne 0) { throw "Backup command failed: $LASTEXITCODE" } }
$running = @(docker ps -q --filter label=com.docker.compose.project=deepvocal); Assert-Exit
$volumes = @(docker volume ls -q --filter label=com.docker.compose.project=deepvocal); Assert-Exit
if (-not $volumes.Count) { throw 'No project volumes found' }
$entries = @()
try {
  if ($running.Count) { docker stop --time 60 @running | Out-Null; Assert-Exit }
  foreach ($volume in $volumes) {
    if ($volume -notmatch '^deepvocal_[a-zA-Z0-9_-]+$') { throw 'Unexpected volume name' }
    docker run --rm --network none --mount "type=volume,source=$volume,target=/source,readonly" --mount "type=bind,source=$target,target=/backup" postgres:16-alpine tar -czf "/backup/$volume.tgz" -C /source .; Assert-Exit
    $entries += @{volume=$volume;file="$volume.tgz";sha256=(Get-FileHash -LiteralPath (Join-Path $target "$volume.tgz") -Algorithm SHA256).Hash}
  }
  if (Test-Path -LiteralPath '.env') { Copy-Item -LiteralPath '.env' -Destination (Join-Path $target 'private.env') }
  @{version=1;created_at=(Get-Date).ToString('o');scope='Cold snapshots of all DeepVocal named volumes; private backup';volumes=$entries} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $target 'manifest.json') -Encoding utf8
} finally {
  if ($running.Count) { docker start @running | Out-Null }
  # Simultaneous starts can race Docker DNS for nginx and the worker. Reconcile
  # dependency order and wait for the API before bringing the application back.
  docker compose --env-file .env -f infra/docker-compose.yml -f infra/compose.app.yml up -d --wait --wait-timeout 180 | Out-Null
  Assert-Exit
}
Write-Output $target
