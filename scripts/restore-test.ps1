param([Parameter(Mandatory=$true)][string]$BackupFile, [string]$DatabaseName = ('restorecheck_' + (Get-Date -Format 'yyyyMMdd_HHmmss')))
$ErrorActionPreference = 'Stop'
if ($DatabaseName -notmatch '^restorecheck_[a-z0-9_]{1,40}$') { throw 'Restore is limited to a new restorecheck_* database.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceFile = (Resolve-Path -LiteralPath $BackupFile).Path
if (-not $sourceFile.StartsWith($projectRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must be inside the project directory.' }
$metadata = Get-Content -LiteralPath ($sourceFile + '.json') -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash -ne $metadata.sha256) { throw 'Backup hash mismatch.' }
Set-Location -LiteralPath $projectRoot
$containerId = docker compose -f infra/docker-compose.yml ps -q postgres
if (-not $containerId) { throw 'PostgreSQL container is unavailable.' }
$containerPath = '/tmp/restore-' + [guid]::NewGuid().ToString('N') + '.dump'
docker cp $sourceFile "${containerId}:$containerPath"
if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed.' }
docker exec $containerId createdb -U deepvocal -T template0 $DatabaseName
if ($LASTEXITCODE -ne 0) { throw 'New isolated database could not be created; existing databases are never overwritten.' }
docker exec $containerId pg_restore -U deepvocal --no-owner --exit-on-error -d $DatabaseName $containerPath
if ($LASTEXITCODE -ne 0) { throw 'Restore verification failed.' }
docker exec $containerId psql -U deepvocal -d $DatabaseName -c 'SELECT version_num FROM alembic_version'
if ($LASTEXITCODE -ne 0) { throw 'Restored schema could not be read.' }
Write-Output "Restored into isolated database $DatabaseName. Application configuration is unchanged."
