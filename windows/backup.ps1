$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:STORE_DATA_DIR = Join-Path $env:ProgramData 'MobilePartsShop'
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'run.py') scheduled-backup
if ($LASTEXITCODE -ne 0) { throw 'ساخت بکاپ ناموفق بود.' }
