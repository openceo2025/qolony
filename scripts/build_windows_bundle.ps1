param(
  [string]$RuntimeEnv = $env:CONDA_DEFAULT_ENV,
  [string]$OutputDir = "",
  [string]$ZipPath = "",
  [switch]$SkipFrontendBuild,
  [switch]$SkipRuntimePack,
  [string]$CondaPackPath = ""
)

# Builds a Windows bundle that runs without Python or Node installed:
#   runtime\        packed conda environment (python.exe and the backend dependencies)
#   backend\        application, migrations and default settings (no database)
#   frontend\dist\  built web UI
#   start_qolony.bat, update_qolony_app.ps1, 00_README_WINDOWS.md, SHA256SUMS.txt
# Site settings and the database stay in the data folder (default C:\QOLONY_DATA).
# This file must stay ASCII only (Windows PowerShell 5.1 reads BOM-less scripts as ANSI).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputDir)) { $OutputDir = Join-Path $root "deploy_windows" }
$out = [System.IO.Path]::GetFullPath($OutputDir)
if ($out.TrimEnd("\") -ieq $root.TrimEnd("\")) { throw "OutputDir must not be the repository root." }
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
New-Item -ItemType Directory -Path $out | Out-Null

if (-not $SkipFrontendBuild) {
  Write-Host "frontend: npm ci + build"
  Push-Location (Join-Path $root "frontend")
  try {
    & npm ci --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
  } finally {
    Pop-Location
  }
}
$dist = Join-Path $root "frontend\dist\index.html"
if (-not (Test-Path -LiteralPath $dist)) { throw "frontend\dist is missing. Build the frontend first." }

Write-Host "copy: backend"
$backendOut = Join-Path $out "backend"
New-Item -ItemType Directory -Path $backendOut | Out-Null
foreach ($name in @("app", "alembic")) {
  Copy-Item -LiteralPath (Join-Path $root "backend\$name") -Destination (Join-Path $backendOut $name) -Recurse
}
foreach ($name in @("alembic.ini", "requirements.txt")) {
  Copy-Item -LiteralPath (Join-Path $root "backend\$name") -Destination (Join-Path $backendOut $name)
}
$configOut = Join-Path $backendOut "config"
New-Item -ItemType Directory -Path $configOut | Out-Null
foreach ($name in @("settings.yaml", "calendar.yaml", "split_templates.yaml")) {
  Copy-Item -LiteralPath (Join-Path $root "backend\config\$name") -Destination (Join-Path $configOut $name)
}
Copy-Item -LiteralPath (Join-Path $root "backend\config\i18n") -Destination (Join-Path $configOut "i18n") -Recurse
New-Item -ItemType Directory -Path (Join-Path $backendOut "data") | Out-Null
Get-ChildItem -LiteralPath $backendOut -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Write-Host "copy: frontend\dist"
New-Item -ItemType Directory -Path (Join-Path $out "frontend") | Out-Null
Copy-Item -LiteralPath (Join-Path $root "frontend\dist") -Destination (Join-Path $out "frontend\dist") -Recurse

if (-not $SkipRuntimePack) {
  if ([string]::IsNullOrWhiteSpace($RuntimeEnv)) { throw "Pass -RuntimeEnv <conda env name> or run from an activated conda env." }
  $packPath = $null
  if (-not [string]::IsNullOrWhiteSpace($CondaPackPath) -and (Test-Path -LiteralPath $CondaPackPath)) { $packPath = $CondaPackPath }
  if ($null -eq $packPath) {
    $condaPack = Get-Command conda-pack -ErrorAction SilentlyContinue
    if ($null -ne $condaPack) { $packPath = $condaPack.Source }
  }
  if ($null -eq $packPath) {
    $bases = @()
    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if ($null -ne $conda) { $bases += (& conda info --base 2>$null | Select-Object -First 1) }
    foreach ($name in @("anaconda3", "miniconda3", "miniforge3")) {
      $bases += Join-Path $env:USERPROFILE $name
      $bases += Join-Path $env:ProgramData $name
    }
    foreach ($base in $bases) {
      if ([string]::IsNullOrWhiteSpace($base)) { continue }
      $candidate = Join-Path $base "envs\$RuntimeEnv\Scripts\conda-pack.exe"
      if (Test-Path -LiteralPath $candidate) { $packPath = $candidate; break }
    }
  }
  if ($null -eq $packPath) { throw "conda-pack was not found. Install it into the env: conda install -n $RuntimeEnv -c conda-forge conda-pack" }
  $runtimeZip = Join-Path $out "runtime.zip"
  Write-Host "runtime: conda-pack $RuntimeEnv"
  # conda-pack -n needs conda on PATH. When conda-pack lives inside the env, pass the env folder instead.
  $envPrefix = Split-Path -Parent (Split-Path -Parent $packPath)
  if ((Split-Path -Leaf $envPrefix) -ieq $RuntimeEnv -and (Test-Path -LiteralPath (Join-Path $envPrefix "python.exe"))) {
    & $packPath -p $envPrefix -o $runtimeZip --format zip --ignore-editable-packages
  } else {
    & $packPath -n $RuntimeEnv -o $runtimeZip --format zip --ignore-editable-packages
  }
  if ($LASTEXITCODE -ne 0) { throw "conda-pack failed" }
  Expand-Archive -LiteralPath $runtimeZip -DestinationPath (Join-Path $out "runtime") -Force
  Remove-Item -LiteralPath $runtimeZip -Force
  if (-not (Test-Path -LiteralPath (Join-Path $out "runtime\python.exe"))) { throw "runtime\python.exe is missing after packing." }

  # Strip files that record the build machine's absolute paths (user name, local folders).
  # QOLONY starts with "python -m uvicorn", so entry-point launchers and caches are not needed.
  $runtimeDir = Join-Path $out "runtime"
  Get-ChildItem -LiteralPath $runtimeDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
  $condaMeta = Join-Path $runtimeDir "conda-meta"
  if (Test-Path -LiteralPath $condaMeta) { Remove-Item -LiteralPath $condaMeta -Recurse -Force }
  $scriptsDir = Join-Path $runtimeDir "Scripts"
  if (Test-Path -LiteralPath $scriptsDir) {
    Get-ChildItem -LiteralPath $scriptsDir -File |
      Where-Object { $_.Extension -ieq ".exe" -or $_.Name -like "conda-unpack*" -or $_.Name -like "conda_unpack*" } |
      Remove-Item -Force
  }
  # Editable (development) installs point at folders on the build machine.
  $sitePackages = Join-Path $runtimeDir "Lib\site-packages"
  foreach ($pth in @(Get-ChildItem -LiteralPath $sitePackages -File -Filter "*.pth")) {
    $text = Get-Content -LiteralPath $pth.FullName -Raw
    if ($text -match '[A-Za-z]:[\\/]') { Remove-Item -LiteralPath $pth.FullName -Force }
  }
  foreach ($info in @(Get-ChildItem -LiteralPath $sitePackages -Directory -Filter "*.dist-info")) {
    $direct = Join-Path $info.FullName "direct_url.json"
    if ((Test-Path -LiteralPath $direct) -and ((Get-Content -LiteralPath $direct -Raw) -match '"editable"\s*:\s*true')) {
      Remove-Item -LiteralPath $info.FullName -Recurse -Force
    }
  }
}

# Refuse to ship anything that still contains this machine's user folder.
$needles = @(('\Users\' + $env:USERNAME), ('/Users/' + $env:USERNAME))
$latin1 = [System.Text.Encoding]::GetEncoding(28591)
$leaks = @()
foreach ($file in Get-ChildItem -LiteralPath $out -Recurse -File) {
  if ($file.Length -gt 64MB) { continue }
  $content = $latin1.GetString([System.IO.File]::ReadAllBytes($file.FullName))
  foreach ($needle in $needles) {
    if ($content.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $leaks += $file.FullName; break }
  }
}
if ($leaks.Count -gt 0) {
  $leaks | Select-Object -First 20 | ForEach-Object { Write-Warning "contains a local user path: $_" }
  throw "The bundle still contains this machine's user folder ($($leaks.Count) files)."
}

$startBat = @'
@echo off
setlocal
rem QOLONY starter. Site settings live in the data folder so that updates never overwrite them.
if not defined QOLONY_DATA_DIR set "QOLONY_DATA_DIR=C:\QOLONY_DATA"
if not exist "%QOLONY_DATA_DIR%" mkdir "%QOLONY_DATA_DIR%"
if exist "%QOLONY_DATA_DIR%\qolony_env.bat" call "%QOLONY_DATA_DIR%\qolony_env.bat"
if not defined QOLONY_SETTINGS_PATH if exist "%QOLONY_DATA_DIR%\settings.yaml" set "QOLONY_SETTINGS_PATH=%QOLONY_DATA_DIR%\settings.yaml"
if not defined QOLONY_DB_PATH set "QOLONY_DB_PATH=%QOLONY_DATA_DIR%\qolony.db"
if not defined QOLONY_PORT set "QOLONY_PORT=8000"
set "QOLONY_SKIP_FRONTEND_BUILD=1"
cd /d "%~dp0backend"
echo QOLONY: http://localhost:%QOLONY_PORT%/
echo data: %QOLONY_DB_PATH%
"%~dp0runtime\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port %QOLONY_PORT%
pause
'@
Set-Content -LiteralPath (Join-Path $out "start_qolony.bat") -Value $startBat -Encoding ASCII
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "windows\update_qolony_app.ps1") -Destination (Join-Path $out "update_qolony_app.ps1")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "windows\00_README_WINDOWS.md") -Destination (Join-Path $out "00_README_WINDOWS.md")

$commit = "unknown"
$git = Get-Command git -ErrorAction SilentlyContinue
if ($null -ne $git) {
  $value = @(& git -C $root rev-parse --short HEAD 2>$null)
  if ($LASTEXITCODE -eq 0 -and $value.Count -gt 0) { $commit = [string]$value[0] }
}
@(
  "Product=QOLONY",
  "Commit=$commit",
  "BuiltAt=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
  "Runtime=$RuntimeEnv",
  "DatabaseIncluded=No",
  "SiteSettingsIncluded=No"
) | Set-Content -LiteralPath (Join-Path $out "BUILD_INFO.txt") -Encoding ASCII

Write-Host "checksums"
$prefix = $out.TrimEnd("\") + "\"
$sums = Get-ChildItem -LiteralPath $out -Recurse -File |
  Where-Object { $_.Name -ne "SHA256SUMS.txt" -and $_.Name -ne "update_qolony_app.ps1" } |
  Sort-Object FullName |
  ForEach-Object {
    $relative = $_.FullName.Substring($prefix.Length) -replace "\\", "/"
    "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relative
  }
Set-Content -LiteralPath (Join-Path $out "SHA256SUMS.txt") -Value $sums -Encoding ASCII

if (-not [string]::IsNullOrWhiteSpace($ZipPath)) {
  $zip = [System.IO.Path]::GetFullPath($ZipPath)
  if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
  Write-Host "zip: $zip"
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::CreateFromDirectory($out, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
  $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
  Set-Content -LiteralPath "$zip.sha256.txt" -Value "$hash  $(Split-Path -Leaf $zip)" -Encoding ASCII
  Write-Host "sha256: $hash"
}
Write-Host "bundle: $out"
