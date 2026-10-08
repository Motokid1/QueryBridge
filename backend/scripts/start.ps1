$ErrorActionPreference='Stop'
Set-Location (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
if (!(Test-Path backend/.venv/Scripts/python.exe)) { throw 'Run backend/scripts/setup.ps1 first.' }
if (!(Test-Path frontend/dist/index.html)) { throw 'Production frontend missing. Run backend/scripts/setup.ps1.' }
Push-Location backend
try {
  $appPort = & './.venv/Scripts/python.exe' -c "from app.config import Settings; print(Settings().app_port)"
  if ($LASTEXITCODE -ne 0) { throw 'Invalid backend configuration.' }
  Write-Host ('Local Lens: http://127.0.0.1:'+$appPort)
  Write-Host 'Workspace access key (paste into the sign-in screen):'
  & './.venv/Scripts/python.exe' -m app.cli key
  & './.venv/Scripts/python.exe' -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $appPort
} finally { Pop-Location }
