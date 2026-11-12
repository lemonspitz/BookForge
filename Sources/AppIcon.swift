import AppKit

/// The app icon (orange book), drawn at every size build.sh needs. `BookForge --icon <dir>` writes
/// icon_<size>.png files into dir.
enum AppIcon {
    static func render(to dir: URL) {
        for size in [16, 32, 64, 128, 256, 512, 1024] {
            let s = CGFloat(size)
            let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                                       samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                       bytesPerRow: 0, bitsPerPixel: 0)!
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
            let inset = s * 0.1
            let rect = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
            let bg = NSBezierPath(roundedRect: rect, xRadius: s * 0.18, yRadius: s * 0.18)
            NSGradient(starting: NSColor(red: 0.98, green: 0.62, blue: 0.25, alpha: 1),
                       ending: NSColor(red: 0.80, green: 0.25, blue: 0.20, alpha: 1))!.draw(in: bg, angle: -90)
            let config = NSImage.SymbolConfiguration(pointSize: s * 0.45, weight: .semibold)
                .applying(.init(paletteColors: [.white]))
            if let sym = NSImage(systemSymbolName: "book.pages.fill", accessibilityDescription: nil)?
                .withSymbolConfiguration(config) {
                let sz = sym.size
                sym.draw(in: NSRect(x: (s - sz.width) / 2, y: (s - sz.height) / 2, width: sz.width, height: sz.height))
            }
            NSGraphicsContext.restoreGraphicsState()
            try! rep.representation(using: .png, properties: [:])!.write(to: dir.appendingPathComponent("icon_\(size).png"))
        }
    }
}
