import AppKit
import PDFKit
import ImageIO

/// Where the reader left off: the spine item (or PDF page), how far into it, and how far into the whole book.
struct ReadingPosition: Codable, Hashable {
    var chapter: Int
    var fraction: Double
    var progress: Double
}

struct LibraryBook: Codable, Identifiable, Hashable {
    var id: String              // also stored on the e-reader, to recognise the book there
    var file: String            // file name inside the library folder
    var title: String
    var author: String
    var language: String        // "id", "en", "ja", or another two-letter code
    var added: Date
    var position: ReadingPosition?

    var format: String { (file as NSString).pathExtension.lowercased() }
}

/// What a book file says about itself.
struct BookMeta {
    var title: String
    var author: String
    var language: String
    var cover: Data?            // JPEG thumbnail
}

/// The catalogue: book files live in one folder next to `library.json` and a `.covers` folder of thumbnails.
@MainActor @Observable
final class Library {
    static let shared = Library()

    let folder: URL
    var books: [LibraryBook] = []
    var importing = false
    var message: String?
    private var covers: [String: NSImage] = [:]
    /// The first look through the folder, started at launch.
    @ObservationIgnored private(set) var loading: Task<Void, Never>?

    private var catalog: URL { folder.appendingPathComponent("library.json") }
    private var coverFolder: URL { folder.appendingPathComponent(".covers") }

