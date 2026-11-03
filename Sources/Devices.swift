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
}

/// A book to put on a reader.
struct SendItem: Sendable {
    var file: URL
    var title: String
    var author: String
    var uuid: String
    var language: String

    @MainActor init(_ book: LibraryBook) {
        file = Library.shared.url(book)
        title = book.title
        author = book.author
        uuid = book.id
        language = book.language
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
    var reader: EReader?
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

    // MARK: - Watching for readers

    /// Rescans whenever a USB device comes or goes, or a disk mounts (older Kindles, Kobo and PocketBook are disks).
    func start() {
        guard !started else { return }
        started = true
        guard Kindle.calibreInstalled else { state = .noCalibre; return }
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
        guard Kindle.calibreInstalled else { state = .noCalibre; return }
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
    func sync(add: [LibraryBook], remove: [DeviceBook]) async {
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
            if !add.isEmpty {
                let (sent, skipped) = try await Self.upload(add.map(SendItem.init), to: reader) { line in
                    Task { @MainActor in self.working = line }
                }
                if sent > 0 { notes.append("added \(sent)") }
                if !skipped.isEmpty { notes.append("skipped \(skipped.joined(separator: ", ")) (format not supported)") }
            }
            message = notes.isEmpty ? "Nothing to change." : "Done: " + notes.joined(separator: ", ") + "."
            if reader.isKindle && !add.isEmpty { message! += " Covers show once the Kindle is unplugged." }
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

    /// The copy of a library book on the reader, if there is one.
    func match(_ book: LibraryBook) -> DeviceBook? {
        guard let books = reader?.books else { return nil }
        return books.first { $0.uuid == book.id } ?? books.first { Library.same($0.title, book.title) }
    }

    // MARK: - Calibre plumbing (callable off the main actor, also from the command line)

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

        var reader: EReader {
            EReader(name: name ?? "E-reader", kind: kind ?? "generic", driver: driver ?? "", formats: formats ?? [],
                    total: total ?? 0, free: free ?? 0, books: books ?? [])
        }
    }

    nonisolated static func script(_ command: String, _ payload: (any Encodable & Sendable)? = nil,
                                   progress: (@Sendable (String) -> Void)? = nil) async throws -> ScriptResult {
        guard let py = Bundle.main.url(forResource: "device", withExtension: "py") else {
            throw Shell.Failure(message: "device.py is missing from the app bundle.")
        }
        return try await Task.detached {
            var args = ["-e", py.path, command]
            var tmp: URL?
            if let payload {
                let u = FileManager.default.temporaryDirectory.appendingPathComponent("bookforge-\(UUID().uuidString).json")
                try JSONEncoder().encode(payload).write(to: u)
                args.append(u.path)
                tmp = u
            }
            defer { if let tmp { try? FileManager.default.removeItem(at: tmp) } }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "\(Kindle.calibreBin)/calibre-debug")
            p.arguments = args
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            try p.run()
            var result: ScriptResult?
            var tail: [String] = []
            for try await line in pipe.fileHandleForReading.bytes.lines {
                if line.hasPrefix("RESULT ") {
                    result = try? JSONDecoder().decode(ScriptResult.self, from: Data(line.dropFirst(7).utf8))
                } else if line.hasPrefix("PROGRESS "),
                          let obj = try? JSONSerialization.jsonObject(with: Data(line.dropFirst(9).utf8)) as? [String: String],
                          let msg = obj["message"] {
                    progress?(msg)
                } else {
                    tail = Array((tail + [line]).suffix(8))
                }
            }
            p.waitUntilExit()
            guard let result else {
                throw Shell.Failure(message: "Calibre gave no answer:\n" + tail.joined(separator: "\n"))
            }
            return result
        }.value
    }

    /// The file to send to this reader: as it is, or converted for Kindle. nil if the reader can't open it.
    nonisolated static func prepare(_ item: SendItem, for reader: EReader, log: @Sendable (String) -> Void) throws -> URL? {
        let ext = item.file.pathExtension.lowercased()
        let wantsAZW3: Bool
        if reader.isKindle {
            // Kindles don't open sideloaded EPUBs
            if ext == "pdf" { return item.file }
            wantsAZW3 = ext == "epub"
        } else if reader.formats.contains(ext) {
            return item.file
        } else {
            wantsAZW3 = ext == "epub" && reader.formats.contains("azw3")
        }
        guard wantsAZW3 else { return nil }

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

    private struct UploadEntry: Codable, Sendable {
        var file: String
        var title: String
        var authors: [String]
        var uuid: String
        var language: String
    }

    /// Converts as needed and uploads. Returns how many were sent and the titles the reader can't open.
    nonisolated static func upload(_ items: [SendItem], to reader: EReader,
                                   log: @escaping @Sendable (String) -> Void) async throws -> (Int, [String]) {
        var entries: [UploadEntry] = [], skipped: [String] = []
        for item in items {
            guard let file = try prepare(item, for: reader, log: log) else {
                skipped.append("“\(item.title)”")
                continue
            }
            entries.append(UploadEntry(file: file.path, title: item.title,
                                       authors: item.author.isEmpty ? [] : item.author.components(separatedBy: " & "),
                                       uuid: item.uuid, language: item.language))
        }
        guard !entries.isEmpty else { return (0, skipped) }
        log("Copying to \(reader.name)…")
        let r = try await script("upload", entries, progress: log)
        guard r.status == "uploaded" else { throw Shell.Failure(message: r.error ?? "The upload failed (\(r.status)).") }
        return (r.count ?? entries.count, skipped)
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
            throw Shell.Failure(message: info.error ?? "Calibre couldn't read the e-reader.")
        }
        let reader = info.reader
        let (sent, skipped) = try await upload(items, to: reader, log: log)
        if sent == 0 { throw Shell.Failure(message: "\(reader.name) can't open this format.") }
        var msg = "On your \(reader.name)"
        msg += reader.isKindle ? ", with its cover. You can unplug it." : ". You can unplug it."
        if !skipped.isEmpty { msg += " Skipped \(skipped.joined(separator: ", ")): format not supported." }
        return msg
    }
}
