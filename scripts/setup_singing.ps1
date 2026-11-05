param([switch]$Start)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$engineRoot = Join-Path $projectRoot 'runtime\singing-engine'
$enginePython = Join-Path $engineRoot '.venv\Scripts\python.exe'
$engineSource = Join-Path $engineRoot 'ACE-Step'
$env:HF_HOME = Join-Path $projectRoot 'runtime\model-cache\huggingface'
$env:TORCH_HOME = Join-Path $projectRoot 'runtime\model-cache\torch'
$env:PIP_CACHE_DIR = Join-Path $projectRoot '_qa\pip-cache'
$env:PIP_DEFAULT_TIMEOUT = '180'
$env:TEMP = Join-Path $projectRoot 'runtime\singing-temp'
New-Item -ItemType Directory -Force $engineRoot,$env:TEMP,$env:HF_HOME,$env:TORCH_HOME | Out-Null
function Assert-Exit { if ($LASTEXITCODE -ne 0) { throw "Singing setup failed: $LASTEXITCODE" } }
if (-not (Test-Path -LiteralPath $enginePython)) {
  & (Join-Path $projectRoot '.venv\Scripts\python.exe') -m venv (Join-Path $engineRoot '.venv'); Assert-Exit
}
if (-not (Test-Path -LiteralPath $engineSource)) {
  git clone --filter=blob:none https://github.com/ace-step/ACE-Step.git $engineSource; Assert-Exit
  git -C $engineSource checkout --detach 1bee4c9f5b43e30995f8d4d33b3919197ce1bd68; Assert-Exit
}
$actualCommit = git -C $engineSource rev-parse HEAD; Assert-Exit
if ($actualCommit -ne '1bee4c9f5b43e30995f8d4d33b3919197ce1bd68') { throw 'ACE-Step revision mismatch' }
& $enginePython -m pip install 'torch==2.6.0' 'torchaudio==2.6.0' 'torchvision==0.21.0' --index-url https://download.pytorch.org/whl/cpu; Assert-Exit
& $enginePython -m pip install -r (Join-Path $projectRoot 'apps\singing\requirements.txt'); Assert-Exit
& $enginePython -m pip install --no-deps -e $engineSource; Assert-Exit
& $enginePython (Join-Path $projectRoot 'apps\singing\prepare.py'); Assert-Exit
if ($Start) { & $enginePython (Join-Path $projectRoot 'apps\singing\server.py'); Assert-Exit }
