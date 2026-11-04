import AppKit

/// E-readers over USB without Calibre: readers that mount as a drive (older Kindles, Kobo, PocketBook) and MTP
/// readers such as newer Kindles (through the bundled libmtp). `ReaderLink` keeps the connection and answers
/// Devices' requests; Calibre, when installed, is only used to convert books.

// MARK: - Storage

protocol ReaderStorage: AnyObject {
    var name: String { get }
    var kind: String { get }
    var localRoot: URL? { get }
    func space() -> (total: Int64, free: Int64)
    func files() throws -> [(lpath: String, size: Int64)]
    func exists(_ lpath: String) -> Bool
    func readSmall(_ lpath: String) -> Data?
    func writeSmall(_ lpath: String, _ data: Data) throws
    func head(_ lpath: String, _ n: Int) throws -> Data
    func get(_ lpath: String, to local: URL) throws
    func put(_ local: URL, as lpath: String) throws
    func remove(_ lpath: String) throws
    func eject()
}

enum ReaderMeta {
    static let bookExts: Set<String> = ["epub", "kepub", "azw", "azw3", "azw4", "kfx", "mobi", "prc", "pdb", "pdf", "fb2", "fbz",
                                        "cbz", "cbr", "djvu", "rtf", "docx", "lit", "lrf"]
    static let notBooks = ["system/", "koreader/", "kmc/", "extensions/", "documents/dictionaries/", ".kobo/", ".adobe-digital-editions/"]

    static func ext(_ lpath: String) -> String { (lpath as NSString).pathExtension.lowercased() }

    /// Readers hold notes, dictionaries and their own software too; keep only e-books outside system folders.
    static func isBook(_ lpath: String) -> Bool {
        let lp = lpath.lowercased()
        let e = ext(lp)
        guard bookExts.contains(e) else { return false }
        if lp.hasPrefix(".") || lp.contains("/.") || lp.contains(".sdr/") { return false }
        return !notBooks.contains { lp.hasPrefix($0) }
    }

    /// Folders that never hold books: hidden ones, the reader's system folders, and books' .sdr side folders
    /// (notes, and asset files of store books).
    static func skipFolder(_ lpath: String) -> Bool {
        let lp = lpath.lowercased() + "/"
        return (lpath as NSString).lastPathComponent.hasPrefix(".") || lp.hasSuffix(".sdr/") || notBooks.contains { lp.hasPrefix($0) }
    }