    private init() {
        // BOOKFORGE_LIBRARY points testing runs at another folder
        folder = ProcessInfo.processInfo.environment["BOOKFORGE_LIBRARY"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Book Forge Library")
        try? FileManager.default.createDirectory(at: coverFolder, withIntermediateDirectories: true)
        if let data = try? Data(contentsOf: catalog) {
            let dec = JSONDecoder()
            dec.dateDecodingStrategy = .iso8601
            books = (try? dec.decode([LibraryBook].self, from: data)) ?? []
        }
        loading = Task { await rescan() }
    }

    func url(_ book: LibraryBook) -> URL { folder.appendingPathComponent(book.file) }
    func coverURL(_ book: LibraryBook) -> URL { coverFolder.appendingPathComponent(book.id + ".jpg") }
    func book(_ id: String) -> LibraryBook? { books.first { $0.id == id } }

    func cover(_ book: LibraryBook) -> NSImage? {
        if let c = covers[book.id] { return c }
        let img = NSImage(contentsOf: coverURL(book))
        covers[book.id] = img
        return img
    }

    /// Picks up book files put into the folder with Finder and forgets ones that were deleted there.
    func rescan() async {
        let before = books.count
        books.removeAll { !FileManager.default.fileExists(atPath: url($0).path) }
        let known = Set(books.map(\.file))
        let loose = ((try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? [])
            .filter { ["epub", "pdf"].contains($0.pathExtension.lowercased()) && !known.contains($0.lastPathComponent) }
        if !loose.isEmpty { _ = await add(loose, quiet: true) }
        else if books.count != before { save() }
    }

    /// Copies books (or every book inside dropped folders) into the library. Files already in the
    /// library folder are catalogued where they are. Returns the books added.
    @discardableResult
    func add(_ urls: [URL], quiet: Bool = false) async -> [LibraryBook] {
        let files = Self.expand(urls)
        guard !files.isEmpty else {
            if !quiet { message = "No EPUB or PDF files there." }
            return []
        }
        importing = true
        defer { importing = false }
        var added: [LibraryBook] = [], skipped = 0
        for (i, f) in files.enumerated() {
            if !quiet && files.count > 1 { message = "Adding \(i + 1) of \(files.count)…" }
            let meta = await Task.detached { Self.readMeta(f) }.value
            let ext = f.pathExtension.lowercased()
            let inFolder = f.deletingLastPathComponent().standardizedFileURL.path == folder.standardizedFileURL.path
            if !inFolder, books.contains(where: { $0.format == ext && Self.same($0.title, meta.title) && Self.same($0.author, meta.author) }) {
                skipped += 1
                continue
            }
            var book = LibraryBook(id: UUID().uuidString, file: f.lastPathComponent, title: meta.title,
                                   author: meta.author, language: meta.language, added: Date())
            if !inFolder {
                let dest = uniqueURL(base: Self.fileName(meta.title, meta.author), ext: ext)
                do {
                    try FileManager.default.copyItem(at: f, to: dest)
                } catch {
                    message = "Couldn't copy \(f.lastPathComponent): \(error.localizedDescription)"
                    continue
                }
                book.file = dest.lastPathComponent
            }
            if let c = meta.cover { try? c.write(to: coverURL(book)) }
            books.append(book)
            added.append(book)
        }
        save()
        if !quiet {
            var msg = added.count == 1 ? "Added “\(added[0].title)”." : "Added \(added.count) books."
            if skipped > 0 { msg += " \(skipped) \(skipped == 1 ? "was" : "were") already in the library." }
            message = msg
        }
        return added
    }

    /// A free file name in the library folder, for books Book Forge makes itself.
    func uniqueURL(base: String, ext: String) -> URL {
        var url = folder.appendingPathComponent("\(base).\(ext)")
        var n = 2
        while FileManager.default.fileExists(atPath: url.path) {
            url = folder.appendingPathComponent("\(base) \(n).\(ext)")
            n += 1
        }
        return url
    }

    /// Moves the books' files to the Trash.
    func remove(_ ids: Set<String>) {
        for b in books where ids.contains(b.id) {
            try? FileManager.default.trashItem(at: url(b), resultingItemURL: nil)
            try? FileManager.default.removeItem(at: coverURL(b))
            covers[b.id] = nil
        }
        books.removeAll { ids.contains($0.id) }
        save()
        message = ids.count == 1 ? "Moved the book to the Trash." : "Moved \(ids.count) books to the Trash."
    }

    func update(_ book: LibraryBook) {
        guard let i = books.firstIndex(where: { $0.id == book.id }) else { return }
        books[i] = book
        save()
    }

    private var pendingSave: Task<Void, Never>?

    /// Reading positions change on every page turn, so they're written a moment later.
    func setPosition(_ id: String, _ pos: ReadingPosition) {
        guard let i = books.firstIndex(where: { $0.id == id }), books[i].position != pos else { return }
        books[i].position = pos
        pendingSave?.cancel()
        pendingSave = Task {
            try? await Task.sleep(for: .seconds(1))
            if !Task.isCancelled { save() }
        }
    }

    func save() {
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        try? enc.encode(books).write(to: catalog, options: .atomic)
    }

    // MARK: - Reading book files

    nonisolated static func expand(_ urls: [URL]) -> [URL] {
        var out: [URL] = []
        for u in urls {
            var isDir: ObjCBool = false
            FileManager.default.fileExists(atPath: u.path, isDirectory: &isDir)
            if isDir.boolValue {
                let items = FileManager.default.enumerator(at: u, includingPropertiesForKeys: nil,
                                                           options: [.skipsHiddenFiles, .skipsPackageDescendants])?.allObjects as? [URL] ?? []
                out += items.filter { ["epub", "pdf"].contains($0.pathExtension.lowercased()) }
                    .sorted { $0.path.localizedStandardCompare($1.path) == .orderedAscending }
            } else if ["epub", "pdf"].contains(u.pathExtension.lowercased()) {
                out.append(u)
            }
        }
        return out
    }

    nonisolated static func same(_ a: String, _ b: String) -> Bool { key(a) == key(b) }

    /// A title or name reduced to its letters and digits, so "The Hobbit: Or There and Back Again" and
    /// "the hobbit - or there and back again" are recognised as the same book.
    nonisolated static func key(_ s: String) -> String {
        let folded = s.folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: nil)
        let k = String(String.UnicodeScalarView(folded.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) }))
        return k.isEmpty ? folded.trimmingCharacters(in: .whitespaces) : k
    }

    nonisolated static func fileName(_ title: String, _ author: String) -> String {
        let raw = author.isEmpty ? title : "\(title) - \(author)"
        let safe = raw.replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: " -")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return String((safe.isEmpty ? "Book" : safe).prefix(120))
    }

    nonisolated static func normalizeLanguage(_ code: String) -> String {
        let c = code.lowercased().split(whereSeparator: { $0 == "-" || $0 == "_" }).first.map(String.init) ?? ""
        return ["in": "id", "ind": "id", "eng": "en", "jp": "ja", "jpn": "ja"][c] ?? String(c.prefix(2))
    }

    nonisolated static func readMeta(_ url: URL) -> BookMeta {
        let guess = BookJob.guessFromFilename(url)
        if url.pathExtension.lowercased() == "pdf" {
            guard let doc = PDFDocument(url: url) else { return BookMeta(title: guess.title, author: guess.author, language: "") }
            let attrs = doc.documentAttributes ?? [:]
            func attr(_ k: PDFDocumentAttribute) -> String? {
                (attrs[k] as? String).flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 }
            }
            let cover = doc.page(at: 0).flatMap { PDFReader.render($0, dpi: 72) }.map { EPUBWriter.jpeg($0, quality: 0.85) }
            return BookMeta(title: attr(.titleAttribute) ?? guess.title, author: attr(.authorAttribute) ?? guess.author,
                            language: "", cover: cover)
        }
        var meta = BookMeta(title: guess.title, author: guess.author, language: "")
        guard let opfPath = EPUBArchive.opfPath(url),
              let opfData = EPUBArchive.entry(url, opfPath),
              let opf = try? XMLDocument(data: opfData, options: []) else { return meta }
        func texts(_ name: String) -> [String] {
            ((try? opf.nodes(forXPath: "//*[local-name()='metadata']/*[local-name()='\(name)']")) ?? [])
                .compactMap { $0.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        }
        if let t = texts("title").first { meta.title = t }
        let creators = texts("creator").filter { !$0.lowercased().hasPrefix("unknown") }
        if !creators.isEmpty { meta.author = creators.joined(separator: " & ") }
        if let l = texts("language").first { meta.language = normalizeLanguage(l) }

        // the cover: EPUB 3 cover-image, EPUB 2 <meta name="cover">, then any image named like a cover
        let items = ((try? opf.nodes(forXPath: "//*[local-name()='manifest']/*[local-name()='item']")) ?? []).compactMap { $0 as? XMLElement }
        func attr(_ e: XMLElement, _ n: String) -> String { e.attribute(forName: n)?.stringValue ?? "" }
        let images = items.filter { attr($0, "media-type").hasPrefix("image/") }
        let coverID = ((try? opf.nodes(forXPath: "//*[local-name()='meta'][@name='cover']")) ?? [])
            .compactMap { ($0 as? XMLElement)?.attribute(forName: "content")?.stringValue }.first
        let item = images.first { attr($0, "properties").split(separator: " ").contains("cover-image") }
            ?? images.first { attr($0, "id") == coverID }
            ?? images.first { (attr($0, "id") + attr($0, "href")).lowercased().contains("cover") }
        if let href = item.map({ attr($0, "href") }), !href.isEmpty,
           let data = EPUBArchive.entry(url, EPUBArchive.resolve(href, relativeTo: opfPath)) {
            meta.cover = thumbnail(data)
        }
        return meta
    }

    /// A JPEG at most 600 pixels tall, for the library grid and device screens.
    nonisolated static func thumbnail(_ data: Data) -> Data? {
        guard let src = CGImageSourceCreateWithData(data as CFData, nil),
              let img = CGImageSourceCreateThumbnailAtIndex(src, 0, [
                  kCGImageSourceCreateThumbnailFromImageAlways: true,
                  kCGImageSourceThumbnailMaxPixelSize: 600,
                  kCGImageSourceCreateThumbnailWithTransform: true,
              ] as CFDictionary) else { return nil }
        return EPUBWriter.jpeg(img, quality: 0.85)
    }
}

