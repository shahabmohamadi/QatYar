param(
  [Parameter(Mandatory=$true)][string]$AssetApiUrl,
  [Parameter(Mandatory=$true)][string]$ExpectedSha256,
  [Parameter(Mandatory=$true)][string]$VersionTag
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $env:ProgramData 'MobilePartsShop'
$work = Join-Path $data ('updates\' + $VersionTag)
$status = Join-Path $data 'update-status.txt'
$progressFile = Join-Path $data 'update-progress.json'
$archive = Join-Path $work 'QatYar-Windows.zip'
$old = Join-Path $work 'previous-code'
$dbBackup = Join-Path $work 'previous-data.zip'
$env:STORE_DATA_DIR = $data
$env:PYTHONIOENCODING = 'utf-8'
function Set-UpdateProgress {
  param([int]$Percent, [string]$Stage, [string]$Message, [string]$State = 'running')
  $script:currentPercent = $Percent
  Set-Content -Path $status -Value $Message -Encoding UTF8
  $payload = @{ tag = $VersionTag; state = $State; stage = $Stage; percent = $Percent; message = $Message } | ConvertTo-Json -Compress
  Set-Content -Path $progressFile -Value $payload -Encoding UTF8
}
$script:currentPercent = 1
try {
  if ($AssetApiUrl -notmatch '^https://api\.github\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/assets/[0-9]+$') { throw 'نشانی انتشار معتبر نیست.' }
  if ($ExpectedSha256 -notmatch '^[0-9a-f]{64}$') { throw 'اثر انگشت معتبر نیست.' }
  New-Item -ItemType Directory -Force -Path $work,$old | Out-Null
  Set-UpdateProgress 5 'download' "در حال دریافت نسخه $VersionTag"
  $headers = @{ Accept = 'application/octet-stream'; 'X-GitHub-Api-Version' = '2022-11-28'; 'User-Agent' = 'QatYar-Updater/1.1' }
  $tokenFile = Join-Path $data 'github-token.txt'
  $token = if ($env:STORE_GITHUB_TOKEN) { $env:STORE_GITHUB_TOKEN } elseif (Test-Path $tokenFile) { [IO.File]::ReadAllText($tokenFile).Trim() } else { '' }
  if ($token) { $headers.Authorization = 'Bearer ' + $token }
  Invoke-WebRequest -Uri $AssetApiUrl -Headers $headers -OutFile $archive -UseBasicParsing -TimeoutSec 180
  Set-UpdateProgress 45 'verify' 'در حال بررسی فایل دریافت‌شده'
  $actual = (Get-FileHash -Path $archive -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $ExpectedSha256) { throw 'SHA256 فایل انتشار مطابقت ندارد.' }
  Set-UpdateProgress 50 'backup' 'در حال تهیهٔ نسخهٔ پشتیبان داده‌ها'
  $python = Join-Path $root '.venv\Scripts\python.exe'
  & $python (Join-Path $root 'run.py') backup --output $dbBackup
  if ($LASTEXITCODE -ne 0) { throw 'بکاپ پیش از نصب ناموفق بود.' }
  Set-UpdateProgress 65 'preserve' 'در حال نگهداری نسخهٔ قبلی برنامه'
  foreach ($name in @('store','windows','run.py','VERSION','requirements.txt','requirements.lock')) {
    $source = Join-Path $root $name
    if (Test-Path $source) { Copy-Item -Path $source -Destination $old -Recurse -Force }
  }
  Set-UpdateProgress 72 'extract' 'در حال آماده‌سازی فایل‌های نسخهٔ جدید'
  $extract = Join-Path $work 'extracted'
  if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $extract | Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [IO.Compression.ZipFile]::ExtractToDirectory($archive,$extract)
  $installer = Join-Path $extract 'QatYar\windows\install.ps1'
  if (-not (Test-Path $installer)) { throw 'ساختار بسته نصب معتبر نیست.' }
  Set-UpdateProgress 82 'install' "در حال نصب نسخه $VersionTag"
  & $installer
  Set-UpdateProgress 100 'done' "نسخه $VersionTag نصب شد." 'done'
} catch {
  $message = $_.Exception.Message
  Set-UpdateProgress $script:currentPercent 'rollback' 'نصب متوقف شد؛ در حال بازگردانی نسخهٔ قبلی'
  try {
    Stop-ScheduledTask -TaskName 'MobilePartsShop' -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    foreach ($name in @('store','windows','run.py','VERSION','requirements.txt','requirements.lock')) {
      $source = Join-Path $old $name
      if (Test-Path $source) {
        $destination = Join-Path $root $name
        if (Test-Path $destination) { Remove-Item $destination -Recurse -Force }
        Copy-Item -Path $source -Destination $destination -Recurse -Force
      }
    }
    if (Test-Path $dbBackup) {
      foreach ($name in @('store.db','store.db-wal','store.db-shm')) {
        Remove-Item (Join-Path $data $name) -Force -ErrorAction SilentlyContinue
      }
      & (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'run.py') restore --input $dbBackup
    }
    Start-ScheduledTask -TaskName 'MobilePartsShop' -ErrorAction SilentlyContinue
  } catch { $message += '؛ بازگردانی خودکار نیز خطا داشت: ' + $_.Exception.Message }
  Set-UpdateProgress $script:currentPercent 'error' ("خطای به‌روزرسانی: " + $message) 'error'
  exit 1
}
