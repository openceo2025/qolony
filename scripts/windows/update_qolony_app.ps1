param(
  [string]$TargetDir = "C:\qolony",
  [string]$DataDir = "C:\QOLONY_DATA",
  [int]$HealthPort = 8000
)

# Installs or updates QOLONY from an extracted Windows bundle.
# - Never touches the database or any file in the data directory except to ADD
#   settings.yaml / qolony_env.bat when they do not exist yet.
# - Keeps the previous app as <TargetDir>.backup_<timestamp>.
# - Moves site settings out of the app folder so the next update cannot overwrite them:
#   old <TargetDir>\backend\config\settings.yaml -> <DataDir>\settings.yaml
#   QOLONY_* lines of old <TargetDir>\start_qolony.bat -> <DataDir>\qolony_env.bat
# This file must stay ASCII only (Windows PowerShell 5.1 reads BOM-less scripts as ANSI).

$ErrorActionPreference = "Stop"

function Get-FullDir([string]$Path) {
  return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]"\/")
}

$bundleRoot = Get-FullDir (Split-Path -Parent $MyInvocation.MyCommand.Path)
$targetPath = Get-FullDir $TargetDir
$dataPath = Get-FullDir $DataDir
$driveRoot = [System.IO.Path]::GetPathRoot($targetPath).TrimEnd([char[]]"\/")
if ($targetPath -eq $driveRoot) { throw "Refusing to install into a drive root: $targetPath" }
if ($targetPath -ieq $dataPath -or $targetPath.StartsWith($dataPath + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "The app folder must not be the data folder: $targetPath"
}
if ($bundleRoot -ieq $targetPath -or $bundleRoot.StartsWith($targetPath + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Extract the bundle outside the app folder before updating."
}

foreach ($relative in @("backend\app\main.py", "frontend\dist\index.html", "runtime\python.exe", "start_qolony.bat", "SHA256SUMS.txt")) {
  if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot $relative))) {
    throw "Bundle file is missing: $relative"
  }
}

Write-Host "Verifying bundle checksums..."
$bad = 0
foreach ($line in Get-Content -LiteralPath (Join-Path $bundleRoot "SHA256SUMS.txt") -Encoding UTF8) {
  if ($line -notmatch "^([0-9a-f]{64})  (.+)$") { continue }
  $expected = $Matches[1]
  $file = Join-Path $bundleRoot ($Matches[2] -replace "/", "\")
  if (-not (Test-Path -LiteralPath $file)) { $bad += 1; Write-Warning "missing: $($Matches[2])"; continue }
  $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $expected) { $bad += 1; Write-Warning "changed: $($Matches[2])" }
}
if ($bad -gt 0) { throw "Bundle verification failed ($bad files). Download the bundle again." }
Write-Host "Bundle verification passed"

$command = Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue
if ($null -ne $command -and $HealthPort -gt 0) {
  $listeners = @(Get-NetTCPConnection -LocalPort $HealthPort -State Listen -ErrorAction SilentlyContinue)
  if ($listeners.Count -gt 0) { throw "Port $HealthPort is in use. Stop QOLONY before updating." }
}

if (-not (Test-Path -LiteralPath $dataPath)) {
  New-Item -ItemType Directory -Path $dataPath | Out-Null
}

# Move site settings out of the old app folder (only when the data folder has none yet).
$dataSettings = Join-Path $dataPath "settings.yaml"
$oldSettings = Join-Path $targetPath "backend\config\settings.yaml"
if (-not (Test-Path -LiteralPath $dataSettings) -and (Test-Path -LiteralPath $oldSettings)) {
  Copy-Item -LiteralPath $oldSettings -Destination $dataSettings
  Write-Host "Kept your settings: $dataSettings"
}
$dataEnv = Join-Path $dataPath "qolony_env.bat"
$oldStart = Join-Path $targetPath "start_qolony.bat"
if (-not (Test-Path -LiteralPath $dataEnv) -and (Test-Path -LiteralPath $oldStart)) {
  $lines = @(Get-Content -LiteralPath $oldStart | Where-Object {
      $_ -match '^\s*set "QOLONY_' -and $_ -notmatch 'QOLONY_SKIP_FRONTEND_BUILD' -and $_ -notmatch 'QOLONY_SETTINGS_PATH'
    })
  if ($lines.Count -gt 0) {
    $content = @("@rem Site settings for QOLONY. Kept outside the app folder so updates do not overwrite them.") + $lines
    Set-Content -LiteralPath $dataEnv -Value $content -Encoding ASCII
    Write-Host "Kept your start options: $dataEnv"
  }
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stage = "$targetPath.installing_$stamp"
$backup = "$targetPath.backup_$stamp"
New-Item -ItemType Directory -Path $stage | Out-Null
try {
  foreach ($item in Get-ChildItem -LiteralPath $bundleRoot) {
    Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $stage $item.Name) -Recurse -Force
  }
  # Company-specific iQUAVIS link targets live in the app folder; carry them over unchanged.
  $oldLinks = Join-Path $targetPath "backend\data\iquavis_link_targets.xlsx"
  if (Test-Path -LiteralPath $oldLinks) {
    $newData = Join-Path $stage "backend\data"
    if (-not (Test-Path -LiteralPath $newData)) { New-Item -ItemType Directory -Path $newData | Out-Null }
    Copy-Item -LiteralPath $oldLinks -Destination (Join-Path $newData "iquavis_link_targets.xlsx") -Force
    Write-Host "Kept iQUAVIS link targets"
  }
  $moved = $false
  try {
    if (Test-Path -LiteralPath $targetPath) {
      Move-Item -LiteralPath $targetPath -Destination $backup
      $moved = $true
    }
    Move-Item -LiteralPath $stage -Destination $targetPath
  } catch {
    if (-not (Test-Path -LiteralPath $targetPath) -and $moved) { Move-Item -LiteralPath $backup -Destination $targetPath }
    throw
  }
} catch {
  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
  throw
}

Write-Host "QOLONY was installed: $targetPath"
if (Test-Path -LiteralPath $backup) { Write-Host "Previous app: $backup" }
Write-Host "Data folder was not changed except for added settings: $dataPath"
Write-Host "Start with: $targetPath\start_qolony.bat"
