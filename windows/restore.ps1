param([Parameter(Mandatory=$true)][string]$BackupFile)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'PowerShell را با Run as administrator اجرا کنید.'
}
$root = Split-Path -Parent $PSScriptRoot
$env:STORE_DATA_DIR = Join-Path $env:ProgramData 'MobilePartsShop'
Stop-ScheduledTask -TaskName 'MobilePartsShop' -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
try {
  & (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'run.py') restore --input $BackupFile
  if ($LASTEXITCODE -ne 0) { throw 'بازگردانی بکاپ ناموفق بود.' }
} finally {
  Start-ScheduledTask -TaskName 'MobilePartsShop'
}
