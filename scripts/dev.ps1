param([ValidateSet('setup','api','worker','web','test','e2e')][string]$Action = 'api')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
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
        & $pythonExe -m app.knowledge.cli prepare; Assert-Exit
        & $pythonExe scripts/configure_storage.py; Assert-Exit
        npm --prefix apps/web ci; Assert-Exit
        Write-Output 'Setup finished. Run api, worker and web in three terminals.'
    }
    'api' { & $pythonExe -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 --no-access-log }
    'worker' { & $pythonExe -m app.worker }
    'web' { npm --prefix apps/web run dev -- --port 5173 --strictPort }
    'test' {
        $env:TEST_DATABASE_URL = 'postgresql+asyncpg://deepvocal:deepvocal@127.0.0.1:15432/deepvocal'
        $env:TEST_REDIS_URL = 'redis://127.0.0.1:16379/15'
        $env:TEST_KNOWLEDGE_INDEXES = '1'
        & $pythonExe -m alembic -c apps/api/alembic.ini upgrade head; Assert-Exit
        Push-Location apps/api
        try { & $pythonExe -m pytest -q --basetemp ../../_qa/pytest-script; Assert-Exit } finally { Pop-Location }
        & .venv/Scripts/ruff.exe check apps/api/app apps/api/tests; Assert-Exit
        npm --prefix apps/web run build; Assert-Exit
    }
    'e2e' {
        Push-Location apps/web
        try { npx playwright install chromium; Assert-Exit; npx playwright test; Assert-Exit } finally { Pop-Location }
    }
}
Assert-Exit
