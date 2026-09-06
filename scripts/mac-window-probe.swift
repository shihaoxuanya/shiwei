// Inspect only the synthetic smoke-test application's PID, not user screen content.
import CoreGraphics
import Foundation

guard CommandLine.arguments.count == 2, let pid = Int32(CommandLine.arguments[1]) else { exit(2) }
let windows = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] ?? []
let count = windows.filter { info in
    let bounds = info[kCGWindowBounds as String] as? [String: Any]
    let width = (bounds?["Width"] as? NSNumber)?.doubleValue ?? 0
    return (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid &&
    (info[kCGWindowLayer as String] as? Int) == 0 &&
    width > 100
}.count
print(count)