    /// Title and authors guessed from a file name like "Title - Author.azw3" or "Title_B076V53ZX8.kfx".
    static func fromFilename(_ lpath: String) -> (String, [String]) {
        var stem = ((lpath as NSString).lastPathComponent as NSString).deletingPathExtension
        if stem.lowercased().hasSuffix(".kepub") { stem = String(stem.dropLast(6)) }
        stem = stem.replacingOccurrences(of: #"[_-](asin_)?B0[0-9A-Z]{8}([_.-].*)?$"#, with: "", options: [.regularExpression, .caseInsensitive])
        stem = stem.replacingOccurrences(of: #"\s+-\s+[0-9a-f]{8}$"#, with: "", options: .regularExpression)
        var title = stem.trimmingCharacters(in: .whitespaces), authors: [String] = []
        let parts = stem.components(separatedBy: " - ")
        if parts.count == 2, !parts[1].trimmingCharacters(in: .whitespaces).isEmpty {
            title = parts[0].trimmingCharacters(in: .whitespaces)
            authors = parts[1].components(separatedBy: CharacterSet(charactersIn: "&;"))
                .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        }
        // Calibre's sortable names: "Hobbit, The" -> "The Hobbit"
        if let m = title.range(of: #", (The|A|An)$"#, options: [.regularExpression, .caseInsensitive]) {
            title = String(title[m].dropFirst(2)) + " " + title[..<m.lowerBound]
        }
        return (title, authors)
    }

    /// Fields from the start of a MOBI/AZW/AZW3 file (the first 64 KB is plenty).
    struct Mobi {
        var title: String?
        var authors: [String] = []
        var asin: String?
        var cdetype: String?
        var encrypted = false

        init?(_ d: Data) {
            let b = [UInt8](d)
            func u16(_ o: Int) -> Int { o + 2 <= b.count ? Int(b[o]) << 8 | Int(b[o + 1]) : 0 }
            func u32(_ o: Int) -> Int { o + 4 <= b.count ? Int(b[o]) << 24 | Int(b[o + 1]) << 16 | Int(b[o + 2]) << 8 | Int(b[o + 3]) : 0 }
            guard b.count >= 86 else { return nil }
            let tag = String(decoding: b[60..<68], as: UTF8.self)
            guard tag == "BOOKMOBI" || tag == "TEXtREAd", u16(76) >= 1 else { return nil }
            let r0 = u32(78)
            guard r0 + 16 <= b.count else { return nil }
            encrypted = u16(r0 + 12) != 0
            guard r0 + 132 <= b.count, String(decoding: b[(r0 + 16)..<(r0 + 20)], as: UTF8.self) == "MOBI" else { return }
            let hlen = u32(r0 + 20)
            let utf8 = u32(r0 + 28) == 65001
            func str(_ s: ArraySlice<UInt8>) -> String {
                utf8 ? String(decoding: s, as: UTF8.self) : (String(bytes: s, encoding: .windowsCP1252) ?? "")
            }
            let nameOff = r0 + u32(r0 + 84), nameLen = u32(r0 + 88)
            if nameOff + nameLen <= b.count { title = str(b[nameOff..<(nameOff + nameLen)]) }
            let ex = r0 + 16 + hlen
            if u32(r0 + 128) & 0x40 != 0, ex + 12 <= b.count, String(decoding: b[ex..<(ex + 4)], as: UTF8.self) == "EXTH" {
                var p = ex + 12
                for _ in 0..<u32(ex + 8) {
                    let typ = u32(p), size = u32(p + 4)
                    guard size >= 8, p + size <= b.count else { break }
                    let val = b[(p + 8)..<(p + size)]
                    switch typ {
                    case 100: authors.append(str(val).trimmingCharacters(in: .whitespaces))
                    case 503: title = str(val).trimmingCharacters(in: .whitespaces)
                    case 113: asin = String(decoding: val, as: UTF8.self).trimmingCharacters(in: .whitespaces)
                    case 501: cdetype = String(decoding: val, as: UTF8.self).trimmingCharacters(in: .whitespaces)
                    default: break
                    }
                    p += size
                }
            }
        }
    }

    static let mobiExts: Set<String> = ["azw", "azw3", "mobi", "prc", "pdb"]
}

/// A reader that shows up as a drive.
final class FolderStorage: ReaderStorage {
    let root: URL, name: String, kind: String
    var localRoot: URL? { root }

    init(root: URL, name: String, kind: String) {
        self.root = root
        self.name = name
        self.kind = kind
    }

    private func url(_ lpath: String) -> URL { root.appendingPathComponent(lpath) }

    func space() -> (total: Int64, free: Int64) {
        let v = try? root.resourceValues(forKeys: [.volumeTotalCapacityKey, .volumeAvailableCapacityKey])
        return (Int64(v?.volumeTotalCapacity ?? 0), Int64(v?.volumeAvailableCapacity ?? 0))
    }

    func files() throws -> [(lpath: String, size: Int64)] {
        var out: [(String, Int64)] = []
        func walk(_ dir: URL, _ rel: String) {
            let items = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.isDirectoryKey, .fileSizeKey])) ?? []
            for u in items {
                let lp = rel + u.lastPathComponent
                let v = try? u.resourceValues(forKeys: [.isDirectoryKey, .fileSizeKey])
                if v?.isDirectory == true {
                    if !ReaderMeta.skipFolder(lp) { walk(u, lp + "/") }
                } else {
                    out.append((lp, Int64(v?.fileSize ?? 0)))
                }
            }
        }
        walk(root, "")
        return out
    }

    func exists(_ lpath: String) -> Bool { FileManager.default.fileExists(atPath: url(lpath).path) }
    func readSmall(_ lpath: String) -> Data? { try? Data(contentsOf: url(lpath)) }

    func writeSmall(_ lpath: String, _ data: Data) throws {
        try FileManager.default.createDirectory(at: url(lpath).deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url(lpath))
    }

    func head(_ lpath: String, _ n: Int) throws -> Data {
        let h = try FileHandle(forReadingFrom: url(lpath))
        defer { try? h.close() }
        return try h.read(upToCount: n) ?? Data()
    }

    func get(_ lpath: String, to local: URL) throws {
        try? FileManager.default.removeItem(at: local)
        try FileManager.default.copyItem(at: url(lpath), to: local)
    }

    func put(_ local: URL, as lpath: String) throws {
        let dest = url(lpath)
        try FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.copyItem(at: local, to: dest)
    }

    func remove(_ lpath: String) throws { try? FileManager.default.removeItem(at: url(lpath)) }

    func eject() { try? NSWorkspace.shared.unmountAndEjectDevice(at: root) }
}

// MARK: - libmtp

/// libmtp's functions, looked up in the bundled library. Every call goes through `queue`: libmtp isn't thread-safe.
final class MTPLib: @unchecked Sendable {
    static let shared = MTPLib()
    let queue = DispatchQueue(label: "com.widagdos.bookforge.mtp")
    let ok: Bool

