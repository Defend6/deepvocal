param(
    [ValidateSet('status','start')][string]$Action = 'status',
    [string]$OllamaRoot = 'D:\ollama'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$ollamaExe = Join-Path $OllamaRoot 'app\ollama.exe'
$modelDirectory = Join-Path $OllamaRoot 'models'
if (-not (Test-Path -LiteralPath $ollamaExe)) { throw "Install Ollama in $OllamaRoot\app first." }
if ($Action -eq 'start') {
    New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null
    $logDirectory = Join-Path $projectRoot 'runtime\local-models'
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    $running = $false
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null
        $running = $true
    } catch { $running = $false }
    if (-not $running) {
        $previousModels, $previousHost = $env:OLLAMA_MODELS, $env:OLLAMA_HOST
        try {
            $env:OLLAMA_MODELS = $modelDirectory
            $env:OLLAMA_HOST = '127.0.0.1:11434'
            Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $logDirectory 'ollama.stdout.log') `
                -RedirectStandardError (Join-Path $logDirectory 'ollama.stderr.log') | Out-Null
        } finally {
            $env:OLLAMA_MODELS = $previousModels
            $env:OLLAMA_HOST = $previousHost
        }
        for ($attempt = 0; $attempt -lt 15; $attempt++) {
            try {
                Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null
                $running = $true
                break
            } catch { Start-Sleep -Seconds 1 }
        }
        if (-not $running) { throw "Ollama did not start. Inspect $logDirectory" }
    }
    $env:DEEPVOCAL_MODEL_CACHE = Join-Path $modelDirectory 'tei-cache'
    New-Item -ItemType Directory -Force -Path $env:DEEPVOCAL_MODEL_CACHE | Out-Null
    & docker compose -f (Join-Path $projectRoot 'infra\compose.models.yml') up -d
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the local reranker container' }
}
$version = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 5
$models = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
[pscustomobject]@{
    OllamaVersion = $version.version
    OllamaExecutable = $ollamaExe
    ModelDirectory = $modelDirectory
    EmbeddingModels = @($models.models | ForEach-Object { $_.name })
} | ConvertTo-Json -Depth 3
& docker compose -f (Join-Path $projectRoot 'infra\compose.models.yml') ps
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the local reranker container' }
