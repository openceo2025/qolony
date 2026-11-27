param(
  [Parameter(Mandatory = $true)]
  [string]$WorkbookPath,
  [int]$ExpectedProjectCount = 3,
  [int]$TimeoutSeconds = 900,
  [switch]$UseEnvironmentCredentials
)

# QOLONY reads this script's output as UTF-8. Windows PowerShell 5.1 (the one on the
# company PCs) writes in the OEM code page (cp932) by default, which turned every
# Japanese error and sheet name into mojibake in the QOLONY event bar.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$ErrorActionPreference = "Stop"

function Write-DebugLog([string]$Json) {
  # The nightly run is unattended, so the only way to see why a save failed is a file.
  # Keep it next to the workbook so it travels with the operational data.
  try {
    $logDir = Join-Path (Split-Path -Parent $WorkbookPath) "qolony_logs"
    if (-not (Test-Path -LiteralPath $logDir)) {
      New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $logPath = Join-Path $logDir "excel_server_save_$stamp.json"
    [System.IO.File]::WriteAllText($logPath, $Json, [System.Text.Encoding]::UTF8)
    # Keep the last 30 runs; unbounded logs would fill the CAD terminal.
    Get-ChildItem -LiteralPath $logDir -Filter "excel_server_save_*.json" |
      Sort-Object LastWriteTime -Descending |
      Select-Object -Skip 30 |
      ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
  } catch {
    # Logging must never break the save itself.
  }
}

function Get-CountSum([object[]]$Projects, [string]$Key) {
  # Do not use Measure-Object -Property here. In Windows PowerShell 5.1 it cannot read
  # the keys of [ordered]@{} (PowerShell 7 can), so the totals threw after Excel had
  # already sent everything to the server, and the run was reported as a failure.
  # A negative count means "could not read it" and is left out of the sum.
  $sum = 0
  foreach ($project in @($Projects)) {
    $value = $project.$Key
    if ($null -ne $value -and [int]$value -ge 0) {
      $sum += [int]$value
    }
  }
  return $sum
}

function Write-Result([string]$Status, [string]$Message, [object[]]$Projects) {
  [ordered]@{
    status = $Status
    message = $Message
    expected_project_count = $ExpectedProjectCount
    project_count = @($Projects).Count
    sent_count = Get-CountSum $Projects "total_count"
    success_count = Get-CountSum $Projects "success_count"
    failure_count = Get-CountSum $Projects "failure_count"
    unknown_summary_count = @($Projects | Where-Object { $_.total_count -lt 0 }).Count
    projects = @($Projects)
  } | ConvertTo-Json -Compress -Depth 8 | ForEach-Object {
    Write-DebugLog $_
    $_
  }
}

function Get-CellText($Worksheet, [int]$Column) {
  return ([string]$Worksheet.Cells.Item(1, $Column).Value2).Trim()
}

function Test-AllTasksWorksheet($Worksheet) {
  for ($column = 1; $column -le 10; $column++) {
    if (-not [string]::Equals((Get-CellText $Worksheet $column), "taskdomain$column", [System.StringComparison]::OrdinalIgnoreCase)) {
      return $false
    }
  }
  $usedRange = $Worksheet.UsedRange
  $lastColumn = [int]$usedRange.Column + [int]$usedRange.Columns.Count - 1
  for ($column = 11; $column -le $lastColumn; $column++) {
    if ([string]::Equals((Get-CellText $Worksheet $column), "Id", [System.StringComparison]::OrdinalIgnoreCase)) {
      return $true
    }
  }
  return $false
}

function Release-ComObject($Value) {
  if ($null -ne $Value -and [System.Runtime.InteropServices.Marshal]::IsComObject($Value)) {
    [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
  }
}

$excel = $null
$workbook = $null
$worksheets = @()
$results = New-Object System.Collections.Generic.List[object]
$status = "failed"
$message = "Excel automation failed"
$finishMacro = $null

try {
  if (-not $UseEnvironmentCredentials) {
    throw "Automation credentials must be supplied through environment variables"
  }
  # Credentials live in the workbook's login-info sheet (B1/B2), not in QOLONY settings.
  # Empty values are passed through on purpose; the VBA reads the sheet and raises a
  # clear error when both the sheet and these values are empty.
  $username = [string]$env:QOLONY_EXCEL_AUTOMATION_USERNAME
  $password = [string]$env:QOLONY_EXCEL_AUTOMATION_PASSWORD
  if (-not (Test-Path -LiteralPath $WorkbookPath -PathType Leaf)) {
    throw "Editor workbook was not found: $WorkbookPath"
  }
  if ($ExpectedProjectCount -le 0) {
    throw "ExpectedProjectCount must be positive"
  }
  if (@(Get-Process -Name EXCEL -ErrorAction SilentlyContinue).Count -gt 0) {
    throw "Excel is already running. Close Excel completely and retry"
  }

  $excel = New-Object -ComObject Excel.Application
  $excel.Visible = $false
  $excel.DisplayAlerts = $false
  $excel.ScreenUpdating = $false
  $workbook = $excel.Workbooks.Open($WorkbookPath, 0, $false)
  if ($workbook.ReadOnly) {
    throw "Editor workbook was opened read-only"
  }

  foreach ($worksheet in @($workbook.Worksheets)) {
    if (Test-AllTasksWorksheet $worksheet) {
      $worksheets += $worksheet
    }
  }
  if ($worksheets.Count -ne $ExpectedProjectCount) {
    $names = (@($worksheets | ForEach-Object { $_.Name }) -join ", ")
    throw "All-tasks worksheet count mismatch. Expected=$ExpectedProjectCount Actual=$($worksheets.Count) [$names]"
  }

  $workbookName = $workbook.Name.Replace("'", "''")
  $macroPrefix = "'$workbookName'!modQolonyExcelAutomation."
  $finishMacro = $macroPrefix + "FinishAutomation"
  [void]$excel.Run($macroPrefix + "PrepareForAutomation", $username, $password)

  foreach ($worksheet in $worksheets) {
    $projectName = [string]$worksheet.Name
    try {
      [void]$worksheet.Activate()
      $summary = [string]$excel.Run($macroPrefix + "SyncActiveAllTasksSheetAutomation")
      $failureCount = 0
      if ($summary -match "Failure:\s*(\d+)") {
        $failureCount = [int]$Matches[1]
      }
      # "Total"/"Success" also matter: without them, "sent nothing" looks the same as
      # "everything succeeded", which is how a silent no-op got reported as a good save.
      $totalCount = -1
      if ($summary -match "Total:\s*(\d+)") {
        $totalCount = [int]$Matches[1]
      }
      $successCount = -1
      if ($summary -match "Success:\s*(\d+)") {
        $successCount = [int]$Matches[1]
      }
      $hadAutomationError = [bool]$excel.Run($macroPrefix + "AutomationHadError")
      $projectStatus = if ($failureCount -eq 0 -and -not $hadAutomationError) { "ok" } else { "failed" }
      $results.Add([ordered]@{
          sheet = $projectName
          status = $projectStatus
          summary = $summary.Substring(0, [Math]::Min(500, $summary.Length))
          total_count = $totalCount
          success_count = $successCount
          failure_count = $failureCount
          automation_error = $hadAutomationError
        })
    } catch {
      $results.Add([ordered]@{
          sheet = $projectName
          status = "failed"
          summary = $_.Exception.Message.Substring(0, [Math]::Min(500, $_.Exception.Message.Length))
          total_count = -1
          success_count = -1
          failure_count = 1
          automation_error = $true
        })
    }
  }

  $hasFailures = @($results | Where-Object { $_.status -ne "ok" }).Count -gt 0
  $workbook.Save()
  if ($hasFailures) {
    throw "One or more project server saves failed"
  }
  $status = "ok"
  $message = "All project server saves and workbook save completed"
} catch {
  $message = $_.Exception.Message
} finally {
  if ($null -ne $excel) {
    try {
      if ($null -ne $workbook) {
        try {
          if ($null -ne $finishMacro) {
            [void]$excel.Run($finishMacro)
          }
        } catch {
        }
        $workbook.Close($false)
      }
    } catch {
    }
    try {
      $excel.Quit()
    } catch {
    }
  }
  Release-ComObject $workbook
  foreach ($worksheet in $worksheets) {
    Release-ComObject $worksheet
  }
  Release-ComObject $excel
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}

try {
  Write-Result $status $message $results
} catch {
  # Excel has already run by now. If the summary itself breaks, say so in the result
  # instead of dying with an empty stdout, which hid whether anything had been sent.
  [ordered]@{
    status = "failed"
    message = "Excel finished (status=$($status), $($message)) but the result summary failed: " + $_.Exception.Message
    project_count = @($results).Count
  } | ConvertTo-Json -Compress
  exit 1
}
if ($status -eq "ok") {
  exit 0
}
exit 1
