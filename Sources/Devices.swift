import AppKit
import IOKit
import IOKit.usb

struct DeviceBook: Codable, Identifiable, Hashable, Sendable {
    var title: String
    var authors: [String]
    var path: String            // what Calibre needs to delete it
    var lpath: String
    var uuid: String
    var size: Int64
    var id: String { path }

    var fileName: String { ((lpath.isEmpty ? path : lpath) as NSString).lastPathComponent }
    var format: String { (fileName as NSString).pathExtension.lowercased() }
    var displayTitle: String { title.isEmpty ? (fileName as NSString).deletingPathExtension : title }

    /// Books that only come into the library as EPUBs through Calibre.
    var needsConversion: Bool { !["epub", "kepub", "pdf"].contains(((lpath.isEmpty ? path : lpath) as NSString).pathExtension.lowercased()) }

    /// Amazon's KFX store books can't be converted or read outside a Kindle.
    var importable: Bool { format != "kfx" }

    /// Bought from the Kindle Store, so almost certainly locked with DRM: KFX and Print Replica files,
    /// and files the Kindle names with the book's Amazon ID ("…_B076V53ZX8.azw").
    var storeBook: Bool {
        ["kfx", "azw4"].contains(format)
            || fileName.range(of: #"[_-](asin_)?B0[0-9A-Z]{8}([_.-]|$)"#, options: [.regularExpression, .caseInsensitive]) != nil
    }
}

struct EReader: Equatable, Sendable {
    var name: String
    var kind: String            // kindle, kindle-oasis, kindle-scribe, kobo, pocketbook, boox, tolino, nook, generic
    var driver: String
    var formats: [String]
    var total: Int64
    var free: Int64
    var books: [DeviceBook]

    var isKindle: Bool { kind.hasPrefix("kindle") }
    var shortName: String { isKindle ? "Kindle" : name }

    /// Whether a library book in this format can go on the reader (Kindles get EPUBs converted).
    func accepts(_ format: String) -> Bool {
        // EPUBs become Kindle books through Calibre; a Kindle running KOReader opens them as they are
        let convert = Kindle.calibreInstalled
        if isKindle { return format == "pdf" || (format == "epub" && (convert || formats.contains("epub"))) }
        return formats.contains(format) || (format == "epub" && formats.contains("azw3") && convert)
    }
}

/// What was on a reader the last time it was connected, so the library can still say which books are on it.
struct SeenReader: Codable {
    var name: String
    var kind: String
    var date: Date
    var ids: Set<String>
    var titles: Set<String>     // Library.key of each title
}

/// A book to put on a reader.
struct SendItem: Sendable {
    var file: URL
    var title: String
    var author: String
    var uuid: String
    var language: String
    var cover: URL?             // for the Kindle's library thumbnail

    @MainActor init(_ book: LibraryBook) {
        file = Library.shared.url(book)
        title = book.title
        author = book.author
        uuid = book.id
        language = book.language
        let c = Library.shared.coverURL(book)
        cover = FileManager.default.fileExists(atPath: c.path) ? c : nil
    }

    init(file: URL, title: String, author: String, uuid: String, language: String) {
        self.file = file
        self.title = title
        self.author = author
        self.uuid = uuid
        self.language = language
    }
}

/// The USB e-reader, reached through Calibre's device drivers (Resources/device.py).
@MainActor @Observable
final class Devices {
    static let shared = Devices()

    enum State: Equatable {
        case none, connected, busy(String), noCalibre, failed(String), ejected(String)
    }

    var state: State = .none
    var reader: EReader? {
        didSet {
            reindex()
            if let reader, reader != oldValue { remember(reader) }
        }
    }
    private(set) var lastSeen: SeenReader?
    var busyKind = "generic"
    var scanning = false
    var working: String?            // what a sync is doing right now
    var message: String?            // how the last sync went
    var askQuitCalibre = false

