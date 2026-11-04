param([string]$Destination = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Destination) { $Destination = Join-Path $projectRoot 'backups' }
$backupRoot = [System.IO.Path]::GetFullPath($Destination)
if (-not $backupRoot.StartsWith($projectRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Backup destination must stay inside the project directory.' }
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
Set-Location -LiteralPath $projectRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$containerId = docker compose -f infra/docker-compose.yml ps -q postgres
if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw 'PostgreSQL container is unavailable.' }
$containerPath = '/tmp/deepvocal-' + [guid]::NewGuid().ToString('N') + '.dump'
$targetFile = Join-Path $backupRoot "deepvocal-$stamp.dump"
if (Test-Path -LiteralPath $targetFile) { throw 'Backup file already exists.' }
docker exec $containerId pg_dump -U deepvocal -d deepvocal -Fc -f $containerPath
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed.' }
docker cp "${containerId}:$containerPath" $targetFile
if ($LASTEXITCODE -ne 0 -or (Get-Item -LiteralPath $targetFile).Length -lt 100) { throw 'Backup copy failed.' }
$hash = (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash
@{sha256=$hash;created_at=(Get-Date).ToString('o');scope='PostgreSQL only; private data, do not commit';file=[System.IO.Path]::GetFileName($targetFile)} | ConvertTo-Json | Set-Content -LiteralPath ($targetFile + '.json') -Encoding utf8
Write-Output $targetFile