    typealias Detect = @convention(c) (UnsafeMutablePointer<UnsafeMutablePointer<BF_raw_device_t>?>, UnsafeMutablePointer<Int32>) -> Int32
    typealias Open = @convention(c) (UnsafeMutablePointer<BF_raw_device_t>) -> UnsafeMutablePointer<BF_mtpdevice_t>?
    typealias GetString = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>) -> UnsafeMutablePointer<CChar>?
    typealias GetStorage = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>, Int32) -> Int32
    typealias List = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>, UInt32, UInt32) -> UnsafeMutablePointer<BF_file_t>?
    typealias GetFile = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>, UInt32, UnsafePointer<CChar>, OpaquePointer?, OpaquePointer?) -> Int32
    typealias SendFile = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>, UnsafePointer<CChar>, UnsafeMutablePointer<BF_file_t>, OpaquePointer?, OpaquePointer?) -> Int32
    typealias NewFile = @convention(c) () -> UnsafeMutablePointer<BF_file_t>?
    typealias FreeFile = @convention(c) (UnsafeMutablePointer<BF_file_t>) -> Void
    typealias Delete = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>, UInt32) -> Int32
    typealias MakeFolder = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>, UnsafeMutablePointer<CChar>, UInt32, UInt32) -> UInt32
    typealias Partial = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>, UInt32, UInt64, UInt32,
                                        UnsafeMutablePointer<UnsafeMutablePointer<UInt8>?>, UnsafeMutablePointer<UInt32>) -> Int32
    typealias Device = @convention(c) (UnsafeMutablePointer<BF_mtpdevice_t>) -> Void

    var detect: Detect!, open: Open!, model: GetString!, friendly: GetString!, manufacturer: GetString!
    var getStorage: GetStorage!, list: List!, getFile: GetFile!, sendFile: SendFile!, newFile: NewFile!, freeFile: FreeFile!
    var delete: Delete!, makeFolder: MakeFolder!, partial: Partial!, release: Device!, clearErrors: Device!

    private init() {
        // bundled in the app; BOOKFORGE_LIBMTP or Calibre's copy when running from a build folder
        let candidates = [ProcessInfo.processInfo.environment["BOOKFORGE_LIBMTP"],
                          Bundle.main.privateFrameworksURL?.appendingPathComponent("libmtp.9.dylib").path,
                          "/Applications/calibre.app/Contents/Frameworks/libmtp.9.dylib"].compactMap { $0 }
        let h = candidates.lazy.compactMap { FileManager.default.fileExists(atPath: $0) ? dlopen($0, RTLD_NOW | RTLD_LOCAL) : nil }.first
        guard let h else { ok = false; return }
        func f<T>(_ name: String, _: T.Type) -> T? { dlsym(h, name).map { unsafeBitCast($0, to: T.self) } }
        detect = f("LIBMTP_Detect_Raw_Devices", Detect.self)
        open = f("LIBMTP_Open_Raw_Device_Uncached", Open.self)
        model = f("LIBMTP_Get_Modelname", GetString.self)
        friendly = f("LIBMTP_Get_Friendlyname", GetString.self)
        manufacturer = f("LIBMTP_Get_Manufacturername", GetString.self)
        getStorage = f("LIBMTP_Get_Storage", GetStorage.self)
        list = f("LIBMTP_Get_Files_And_Folders", List.self)
        getFile = f("LIBMTP_Get_File_To_File", GetFile.self)
        sendFile = f("LIBMTP_Send_File_From_File", SendFile.self)
        newFile = f("LIBMTP_new_file_t", NewFile.self)
        freeFile = f("LIBMTP_destroy_file_t", FreeFile.self)
        delete = f("LIBMTP_Delete_Object", Delete.self)
        makeFolder = f("LIBMTP_Create_Folder", MakeFolder.self)
        partial = f("LIBMTP_GetPartialObject", Partial.self)
        release = f("LIBMTP_Release_Device", Device.self)
        clearErrors = f("LIBMTP_Clear_Errorstack", Device.self)
        let initialize = f("LIBMTP_Init", (@convention(c) () -> Void).self)
        ok = detect != nil && open != nil && list != nil && initialize != nil
        if ok { initialize!() }
    }

    struct Raw: Hashable { let bus: UInt32; let devnum: UInt8; let vendor: String; let product: String }

    /// Attached MTP devices (cheap: no device is opened).
    func attached() -> [Raw] {
        guard ok else { return [] }
        return queue.sync {
            var raw: UnsafeMutablePointer<BF_raw_device_t>?
            var n: Int32 = 0
            guard detect(&raw, &n) == 0, let raw, n > 0 else { return [] }
            defer { free(raw) }
            return (0..<Int(n)).map { i in
                let e = raw[i].device_entry
                return Raw(bus: raw[i].bus_location, devnum: raw[i].devnum,
                           vendor: e.vendor.map { String(cString: $0) } ?? "", product: e.product.map { String(cString: $0) } ?? "")
            }
        }
    }
}

/// One storage area (usually "Internal Storage") of an MTP reader. The device stays open while Book Forge runs:
/// releasing an MTP connection makes libmtp reset the USB port, and Kindles then drop off the Mac until they're
/// replugged. It's only released when the user ejects the reader.
final class MTPStorage: ReaderStorage, @unchecked Sendable {
    static let root: UInt32 = 0xFFFF_FFFF
    let lib = MTPLib.shared
    let dev: UnsafeMutablePointer<BF_mtpdevice_t>
    let raw: MTPLib.Raw
    let name: String, kind: String, manufacturer: String
    let sid: UInt32
    var localRoot: URL? { nil }
    private var folders: [String: [String: (id: UInt32, size: Int64, dir: Bool)]] = [:]
    private(set) var released = false