    private var running = false
    private var pendingScan: Task<Void, Never>?
    private var pendingAction: (() async -> Void)?
    private var started = false
    private var iterators: [io_iterator_t] = []
    @ObservationIgnored private var byID: [String: DeviceBook] = [:]
    @ObservationIgnored private var byTitle: [String: [DeviceBook]] = [:]
    /// Reader books that turned out to be locked with DRM, so one-click import doesn't copy them again.
    @ObservationIgnored private var locked = Set(UserDefaults.standard.stringArray(forKey: "lockedReaderBooks") ?? [])

    private init() {
        let dec = JSONDecoder()
        dec.dateDecodingStrategy = .iso8601
        lastSeen = (try? Data(contentsOf: Self.seenFile)).flatMap { try? dec.decode(SeenReader.self, from: $0) }
    }

    // MARK: - Watching for readers

    /// Rescans whenever a USB device comes or goes, or a disk mounts (older Kindles, Kobo and PocketBook are disks).
    func start() {
        guard !started else { return }
        started = true
        if let port = IONotificationPortCreate(kIOMainPortDefault) {
            IONotificationPortSetDispatchQueue(port, .main)
            let callback: IOServiceMatchingCallback = { _, iterator in
                Devices.drain(iterator)
                MainActor.assumeIsolated { Devices.shared.scheduleScan() }
            }
            for type in [kIOFirstMatchNotification, kIOTerminatedNotification] {
                var it: io_iterator_t = 0
                if IOServiceAddMatchingNotification(port, type, IOServiceMatching("IOUSBHostDevice"), callback, nil, &it) == KERN_SUCCESS {
                    Devices.drain(it)   // arms the notification
                    iterators.append(it)
                }
            }
        }
        let nc = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.didMountNotification, NSWorkspace.didUnmountNotification] {
            nc.addObserver(forName: name, object: nil, queue: .main) { _ in
                MainActor.assumeIsolated { Devices.shared.scheduleScan() }
            }
        }
        scheduleScan(after: 0)
    }

    private nonisolated static func drain(_ it: io_iterator_t) {
        var obj = IOIteratorNext(it)
        while obj != 0 {
            IOObjectRelease(obj)
            obj = IOIteratorNext(it)
        }
    }

    /// Readers take a moment to appear after they're plugged in, and one plug-in fires several events.
    func scheduleScan(after delay: Double = 2) {
        pendingScan?.cancel()
        pendingScan = Task {
            try? await Task.sleep(for: .seconds(delay))
            if !Task.isCancelled { await scan() }
        }
    }

    func scan() async {
        guard !running else { scheduleScan(); return }
        running = true
        scanning = true
        defer { running = false; scanning = false }
        do {
            let r = try await Self.script("info")
            switch r.status {
            case "ok":
                let found = r.reader
                if case .ejected(let name) = state, name == found.name { return }
                reader = found
                state = .connected
            case "no_device":
                reader = nil
                state = .none
            case "open_failed":
                reader = nil
                busyKind = r.kind ?? "generic"
                state = .busy(r.name ?? "Your e-reader")
            default:
                reader = nil
                state = .failed(r.error ?? "Calibre couldn't read the e-reader.")
            }
        } catch {
            reader = nil
            state = .failed(error.localizedDescription)
        }
    }

    // MARK: - Changing what's on the reader

    /// Runs a device action, first asking to close the Calibre app if it's open (it holds the connection).
    func perform(_ action: @escaping () async -> Void) {
        if Kindle.calibreAppRunning {
            pendingAction = action
            askQuitCalibre = true
        } else {
            Task { await action() }
        }
    }

    func quitCalibreAndContinue() {
        let action = pendingAction
        pendingAction = nil
        Task {
            working = "Closing Calibre…"
            let closed = await Kindle.quitCalibre()
            working = nil
            guard closed else {
                message = "Calibre didn't close (a dialog may be open in it). Close it yourself and try again."
                return
            }
            try? await Task.sleep(for: .seconds(1))
            if let action { await action() } else { await scan() }
        }
    }

    func cancelPending() { pendingAction = nil }

