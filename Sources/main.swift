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

// `BookForge --send <book.epub|pdf>` sends one file to the USB e-reader, for troubleshooting.
// `BookForge --device` prints what Calibre sees on the connected e-reader.
if let i = CommandLine.arguments.firstIndex(of: "--send"), i + 1 < CommandLine.arguments.count {
    let url = URL(fileURLWithPath: CommandLine.arguments[i + 1])
    let meta = Library.readMeta(url)
    Task.detached {
        do {
            let item = SendItem(file: url, title: meta.title, author: meta.author, uuid: UUID().uuidString, language: meta.language)
            print(try await Devices.sendNow([item]) { print($0) })
        } catch {
            print("error: \(error.localizedDescription)"); exit(1)
        }
        exit(0)
    }
    dispatchMain()
}
if CommandLine.arguments.contains("--device") {
    Task.detached {
        do {
            let r = try await Devices.script("info")
            print("status \(r.status)\(r.error.map { ": \($0)" } ?? "")")
            if r.status == "ok" {
                let d = r.reader
                print("\(d.name) [\(d.kind), \(d.driver)] formats \(d.formats.joined(separator: ",")) free \(d.free) of \(d.total)")
                for b in d.books { print("  \(b.title) — \(b.authors.joined(separator: ", ")) (\(b.lpath)) uuid \(b.uuid)") }
            }
        } catch {
            print("error: \(error.localizedDescription)"); exit(1)
        }
        exit(0)
    }
    dispatchMain()
}

// `BookForge --import-new` and `--send-new` do what the device screen's one-click Import and Send buttons do.
// `BookForge --plan` shows what Import and Send would copy, without copying anything.
for flag in ["--import-new", "--send-new", "--plan"] where CommandLine.arguments.contains(flag) {
    Task { @MainActor in
        let devices = Devices.shared, library = Library.shared
        await library.loading?.value
        await devices.scan()
        guard let reader = devices.reader else { print("No e-reader: \(devices.state)"); exit(1) }
        let both = library.books.filter { devices.match($0) != nil }
        print("\(reader.name): \(both.count) in both, \(library.books.count - both.count) only in the library, \(devices.onlyOnReader.count) only on the reader")
        if flag == "--plan" {
            print("To import:", devices.toImport.map(\.displayTitle).joined(separator: " | ").nilIfEmpty ?? "nothing")
            print("To send:", devices.toSend.map(\.title).joined(separator: " | ").nilIfEmpty ?? "nothing")
            let locked = devices.onlyOnReader.filter { devices.likelyLocked($0) }
            print("Left on the reader (store books):", locked.count)
            exit(0)
        }
        let titles = flag == "--import-new" ? devices.toImport.map(\.displayTitle) : devices.toSend.map(\.title)
        print(flag == "--import-new" ? "To import:" : "To send:", titles.isEmpty ? "nothing" : titles.joined(separator: " | "))
        if flag == "--import-new" { await devices.importNew() } else { await devices.sendNew() }
        print(devices.message ?? "")
        exit(0)
    }
    dispatchMain()
}

BookForgeApp.main()

extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
