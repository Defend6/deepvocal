param([Parameter(Mandatory=$true)][string]$BackupDirectory)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$source=[IO.Path]::GetFullPath($BackupDirectory)
if (-not $source.StartsWith($projectRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must be inside project' }
$manifest=Get-Content -LiteralPath (Join-Path $source 'manifest.json') -Raw | ConvertFrom-Json
$privateEnv = @{}
if (Test-Path -LiteralPath (Join-Path $projectRoot '.env')) {
  Get-Content -LiteralPath (Join-Path $projectRoot '.env') | ForEach-Object {
    if ($_ -match '^NEO4J_PASSWORD=(.*)$') { $privateEnv.NEO4J_PASSWORD = $Matches[1].Trim('"').Trim("'") }
  }
}
$suffix=[Guid]::NewGuid().ToString('N').Substring(0,12)
$restored=@()
function Assert-Exit { if ($LASTEXITCODE -ne 0) { throw "Restore failed: $LASTEXITCODE" } }
foreach ($entry in $manifest.volumes) {
  if ($entry.file -notmatch '^deepvocal_[a-zA-Z0-9_-]+\.tgz$') { throw 'Invalid backup archive name' }
  if ($entry.volume -notmatch '^deepvocal_[a-zA-Z0-9_-]+$' -or $entry.file -ne ($entry.volume + '.tgz')) { throw 'Invalid volume manifest' }
  $archive=Join-Path $source $entry.file
  if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $entry.sha256) { throw 'Backup hash mismatch' }
  $newVolume='deepvocal_restorecheck_'+$suffix+'_'+$entry.volume.Substring(10)
  docker volume create --label deepvocal.restorecheck=$suffix $newVolume | Out-Null; Assert-Exit
  docker run --rm --network none --mount "type=volume,source=$newVolume,target=/restore" --mount "type=bind,source=$source,target=/backup,readonly" postgres:16-alpine tar -xzf "/backup/$($entry.file)" -C /restore; Assert-Exit
  docker run --rm --network none --mount "type=volume,source=$newVolume,target=/restore,readonly" postgres:16-alpine test -d /restore; Assert-Exit
  $restored+=@{source=$entry.volume;restored=$newVolume}
}
$pg=$restored | Where-Object { $_.source -eq 'deepvocal_postgres_data' }
if ($pg) {
  $container='deepvocal-restorecheck-'+$suffix
  docker run -d --name $container --network none --mount "type=volume,source=$($pg.restored),target=/var/lib/postgresql/data" postgres:16-alpine | Out-Null; Assert-Exit
  try {
    $ready=$false
    for($attempt=0;$attempt -lt 30;$attempt++) {
      docker exec $container pg_isready -U deepvocal -d deepvocal *> $null
      if ($LASTEXITCODE -eq 0) {$ready=$true;break}
      Start-Sleep -Seconds 1
    }
    if (-not $ready) {throw 'Restored PostgreSQL did not start'}
    docker exec $container psql -U deepvocal -d deepvocal -tAc 'SELECT version_num FROM alembic_version'; Assert-Exit
  } finally {docker stop $container | Out-Null; docker rm $container | Out-Null}
}
$redis=$restored | Where-Object { $_.source -eq 'deepvocal_redis_data' }
if ($redis) {
  $container='deepvocal-restorecheck-redis-'+$suffix
  docker run -d --name $container --network none --mount "type=volume,source=$($redis.restored),target=/data" redis:7-alpine redis-server --appendonly yes | Out-Null; Assert-Exit
  try {
    $ready=$false
    for($attempt=0;$attempt -lt 30;$attempt++) {
      $pong=docker exec $container redis-cli ping 2>$null
      if ($LASTEXITCODE -eq 0 -and $pong -eq 'PONG') {$ready=$true;break}
      Start-Sleep -Seconds 1
    }
    if (-not $ready) {throw 'Restored Redis did not start'}
    docker exec $container redis-cli dbsize; Assert-Exit
  } finally {docker stop $container | Out-Null; docker rm $container | Out-Null}
}
$graph=$restored | Where-Object { $_.source -eq 'deepvocal_neo4j_data' }
if ($graph) {
  $container='deepvocal-restorecheck-graph-'+$suffix
  if (-not $privateEnv.NEO4J_PASSWORD) { throw 'Local Neo4j credentials are required for a real restore query' }
  docker run -d --name $container --hostname localhost --network none --env "NEO4J_AUTH=neo4j/$($privateEnv.NEO4J_PASSWORD)" --env NEO4J_server_memory_heap_initial__size=512m --env NEO4J_server_memory_heap_max__size=1G --mount "type=volume,source=$($graph.restored),target=/data" neo4j:5-community | Out-Null; Assert-Exit
  try {
    $ready=$false
    for($attempt=0;$attempt -lt 120;$attempt++) {
      docker exec $container cypher-shell -a bolt://localhost:7687 -u neo4j -p $privateEnv.NEO4J_PASSWORD 'MATCH (n) RETURN count(n)' *> $null
      if ($LASTEXITCODE -eq 0) {$ready=$true;break}
      Start-Sleep -Seconds 1
    }
    if (-not $ready) { docker logs --tail 30 $container; throw 'Restored Neo4j did not answer a query' }
  } finally {docker stop $container | Out-Null; docker rm $container | Out-Null}
}
$result=Join-Path $source ('restorecheck-'+$suffix+'.json')
@{verified_at=(Get-Date).ToString('o');volumes=$restored;scope='All archive hashes and extraction verified; restored PostgreSQL, Redis and Neo4j queried; vector semantic recovery remains a separate check'} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $result -Encoding utf8
Write-Output $result
