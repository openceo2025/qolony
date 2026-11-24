$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $root "frontend"
$frontendSrcDir = Join-Path $frontendDir "src"
$frontendDistIndex = Join-Path $frontendDir "dist/index.html"
$forceFrontendBuild = $env:QOLONY_FORCE_FRONTEND_BUILD -eq "1"

function Get-NeedsFrontendBuild {
  param(
    [string]$DistIndexPath,
    [string]$SrcDirPath,
    [bool]$ForceBuild
  )

  if ($ForceBuild) {
    return $true
  }

  if (-not (Test-Path $DistIndexPath)) {
    return $true
  }

  if (-not (Test-Path $SrcDirPath)) {
    return $false
  }

  $distMtime = (Get-Item $DistIndexPath).LastWriteTimeUtc
  $latestSource =
    Get-ChildItem -Path $SrcDirPath -Recurse -File |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

  if ($null -eq $latestSource) {
    return $false
  }

  return $latestSource.LastWriteTimeUtc -gt $distMtime
}

Write-Host "qolony root: $root"

$needsFrontendBuild = $false
if ($env:QOLONY_SKIP_FRONTEND_BUILD -ne "1") {
  $needsFrontendBuild = Get-NeedsFrontendBuild -DistIndexPath $frontendDistIndex -SrcDirPath $frontendSrcDir -ForceBuild $forceFrontendBuild
}

if ($needsFrontendBuild) {
  Write-Host "frontend build: running"
  Set-Location $frontendDir
  & npm install --no-fund --no-audit
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  & npm run build
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} elseif ($env:QOLONY_SKIP_FRONTEND_BUILD -eq "1") {
  Write-Host "frontend build: skipped by QOLONY_SKIP_FRONTEND_BUILD=1"
} else {
  Write-Host "frontend build: skip (dist is up-to-date)"
}

Set-Location (Join-Path $root "backend")
Write-Host "QOLONY URL: http://localhost:8000/"

if ($env:QOLONY_RELOAD -eq "1") {
  & python -m uvicorn "app.main:app" --host 0.0.0.0 --port 8000 --reload --reload-exclude "data/*"
  exit $LASTEXITCODE
}

& python -m uvicorn "app.main:app" --host 0.0.0.0 --port 8000
exit $LASTEXITCODE