    /// Removes and adds books in one go, then reads the reader again.
    func sync(add: [LibraryBook], remove: [DeviceBook], note: String = "") async {
        guard let reader, !running else { return }
        running = true
        message = nil
        var notes: [String] = []
        do {
            if !remove.isEmpty {
                working = "Removing \(remove.count) book\(remove.count == 1 ? "" : "s")…"
                let r = try await Self.script("delete", remove.map(\.path))
                if r.status != "deleted" { throw Shell.Failure(message: r.error ?? "Couldn't remove books.") }
                notes.append("removed \(remove.count)")
            }
            var needCalibre: [String] = []
            if !add.isEmpty {
                let (sent, skipped, need) = try await Self.upload(add.map(SendItem.init), to: reader) { line in
                    Task { @MainActor in self.working = line }
                }
                needCalibre = need
                if sent > 0 { notes.append("added \(sent)") }
                if !skipped.isEmpty { notes.append("skipped \(skipped.joined(separator: ", ")) (format not supported)") }
            }
            message = notes.isEmpty ? "Nothing to change." : "Done: " + notes.joined(separator: ", ") + "."
            message! += note
            if !needCalibre.isEmpty {
                message! += " The \(reader.shortName) can't open \(needCalibre.joined(separator: ", ")) as EPUB. " + Self.needsCalibreAdvice
            }
            if reader.isKindle && add.contains(where: { $0.format == "epub" }) && Kindle.calibreInstalled {
                message! += " Covers show once the Kindle is unplugged."
            }
        } catch {
            message = error.localizedDescription
        }
        working = nil
        running = false
        await scan()
    }

    func eject() async {
        guard let name = reader?.name, !running else { return }
        running = true
        working = "Ejecting…"
        _ = try? await Self.script("eject")
        working = nil
        running = false
        busyKind = reader?.kind ?? "generic"
        reader = nil
        state = .ejected(name)
    }

