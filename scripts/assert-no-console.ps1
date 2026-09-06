param([Parameter(Mandatory=$true)][string]$ProcessIds, [Parameter(Mandatory=$true)][string]$ReportPath)
$ErrorActionPreference = 'Stop'
# Run only in a disposable child PowerShell. Detaching this helper's console
# never closes the console of the caller or the application under inspection.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ShiweiConsoleProbe {
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool FreeConsole();
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool AttachConsole(uint processId);
  [DllImport("kernel32.dll")]
  public static extern IntPtr GetConsoleWindow();
  public static int Probe(uint processId, out bool windowExists) {
    windowExists = false;
    FreeConsole();
    if (AttachConsole(processId)) {
      windowExists = GetConsoleWindow() != IntPtr.Zero;
      FreeConsole();
      return 0;
    }
    return Marshal.GetLastWin32Error();
  }
}
'@
$taskResults = @()
foreach ($taskId in $ProcessIds.Split(',')) {
  $taskProcessId = [uint32]::Parse($taskId)
  $taskWindowExists = $false
  $taskErrorCode = [ShiweiConsoleProbe]::Probe($taskProcessId, [ref]$taskWindowExists)
  $taskAlive = $null -ne (Get-Process -Id $taskProcessId -ErrorAction SilentlyContinue)
  $taskResults += @{ processId = $taskProcessId; attachErrorCode = $taskErrorCode; consoleWindowPresent = $taskWindowExists; alive = $taskAlive }
  # ERROR_INVALID_HANDLE means no console. CREATE_NO_WINDOW can also retain a
  # headless console object (AttachConsole succeeds, GetConsoleWindow is null).
  # Both are valid; a merely hidden window, access denied or a dead PID is not.
}
# Detaching a PowerShell console can invalidate its standard handles. Keep the
# machine-readable result in the caller's isolated QA directory, not stdout.
[IO.File]::WriteAllText($ReportPath, (ConvertTo-Json -InputObject @($taskResults)))
if (@($taskResults | Where-Object { $_.attachErrorCode -notin @(0, 6) -or $_.consoleWindowPresent -or -not $_.alive }).Count) { exit 1 }
exit 0
