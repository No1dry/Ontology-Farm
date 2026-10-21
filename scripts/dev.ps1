param([ValidateSet('prepare','check','up','test','e2e','down','status')][string]$Action='status')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$projectPython=Join-Path $projectRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $projectPython)) { throw 'Create .venv and install backend/requirements.lock first; see docs/operations/environment.md.' }
& $projectPython (Join-Path $PSScriptRoot 'dev.py') $Action
exit $LASTEXITCODE