    /// Copies books from the reader into the library. Kindle books and other formats become EPUBs; store
    /// books locked with DRM can't be copied. Returns the library books added.
    func importBooks(_ books: [DeviceBook]) async -> [LibraryBook] {
        guard reader != nil, !running, !books.isEmpty else { return [] }
        running = true
        message = nil
        let fm = FileManager.default
        let dest = fm.temporaryDirectory.appendingPathComponent("bookforge-import-\(UUID().uuidString)", isDirectory: true)
        defer {
            try? fm.removeItem(at: dest)
            working = nil
            running = false
        }
        do {
            let byPath = Dictionary(books.map { ($0.path, $0) }, uniquingKeysWith: { a, _ in a })
            func name(_ path: String) -> String {
                let b = byPath[path]
                return "“\(b?.displayTitle ?? (path as NSString).lastPathComponent)”"
            }
            var added: [LibraryBook] = [], already = 0, locked: [String] = [], failed: [String] = [], needCalibre: [String] = []
            // books whose own header says they're encrypted aren't copied at all
            var todo: [DeviceBook] = []
            for b in books {
                if b.needsConversion && !Kindle.calibreInstalled {
                    needCalibre.append(name(b.path))
                } else if await Task.detached(operation: { ReaderLink.shared.locked(b.path) }).value == true {
                    locked.append(name(b.path))
                    self.locked.insert(b.path)
                } else {
                    todo.append(b)
                }
            }
            UserDefaults.standard.set(Array(self.locked), forKey: "lockedReaderBooks")
            working = "Copying from the reader…"
            let r = todo.isEmpty ? ScriptResult(status: "fetched", files: [], failed: []) :
                try await Self.script("fetch", FetchRequest(dest: dest.path, paths: todo.map(\.path))) { line in
                    Task { @MainActor in self.working = line }
                }
            guard r.status == "fetched" else { throw Shell.Failure(message: r.error ?? "Couldn't copy from the reader.") }
            failed += (r.failed ?? []).map { name($0.path) }
            for f in r.files ?? [] {
                guard let path = f.file else { continue }
                // a book with this title may have come into the library meanwhile (or earlier in this batch)
                if let d = byPath[f.path], Library.shared.books.contains(where: { $0.id == d.uuid || Library.same($0.title, d.displayTitle) }) {
                    already += 1
                    continue
                }
                let file = URL(fileURLWithPath: path)
                if !["epub", "pdf"].contains(file.pathExtension.lowercased()) { working = "Converting \(name(f.path)) to EPUB…" }
                let ready: URL
                do {
                    ready = try await Task.detached { try Self.readable(file) }.value
                } catch let e as Shell.Failure where e.message.contains("DRM") {
                    locked.append(name(f.path))
                    self.locked.insert(f.path)
                    UserDefaults.standard.set(Array(self.locked), forKey: "lockedReaderBooks")
                    continue
                } catch {
                    failed.append(name(f.path))
                    continue
                }
                guard var book = await Library.shared.add([ready], quiet: true).first else {
                    already += 1
                    continue
                }
                // use the reader's title so the two copies are recognised as the same book
                if let d = byPath[f.path], !d.title.isEmpty, !Library.same(d.title, book.title) {
                    book.title = d.title
                    if !d.authors.isEmpty { book.author = d.authors.joined(separator: " & ") }
                    Library.shared.update(book)
                }
                added.append(book)
            }
            var notes: [String] = []
            if !added.isEmpty { notes.append(added.count == 1 ? "Imported “\(added[0].title)”." : "Imported \(added.count) books.") }
            if already > 0 { notes.append("\(already) \(already == 1 ? "was" : "were") already in the library.") }
            if !locked.isEmpty { notes.append("\(locked.joined(separator: ", ")) \(locked.count == 1 ? "is a store book" : "are store books") locked with DRM, so \(locked.count == 1 ? "it" : "they") can't be copied.") }
            if !needCalibre.isEmpty {
                notes.append("\(needCalibre.joined(separator: ", ")) \(needCalibre.count == 1 ? "is" : "are") in a Kindle format; install Calibre (free, calibre-ebook.com) to bring \(needCalibre.count == 1 ? "it" : "them") in as EPUB.")
            }
            if !failed.isEmpty { notes.append("Couldn't import \(failed.joined(separator: ", ")).") }
            message = notes.joined(separator: " ")
            return added
        } catch {
            message = error.localizedDescription
            return []
        }
    }

    // MARK: - What's where

    private func reindex() {
        let books = reader?.books ?? []
        byID = Dictionary(books.filter { !$0.uuid.isEmpty }.map { ($0.uuid, $0) }, uniquingKeysWith: { a, _ in a })
        byTitle = Dictionary(grouping: books) { Library.key($0.displayTitle) }
    }

    private static var seenFile: URL { Library.shared.folder.appendingPathComponent(".last-reader.json") }

    private func remember(_ reader: EReader) {
        let seen = SeenReader(name: reader.name, kind: reader.kind, date: Date(),
                              ids: Set(reader.books.map(\.uuid).filter { !$0.isEmpty }),
                              titles: Set(reader.books.map { Library.key($0.displayTitle) }))
        lastSeen = seen
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        try? enc.encode(seen).write(to: Self.seenFile, options: .atomic)
    }

    /// Every copy of a library book on the reader: the one Book Forge put there (same id) and any with the same title.
    func copies(of book: LibraryBook) -> [DeviceBook] {
        var out = byID[book.id].map { [$0] } ?? []
        for d in byTitle[Library.key(book.title)] ?? [] where !out.contains(d) { out.append(d) }
        return out
    }

    /// The copy of a library book on the reader, if there is one.
    func match(_ book: LibraryBook) -> DeviceBook? { copies(of: book).first }

    /// Whether the book was on the reader when it was last connected (while it's unplugged).
    func wasOnReader(_ book: LibraryBook) -> Bool {
        guard reader == nil, let seen = lastSeen else { return false }
        return seen.ids.contains(book.id) || seen.titles.contains(Library.key(book.title))
    }

