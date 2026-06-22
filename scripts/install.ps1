# Install the burnstop hook into settings.json (Windows).
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1            # user scope
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Scope project
param(
    [ValidateSet("user", "project")]
    [string]$Scope = "user"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
if (-not $python) { Write-Error "Python 3.8+ not found on PATH."; exit 1 }
& $python (Join-Path $repo "cli.py") install --scope $Scope
