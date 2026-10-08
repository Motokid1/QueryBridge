$ErrorActionPreference='Stop'
Set-Location (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
python -m venv backend/.venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create backend Python environment.' }
$requirements = 'backend/requirements.lock.txt'
& './backend/.venv/Scripts/python.exe' -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }

if (!(Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
Push-Location frontend
try {
  npm.cmd ci
  if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
  npm.cmd run build
  if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host 'Setup complete. Download Ollama models, then run backend/scripts/start.ps1.'