    /// Books on the reader that aren't in the library.
    var onlyOnReader: [DeviceBook] {
        let lib = Library.shared.books
        let ids = Set(lib.map(\.id)), titles = Set(lib.map { Library.key($0.title) })
        return (reader?.books ?? []).filter { !ids.contains($0.uuid) && !titles.contains(Library.key($0.displayTitle)) }
    }

    /// Store books and books found to be locked: Import All leaves them on the reader.
    func likelyLocked(_ b: DeviceBook) -> Bool { !b.importable || b.storeBook || locked.contains(b.path) }

    /// What one-click Import copies: books only on the reader that aren't locked, one copy per title
    /// (EPUB, then Kindle formats, then PDF).
    var toImport: [DeviceBook] {
        let rank = ["epub": 0, "kepub": 0, "azw3": 1, "mobi": 2, "azw": 3, "pdf": 9]
        let convert = Kindle.calibreInstalled
        return Self.onePerTitle(onlyOnReader.filter { !likelyLocked($0) && (convert || !$0.needsConversion) },
                                key: { Library.key($0.displayTitle) }, rank: { rank[$0.format] ?? 5 })
    }

    /// What one-click Send copies: library books not on the reader that it can open, one copy per title (EPUB before PDF).
    var toSend: [LibraryBook] {
        guard let reader else { return [] }
        return Self.onePerTitle(Library.shared.books.filter { match($0) == nil && reader.accepts($0.format) },
                                key: { Library.key($0.title) }, rank: { $0.format == "epub" ? 0 : 1 })
    }

    /// Keeps the best-ranked item of each title, in the original order.
    private static func onePerTitle<T>(_ items: [T], key: (T) -> String, rank: (T) -> Int) -> [T] {
        var best: [String: (Int, Int)] = [:]        // key -> (rank, index)
        for (i, item) in items.enumerated() {
            let k = key(item), r = rank(item)
            if let b = best[k], b.0 <= r { continue }
            best[k] = (r, i)
        }
        let keep = Set(best.values.map(\.1))
        return items.enumerated().filter { keep.contains($0.offset) }.map(\.element)
    }

    // MARK: - One click

    /// Copies every book that's only on the reader into the library. Books already in the library, second
    /// copies of the same title and store books are left alone.
    func importNew() async {
        guard let reader else { return }
        let books = toImport
        let store = onlyOnReader.filter { likelyLocked($0) }.count
        let need = Kindle.calibreInstalled ? 0 : onlyOnReader.filter { !likelyLocked($0) && $0.needsConversion }.count
        func storeNote(other: Bool) -> String {
            (store == 0 ? "" : " Left \(store)\(other ? " other" : "") store book\(store == 1 ? "" : "s") on the \(reader.shortName) (locked with DRM).")
                + (need == 0 ? "" : " \(need) book\(need == 1 ? " is" : "s are") in a Kindle format; install Calibre (free, calibre-ebook.com) to bring \(need == 1 ? "it" : "them") in as EPUB.")
        }
        guard !books.isEmpty else {
            message = "Every book on the \(reader.shortName) is already in your library." + storeNote(other: false)
            return
        }
        let lockedBefore = locked.count
        _ = await importBooks(books)
        message = (message ?? "") + storeNote(other: locked.count > lockedBefore)
    }