    init?(_ raw: MTPLib.Raw) {
        let lib = MTPLib.shared
        guard lib.ok else { return nil }
        guard let dev: UnsafeMutablePointer<BF_mtpdevice_t> = lib.queue.sync(execute: {
            var list: UnsafeMutablePointer<BF_raw_device_t>?
            var n: Int32 = 0
            guard lib.detect(&list, &n) == 0, let list else { return nil }
            defer { free(list) }
            for i in 0..<Int(n) where list[i].bus_location == raw.bus && list[i].devnum == raw.devnum {
                return lib.open(list.advanced(by: i))
            }
            return nil
        }) else { return nil }
        func s(_ f: MTPLib.GetString?) -> String {
            guard let f, let p = f(dev) else { return "" }
            defer { free(p) }
            return String(cString: p).trimmingCharacters(in: .whitespaces)
        }
        let (model, friendly, maker) = lib.queue.sync { (s(lib.model), s(lib.friendly), s(lib.manufacturer)) }
        let storages = lib.queue.sync { () -> [(UInt32, String)] in
            _ = lib.getStorage(dev, 0)
            var out: [(UInt32, String)] = []
            var p = dev.pointee.storage
            while let st = p {
                out.append((st.pointee.id, st.pointee.StorageDescription.map { String(cString: $0) } ?? ""))
                p = st.pointee.next
            }
            return out
        }
        guard let st = storages.first(where: { $0.1.lowercased().contains("internal") }) ?? storages.first else { return nil }
        self.dev = dev
        self.raw = raw
        self.sid = st.0
        self.manufacturer = maker
        // without the serial number, e.g. "Kindle Paperwhite Signature Edition GN433W11534501"
        let full = model.isEmpty ? (friendly.isEmpty ? raw.product : friendly) : model
        self.name = full.replacingOccurrences(of: #"\s+[A-Z0-9]{10,}$"#, with: "", options: .regularExpression)
        // the reader's own name decides; libmtp's device table can be wrong (it calls this Paperwhite a Scribe)
        let own = ReaderDetect.kind(of: [self.name, maker])
        self.kind = own != "generic" ? own : ReaderDetect.kind(of: [raw.vendor, raw.product])
    }

    func space() -> (total: Int64, free: Int64) {
        lib.queue.sync {
            _ = lib.getStorage(dev, 0)
            var p = dev.pointee.storage
            while let st = p {
                if st.pointee.id == sid { return (Int64(st.pointee.MaxCapacity), Int64(st.pointee.FreeSpaceInBytes)) }
                p = st.pointee.next
            }
            return (0, 0)
        }
    }

    private func listing(_ parent: UInt32) -> [String: (id: UInt32, size: Int64, dir: Bool)] {
        lib.queue.sync {
            var out: [String: (UInt32, Int64, Bool)] = [:]
            var f = lib.list(dev, sid, parent)
            while let c = f {
                if let n = c.pointee.filename {
                    out[String(cString: n)] = (c.pointee.item_id, Int64(c.pointee.filesize), c.pointee.filetype == 0)
                }
                f = c.pointee.next
                lib.freeFile(c)
            }
            lib.clearErrors(dev)
            return out
        }
    }

    private func folderID(_ folder: String) -> UInt32? {
        if folder.isEmpty { return Self.root }
        let parent = (folder as NSString).deletingLastPathComponent, name = (folder as NSString).lastPathComponent
        guard let e = entries(parent)[name], e.dir else { return nil }
        return e.id
    }

    private func entries(_ folder: String) -> [String: (id: UInt32, size: Int64, dir: Bool)] {
        if let e = folders[folder] { return e }
        let e = folderID(folder).map(listing) ?? [:]
        folders[folder] = e
        return e
    }

    private func find(_ lpath: String) -> (id: UInt32, size: Int64, dir: Bool)? {
        entries((lpath as NSString).deletingLastPathComponent)[(lpath as NSString).lastPathComponent]
    }

    func files() throws -> [(lpath: String, size: Int64)] {
        folders = [:]
        var out: [(String, Int64)] = []
        func walk(_ folder: String) {
            for (name, e) in entries(folder) {
                let lp = folder.isEmpty ? name : folder + "/" + name
                if e.dir {
                    if !ReaderMeta.skipFolder(lp) { walk(lp) }
                } else {
                    out.append((lp, e.size))
                }
            }
        }
        walk("")
        return out
    }

    func exists(_ lpath: String) -> Bool { find(lpath) != nil }

    func readSmall(_ lpath: String) -> Data? {
        guard find(lpath) != nil else { return nil }
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("bookforge-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmp) }
        return (try? get(lpath, to: tmp)).flatMap { try? Data(contentsOf: tmp) }
    }

    func writeSmall(_ lpath: String, _ data: Data) throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("bookforge-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmp) }
        try data.write(to: tmp)
        try put(tmp, as: lpath)
    }

    func head(_ lpath: String, _ n: Int) throws -> Data {
        guard let e = find(lpath) else { throw Shell.Failure(message: "Not on the reader: \(lpath)") }
        if let partial = lib.partial {
            let d: Data? = lib.queue.sync {
                var p: UnsafeMutablePointer<UInt8>?
                var size: UInt32 = 0
                let r = partial(dev, e.id, 0, UInt32(min(Int64(n), max(e.size, 1))), &p, &size)
                lib.clearErrors(dev)
                guard r == 0, let p else { return nil }
                defer { free(p) }
                return Data(bytes: p, count: Int(size))
            }
            if let d { return d }
        }
        // the reader can't send part of a file: fetch it all
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("bookforge-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmp) }
        try get(lpath, to: tmp)
        return try Data(contentsOf: tmp).prefix(n)
    }

    func get(_ lpath: String, to local: URL) throws {
        guard let e = find(lpath) else { throw Shell.Failure(message: "Not on the reader: \(lpath)") }
        let r = lib.queue.sync {
            let r = lib.getFile(dev, e.id, local.path, nil, nil)
            lib.clearErrors(dev)
            return r
        }
        if r != 0 { throw Shell.Failure(message: "Couldn't copy \((lpath as NSString).lastPathComponent) from the reader.") }
    }

    private func makeFolder(_ folder: String) throws -> UInt32 {
        if let id = folderID(folder) { return id }
        let parent = (folder as NSString).deletingLastPathComponent, name = (folder as NSString).lastPathComponent
        let pid = try makeFolder(parent)
        let id: UInt32 = lib.queue.sync {
            var buf = Array(name.utf8CString)
            return buf.withUnsafeMutableBufferPointer { (p: inout UnsafeMutableBufferPointer<CChar>) -> UInt32 in
                lib.makeFolder(dev, p.baseAddress!, pid == Self.root ? 0 : pid, sid)
            }
        }
        if id == 0 { throw Shell.Failure(message: "Couldn't make the folder \(folder) on the reader.") }
        var e = entries(parent)
        e[name] = (id, 0, true)
        folders[parent] = e
        folders[folder] = [:]
        return id
    }

    func put(_ local: URL, as lpath: String) throws {
        if find(lpath) != nil { try remove(lpath) }
        let folder = (lpath as NSString).deletingLastPathComponent, name = (lpath as NSString).lastPathComponent
        let pid = try makeFolder(folder)
        let size = Int64((try? local.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0)
        let (r, id) = lib.queue.sync { () -> (Int32, UInt32) in
            guard let f = lib.newFile() else { return (-1, 0) }
            f.pointee.filename = strdup(name)
            f.pointee.filesize = UInt64(size)
            f.pointee.filetype = name.lowercased().hasSuffix(".jpg") ? 14 : 44     // JPEG, UNKNOWN
            f.pointee.parent_id = pid == Self.root ? 0 : pid
            f.pointee.storage_id = sid
            let r = lib.sendFile(dev, local.path, f, nil, nil)
            let id = f.pointee.item_id
            lib.freeFile(f)
            lib.clearErrors(dev)
            return (r, id)
        }
        if r != 0 { throw Shell.Failure(message: "Couldn't copy \(name) to the reader.") }
        var e = entries(folder)
        e[name] = (id, size, false)
        folders[folder] = e
    }

    func remove(_ lpath: String) throws {
        guard let e = find(lpath) else { return }
        if e.dir {
            for name in entries(lpath).keys { try remove(lpath + "/" + name) }
            folders[lpath] = nil
        }
        _ = lib.queue.sync {
            let r = lib.delete(dev, e.id)
            lib.clearErrors(dev)
            return r
        }
        let folder = (lpath as NSString).deletingLastPathComponent
        var es = entries(folder)
        es[(lpath as NSString).lastPathComponent] = nil
        folders[folder] = es
    }

    func eject() {
        lib.queue.sync {
            if !released { lib.release(dev) }
            released = true
        }
    }
}

// MARK: - Finding readers

enum ReaderDetect {
    static func kind(of names: [String]) -> String {
        let n = names.joined(separator: " ").lowercased()
        if n.contains("kindle") || n.contains("amazon") {
            return n.contains("scribe") ? "kindle-scribe" : n.contains("oasis") ? "kindle-oasis" : "kindle"
        }
        for (k, words) in [("kobo", ["kobo", "rakuten"]), ("pocketbook", ["pocketbook"]), ("boox", ["boox", "onyx"]),
                           ("tolino", ["tolino"]), ("nook", ["nook", "barnes"])] where words.contains(where: n.contains) {
            return k
        }
        return "generic"
    }

    private static func has(_ root: URL, _ parts: String...) -> Bool {
        var cur = root
        for p in parts {
            guard let m = (try? FileManager.default.contentsOfDirectory(atPath: cur.path))?.first(where: { $0.lowercased() == p }) else { return false }
            cur = cur.appendingPathComponent(m)
        }
        return true
    }

    /// A mounted drive that's an e-reader: (kind, name).
    static func volumeKind(_ root: URL) -> (String, String)? {
        if has(root, "documents") && has(root, "system") { return ("kindle", "Kindle") }
        if has(root, ".kobo") { return ("kobo", "Kobo eReader") }
        if has(root, "system", "config") && has(root, "system", "profiles") { return ("pocketbook", "PocketBook") }
        return nil
    }

    enum Candidate: Hashable { case volume(URL), mtp(MTPLib.Raw) }

    /// What's attached, cheaply. BOOKFORGE_TEST_READER adds a folder that stands in for a reader (testing).
    static func candidates() -> [Candidate] {
        var vols = (FileManager.default.mountedVolumeURLs(includingResourceValuesForKeys: nil, options: [.skipHiddenVolumes]) ?? [])
            .filter { $0.path.hasPrefix("/Volumes/") }
        if let t = ProcessInfo.processInfo.environment["BOOKFORGE_TEST_READER"] { vols = [URL(fileURLWithPath: t)] }
        return vols.filter { volumeKind($0) != nil }.map(Candidate.volume) + MTPLib.shared.attached().map(Candidate.mtp)
    }
}

// MARK: - Books on a reader

enum ReaderOps {
    static func bookDir(_ kind: String) -> String { kind.hasPrefix("kindle") ? "documents/" : "Books/" }
    static func indexPath(_ kind: String) -> String { bookDir(kind) + "bookforge.json" }

    /// What the reader opens, best first.
    static func formats(_ kind: String, koreader: Bool) -> [String] {
        if kind.hasPrefix("kindle") { return (koreader ? ["epub"] : []) + ["azw3", "mobi", "pdf", "txt"] }
        switch kind {
        case "kobo": return ["kepub", "epub", "pdf", "cbz"]
        case "pocketbook": return ["epub", "pdf", "fb2", "mobi", "djvu", "cbz"]
        default: return ["epub", "pdf"]
        }
    }

    struct Entry: Codable { var title: String; var authors: [String]; var uuid: String }

    /// What Book Forge knows about the reader's books, by lowercased path: its own bookforge.json, and Calibre's
    /// metadata.calibre for books sent by older versions (Calibre writes MTP paths in lowercase).
    static func loadIndex(_ s: ReaderStorage) -> [String: Entry] {
        var known: [String: Entry] = [:]
        for name in ["metadata.calibre", ".metadata.calibre"] {
            if let d = s.readSmall(name), let list = try? JSONSerialization.jsonObject(with: d) as? [[String: Any]] {
                for b in list {
                    let lp = (b["lpath"] as? String ?? "").replacingOccurrences(of: "\\", with: "/").lowercased()
                    if !lp.isEmpty {
                        known[lp] = Entry(title: b["title"] as? String ?? "", authors: b["authors"] as? [String] ?? [],
                                          uuid: b["uuid"] as? String ?? "")
                    }
                }
            }
        }
        if let d = s.readSmall(indexPath(s.kind)), let ours = try? JSONDecoder().decode([String: Entry].self, from: d) {
            for (lp, e) in ours { known[lp.lowercased()] = e }
        }
        return known
    }

    static func saveIndex(_ s: ReaderStorage, _ entries: [String: Entry]) {
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys]
        if let d = try? enc.encode(entries) { try? s.writeSmall(indexPath(s.kind), d) }
    }

    private static let titleCacheURL = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("com.widagdos.bookforge/reader-titles.json")
    nonisolated(unsafe) private static var titleCache: [String: [String]]? = nil

    /// Title and authors of a book Book Forge didn't put there: from the file itself (cached by path and size), else
    /// from its name. Over MTP only the start of Kindle books is read, and EPUBs only when they're small.
    static func readTitle(_ s: ReaderStorage, _ lpath: String, _ size: Int64) -> (String, [String]) {
        let fallback = ReaderMeta.fromFilename(lpath)
        let e = ReaderMeta.ext(lpath)
        guard ReaderMeta.mobiExts.contains(e) || ["epub", "kepub", "pdf"].contains(e) else { return fallback }
        if titleCache == nil {
            titleCache = (try? Data(contentsOf: titleCacheURL)).flatMap { try? JSONDecoder().decode([String: [String]].self, from: $0) } ?? [:]
        }
        let key = "\(s.name)|\(lpath)|\(size)"
        if let c = titleCache?[key] { return c.isEmpty ? fallback : (c[0], Array(c.dropFirst())) }
        var found: (String, [String])?
        if ReaderMeta.mobiExts.contains(e) {
            if let d = try? s.head(lpath, 65536), let m = ReaderMeta.Mobi(d), let t = m.title, !t.isEmpty { found = (t, m.authors) }
        } else if let root = s.localRoot ?? nil {
            let meta = Library.readMeta(root.appendingPathComponent(lpath))
            if e != "pdf" || !meta.author.isEmpty { found = (meta.title, meta.author.components(separatedBy: " & ").filter { !$0.isEmpty }) }
        } else if e != "pdf", size < 20_000_000 {
            let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("bookforge-\(UUID().uuidString).epub")
            defer { try? FileManager.default.removeItem(at: tmp) }
            if (try? s.get(lpath, to: tmp)) != nil {
                let meta = Library.readMeta(tmp)
                found = (meta.title, meta.author.components(separatedBy: " & ").filter { !$0.isEmpty })
            }
        }
        titleCache?[key] = found.map { [$0.0] + $0.1 } ?? []
        return found ?? fallback
    }

    static func saveTitleCache() {
        guard let c = titleCache, let d = try? JSONEncoder().encode(c) else { return }
        try? FileManager.default.createDirectory(at: titleCacheURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? d.write(to: titleCacheURL)
    }

    static func listBooks(_ s: ReaderStorage, files: [(lpath: String, size: Int64)]) -> [DeviceBook] {
        let known = loadIndex(s)
        let books = files.filter { ReaderMeta.isBook($0.lpath) }.map { f -> DeviceBook in
            let k = known[f.lpath.lowercased()]
            let (title, authors) = (k?.title).flatMap { $0.isEmpty ? nil : ($0, k?.authors ?? []) } ?? readTitle(s, f.lpath, f.size)
            return DeviceBook(title: title, authors: authors, path: f.lpath, lpath: f.lpath, uuid: k?.uuid ?? "", size: f.size)
        }
        saveTitleCache()
        return books
    }

    static func safeName(_ title: String) -> String {
        let s = String(title.filter { !"\\/:*?\"<>|".contains($0) }.prefix(80)).trimmingCharacters(in: .whitespaces)
        return s.isEmpty ? "Book" : s
    }

    /// Kindles show sideloaded books' covers from system/thumbnails, named after the book's ASIN and type.
    static func kindleThumbnail(_ s: ReaderStorage, file: URL, cover: URL?) {
        guard let cover, let head = try? FileHandle(forReadingFrom: file).read(upToCount: 65536),
              let m = ReaderMeta.Mobi(head), let asin = m.asin,
              let img = NSImage(contentsOf: cover), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return }
        let scale = min(330 / Double(cg.width), 470 / Double(cg.height), 1)
        let w = Int(Double(cg.width) * scale), h = Int(Double(cg.height) * scale)
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return }
        ctx.interpolationQuality = .high
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        guard let small = ctx.makeImage() else { return }
        try? s.writeSmall("system/thumbnails/thumbnail_\(asin)_\(m.cdetype ?? "EBOK")_portrait.jpg", EPUBWriter.jpeg(small, quality: 0.9))
    }

    struct Item { var file: URL; var title: String; var authors: [String]; var uuid: String; var cover: URL? }

    /// Replaces older copies of the same books, copies the new ones and records them. Returns how many were sent.
    static func upload(_ s: ReaderStorage, _ items: [Item], log: (String) -> Void) throws -> Int {
        let files = try s.files()
        let books = listBooks(s, files: files)
        var ours = loadIndex(s)
        let uuids = Set(items.map(\.uuid))
        let titles = Set(items.map { "\(Library.key($0.title))|\($0.file.pathExtension.lowercased())" })
        let old = books.filter { uuids.contains($0.uuid) || titles.contains("\(Library.key($0.title))|\(ReaderMeta.ext($0.lpath))") }.map(\.lpath)
        if !old.isEmpty {
            log("Replacing \(old.count) older cop\(old.count == 1 ? "y" : "ies")…")
            try removeFiles(s, old)
            for lp in old { ours[lp.lowercased()] = nil }
        }
        var present = Set(books.map { $0.lpath.lowercased() }).subtracting(old.map { $0.lowercased() })
        for (i, it) in items.enumerated() {
            let e = it.file.pathExtension.lowercased()
            let lpath = "\(bookDir(s.kind))\(safeName(it.title)) - \(it.uuid.prefix(8).lowercased()).\(e)"
            log("Copying \(i + 1) of \(items.count) to \(s.name)…")
            try s.put(it.file, as: lpath)
            if s.kind.hasPrefix("kindle"), ReaderMeta.mobiExts.contains(e) { kindleThumbnail(s, file: it.file, cover: it.cover) }
            ours[lpath.lowercased()] = Entry(title: it.title, authors: it.authors, uuid: it.uuid)
            present.insert(lpath.lowercased())
        }
        saveIndex(s, ours.filter { present.contains($0.key) })
        return items.count
    }

    static func removeFiles(_ s: ReaderStorage, _ lpaths: [String]) throws {
        for lp in lpaths {
            try s.remove(lp)
            // the Kindle keeps reading position and notes next to the book in "<name>.sdr"
            if s.kind.hasPrefix("kindle") { try s.remove((lp as NSString).deletingPathExtension + ".sdr") }
        }
    }

    static func delete(_ s: ReaderStorage, _ lpaths: [String]) throws {
        try removeFiles(s, lpaths)
        var ours = loadIndex(s)
        for lp in lpaths { ours[lp.lowercased()] = nil }
        let present = Set(try s.files().map { $0.lpath.lowercased() })
        saveIndex(s, ours.filter { present.contains($0.key) })
    }

    /// True if a MOBI/AZW file's header says it's encrypted (DRM), nil if unknown.
    static func locked(_ s: ReaderStorage, _ lpath: String) -> Bool? {
        guard ReaderMeta.mobiExts.contains(ReaderMeta.ext(lpath)), let d = try? s.head(lpath, 4096) else { return nil }
        return ReaderMeta.Mobi(d)?.encrypted
    }
}

// MARK: - The connection Devices talks to

/// Keeps the open reader and answers Devices' requests in the shape device.py used to.
final class ReaderLink: @unchecked Sendable {
    static let shared = ReaderLink()
    private let lock = NSRecursiveLock()
    private var storage: ReaderStorage?
    private var key: ReaderDetect.Candidate?
    private var ejected: ReaderDetect.Candidate?

    func info() -> Devices.ScriptResult {
        lock.lock(); defer { lock.unlock() }
        let found = ReaderDetect.candidates()
        if let key, !found.contains(key) { storage = nil; self.key = nil }        // unplugged: drop without releasing
        if ejected != nil, !found.contains(ejected!) { ejected = nil }
        if storage == nil {
            // an MTP reader before a drive
            guard let c = found.sorted(by: { a, _ in if case .mtp = a { return true } else { return false } })
                .first(where: { $0 != ejected }) else { return Devices.ScriptResult(status: "no_device") }
            switch c {
            case .volume(let url):
                let (kind, name) = ReaderDetect.volumeKind(url)!
                storage = FolderStorage(root: url, name: name, kind: kind)
            case .mtp(let raw):
                guard let s = MTPStorage(raw) else {
                    return Devices.ScriptResult(status: "open_failed", name: raw.product.isEmpty ? "Your e-reader" : raw.product,
                                                kind: ReaderDetect.kind(of: [raw.vendor, raw.product]))
                }
                storage = s
            }
            key = c
        }
        guard let s = storage else { return Devices.ScriptResult(status: "no_device") }
        do {
            let files = try s.files()
            let books = ReaderOps.listBooks(s, files: files)
            let koreader = s.kind.hasPrefix("kindle") && s.exists("koreader")
            let (total, free) = s.space()
            return Devices.ScriptResult(status: "ok", name: s.name, kind: s.kind, driver: String(describing: type(of: s)),
                                        formats: ReaderOps.formats(s.kind, koreader: koreader), total: total, free: free, books: books)
        } catch {
            storage = nil
            key = nil
            return Devices.ScriptResult(status: "error", error: error.localizedDescription)
        }
    }

    private func current() throws -> ReaderStorage {
        guard let s = storage else { throw Shell.Failure(message: "The e-reader isn't connected any more.") }
        return s
    }

    func upload(_ items: [ReaderOps.Item], log: (String) -> Void) throws -> Int {
        lock.lock(); defer { lock.unlock() }
        return try ReaderOps.upload(try current(), items, log: log)
    }

    func delete(_ paths: [String]) throws {
        lock.lock(); defer { lock.unlock() }
        try ReaderOps.delete(try current(), paths)
    }

    func fetch(_ paths: [String], to dest: URL, log: (String) -> Void) throws -> (files: [Devices.Fetched], failed: [Devices.Fetched]) {
        lock.lock(); defer { lock.unlock() }
        let s = try current()
        try FileManager.default.createDirectory(at: dest, withIntermediateDirectories: true)
        var files: [Devices.Fetched] = [], failed: [Devices.Fetched] = []
        for (i, lp) in paths.enumerated() {
            var stem = ((lp as NSString).lastPathComponent as NSString).deletingPathExtension
            let e = (lp as NSString).pathExtension
            if stem.lowercased().hasSuffix(".kepub") { stem = String(stem.dropLast(6)) }   // Kobo's "book.kepub.epub"
            var out = dest.appendingPathComponent(stem).appendingPathExtension(e)
            var n = 2
            while FileManager.default.fileExists(atPath: out.path) {
                out = dest.appendingPathComponent("\(stem) \(n)").appendingPathExtension(e)
                n += 1
            }
            log("Copying \(i + 1) of \(paths.count) from the reader…")
            do {
                try s.get(lp, to: out)
                files.append(Devices.Fetched(path: lp, file: out.path))
            } catch {
                failed.append(Devices.Fetched(path: lp, error: error.localizedDescription))
            }
        }
        return (files, failed)
    }

    func locked(_ path: String) -> Bool? {
        lock.lock(); defer { lock.unlock() }
        return storage.flatMap { ReaderOps.locked($0, path) }
    }

    func eject() {
        lock.lock(); defer { lock.unlock() }
        storage?.eject()
        ejected = key
        storage = nil
        key = nil
    }
}
