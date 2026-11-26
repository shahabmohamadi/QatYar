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
$archive = Join-Path $work 'QatYar-Windows.zip'
$old = Join-Path $work 'previous-code'
$dbBackup = Join-Path $work 'previous-data.zip'
$env:STORE_DATA_DIR = $data
$env:PYTHONIOENCODING = 'utf-8'
try {
  if ($AssetApiUrl -notmatch '^https://api\.github\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/assets/[0-9]+$') { throw 'نشانی انتشار معتبر نیست.' }
  if ($ExpectedSha256 -notmatch '^[0-9a-f]{64}$') { throw 'اثر انگشت معتبر نیست.' }
  New-Item -ItemType Directory -Force -Path $work,$old | Out-Null
  Set-Content -Path $status -Value "در حال دریافت نسخه $VersionTag" -Encoding UTF8
  $headers = @{ Accept = 'application/octet-stream'; 'X-GitHub-Api-Version' = '2022-11-28'; 'User-Agent' = 'QatYar-Updater/1.1' }
  $tokenFile = Join-Path $data 'github-token.txt'
  $token = if ($env:STORE_GITHUB_TOKEN) { $env:STORE_GITHUB_TOKEN } elseif (Test-Path $tokenFile) { [IO.File]::ReadAllText($tokenFile).Trim() } else { '' }
  if ($token) { $headers.Authorization = 'Bearer ' + $token }
  Invoke-WebRequest -Uri $AssetApiUrl -Headers $headers -OutFile $archive -UseBasicParsing -TimeoutSec 180
  $actual = (Get-FileHash -Path $archive -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $ExpectedSha256) { throw 'SHA256 فایل انتشار مطابقت ندارد.' }
  Set-Content -Path $status -Value 'در حال تهیهٔ نسخهٔ پشتیبان' -Encoding UTF8
  $python = Join-Path $root '.venv\Scripts\python.exe'
  & $python (Join-Path $root 'run.py') backup --output $dbBackup
  if ($LASTEXITCODE -ne 0) { throw 'بکاپ پیش از نصب ناموفق بود.' }
  foreach ($name in @('store','windows','run.py','VERSION','requirements.txt','requirements.lock')) {
    $source = Join-Path $root $name
    if (Test-Path $source) { Copy-Item -Path $source -Destination $old -Recurse -Force }
  }
  $extract = Join-Path $work 'extracted'
  if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $extract | Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [IO.Compression.ZipFile]::ExtractToDirectory($archive,$extract)
  $installer = Join-Path $extract 'QatYar\windows\install.ps1'
  if (-not (Test-Path $installer)) { throw 'ساختار بسته نصب معتبر نیست.' }
  Set-Content -Path $status -Value "در حال نصب نسخه $VersionTag" -Encoding UTF8
  & $installer
  Set-Content -Path $status -Value "نسخه $VersionTag نصب شد." -Encoding UTF8
} catch {
  $message = $_.Exception.Message
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
  Set-Content -Path $status -Value ("خطای به‌روزرسانی: " + $message) -Encoding UTF8
  exit 1
}
