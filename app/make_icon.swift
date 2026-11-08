import AppKit
let size = CGFloat(1024)
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()
let rect = NSRect(x: 40, y: 40, width: size - 80, height: size - 80)
let path = NSBezierPath(roundedRect: rect, xRadius: 220, yRadius: 220)
NSGradient(colors: [NSColor(calibratedRed: 0.20, green: 0.24, blue: 0.40, alpha: 1),
                    NSColor(calibratedRed: 0.07, green: 0.08, blue: 0.16, alpha: 1)])!.draw(in: path, angle: -90)
let cfg = NSImage.SymbolConfiguration(pointSize: 430, weight: .semibold)
if let sym = NSImage(systemSymbolName: "power", accessibilityDescription: nil)?.withSymbolConfiguration(cfg) {
    let tinted = NSImage(size: sym.size)
    tinted.lockFocus()
    sym.draw(at: .zero, from: .zero, operation: .sourceOver, fraction: 1)
    NSColor(calibratedRed: 0.45, green: 0.85, blue: 1.0, alpha: 1).set()
    NSRect(origin: .zero, size: sym.size).fill(using: .sourceAtop)
    tinted.unlockFocus()
    tinted.draw(at: NSPoint(x: (size - sym.size.width) / 2, y: (size - sym.size.height) / 2),
                from: .zero, operation: .sourceOver, fraction: 1)
}
img.unlockFocus()
let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
