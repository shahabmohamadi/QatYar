param([int]$Port = 8080)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:STORE_DATA_DIR = Join-Path $env:ProgramData 'MobilePartsShop'
$env:STORE_HOST = '0.0.0.0'
$env:STORE_PORT = [string]$Port
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'run.py') serve
