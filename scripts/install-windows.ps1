param([string]$Environment = "$env:LOCALAPPDATA\MCPManager\venv")
$ErrorActionPreference = 'Stop'
$Source = Split-Path -Parent $PSScriptRoot
py -3 -m venv $Environment
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required' }
$Python = Join-Path $Environment 'Scripts\python.exe'
& $Python -m pip install "$Source[gui]"
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed' }
& $Python (Join-Path $PSScriptRoot 'install_user_entry.py') --python $Python
Write-Output "Run: & '$Python' -m mcp_manager gui"
