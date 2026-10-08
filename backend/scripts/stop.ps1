$ErrorActionPreference='Stop'
$root=(Resolve-Path -LiteralPath (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)).Path
$python=Join-Path $root 'backend\.venv\Scripts\python.exe'
Push-Location (Join-Path $root 'backend')
try { $appPort=& $python -c "from app.config import Settings; print(Settings().app_port)" }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw 'Invalid local configuration.' }
$listeners=Get-NetTCPConnection -LocalPort $appPort -State Listen -ErrorAction SilentlyContinue
if (!$listeners) { Write-Host 'Local Lens is already stopped.'; exit 0 }
foreach ($entry in $listeners) {
  $server=Get-CimInstance Win32_Process -Filter ('ProcessId='+$entry.OwningProcess)
  $parent=Get-CimInstance Win32_Process -Filter ('ProcessId='+$server.ParentProcessId)
  $ownServer=$server.CommandLine.Contains('uvicorn app.main:create_app')
  $ownPath=$server.CommandLine.Contains($python) -or ($parent -and $parent.CommandLine.Contains($python))
  if (!$ownServer -or !$ownPath) { throw 'Port is owned by another process; it will not be stopped.' }
  Stop-Process -Id $server.ProcessId
  if ($parent -and $parent.CommandLine.Contains($python) -and $parent.CommandLine.Contains('uvicorn app.main:create_app')) { Stop-Process -Id $parent.ProcessId -ErrorAction SilentlyContinue }
}
Write-Host 'Local Lens stopped. MySQL and Ollama remain running.'
