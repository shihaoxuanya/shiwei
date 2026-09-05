param(
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workerRoot = Join-Path $projectRoot "services\ai-worker"
$distributionRoot = Join-Path $workerRoot "dist\shiwei-ai-worker"
$distributionExe = Join-Path $distributionRoot "shiwei-ai-worker.exe"
$binaryRoot = Join-Path $projectRoot "apps\desktop\src-tauri\binaries"
$targetExe = Join-Path $binaryRoot "shiwei-ai-worker-x86_64-pc-windows-msvc.exe"
$targetRuntime = Join-Path $binaryRoot "_internal"
$sourceRuntime = Join-Path $distributionRoot "_internal"

if (-not $binaryRoot.StartsWith($projectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to write outside the workspace: $binaryRoot"
}

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $SkipBuild) {
  if ($null -eq $uvCommand) {
    throw "uv was not found on PATH."
  }

  Push-Location $workerRoot
  try {
    & $uvCommand.Source run pyinstaller --noconfirm shiwei-ai.spec
    if ($LASTEXITCODE -ne 0) {
      throw "Python Worker packaging failed with exit code $LASTEXITCODE."
    }
  }
  finally {
    Pop-Location
  }
}

if (-not (Test-Path -LiteralPath $distributionExe -PathType Leaf)) {
  throw "Worker executable is missing: $distributionExe"
}
if (-not (Test-Path -LiteralPath $sourceRuntime -PathType Container)) {
  throw "Worker runtime directory is missing: $sourceRuntime"
}

New-Item -ItemType Directory -Path $binaryRoot -Force | Out-Null
if (Test-Path -LiteralPath $targetRuntime) {
  $resolvedTarget = (Resolve-Path -LiteralPath $targetRuntime).Path
  if (-not $resolvedTarget.StartsWith($binaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to delete outside the sidecar staging directory: $resolvedTarget"
  }
  Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
}

Copy-Item -LiteralPath $distributionExe -Destination $targetExe -Force
Copy-Item -LiteralPath $sourceRuntime -Destination $targetRuntime -Recurse -Force

Write-Host "Worker packaged and staged for Tauri: $targetExe"