/// Reading single files out of an EPUB without unpacking it.
enum EPUBArchive {
    static func entry(_ epub: URL, _ path: String) -> Data? {
        // unzip treats names as patterns, so brackets must be escaped
        let pattern = path.replacingOccurrences(of: "[", with: "[[]")
        guard let d = try? Shell.data("/usr/bin/unzip", ["-p", epub.path, pattern], errors: false), !d.isEmpty else { return nil }
        return d
    }

    static func opfPath(_ epub: URL) -> String? {
        guard let data = entry(epub, "META-INF/container.xml"),
              let doc = try? XMLDocument(data: data, options: []),
              let el = (try? doc.nodes(forXPath: "//*[local-name()='rootfile']"))?.first as? XMLElement else { return nil }
        return el.attribute(forName: "full-path")?.stringValue
    }

    /// `href` as written in a file at `base` (both inside the archive) → archive path.
    static func resolve(_ href: String, relativeTo base: String) -> String {
        let clean = (href.components(separatedBy: "#")[0].removingPercentEncoding ?? href)
        var parts = base.split(separator: "/").dropLast().map(String.init)
        for p in clean.split(separator: "/") {
            if p == ".." { if !parts.isEmpty { parts.removeLast() } } else if p != "." { parts.append(String(p)) }
        }
        return parts.joined(separator: "/")
    }
}
