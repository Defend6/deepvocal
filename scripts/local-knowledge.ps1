param(
    [ValidateSet('ingest','verify','benchmark','rebuild-graph')][string]$Action = 'verify',
    [ValidateSet('pilot','full')][string]$Scope = 'pilot',
    [string]$DatasetRoot = 'D:\deepvocal\datasets',
    [string]$Cases = '',
    [string]$Output = '',
    [string]$EmbeddingModel = 'bge-m3:latest',
    [string]$RerankerModel = 'kftof/bge-reranker-v2-m3-onnx-int8-avx2',
    [string]$RerankerRevision = 'da3c866dd7312855dbe7d114d61a4b517c5457b1',
    [switch]$UseIndexes,
    [switch]$RetrievalOnly,
    [switch]$UseCache,
    [string]$FrozenLock = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedDatasetRoot = (Resolve-Path -LiteralPath $DatasetRoot).Path
$bundleName = if ($Scope -eq 'pilot') { 'knowledge-pilot-20.json' } else { 'knowledge.json' }
$bundle = Join-Path $resolvedDatasetRoot "working\$bundleName"
if (-not (Test-Path -LiteralPath $bundle)) { throw "Knowledge bundle missing: $bundle" }
if (-not $Cases) {
    $caseName = if ($Scope -eq 'pilot') { 'retrieval-eval-pilot-v1.json' } else { 'retrieval-model-comparison-v1.json' }
    $Cases = Join-Path $resolvedDatasetRoot "working\$caseName"
}
if (-not $Output) { $Output = Join-Path $resolvedDatasetRoot "evaluation\$Scope-retrieval-bge-m3.json" }
New-Item -ItemType Directory -Force -Path (Join-Path $resolvedDatasetRoot 'evaluation') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $resolvedDatasetRoot 'indexes') | Out-Null
$models = (Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10).models
$selected = @($models | Where-Object { $_.name -eq $EmbeddingModel })
if ($selected.Count -ne 1) { throw "Pull the exact Ollama model tag first: $EmbeddingModel" }
$dockerArgs = @(
    'run', '--rm', '--network', 'deepvocal_default',
    '--mount', "type=bind,source=$projectRoot/apps/api,target=/app/apps/api,readonly",
    '--mount', "type=bind,source=$resolvedDatasetRoot,target=/app/datasets",
    '-e', 'PYTHONDONTWRITEBYTECODE=1',
    '-e', "KNOWLEDGE_BUNDLE=/app/datasets/working/$bundleName",
    '-e', "KNOWLEDGE_INDEX_MANIFEST=/app/datasets/indexes/$Scope-bge-m3.json",
    '-e', 'KNOWLEDGE_MODE=research',
    '-e', 'MODEL_API_KEY=',
    '-e', 'MODEL_MONTHLY_LIMIT_MINOR=0',
    '-e', 'EMBEDDING_API_BASE=http://host.docker.internal:11434/v1',
    '-e', 'RERANKER_API_BASE=http://host.docker.internal:18081',
    '-e', "EMBEDDING_MODEL=$EmbeddingModel",
    '-e', "EMBEDDING_MODEL_REVISION=$($selected[0].digest)",
    '-e', "RERANKER_MODEL=$RerankerModel",
    '-e', "RERANKER_MODEL_REVISION=$RerankerRevision",
    '-e', 'NEO4J_URI=bolt://neo4j:7687',
    '-e', 'NEO4J_PASSWORD=change-me-local-only',
    '-e', 'MILVUS_URI=http://milvus:19530'
)
if ($Action -eq 'benchmark') {
    if (-not (Test-Path -LiteralPath $Cases)) { throw "Evaluation cases missing: $Cases" }
    $resolvedCases = (Resolve-Path -LiteralPath $Cases).Path
    $outputDirectory = Split-Path -Parent ([IO.Path]::GetFullPath($Output))
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    $dockerArgs += @('--mount', "type=bind,source=$resolvedCases,target=/evaluation-cases.json,readonly")
    $dockerArgs += @('--mount', "type=bind,source=$outputDirectory,target=/evaluation-output")
    if ($FrozenLock) {
        $resolvedLock = (Resolve-Path -LiteralPath $FrozenLock).Path
        $dockerArgs += @('--mount', "type=bind,source=$resolvedLock,target=/frozen-lock.json,readonly")
    }
    $dockerArgs += @('deepvocal-api:local', 'python', '-m', 'app.knowledge.local_benchmark',
        '--bundle', "/app/datasets/working/$bundleName", '--cases', '/evaluation-cases.json',
        '--output', "/evaluation-output/$(Split-Path -Leaf $Output)")
    if ($UseIndexes) { $dockerArgs += '--use-indexes' }
    if ($RetrievalOnly) { $dockerArgs += '--retrieval-only' }
    if ($UseCache) { $dockerArgs += @('--cache', '/app/datasets/evaluation/local-model-benchmark-cache.json') }
    if ($FrozenLock) { $dockerArgs += @('--frozen-lock', '/frozen-lock.json') }
} else {
    $dockerArgs += @('deepvocal-api:local', 'python', '-m', 'app.knowledge.cli', $Action)
}
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) { throw "Local knowledge $Action failed: $LASTEXITCODE" }
