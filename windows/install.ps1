# Run in an elevated PowerShell window from the extracted project folder.
param(
  [string]$BackupDir = '',
  [string]$AdminPasswordFile = '',
  [int]$Port = 8080
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'PowerShell را با Run as administrator اجرا کنید.'
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'پورت باید بین 1024 و 65535 باشد.' }
if ($BackupDir -and -not [IO.Path]::IsPathRooted($BackupDir)) { throw 'مسیر بکاپ باید کامل باشد.' }
if ($AdminPasswordFile -and -not [IO.Path]::IsPathRooted($AdminPasswordFile)) { throw 'مسیر فایل رمز مدیر باید کامل باشد.' }
$source = Split-Path -Parent $PSScriptRoot
$installDir = Join-Path $env:ProgramFiles 'MobilePartsShop'
$dataDir = Join-Path $env:ProgramData 'MobilePartsShop'
$effectiveBackupDir = if ($BackupDir) { $BackupDir } else { Join-Path $dataDir 'backups' }

function Get-Python312 {
  $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($launcher) {
    $result = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0 -and $result) { return $result.Trim() }
  }
  $candidate = Join-Path $env:ProgramFiles 'Python312\python.exe'
  if (Test-Path $candidate) { return $candidate }
  return $null
}
$python = Get-Python312
if (-not $python) {
  $offlinePython = Join-Path $source 'windows\offline\python-3.12.10-amd64.exe'
  if (Test-Path $offlinePython) {
    $pythonTarget = Join-Path $env:ProgramFiles 'Python312'
    $process = Start-Process -FilePath $offlinePython -ArgumentList @('/quiet','InstallAllUsers=1','PrependPath=0','Include_launcher=1','Include_pip=1',"TargetDir=`"$pythonTarget`"") -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "نصب آفلاین Python ناموفق بود: $($process.ExitCode)" }
  } elseif (Get-Command winget.exe -ErrorAction SilentlyContinue) {
    & winget install --id Python.Python.3.12 --exact --scope machine --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw 'نصب Python انجام نشد.' }
  } else {
    throw 'Python 3.12 پیدا نشد و بسته نصب آفلاین یا winget هم در دسترس نیست.'
  }
  $python = Get-Python312
  if (-not $python) { throw 'Python نصب شد؛ یک پنجرهٔ PowerShell تازه باز کنید و نصب را دوباره اجرا کنید.' }
}

New-Item -ItemType Directory -Force -Path $installDir, $dataDir, $effectiveBackupDir, (Join-Path $installDir 'store'), (Join-Path $installDir 'windows') | Out-Null
& icacls $dataDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'تنظیم دسترسی پوشه داده ناموفق بود.' }
Copy-Item (Join-Path $source 'run.py') $installDir -Force
Copy-Item (Join-Path $source 'VERSION') $installDir -Force
Copy-Item (Join-Path $source 'requirements.txt') $installDir -Force
Copy-Item (Join-Path $source 'requirements.lock') $installDir -Force
Copy-Item (Join-Path $source 'store\*') (Join-Path $installDir 'store') -Recurse -Force
Copy-Item (Join-Path $source 'windows\start.ps1') (Join-Path $installDir 'windows') -Force
Copy-Item (Join-Path $source 'windows\backup.ps1') (Join-Path $installDir 'windows') -Force
Copy-Item (Join-Path $source 'windows\restore.ps1') (Join-Path $installDir 'windows') -Force
Copy-Item (Join-Path $source 'windows\update.ps1') (Join-Path $installDir 'windows') -Force

$venvPython = Join-Path $installDir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) { & $python -m venv (Join-Path $installDir '.venv') }
if ($LASTEXITCODE -ne 0) { throw 'ساخت محیط Python ناموفق بود.' }
$wheels = Join-Path $source 'windows\wheels'
if (Test-Path $wheels) {
  & $venvPython -m pip install --no-index --find-links $wheels -r (Join-Path $installDir 'requirements.lock')
} else {
  & $venvPython -m pip install -r (Join-Path $installDir 'requirements.lock')
}
if ($LASTEXITCODE -ne 0) { throw 'نصب وابستگی‌های برنامه ناموفق بود.' }

$env:STORE_DATA_DIR = $dataDir
$ownerCount = & $venvPython (Join-Path $installDir 'run.py') owner-count
if ($LASTEXITCODE -ne 0) { throw 'بررسی حساب مدیر ناموفق بود.' }
if ([int]$ownerCount -eq 0) {
  try {
    if ($AdminPasswordFile) {
      if (-not (Test-Path $AdminPasswordFile -PathType Leaf)) { throw 'فایل رمز مدیر اولیه پیدا نشد.' }
      $env:STORE_INITIAL_ADMIN_PASSWORD = [IO.File]::ReadAllText($AdminPasswordFile).TrimEnd([char[]]@("`r","`n"))
    } else {
      Write-Host 'اکنون رمز مدیر اولیه را تعیین کنید.'
    }
    & $venvPython (Join-Path $installDir 'run.py') init
    if ($LASTEXITCODE -ne 0) { throw 'ساخت حساب مدیر ناموفق بود.' }
  } finally {
    Remove-Item Env:\STORE_INITIAL_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    if ($AdminPasswordFile -and (Test-Path $AdminPasswordFile)) { Remove-Item $AdminPasswordFile -Force }
  }
}
if ($AdminPasswordFile -and (Test-Path $AdminPasswordFile)) { Remove-Item $AdminPasswordFile -Force }
if ($BackupDir) {
  & $venvPython (Join-Path $installDir 'run.py') set-backup-directory --path $BackupDir
  if ($LASTEXITCODE -ne 0) { throw 'تنظیم مسیر بکاپ ناموفق بود.' }
}

$taskName = 'MobilePartsShop'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
}
$startFile = Join-Path $installDir 'windows\start.ps1'
$startAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startFile`" -Port $Port" -WorkingDirectory $installDir
$startup = New-ScheduledTaskTrigger -AtStartup
$system = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $startAction -Trigger $startup -Principal $system -Settings $taskSettings -Force | Out-Null

$backupFile = Join-Path $installDir 'windows\backup.ps1'
$backupAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backupFile`"" -WorkingDirectory $installDir
$nightly = New-ScheduledTaskTrigger -Daily -At 2:00AM
$backupSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName 'MobilePartsShop-Backup' -Action $backupAction -Trigger $nightly -Principal $system -Settings $backupSettings -Force | Out-Null

$oldFirewallRule = Get-NetFirewallRule -DisplayName 'MobilePartsShop Private Web' -ErrorAction SilentlyContinue
if ($oldFirewallRule) { $oldFirewallRule | Remove-NetFirewallRule }
New-NetFirewallRule -DisplayName 'MobilePartsShop Private Web' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
$programs = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
$startupFolder = Join-Path $programs 'Startup'
$publicDesktop = Join-Path $env:PUBLIC 'Desktop'
New-Item -ItemType Directory -Force -Path $startupFolder,$publicDesktop | Out-Null
$shortcut = "[InternetShortcut]`r`nURL=http://localhost:$Port/login`r`n"
foreach ($destination in @((Join-Path $programs 'QatYar.url'),(Join-Path $startupFolder 'QatYar.url'),(Join-Path $publicDesktop 'QatYar.url'))) {
  [IO.File]::WriteAllText($destination,$shortcut,[Text.Encoding]::ASCII)
}
Start-ScheduledTask -TaskName $taskName
$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
  Start-Sleep -Seconds 1
  try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) { $ready = $true; break }
  } catch { }
}
if (-not $ready) { throw 'برنامه نصب شد اما راه‌اندازی خودکار پاسخ نداد؛ وضعیت وظیفه MobilePartsShop را بررسی کنید.' }
try { Start-Process "http://localhost:$Port/login" } catch { Write-Host "صفحه ورود: http://localhost:$Port/login" }
Write-Host "نصب کامل شد. دیتابیس به‌صورت خودکار در $dataDir ساخته شد."
Write-Host "پنل روی همین ویندوز: http://localhost:$Port"
Write-Host "بکاپ روزانه: $effectiveBackupDir"
Write-Host 'میانبر برنامه برای همهٔ کاربران در Start Menu، Startup و دسکتاپ عمومی ساخته شد.'
