param(
    [string]$DatasetRoot = 'D:\deepvocal\datasets',
    [string]$OutputDirectory = 'D:\deepvocal\测试相关报告\2026-11-24_pgvector与Milvus检索对照',
    [string]$Python = 'D:\deepvocal\.venv\Scripts\python.exe',
    [int]$Repeats = 3,
    [int]$LiveSamples = 8
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$variables = @{
    PYTHONPATH = Join-Path $projectRoot 'apps/api'
    EMBEDDING_API_BASE = 'http://127.0.0.1:11434/v1'
    RERANKER_API_BASE = 'http://127.0.0.1:18081'
    EMBEDDING_MODEL = 'bge-m3:latest'
    EMBEDDING_MODEL_REVISION = '7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab'
    RERANKER_MODEL = 'kftof/bge-reranker-v2-m3-onnx-int8-avx2'
    RERANKER_MODEL_REVISION = 'da3c866dd7312855dbe7d114d61a4b517c5457b1'
    PGVECTOR_URL = 'postgresql://deepvocal:deepvocal-local-experiment@127.0.0.1:15433/deepvocal_vectors'
    NEO4J_URI = 'bolt://127.0.0.1:7687'
    MILVUS_URI = 'http://127.0.0.1:19530'
}
$previous = @{}
try {
    foreach ($key in $variables.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $variables[$key], 'Process')
    }
    Push-Location -LiteralPath $projectRoot
    try {
        & $Python -m app.knowledge.comparison `
            --bundle (Join-Path $DatasetRoot 'working/knowledge.json') `
            --cases (Join-Path $DatasetRoot 'working/retrieval-model-comparison-v2.1.json') `
            --frozen-cases (Join-Path $DatasetRoot 'working/retrieval-frozen-test-v2.1.json') `
            --frozen-lock (Join-Path $DatasetRoot 'evaluation/frozen-test-v2.1-lock.json') `
            --output (Join-Path $OutputDirectory 'comparison.json') `
            --cache (Join-Path $OutputDirectory 'model-cache.json') `
            --repeats $Repeats --live-samples $LiveSamples `
            --resource-containers deepvocal-retrieval-pgvector-1 deepvocal-retrieval-milvus-1 deepvocal-retrieval-neo4j-1 deepvocal-retrieval-milvus-etcd-1
        if ($LASTEXITCODE -ne 0) { throw 'Retrieval comparison failed; inspect output logs.' }
    } finally { Pop-Location }
} finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}
