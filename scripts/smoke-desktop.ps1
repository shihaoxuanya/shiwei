param([string]$Executable = "apps/desktop/src-tauri/target/release/shiwei-desktop.exe")
$ErrorActionPreference = "Stop"
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$taskExe = (Resolve-Path (Join-Path $taskRoot $Executable)).Path
$expectedVersion = (Get-Content -LiteralPath (Join-Path $taskRoot 'VERSION') -Raw).Trim()
$taskSandbox = Join-Path $taskRoot ("output\qa-release\desktop-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $taskSandbox | Out-Null
$savedData = $env:SHIWEI_DATA_DIR
$savedWebView = $env:WEBVIEW2_USER_DATA_FOLDER
$savedRoaming = $env:APPDATA
$savedLocal = $env:LOCALAPPDATA
$savedTelemetry = $env:SHIWEI_TELEMETRY_DISABLED
$taskProcess = $null
$taskChildren = @()
$expectedTitle = -join ([char]0x62FE, [char]0x5FAE)
try {
  $env:SHIWEI_TELEMETRY_DISABLED = '1'
  $env:SHIWEI_DATA_DIR = Join-Path $taskSandbox "library"
  $env:WEBVIEW2_USER_DATA_FOLDER = Join-Path $taskSandbox "webview"
  $env:APPDATA = Join-Path $taskSandbox "roaming"
  $env:LOCALAPPDATA = Join-Path $taskSandbox "local"
  New-Item -ItemType Directory -Path $env:APPDATA, $env:LOCALAPPDATA | Out-Null
  $taskProcess = Start-Process -FilePath $taskExe -WorkingDirectory (Split-Path $taskExe) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskSandbox "stdout.log") -RedirectStandardError (Join-Path $taskSandbox "stderr.log")
  for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Milliseconds 1000
    $taskProcess.Refresh()
    if ($taskProcess.HasExited) { throw "Isolated desktop exited before readiness" }
    $taskChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($taskProcess.Id)")
    if ($taskProcess.MainWindowTitle -eq $expectedTitle -and ($taskChildren.Name -contains "shiwei-ai-worker.exe")) { break }
  }
  if ($taskProcess.MainWindowTitle -ne $expectedTitle -or -not $taskProcess.Responding) { throw "Desktop did not become responsive" }
  if ($taskChildren.Name -notcontains "shiwei-ai-worker.exe") { throw "Packaged worker was not started" }
  $taskWorker = $taskChildren | Where-Object Name -eq "shiwei-ai-worker.exe" | Select-Object -First 1
  if ($taskWorker.ExecutablePath -ne (Join-Path (Split-Path $taskExe) "shiwei-ai-worker.exe")) { throw "Wrong worker executable" }
  $taskVersion = (Get-Item -LiteralPath $taskExe).VersionInfo
  if ($taskVersion.ProductVersion -ne $expectedVersion) { throw "Wrong desktop version" }
  [ordered]@{
    status = "passed"; productVersion = $taskVersion.ProductVersion
    windowTitle = $taskProcess.MainWindowTitle; responding = $taskProcess.Responding
    packagedWorkerStarted = $true; isolatedDataDirectory = $true
    webViewStarted = [bool]($taskChildren.Name -contains "msedgewebview2.exe")
    stdoutBytes = (Get-Item -LiteralPath (Join-Path $taskSandbox "stdout.log")).Length
    stderrBytes = (Get-Item -LiteralPath (Join-Path $taskSandbox "stderr.log")).Length
    scope = "native startup/lifecycle only; chat RPC is covered by smoke-chat-worker"
  } | ConvertTo-Json
}
finally {
  $env:SHIWEI_TELEMETRY_DISABLED = $savedTelemetry
  $env:SHIWEI_DATA_DIR = $savedData
  $env:WEBVIEW2_USER_DATA_FOLDER = $savedWebView
  $env:APPDATA = $savedRoaming
  $env:LOCALAPPDATA = $savedLocal
  # Only this test process and its captured children; never stop an installed app.
  if ($null -ne $taskProcess -and -not $taskProcess.HasExited) {
    $null = $taskProcess.CloseMainWindow()
    if (-not $taskProcess.WaitForExit(5000)) { Stop-Process -Id $taskProcess.Id -Force }
  }
  foreach ($child in $taskChildren) {
    $live = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.ProcessId)" -ErrorAction SilentlyContinue
    if ($null -ne $live -and $live.CreationDate -eq $child.CreationDate -and $live.ParentProcessId -eq $taskProcess.Id) {
      Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
}