    /// Copies every library book that isn't on the reader yet. Nothing is removed, and books already on it aren't copied again.
    func sendNew() async {
        guard let reader else { return }
        let books = toSend
        let already = Library.shared.books.filter { match($0) != nil }.count
        let alreadyNote = already == 0 ? "" : " \(already) \(already == 1 ? "was" : "were") already on it."
        let waiting = Library.shared.books.filter { match($0) == nil && $0.format == "epub" && !reader.accepts("epub") }.count
        let needNote = waiting == 0 ? "" : " \(waiting) EPUB\(waiting == 1 ? "" : "s") can't go on the \(reader.shortName) without converting. " + Self.needsCalibreAdvice
        guard !books.isEmpty else {
            message = waiting == 0 ? "Every book in your library is already on the \(reader.shortName)." : String(needNote.dropFirst())
            return
        }
        let size = books.reduce(Int64(0)) { sum, b in
            sum + Int64((try? Library.shared.url(b).resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0)
        }
        if reader.free > 0, size > reader.free - 20_000_000 {
            message = "Not enough space on the \(reader.shortName): these books need about \(ByteCountFormatter.string(fromByteCount: size, countStyle: .file)) and \(ByteCountFormatter.string(fromByteCount: reader.free, countStyle: .file)) is free. Tick fewer books below and press Sync."
            return
        }
        await sync(add: books, remove: [], note: alreadyNote + needNote)
    }

    // MARK: - Reader plumbing (callable off the main actor, also from the command line)

    struct ScriptResult: Decodable, Sendable {
        var status: String
        var error: String?
        var name: String?
        var kind: String?
        var driver: String?
        var formats: [String]?
        var total: Int64?
        var free: Int64?
        var books: [DeviceBook]?
        var count: Int?
        var files: [Fetched]?
        var failed: [Fetched]?

        var reader: EReader {
            EReader(name: name ?? "E-reader", kind: kind ?? "generic", driver: driver ?? "", formats: formats ?? [],
                    total: total ?? 0, free: free ?? 0, books: books ?? [])
        }
    }

    struct Fetched: Decodable, Sendable {
        var path: String
        var file: String?
        var error: String?
    }

    private struct FetchRequest: Encodable, Sendable {
        var dest: String
        var paths: [String]
    }

    /// A file fetched from a reader, as something the library can hold: EPUBs and PDFs as they are,
    /// anything else (AZW3, MOBI, FB2…) converted to EPUB with Calibre. Throws Calibre's error, which names DRM if that's the problem.
    nonisolated static func readable(_ file: URL) throws -> URL {
        if ["epub", "pdf"].contains(file.pathExtension.lowercased()) { return file }
        guard Kindle.calibreInstalled else { throw Shell.Failure(message: "needs Calibre") }
        let out = file.deletingPathExtension().appendingPathExtension("converted.epub")
        try Shell.run("\(Kindle.calibreBin)/ebook-convert", [file.path, out.path])
        return out
    }

    /// Runs a request against the connected reader (ReaderLink), in the shape device.py's answers had.
    nonisolated static func script(_ command: String, _ payload: (any Encodable & Sendable)? = nil,
                                   progress: (@Sendable (String) -> Void)? = nil) async throws -> ScriptResult {
        try await Task.detached {
            let link = ReaderLink.shared
            switch command {
            case "info":
                return link.info()
            case "delete":
                let paths = payload as? [String] ?? []
                try link.delete(paths)
                return ScriptResult(status: "deleted", count: paths.count)
            case "fetch":
                guard let req = payload as? FetchRequest else { throw Shell.Failure(message: "Nothing to copy.") }
                let (files, failed) = try link.fetch(req.paths, to: URL(fileURLWithPath: req.dest)) { progress?($0) }
                return ScriptResult(status: "fetched", files: files, failed: failed)
            case "eject":
                link.eject()
                return ScriptResult(status: "ejected")
            default:
                throw Shell.Failure(message: "Unknown request \(command).")
            }
        }.value
    }

    /// The file to send to this reader: as it is, or converted for Kindle. nil if the reader can't open it.
    nonisolated static func prepare(_ item: SendItem, for reader: EReader, log: @Sendable (String) -> Void) throws -> URL? {
        let ext = item.file.pathExtension.lowercased()
        let wantsAZW3: Bool
        if reader.isKindle {
            // the Kindle's own library doesn't open sideloaded EPUBs; KOReader does
            if ext == "pdf" { return item.file }
            if ext == "epub" && !Kindle.calibreInstalled && reader.formats.contains("epub") { return item.file }
            wantsAZW3 = ext == "epub"
        } else if reader.formats.contains(ext) {
            return item.file
        } else {
            wantsAZW3 = ext == "epub" && reader.formats.contains("azw3")
        }
        guard wantsAZW3 else { return nil }
        guard Kindle.calibreInstalled else { throw NeedsCalibre() }

        let fm = FileManager.default
        let cache = fm.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("com.widagdos.bookforge/Converted", isDirectory: true)
        try fm.createDirectory(at: cache, withIntermediateDirectories: true)
        let out = cache.appendingPathComponent("\(item.uuid).azw3")
        let srcDate = (try? item.file.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantFuture
        if let outDate = (try? out.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate, outDate > srcDate {
            return out
        }
        log("Converting “\(item.title)” for Kindle…")
        let cover = cache.appendingPathComponent("\(item.uuid)-cover.jpg")
        defer { try? fm.removeItem(at: cover) }
        _ = try? Shell.run("\(Kindle.calibreBin)/ebook-meta", [item.file.path, "--get-cover=\(cover.path)"])
        var args = [item.file.path, out.path, "--output-profile", "kindle_pw3"]
        if fm.fileExists(atPath: cover.path) { args += ["--cover", cover.path] }
        try Shell.run("\(Kindle.calibreBin)/ebook-convert", args)
        return out
    }

    struct NeedsCalibre: Error {}

    static let needsCalibreAdvice = "Install Calibre (free, calibre-ebook.com) to convert them, or send them with Email to Kindle, which converts them for you."

    /// Converts as needed and copies. Returns how many were sent, the titles the reader can't open, and the
    /// titles that need Calibre to be converted.
    nonisolated static func upload(_ items: [SendItem], to reader: EReader,
                                   log: @escaping @Sendable (String) -> Void) async throws -> (Int, [String], [String]) {
        var ready: [ReaderOps.Item] = [], skipped: [String] = [], needCalibre: [String] = []
        for item in items {
            do {
                guard let file = try prepare(item, for: reader, log: log) else {
                    skipped.append("“\(item.title)”")
                    continue
                }
                ready.append(ReaderOps.Item(file: file, title: item.title,
                                            authors: item.author.isEmpty ? [] : item.author.components(separatedBy: " & "),
                                            uuid: item.uuid, cover: item.cover))
            } catch is NeedsCalibre {
                needCalibre.append("“\(item.title)”")
            }
        }
        guard !ready.isEmpty else { return (0, skipped, needCalibre) }
        let sent = try await Task.detached { try ReaderLink.shared.upload(ready, log: log) }.value
        return (sent, skipped, needCalibre)
    }

    /// Finds the reader and sends books to it in one step (the Convert & Translate screen and the command line).
    nonisolated static func sendNow(_ items: [SendItem], log: @escaping @Sendable (String) -> Void) async throws -> String {
        log("Looking for your e-reader…")
        let info = try await script("info")
        switch info.status {
        case "ok": break
        case "no_device":
            throw Shell.Failure(message: "No e-reader found. Connect it straight to the Mac with a data cable (not a hub) and unlock it.")
        case "open_failed":
            throw Shell.Failure(message: "\(info.name ?? "The e-reader") is busy. Close Calibre or Android File Transfer, replug it and try again.")
        default:
            throw Shell.Failure(message: info.error ?? "Couldn't read the e-reader.")
        }
        let reader = info.reader
        let (sent, skipped, needCalibre) = try await upload(items, to: reader, log: log)
        if sent == 0 {
            throw Shell.Failure(message: needCalibre.isEmpty ? "\(reader.name) can't open this format."
                                : "\(reader.name) can't open EPUBs. " + needsCalibreAdvice)
        }
        var msg = "On your \(reader.name)"
        msg += reader.isKindle && Kindle.calibreInstalled ? ", with its cover. You can unplug it." : ". You can unplug it."
        if !skipped.isEmpty { msg += " Skipped \(skipped.joined(separator: ", ")): format not supported." }
        return msg
    }
}
