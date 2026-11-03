import AppKit
import PDFKit

// `BookForge --icon <dir>` renders the app icon PNGs for build.sh; otherwise start the app.
if let i = CommandLine.arguments.firstIndex(of: "--icon"), i + 1 < CommandLine.arguments.count {
    let dir = URL(fileURLWithPath: CommandLine.arguments[i + 1])
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
    exit(0)
}

// `BookForge --convert <in.pdf> <out.epub> [id|en]` converts without the window.
if let i = CommandLine.arguments.firstIndex(of: "--convert"), i + 2 < CommandLine.arguments.count {
    let args = CommandLine.arguments
    let src = URL(fileURLWithPath: args[i + 1]), out = URL(fileURLWithPath: args[i + 2])
    let lang = i + 3 < args.count ? args[i + 3] : "id"
    let languages = lang == "id" ? ["id-ID", "en-US"] : ["en-US"]
    let sem = DispatchSemaphore(value: 0)
    Task.detached {
        guard let doc = PDFDocument(url: src) else { print("cannot open PDF"); exit(1) }
        let guess = BookJob.guessFromFilename(src)
        let attrs = doc.documentAttributes ?? [:]
        let info = BookInfo(title: attrs[PDFDocumentAttribute.titleAttribute] as? String ?? guess.title,
                            author: attrs[PDFDocumentAttribute.authorAttribute] as? String ?? guess.author, language: lang)
        let pages = await PDFReader.read(doc, languages: languages, forceOCR: false) { _ in }
        var layout = Layout.build(pages: pages, doc: doc, languages: languages)
        layout.blocks = TextFixer(blocks: layout.blocks).finalize(layout.blocks)
        do {
            try EPUBWriter.write(layout: layout, doc: doc, info: info, to: out)
        } catch {
            print("error: \(error.localizedDescription)"); exit(1)
        }
        let headings = layout.blocks.compactMap { if case .heading(let h) = $0 { h } else { nil } }
        print("body pages \(layout.bodyStart + 1)-\(layout.bodyEnd + 1), chapters: \(headings.joined(separator: " | "))")
        sem.signal()
    }
    sem.wait()
    exit(0)
}

// `BookForge --translate <in.epub> <out.epub> <from> <to> [apple|claude]` translates an EPUB without the window.
// The Claude engine reads the key from ANTHROPIC_API_KEY, else the Keychain.
if let i = CommandLine.arguments.firstIndex(of: "--translate"), i + 4 < CommandLine.arguments.count {
    let a = CommandLine.arguments
    let engine = i + 5 < a.count ? Translator.Engine(rawValue: a[i + 5]) ?? .apple : .apple
    let key = ProcessInfo.processInfo.environment["ANTHROPIC_API_KEY"] ?? KeychainKey.load()
    Task.detached {
        do {
            let r = try await Translator.translate(epub: URL(fileURLWithPath: a[i + 1]), from: a[i + 3], to: a[i + 4],
                                                   output: URL(fileURLWithPath: a[i + 2]), engine: engine, apiKey: key) { line, _ in
                print(line)
            }
            print("translated \(r.paragraphs) paragraphs, fallbacks \(r.fellBack), cost \(r.cost.map { String(format: "$%.2f", $0) } ?? "free")")
            if !r.styleSheet.isEmpty { print(r.styleSheet) }
        } catch {
            print("error: \(error.localizedDescription)"); exit(1)
        }
        exit(0)
    }
    dispatchMain()
}

// `BookForge --send <book.epub>` runs only the Kindle step, for troubleshooting.
if let i = CommandLine.arguments.firstIndex(of: "--send"), i + 1 < CommandLine.arguments.count {
    do {
        switch try Kindle.send(epub: URL(fileURLWithPath: CommandLine.arguments[i + 1]), log: { print($0) }) {
        case .sent(let path, let thumb): print("sent to \(path), cover thumbnail: \(thumb)")
        case .noDevice: print("no Kindle found")
        case .busy: print("Kindle busy (close Calibre)")
        }
    } catch {
        print("error: \(error.localizedDescription)")
        exit(1)
    }
    exit(0)
}

BookForgeApp.main()
