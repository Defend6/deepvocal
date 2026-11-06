param([ValidateSet('setup','api','worker','web','test','e2e')][string]$Action = 'api')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$sharedEnv = Join-Path (Split-Path -Parent (Split-Path -Parent $projectRoot)) '.env'
$localEnv = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $localEnv) -and (Test-Path -LiteralPath $sharedEnv)) {
    Copy-Item -LiteralPath $sharedEnv -Destination $localEnv
}
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot '_qa') | Out-Null
$env:npm_config_cache = Join-Path $projectRoot '_qa\npm-cache'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '_qa\playwright-browsers'
function Assert-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed: $LASTEXITCODE" } }
switch ($Action) {
    'setup' {
        if (-not (Test-Path -LiteralPath $pythonExe)) { python -m venv .venv; Assert-Exit }
        & $pythonExe -m pip install -r apps/api/requirements.lock -e 'apps/api[dev]'; Assert-Exit
        if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
        docker compose -f infra/docker-compose.yml up -d --wait; Assert-Exit
        & $pythonExe -m alembic -c apps/api/alembic.ini upgrade head; Assert-Exit
        & $pythonExe -m app.knowledge.cli prepare --allow-missing; Assert-Exit
        & $pythonExe scripts/configure_storage.py; Assert-Exit
        npm --prefix apps/web ci; Assert-Exit
        Write-Output 'Setup finished. Run api, worker and web in three terminals.'
    }
    'api' {
        $env:EMBEDDING_API_BASE = 'http://127.0.0.1:11434/v1'
        $env:RERANKER_API_BASE = 'http://127.0.0.1:18081'
        & $pythonExe -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 --no-access-log
    }
    'worker' {
        $env:EMBEDDING_API_BASE = 'http://127.0.0.1:11434/v1'
        $env:RERANKER_API_BASE = 'http://127.0.0.1:18081'
        & $pythonExe -m app.worker
    }
    'web' { npm --prefix apps/web run dev -- --port 5173 --strictPort }
    'test' {
        $testDbExists = docker exec deepvocal-postgres-1 psql -U deepvocal -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='deepvocal_test'"; Assert-Exit
        if ($testDbExists -ne '1') { docker exec deepvocal-postgres-1 createdb -U deepvocal deepvocal_test; Assert-Exit }
        $env:TEST_DATABASE_URL = 'postgresql+asyncpg://deepvocal:deepvocal@127.0.0.1:15432/deepvocal_test'
        $env:TEST_REDIS_URL = 'redis://127.0.0.1:16379/15'
        $env:TEST_KNOWLEDGE_INDEXES = '1'
        $priorDatabaseUrl = $env:DATABASE_URL
        try {
            $env:DATABASE_URL = $env:TEST_DATABASE_URL
            & $pythonExe -m alembic -c apps/api/alembic.ini upgrade head; Assert-Exit
            Push-Location apps/api
            try { & $pythonExe -m pytest -q --basetemp ../../_qa/pytest-script; Assert-Exit } finally { Pop-Location }
        } finally {
            if ($null -eq $priorDatabaseUrl) { Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue }
            else { $env:DATABASE_URL = $priorDatabaseUrl }
        }
        & .venv/Scripts/ruff.exe check apps/api/app apps/api/tests; Assert-Exit
        npm --prefix apps/web run build; Assert-Exit
    }
    'e2e' {
        Push-Location apps/web
        try { npx playwright install chromium; Assert-Exit; npx playwright test; Assert-Exit } finally { Pop-Location }
    }
}
Assert-Exit
