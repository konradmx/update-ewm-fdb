#requires -Version 5.1
$ErrorActionPreference = 'Stop'

# Edit when porting this script to another project.
$venvName = 'venv_py314_ewm_fdb'

$projectRoot = $PSScriptRoot
$venvBase = if ($env:VIRTUALENVS_HOME) { $env:VIRTUALENVS_HOME } else { 'D:\.virtualenvs' }
$venvPath = Join-Path $venvBase $venvName
$venvLink = Join-Path $projectRoot '.venv'

if (-not (Test-Path -LiteralPath $venvBase)) {
    Write-Host "Creating $venvBase"
    New-Item -ItemType Directory -Path $venvBase -Force | Out-Null
}

if (-not (Test-Path -LiteralPath $venvPath)) {
    Write-Host "Creating venv at $venvPath"
    uv venv --python 3.14 $venvPath
}

if (-not (Test-Path -LiteralPath $venvLink)) {
    Write-Host "Linking $venvLink -> $venvPath"
    New-Item -ItemType Junction -Path $venvLink -Target $venvPath | Out-Null
}

Write-Host "Running uv sync"
uv sync
