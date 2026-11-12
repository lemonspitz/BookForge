import AppKit
import ImageIO
import UniformTypeIdentifiers

// Kindle books without Calibre, the same way as the Windows and Linux app (windows-linux/bookforge/kindlebook.py
// and kindleread.py):
//   KindleBook.write(epub:to:asin:cover:title:authors:)   EPUB → AZW3 (Kindle Format 8)
//   KindleRead.toEPUB(_:to:)                               AZW3, MOBI, AZW, PRC → EPUB
//
// An AZW3 file is a Palm database: record 0 holds the MOBI header and metadata (EXTH), then come the text in
// 4 KB records, the indexes that say how the text is cut into files (skeletons and fragments) and the table of
// contents, the images and fonts, and a few fixed records.

// MARK: - Bytes

typealias Bytes = [UInt8]

enum KF {
    static let null = 0xffff_ffff
    static let recordSize = 4096
    static let chunkSize = 8192
    static let xhtmlNS = "http://www.w3.org/1999/xhtml"
    static let digits = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    static func be16(_ v: Int) -> Bytes { [UInt8((v >> 8) & 0xff), UInt8(v & 0xff)] }
    static func be32(_ v: Int) -> Bytes {
        [UInt8((v >> 24) & 0xff), UInt8((v >> 16) & 0xff), UInt8((v >> 8) & 0xff), UInt8(v & 0xff)]
    }
    static func u16(_ b: Bytes, _ o: Int) -> Int { o + 2 <= b.count && o >= 0 ? Int(b[o]) << 8 | Int(b[o + 1]) : 0 }
    static func u32(_ b: Bytes, _ o: Int) -> Int {
        o + 4 <= b.count && o >= 0 ? Int(b[o]) << 24 | Int(b[o + 1]) << 16 | Int(b[o + 2]) << 8 | Int(b[o + 3]) : 0
    }
    static func align(_ b: Bytes) -> Bytes { b + Bytes(repeating: 0, count: (4 - b.count % 4) % 4) }
    static func utf8(_ s: String) -> Bytes { Array(s.utf8) }

    /// Forward variable-width integer: 7 bits a byte, the last byte flagged with its high bit.
    static func encint(_ value: Int) -> Bytes {
        var v = value
        var out: Bytes = [UInt8(v & 0x7f)]
        v >>= 7
        while v > 0 {
            out.insert(UInt8(v & 0x7f), at: 0)
            v >>= 7
        }
        out[out.count - 1] |= 0x80
        return out
    }

    static func decint(_ b: Bytes, _ start: Int) -> (Int, Int) {
        var v = 0, pos = start
        while pos < b.count {
            let c = Int(b[pos])
            pos += 1
            v = (v << 7) | (c & 0x7f)
            if c & 0x80 != 0 { break }
        }
        return (v, pos)
    }

    static func base32(_ n: Int, _ width: Int) -> String {
        var s = "", v = n
        while v > 0 {
            s = String(digits[v % 32]) + s
            v /= 32
        }
        return String(repeating: "0", count: max(0, width - s.count)) + s
    }

    static func find(_ hay: Bytes, _ needle: Bytes, from: Int = 0) -> Int? {
        guard !needle.isEmpty, hay.count >= needle.count, from <= hay.count - needle.count else { return nil }
        let first = needle[0]
        var i = max(0, from)
        let last = hay.count - needle.count
        while i <= last {
            if hay[i] == first {
                var ok = true
                for j in 1..<needle.count where hay[i + j] != needle[j] {
                    ok = false
                    break
                }
                if ok { return i }
            }
            i += 1
        }
        return nil
    }

    static func rfind(_ hay: Bytes, _ needle: Bytes, before: Int? = nil) -> Int? {
        guard !needle.isEmpty, hay.count >= needle.count else { return nil }
        var i = min((before ?? hay.count) - 1, hay.count - needle.count)
        while i >= 0 {
            if hay[i] == needle[0] && Array(hay[i..<(i + needle.count)]) == needle { return i }
            i -= 1
        }
        return nil
    }

    // MARK: strings

    /// Replaces every match of `pattern`, giving `transform` the match and its groups ("" for groups that didn't take part).
    static func sub(_ text: String, _ pattern: String, _ options: NSRegularExpression.Options = [],
                    _ transform: ([String]) -> String) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern, options: options) else { return text }
        let ns = text as NSString
        var out = "", last = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            let groups = (0..<m.numberOfRanges).map { m.range(at: $0).location == NSNotFound ? "" : ns.substring(with: m.range(at: $0)) }
            out += transform(groups)
            last = m.range.location + m.range.length
        }
        return out + ns.substring(from: last)
    }

    static func firstMatch(_ text: String, _ pattern: String, _ options: NSRegularExpression.Options = []) -> [String]? {
        guard let re = try? NSRegularExpression(pattern: pattern, options: options) else { return nil }
        let ns = text as NSString
        guard let m = re.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return (0..<m.numberOfRanges).map { m.range(at: $0).location == NSNotFound ? "" : ns.substring(with: m.range(at: $0)) }
    }

    static func escape(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
    }

    static let void: Set<String> = ["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                                    "source", "track", "wbr"]

    /// XML with `<div/>` written out as `<div></div>`, which HTML renderers read correctly.
    static func openEmpty(_ xml: String) -> String {
        sub(xml, #"<([A-Za-z][\w.-]*)((?:\s[^<>]*?)?)/>"#) { g in
            void.contains(g[1].lowercased()) || g[1].contains(":") ? g[0] : "<\(g[1])\(g[2])></\(g[1])>"
        }
    }

    /// An archive path for `href` written in a file in folder `base`.
    static func join(_ base: String, _ href: String) -> String {
        var clean = href.components(separatedBy: "#")[0].components(separatedBy: "?")[0]
        clean = clean.removingPercentEncoding ?? clean
        if clean.isEmpty { return "" }
        var parts = clean.hasPrefix("/") ? [] : base.split(separator: "/").map(String.init)
        for p in clean.split(separator: "/") {
            if p == ".." { if !parts.isEmpty { parts.removeLast() } } else if p != "." { parts.append(String(p)) }
        }
        return parts.joined(separator: "/")
    }

    static func dir(_ path: String) -> String { (path as NSString).deletingLastPathComponent }

    static func isExternal(_ href: String) -> Bool { firstMatch(href, #"^[a-zA-Z][a-zA-Z0-9+.-]*:"#) != nil }

    static func fragment(_ href: String) -> String {
        let parts = href.components(separatedBy: "#")
        return parts.count > 1 ? parts[1] : ""
    }

    // MARK: XML

    static func elements(_ node: XMLNode) -> [XMLElement] {
        var out: [XMLElement] = []
        func walk(_ n: XMLNode) {
            if let e = n as? XMLElement { out.append(e) }
            for c in n.children ?? [] { walk(c) }
        }
        walk(node)
        return out
    }

    static func local(_ n: XMLNode) -> String { (n.localName ?? n.name ?? "").lowercased() }

    static func child(_ e: XMLElement, _ name: String) -> XMLElement? {
        (e.children ?? []).compactMap { $0 as? XMLElement }.first { local($0) == name }
    }

    static func attr(_ e: XMLElement, _ name: String) -> String? { e.attribute(forName: name)?.stringValue }

    static func setAttr(_ e: XMLElement, _ name: String, _ value: String) {
        e.removeAttribute(forName: name)
        e.addAttribute(XMLNode.attribute(withName: name, stringValue: value) as! XMLNode)
    }

    static func parseXHTML(_ data: Data) -> XMLDocument? {
        if let d = try? XMLDocument(data: data, options: [.nodeLoadExternalEntitiesNever]), d.rootElement() != nil { return d }
        return try? XMLDocument(data: data, options: [.documentTidyHTML])
    }

    static func serialize(_ n: XMLNode) -> String {
        openEmpty(n.xmlString(options: [.nodeCompactEmptyElement]))
    }

    // MARK: images

    static func imageType(_ d: Bytes) -> String? {
        if d.count > 8 && d[0] == 0xff && d[1] == 0xd8 && d[2] == 0xff { return "jpg" }
        if d.count > 8 && Array(d[0..<8]) == [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a] { return "png" }
        if d.count > 6 && (Array(d[0..<6]) == utf8("GIF87a") || Array(d[0..<6]) == utf8("GIF89a")) { return "gif" }
        if d.count > 2 && d[0] == 0x42 && d[1] == 0x4d { return "bmp" }
        if d.count > 12 && Array(d[0..<4]) == utf8("RIFF") && Array(d[8..<12]) == utf8("WEBP") { return "webp" }
        if d.count > 5 && (Array(d[0..<5]) == utf8("<?xml") || Array(d[0..<4]) == utf8("<svg")) { return "svg" }
        return nil
    }

    static func encode(_ img: CGImage, png: Bool) -> Data {
        let data = NSMutableData()
        let type = png ? UTType.png.identifier : UTType.jpeg.identifier
        guard let dest = CGImageDestinationCreateWithData(data, type as CFString, 1, nil) else { return Data() }
        CGImageDestinationAddImage(dest, img, png ? nil : [kCGImageDestinationLossyCompressionQuality: 0.88] as CFDictionary)
        CGImageDestinationFinalize(dest)
        return data as Data
    }

    /// An image the Kindle shows: JPEG, PNG and GIF as they are, anything else made into JPEG or PNG.
    static func kindleImage(_ data: Data) -> (Bytes, String)? {
        let b = Bytes(data)
        switch imageType(b) {
        case "jpg": return (b, "image/jpeg")
        case "png": return (b, "image/png")
        case "gif": return (b, "image/gif")
        default:
            guard let src = CGImageSourceCreateWithData(data as CFData, nil),
                  let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return nil }
            let alpha = ![.none, .noneSkipFirst, .noneSkipLast].contains(img.alphaInfo)
            return (Bytes(encode(img, png: alpha)), alpha ? "image/png" : "image/jpeg")
        }
    }

    static func thumbnail(_ data: Bytes) -> Bytes? {
        guard let src = CGImageSourceCreateWithData(Data(data) as CFData, nil),
              let img = CGImageSourceCreateThumbnailAtIndex(src, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 470,
                kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary) else { return nil }
        return Bytes(encode(img, png: false))
    }

    // MARK: archives

    static func unzip(_ file: URL) throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("bookforge-kf-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Shell.run("/usr/bin/unzip", ["-q", "-o", file.path, "-d", dir.path])
        return dir
    }

    /// Writes an EPUB from {path inside the archive: bytes}.
    static func zipEPUB(_ files: [String: Data], to out: URL) throws {
        let fm = FileManager.default
        let tmp = fm.temporaryDirectory.appendingPathComponent("bookforge-epub-\(UUID().uuidString)")
        defer { try? fm.removeItem(at: tmp) }
        try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
        try Data("application/epub+zip".utf8).write(to: tmp.appendingPathComponent("mimetype"))
        for (path, data) in files {
            let url = tmp.appendingPathComponent(path)
            try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url)
        }
        try? fm.removeItem(at: out)
        try Shell.run("/usr/bin/zip", ["-X0q", out.path, "mimetype"], cwd: tmp)
        try Shell.run("/usr/bin/zip", ["-Xr9Dq", out.path, "META-INF", "OEBPS"], cwd: tmp)
    }
}

// MARK: - Index records (INDX)

/// The string table index entries point into: each string is its length then UTF-8, in 64 KB records.
struct CNCX {
    var offsets: [String: Int] = [:]
    var records: [Bytes] = []
    private var cur: Bytes = []

    @discardableResult
    mutating func add(_ s: String) -> Int {
        if let o = offsets[s] { return o }
        let raw = Array(KF.utf8(s).prefix(500))
        let item = KF.encint(raw.count) + raw
        if cur.count + item.count > 0xfbf8 {
            records.append(KF.align(cur))
            cur = []
        }
        let o = records.count * 0x10000 + cur.count
        offsets[s] = o
        cur += item
        return o
    }

    mutating func finish() -> [Bytes] {
        if !cur.isEmpty {
            records.append(KF.align(cur))
            cur = []
        }
        return records
    }
}

/// INDX records: a header (with the tag table), entry records, then the CNCX string records.
/// tags: [(tag, values per entry, mask)]; entries: [(key, [tag: values])].
func buildIndex(_ entries: [(String, [Int: [Int]])], tags: [(Int, Int, Int)], cncx: [Bytes] = []) -> [Bytes] {
    let limit = 0x10000 - 192 - 1048
    var blocks: [(body: Bytes, idxt: Bytes, count: Int, last: Bytes)] = [([], [], 0, [])]
    for (key, values) in entries {
        let k = KF.utf8(key)
        var cb = 0
        for (tag, vpe, mask) in tags {
            let n = (values[tag]?.count ?? 0) / vpe
            let shift = mask.trailingZeroBitCount
            cb |= mask & (n << shift)
        }
        var raw: Bytes = [UInt8(k.count)] + k + [UInt8(cb)]
        for (tag, _, _) in tags {
            for v in values[tag] ?? [] { raw += KF.encint(v) }
        }
        if blocks[blocks.count - 1].body.count + blocks[blocks.count - 1].idxt.count + raw.count + 2 > limit {
            blocks.append(([], [], 0, []))
        }
        let i = blocks.count - 1
        blocks[i].idxt += KF.be16(192 + blocks[i].body.count)
        blocks[i].body += raw
        blocks[i].count += 1
        blocks[i].last = k
    }
    var records: [Bytes] = []
    for b in blocks {
        let body = KF.align(b.body)
        var head = KF.utf8("INDX") + KF.be32(192)
        head += KF.be32(0) + KF.be32(1)
        head += KF.be32(0) + KF.be32(192 + body.count)
        head += KF.be32(b.count)
        head += Bytes(repeating: 0xff, count: 8)
        head += Bytes(repeating: 0, count: 156)
        records.append(head + body + KF.align(KF.utf8("IDXT") + b.idxt))
    }
    var tagx = KF.utf8("TAGX")
    tagx += KF.be32(12 + 4 * (tags.count + 1)) + KF.be32(1)
    for (tag, vpe, mask) in tags { tagx += [UInt8(tag), UInt8(vpe), UInt8(mask), 0] }
    tagx += [0, 0, 0, 1]
    var geometry: Bytes = [], idxt = KF.utf8("IDXT"), pos = 192 + tagx.count
    for b in blocks {
        let g: Bytes = [UInt8(b.last.count)] + b.last + KF.be16(b.count)
        idxt += KF.be16(pos)
        geometry += g
        pos += g.count
    }
    geometry = KF.align(geometry)
    var header = KF.utf8("INDX") + KF.be32(192)
    header += KF.be32(0) + KF.be32(0)
    header += KF.be32(2)
    header += KF.be32(192 + tagx.count + geometry.count)
    header += KF.be32(records.count) + KF.be32(65001)
    header += KF.be32(KF.null)
    header += KF.be32(blocks.reduce(0) { $0 + $1.count })
    header += KF.be32(0) + KF.be32(0)
    header += KF.be32(0) + KF.be32(cncx.count)
    header += Bytes(repeating: 0, count: 180 - header.count)
    header += KF.be32(192)
    header += Bytes(repeating: 0, count: 8)
    return [header + tagx + geometry + KF.align(idxt)] + records + cncx
}

// MARK: - EPUB → AZW3

enum KindleBook {
    struct Failure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    static let locales: [String: Int] = [
        "en": 0x09, "id": 0x21, "ja": 0x11, "ko": 0x12, "zh": 0x04, "zh-hans": 0x804, "zh-cn": 0x804, "zh-hant": 0x404,
        "zh-tw": 0x404, "zh-hk": 0xc04, "th": 0x1e, "vi": 0x2a, "fr": 0x0c, "de": 0x07, "es": 0x0a, "it": 0x10,
        "pt": 0x16, "nl": 0x13, "ru": 0x19, "pl": 0x15, "tr": 0x1f, "ar": 0x01]

    static func locale(_ lang: String) -> Int {
        let l = lang.lowercased().replacingOccurrences(of: "_", with: "-")
        return locales[l] ?? locales[l.components(separatedBy: "-")[0]] ?? 0
    }

    /// The parts of an unpacked EPUB the converter needs.
    final class Source {
        let root: URL
        var title = "Unknown", authors: [String] = [], language = "en", publisher = "", summary = "", date = ""
        var subjects: [String] = []
        var manifest: [String: (path: String, type: String, props: String)] = [:]
        var spine: [String] = []
        var cover: String?
        var direction: String?
        var toc: [(label: String, path: String, frag: String, depth: Int)] = []

        init(_ root: URL) throws {
            self.root = root
            guard let c = read("META-INF/container.xml"), let cdoc = try? XMLDocument(data: c, options: []),
                  let rf = (try? cdoc.nodes(forXPath: "//*[local-name()='rootfile']"))?.first as? XMLElement,
                  let opfPath = KF.attr(rf, "full-path"), let o = read(opfPath),
                  let opf = try? XMLDocument(data: o, options: [.nodeLoadExternalEntitiesNever]) else {
                throw Failure(message: "This EPUB has no package file.")
            }
            let base = KF.dir(opfPath)
            func dc(_ name: String) -> [String] {
                ((try? opf.nodes(forXPath: "//*[local-name()='\(name)']")) ?? [])
                    .compactMap { $0.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
            }
            title = dc("title").first ?? "Unknown"
            authors = dc("creator")
            language = dc("language").first ?? "en"
            publisher = dc("publisher").first ?? ""
            summary = KF.sub(dc("description").first ?? "", "<[^>]+>") { _ in "" }
            date = dc("date").first ?? ""
            subjects = dc("subject")
            let items = ((try? opf.nodes(forXPath: "//*[local-name()='manifest']/*[local-name()='item']")) ?? []).compactMap { $0 as? XMLElement }
            for it in items {
                guard let id = KF.attr(it, "id") else { continue }
                manifest[id] = (KF.join(base, KF.attr(it, "href") ?? ""), KF.attr(it, "media-type") ?? "", KF.attr(it, "properties") ?? "")
            }
            let spineEl = (try? opf.nodes(forXPath: "//*[local-name()='spine']"))?.first as? XMLElement
            direction = spineEl.flatMap { KF.attr($0, "page-progression-direction") }
            for ref in ((try? opf.nodes(forXPath: "//*[local-name()='spine']/*[local-name()='itemref']")) ?? []).compactMap({ $0 as? XMLElement }) {
                guard let id = KF.attr(ref, "idref"), let m = manifest[id], exists(m.path) else { continue }
                let lower = m.path.lowercased()
                if (m.type.contains("html") || lower.hasSuffix(".html") || lower.hasSuffix(".xhtml") || lower.hasSuffix(".htm"))
                    && !spine.contains(m.path) { spine.append(m.path) }
            }
            guard !spine.isEmpty else { throw Failure(message: "This EPUB has no pages to convert.") }
            cover = manifest.values.first { $0.props.split(separator: " ").contains("cover-image") && exists($0.path) }?.path
            if cover == nil, let meta = ((try? opf.nodes(forXPath: "//*[local-name()='meta'][@name='cover']")) ?? []).first as? XMLElement,
               let id = KF.attr(meta, "content"), let m = manifest[id], m.type.hasPrefix("image/") {
                cover = m.path
            }
            if cover == nil {
                cover = manifest.values.first { $0.type.hasPrefix("image/") && ($0.path as NSString).lastPathComponent.lowercased().contains("cover") }?.path
            }
            toc = readNav() ?? []
            if toc.isEmpty { toc = readNCX(spineEl.flatMap { KF.attr($0, "toc") }) }
        }

        func url(_ path: String) -> URL { root.appendingPathComponent(path) }
        func exists(_ path: String) -> Bool { !path.isEmpty && FileManager.default.fileExists(atPath: url(path).path) }
        func read(_ path: String) -> Data? { path.isEmpty ? nil : try? Data(contentsOf: url(path)) }

        private func readNav() -> [(label: String, path: String, frag: String, depth: Int)]? {
            guard let nav = manifest.values.first(where: { $0.props.split(separator: " ").contains("nav") })?.path,
                  let data = read(nav), let doc = KF.parseXHTML(data), let root = doc.rootElement() else { return nil }
            let navs = KF.elements(root).filter { KF.local($0) == "nav" }
            let tocNav = navs.first { (KF.attr($0, "epub:type") ?? KF.attr($0, "type") ?? "").contains("toc") } ?? navs.first
            guard let tocNav else { return nil }
            var out: [(label: String, path: String, frag: String, depth: Int)] = []
            func walk(_ ol: XMLElement, _ depth: Int) {
                for li in (ol.children ?? []).compactMap({ $0 as? XMLElement }) where KF.local(li) == "li" {
                    if let a = KF.elements(li).first(where: { KF.local($0) == "a" }), let href = KF.attr(a, "href") {
                        let label = (a.stringValue ?? "").split(whereSeparator: \.isWhitespace).joined(separator: " ")
                        out.append((label.isEmpty ? "•" : label, KF.join(KF.dir(nav), href), KF.fragment(href), depth))
                    }
                    for sub in (li.children ?? []).compactMap({ $0 as? XMLElement }) where KF.local(sub) == "ol" { walk(sub, depth + 1) }
                }
            }
            for ol in (tocNav.children ?? []).compactMap({ $0 as? XMLElement }) where KF.local(ol) == "ol" { walk(ol, 0) }
            return out
        }

        private func readNCX(_ id: String?) -> [(label: String, path: String, frag: String, depth: Int)] {
            let path = id.flatMap { manifest[$0]?.path } ?? manifest.values.first { $0.type == "application/x-dtbncx+xml" }?.path
            guard let path, let data = read(path), let doc = try? XMLDocument(data: data, options: [.nodeLoadExternalEntitiesNever]),
                  let root = doc.rootElement(), let map = KF.elements(root).first(where: { KF.local($0) == "navmap" }) else { return [] }
            var out: [(label: String, path: String, frag: String, depth: Int)] = []
            func walk(_ parent: XMLElement, _ depth: Int) {
                for np in (parent.children ?? []).compactMap({ $0 as? XMLElement }) where KF.local(np) == "navpoint" {
                    let label = (KF.child(np, "navlabel")?.stringValue ?? "").split(whereSeparator: \.isWhitespace).joined(separator: " ")
                    if let c = KF.child(np, "content"), let src = KF.attr(c, "src") {
                        out.append((label.isEmpty ? "•" : label, KF.join(KF.dir(path), src), KF.fragment(src), depth))
                    }
                    walk(np, depth + 1)
                }
            }
            walk(map, 0)
            return out
        }
    }

    final class Writer {
        let epub: Source
        let asin: String?
        let title: String
        let authors: [String]
        let coverOverride: Data?
        var resources: [(Bytes, String)] = []          // kindle:embed order (1-based)
        var resIndex: [String: Int] = [:]
        var flows: [Bytes] = [[]]                       // flow 0 is the XHTML; then style sheets
        var flowIndex: [String: Int] = [:]
        var aid = 0
        var targets: [String: String] = [:]             // "path#fragment" -> aid
        var links: [String] = []                        // placeholder number -> "path#fragment"
        var vertical = false

        init(epub: Source, asin: String?, cover: Data?, title: String?, authors: [String]?) {
            self.epub = epub
            self.asin = asin
            self.coverOverride = cover
            self.title = title ?? epub.title
            self.authors = authors ?? epub.authors
        }

        func nextAID() -> String {
            aid += 1
            return KF.base32(aid, 1)
        }

        // resources

        func resource(_ path: String) -> Int? {
            if let i = resIndex[path] { return i }
            guard let data = epub.read(path) else { return nil }
            let ext = (path as NSString).pathExtension.lowercased()
            let fonts = ["ttf": "font/ttf", "otf": "font/otf", "woff": "font/woff", "woff2": "font/woff2"]
            if let mime = fonts[ext] {
                // FONT record: size, flags (0: stored as is), data offset, no obfuscation
                var rec = KF.utf8("FONT") + KF.be32(data.count)
                rec += KF.be32(0) + KF.be32(24)
                rec += KF.be32(0) + KF.be32(0)
                rec += Bytes(data)
                resources.append((rec, mime))
            } else {
                guard let img = KF.kindleImage(data) else { return nil }
                resources.append(img)
            }
            resIndex[path] = resources.count
            return resources.count
        }

        func embed(_ path: String) -> String? {
            guard let i = resource(path) else { return nil }
            return "kindle:embed:\(KF.base32(i, 4))?mime=\(resources[i - 1].1)"
        }

        /// A style sheet with its url()s pointing into the book; @import dropped.
        func css(_ text: String, base: String) -> String {
            var t = KF.sub(text, #"@import[^;]*;"#) { _ in "" }
            if KF.firstMatch(t, #"writing-mode\s*:\s*vertical-rl"#) != nil { vertical = true }
            t = KF.sub(t, #"url\(\s*(['"]?)([^'")]+)\1\s*\)"#) { g in
                let href = g[2].trimmingCharacters(in: .whitespaces)
                if KF.isExternal(href) { return g[0] }
                if let e = self.embed(KF.join(base, href)) { return "url(\(e))" }
                return "url()"
            }
            return t
        }

        func flow(_ path: String) -> String? {
            if flowIndex[path] == nil {
                guard let data = epub.read(path) else { return nil }
                flows.append(KF.utf8(css(String(decoding: data, as: UTF8.self), base: KF.dir(path))))
                flowIndex[path] = flows.count - 1
            }
            return "kindle:flow:\(KF.base32(flowIndex[path]!, 4))?mime=text/css"
        }

        // documents

        func prepare(_ path: String) -> (XMLElement, XMLElement)? {
            guard let data = epub.read(path), let doc = KF.parseXHTML(data), let root = doc.rootElement(),
                  KF.local(root) == "html" else { return nil }
            let base = KF.dir(path)
            root.namespaces = (root.namespaces ?? []).filter { ($0.name ?? "").isEmpty || $0.name == "xlink" || $0.name == "svg" }
            if root.namespaces?.contains(where: { ($0.name ?? "").isEmpty }) != true {
                root.addNamespace(XMLNode.namespace(withName: "", stringValue: KF.xhtmlNS) as! XMLNode)
            }
            for el in KF.elements(root) {
                let n = el.name ?? ""
                if ["script", "noscript"].contains(KF.local(el)) || (n.contains(":") && !n.hasPrefix("svg:")) {
                    el.detach()
                    continue
                }
                for a in el.attributes ?? [] {
                    let an = a.name ?? ""
                    if an.contains(":") && an != "xml:lang" && an != "xlink:href" { el.removeAttribute(forName: an) }
                }
            }
            guard let body = KF.child(root, "body") else { return nil }
            var head = KF.child(root, "head")
            if head == nil {
                head = XMLElement(name: "head")
                root.insertChild(head!, at: 0)
            }
            if KF.child(head!, "title") == nil { head!.addChild(XMLElement(name: "title", stringValue: title)) }
            for link in (head!.children ?? []).compactMap({ $0 as? XMLElement }) where KF.local(link) == "link" {
                if (KF.attr(link, "rel") ?? "").lowercased().contains("stylesheet"), let href = KF.attr(link, "href"),
                   let ref = flow(KF.join(base, href)) {
                    link.attributes = []
                    KF.setAttr(link, "rel", "stylesheet")
                    KF.setAttr(link, "type", "text/css")
                    KF.setAttr(link, "href", ref)
                } else {
                    link.detach()
                }
            }
            for el in KF.elements(root) {
                let name = KF.local(el)
                if name == "style" { el.stringValue = css(el.stringValue ?? "", base: base) }
                if let st = KF.attr(el, "style") {
                    if st.contains("url(") { KF.setAttr(el, "style", css(st, base: base)) }
                    if st.contains("vertical-rl") { vertical = true }
                }
                if name == "img" || name == "image" {
                    let key = name == "img" ? "src" : (KF.attr(el, "xlink:href") != nil ? "xlink:href" : "href")
                    if let href = KF.attr(el, key), !KF.isExternal(href), let e = embed(KF.join(base, href)) {
                        KF.setAttr(el, key, e)
                    } else if name == "img" {
                        el.detach()
                    }
                }
                if name == "a", let href = KF.attr(el, "href"), !KF.isExternal(href) {
                    let p = href.hasPrefix("#") ? path : KF.join(base, href)
                    links.append(p + "#" + KF.fragment(href))
                    KF.setAttr(el, "href", String(format: "kindle:pos:fid:0000:off:%010d", links.count - 1))
                }
            }
            // anchors: the body, its children and everything with an id
            let bodyAID = nextAID()
            KF.setAttr(body, "aid", bodyAID)
            targets[path + "#"] = bodyAID
            if let id = KF.attr(body, "id") { targets[path + "#" + id] = bodyAID }
            func walk(_ e: XMLElement, inSVG: Bool) {
                for c in (e.children ?? []).compactMap({ $0 as? XMLElement }) {
                    let svg = inSVG || KF.local(c) == "svg"
                    if (KF.attr(c, "id") != nil || e === body) && (!inSVG) {
                        let a = nextAID()
                        KF.setAttr(c, "aid", a)
                        if let id = KF.attr(c, "id") { targets[path + "#" + id] = a }
                    }
                    walk(c, inSVG: svg)
                }
            }
            walk(body, inSVG: false)
            return (root, body)
        }

        /// (skeleton, insert offset in it, fragments)
        func split(_ root: XMLElement, _ body: XMLElement) -> (Bytes, Int, [Bytes]) {
            let pieces = (body.children ?? []).map { KF.utf8(KF.serialize($0)) }
            body.setChildren(nil)
            var skel = KF.serialize(root)
            if !skel.prefix(300).contains("xmlns=\"\(KF.xhtmlNS)\"") {
                skel = KF.sub(skel, "^<html") { _ in "<html xmlns=\"\(KF.xhtmlNS)\"" }
            }
            let s = KF.utf8("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" + skel)
            let insert = KF.rfind(s, KF.utf8("</body>")) ?? s.count
            var chunks: [Bytes] = [], cur: Bytes = []
            for p in pieces {
                if !cur.isEmpty && cur.count + p.count > KF.chunkSize {
                    chunks.append(cur)
                    cur = []
                }
                cur += p
            }
            if !cur.isEmpty || chunks.isEmpty { chunks.append(cur.isEmpty ? [0x20] : cur) }
            return (s, insert, chunks)
        }

        struct Frag { var insert: Int; var selector: String; var file: Int; var seq: Int; var start: Int; var length: Int }
        struct TOCEntry { var label: String; var depth: Int; var fid: (Int, Int); var offset: Int; var id = 0; var parent: Int? }

        func build() throws -> ([(String, Int, Int, Int)], [Frag], [TOCEntry]) {
            var skels: [(String, Int, Int, Int)] = [], frags: [Frag] = []
            var text: Bytes = []
            var docs: [(String, XMLElement, XMLElement)] = []
            for path in epub.spine {
                if let r = prepare(path) { docs.append((path, r.0, r.1)) }
            }
            guard !docs.isEmpty else { throw Failure(message: "No readable pages in this EPUB.") }
            var seq = 0
            for (n, (_, root, body)) in docs.enumerated() {
                let bodyAID = KF.attr(body, "aid") ?? "0"
                let (skel, insert, chunks) = split(root, body)
                let start = text.count
                skels.append((String(format: "SKEL%010d", n), chunks.count, start, skel.count))
                var pos = start + insert, cp = 0
                for c in chunks {
                    frags.append(Frag(insert: pos, selector: "P-//*[@aid='\(bodyAID)']", file: n, seq: seq, start: cp, length: c.count))
                    pos += c.count
                    cp += c.count
                    seq += 1
                }
                text += skel
                for c in chunks { text += c }
            }
            // rebuild the files to find where each aid is (the placeholders keep their width)
            var whole: Bytes = []
            for (n, s) in skels.enumerated() {
                var doc = Array(text[s.2..<(s.2 + s.3)])
                var basePos = s.2 + s.3
                for f in frags where f.file == n {
                    let ins = f.insert - s.2
                    doc.insert(contentsOf: text[basePos..<(basePos + f.length)], at: min(ins, doc.count))
                    basePos += f.length
                }
                whole += doc
            }
            var aidPos: [String: ((Int, Int), Int)] = [:]
            let marker = KF.utf8(" aid=\"")
            var from = 0
            while let i = KF.find(whole, marker, from: from) {
                from = i + marker.count
                var end = from
                while end < whole.count && whole[end] != 0x22 { end += 1 }
                let a = String(decoding: whole[from..<end], as: UTF8.self)
                guard let off = KF.rfind(whole, [0x3c], before: i) else { continue }
                var fid: (Int, Int)?
                for f in frags {
                    if f.insert <= off && off < f.insert + f.length { fid = (f.seq, off - f.insert); break }
                    if f.insert > off { fid = (f.seq, 0); break }
                }
                let last = frags[frags.count - 1]
                aidPos[a] = (fid ?? (last.seq, max(0, off - last.insert)), off)
            }
            func whereIs(_ target: String) -> ((Int, Int), Int)? {
                let page = target.components(separatedBy: "#")[0]
                guard let a = targets[target] ?? targets[page + "#"] else { return nil }
                return aidPos[a]
            }
            // links
            let ph = KF.utf8("kindle:pos:fid:0000:off:")
            from = 0
            while let i = KF.find(text, ph, from: from) {
                let numStart = i + ph.count
                guard numStart + 10 <= text.count else { break }
                let num = Int(String(decoding: text[numStart..<(numStart + 10)], as: UTF8.self)) ?? -1
                var rep = KF.utf8("kindle:pos:fid:0000:off:0000000000")
                if num >= 0 && num < links.count, let w = whereIs(links[num]) {
                    rep = KF.utf8("kindle:pos:fid:\(KF.base32(w.0.0, 4)):off:\(KF.base32(w.0.1, 10))")
                }
                text.replaceSubrange(i..<(numStart + 10), with: rep)
                from = i + rep.count
            }
            flows[0] = text
            // table of contents
            var toc: [TOCEntry] = []
            for t in epub.toc {
                if let w = whereIs(t.path + "#" + t.frag) {
                    toc.append(TOCEntry(label: t.label, depth: min(t.depth, 3), fid: w.0, offset: w.1))
                }
            }
            if toc.isEmpty {
                for (path, _, _) in docs {
                    if let w = whereIs(path + "#") {
                        toc.append(TOCEntry(label: toc.isEmpty ? title : "Part \(toc.count + 1)", depth: 0, fid: w.0, offset: w.1))
                    }
                }
            }
            return (skels, frags, toc)
        }

        func ncxIndex(_ input: [TOCEntry], textLength: Int) -> [Bytes] {
            var toc = input
            for i in toc.indices {
                toc[i].id = i
                toc[i].parent = nil
                var j = i - 1
                var found = false
                while j >= 0 {
                    if toc[j].depth < toc[i].depth {
                        toc[i].depth = toc[j].depth + 1
                        toc[i].parent = j
                        found = true
                        break
                    }
                    j -= 1
                }
                if !found { toc[i].depth = 0 }
            }
            let entries = toc.sorted { ($0.depth, $0.offset) < ($1.depth, $1.offset) }
            var index: [Int: Int] = [:]
            for (k, e) in entries.enumerated() { index[e.id] = k }
            var cncx = CNCX()
            let width = max(2, String(max(entries.count - 1, 0), radix: 16).count)
            var out: [(String, [Int: [Int]])] = []
            for (k, e) in entries.enumerated() {
                let children = toc.filter { $0.parent == e.id }.compactMap { index[$0.id] }
                let later = entries.filter { $0.depth <= e.depth && $0.offset > e.offset }.map(\.offset)
                var vals: [Int: [Int]] = [1: [e.offset], 2: [max(1, (later.min() ?? textLength) - e.offset)],
                                          3: [cncx.add(e.label)], 4: [e.depth], 6: [e.fid.0, e.fid.1]]
                if let p = e.parent, let pi = index[p] { vals[21] = [pi] }
                if let lo = children.min(), let hi = children.max() {
                    vals[22] = [lo]
                    vals[23] = [hi]
                }
                let key = String(k, radix: 16, uppercase: true)
                out.append((String(repeating: "0", count: max(0, width - key.count)) + key, vals))
            }
            let tags = [(1, 1, 1), (2, 1, 2), (3, 1, 4), (4, 1, 8), (21, 1, 16), (22, 1, 32), (23, 1, 64), (6, 2, 128)]
            return buildIndex(out, tags: tags, cncx: cncx.finish())
        }

        /// (cover, thumbnail) as 0-based resource numbers.
        func coverResources() -> (Int?, Int?) {
            var cover: Int?
            if let data = coverOverride, let img = KF.kindleImage(data) {
                resources.append(img)
                cover = resources.count - 1
            } else if let path = epub.cover, let i = resource(path) {
                cover = i - 1
            }
            guard let cover else { return (nil, nil) }
            if let t = KF.thumbnail(resources[cover].0) {
                resources.append((t, "image/jpeg"))
                return (cover, resources.count - 1)
            }
            return (cover, cover)
        }

        func exth(cover: Int?, thumb: Int?) -> Bytes {
            var recs: [Bytes] = []
            func s(_ code: Int, _ value: String) {
                guard !value.isEmpty else { return }
                let v = KF.utf8(value)
                recs.append(KF.be32(code) + KF.be32(8 + v.count) + v)
            }
            func n(_ code: Int, _ value: Int) { recs.append(KF.be32(code) + KF.be32(12) + KF.be32(value)) }
            for a in authors { s(100, a) }
            s(101, epub.publisher)
            s(103, String(epub.summary.prefix(2000)))
            for subj in epub.subjects.prefix(10) { s(105, subj) }
            s(106, epub.date)
            if let asin, !asin.isEmpty {
                s(113, asin)
                s(504, asin)
            }
            s(501, "EBOK")
            s(503, title)
            s(524, epub.language)
            for (code, value) in [(204, 202), (205, 2), (206, 9), (207, 0)] { n(code, value) }
            s(535, "0730-890adc2")
            if let cover {
                n(201, cover)
                n(203, 0)
            }
            if let thumb {
                n(202, thumb)
                s(129, "kindle:embed:\(KF.base32(thumb, 4))")
            }
            n(125, resources.count)
            n(131, 0)
            if vertical { s(525, "vertical-rl") }
            if let d = epub.direction, d == "rtl" || d == "ltr" { s(527, d) }
            s(528, "true")
            let body = recs.reduce([], +)
            var out = KF.utf8("EXTH")
            out += KF.be32(body.count + 12) + KF.be32(recs.count)
            out += body
            out += Bytes(repeating: 0, count: 4 - body.count % 4)
            return out
        }

        func write(to out: URL) throws {
            let (skels, frags, toc) = try build()
            let (cover, thumb) = coverResources()
            let text = flows.reduce([], +)
            var records: [Bytes] = [[]]
            // text records, each with the bytes that complete its last character and their count
            var size = 0
            var i = 0
            while i < text.count {
                let end = min(i + KF.recordSize, text.count)
                let data = Array(text[i..<end])
                var overlap: Bytes = []
                var j = data.count - 1
                while j >= 0 && j >= data.count - 4 && data[j] & 0xc0 == 0x80 { j -= 1 }
                if j >= 0 && data[j] >= 0xc0 {
                    let need = data[j] < 0xe0 ? 2 : data[j] < 0xf0 ? 3 : 4
                    let missing = need - (data.count - j)
                    if missing > 0 && end < text.count { overlap = Array(text[end..<min(end + missing, text.count)]) }
                }
                let rec = data + overlap + [UInt8(overlap.count)]
                records.append(rec)
                size += rec.count
                i = end
            }
            let lastText = records.count - 1
            if size % 4 != 0 { records.append(Bytes(repeating: 0, count: 4 - size % 4)) }
            let firstNonText = records.count
            var fragCNCX = CNCX()
            for f in frags { fragCNCX.add(f.selector) }
            let chunkIndex = records.count
            records += buildIndex(frags.map { f in
                (String(format: "%010d", f.insert), [2: [fragCNCX.offsets[f.selector]!], 3: [f.file], 4: [f.seq], 6: [f.start, f.length]])
            }, tags: [(2, 1, 1), (3, 1, 2), (4, 1, 4), (6, 2, 8)], cncx: fragCNCX.finish())
            let skelIndex = records.count
            records += buildIndex(skels.map { s in (s.0, [1: [s.1, s.1], 6: [s.2, s.3, s.2, s.3]]) },
                                  tags: [(1, 1, 3), (6, 2, 12)])
            var ncx = KF.null
            if !toc.isEmpty {
                ncx = records.count
                records += ncxIndex(toc, textLength: flows[0].count)
            }
            var firstResource = KF.null
            if !resources.isEmpty {
                firstResource = records.count
                records += resources.map { $0.0 }
            }
            let fdst = records.count
            var bounds: Bytes = [], start = 0
            for f in flows {
                bounds += KF.be32(start) + KF.be32(start + f.count)
                start += f.count
            }
            var fdstRec = KF.utf8("FDST")
            fdstRec += KF.be32(12) + KF.be32(flows.count)
            fdstRec += bounds
            records.append(fdstRec)
            let flis = records.count
            let flisTail: Bytes = [0, 0, 0, 8, 0, 0x41, 0, 0, 0, 0, 0, 0, 0xff, 0xff, 0xff, 0xff, 0, 1, 0, 3, 0, 0, 0, 3, 0, 0, 0, 1,
                                   0xff, 0xff, 0xff, 0xff]
            records.append(KF.utf8("FLIS") + flisTail)
            let fcis = records.count
            let fcisHead: Bytes = [0, 0, 0, 0x14, 0, 0, 0, 0x10, 0, 0, 0, 2, 0, 0, 0, 0]
            let fcisTail: Bytes = [0, 0, 0, 0, 0, 0, 0, 0x28, 0, 0, 0, 0, 0, 0, 0, 0x28, 0, 0, 0, 8, 0, 1, 0, 1, 0, 0, 0, 0]
            var fcisRec = KF.utf8("FCIS")
            fcisRec += fcisHead
            fcisRec += KF.be32(text.count)
            fcisRec += fcisTail
            records.append(fcisRec)
            records.append([0xe9, 0x8e, 0x0d, 0x0a])

            // record 0: PalmDOC header, MOBI header (264 bytes), EXTH, full title
            let ex = exth(cover: cover, thumb: thumb)
            let titleBytes = KF.utf8(title)
            var r0 = KF.be16(1) + KF.be16(0)
            r0 += KF.be32(text.count)
            r0 += KF.be16(lastText) + KF.be16(KF.recordSize)
            r0 += KF.be16(0) + KF.be16(0)
            var m = KF.utf8("MOBI") + KF.be32(264)
            m += KF.be32(2) + KF.be32(65001)
            m += KF.be32(Int(UInt32.random(in: 0...UInt32.max))) + KF.be32(8)
            for _ in 0..<10 { m += KF.be32(KF.null) }
            m += KF.be32(firstNonText)
            m += KF.be32(16 + 264 + ex.count) + KF.be32(titleBytes.count)
            m += KF.be32(locale(epub.language)) + KF.be32(0)
            m += KF.be32(0) + KF.be32(8)
            m += KF.be32(firstResource)
            m += Bytes(repeating: 0, count: 16)
            m += KF.be32(0x50)
            m += Bytes(repeating: 0, count: 32)
            m += KF.be32(KF.null)
            m += KF.be32(KF.null) + KF.be32(0)
            m += Bytes(repeating: 0, count: 16)
            m += KF.be32(fdst) + KF.be32(flows.count)
            m += KF.be32(fcis) + KF.be32(1)
            m += KF.be32(flis) + KF.be32(1)
            m += Bytes(repeating: 0, count: 8)
            m += KF.be32(KF.null) + KF.be32(0)
            m += Bytes(repeating: 0xff, count: 8)
            m += KF.be32(1) + KF.be32(ncx)
            m += KF.be32(chunkIndex) + KF.be32(skelIndex)
            m += KF.be32(KF.null) + KF.be32(KF.null)
            m += KF.be32(KF.null) + KF.be32(0)
            m += KF.be32(KF.null) + KF.be32(0)
            precondition(m.count == 264)
            r0 += m + ex + titleBytes
            r0 += Bytes(repeating: 0, count: 2 + (4 - (titleBytes.count + 2) % 4) % 4) + Bytes(repeating: 0, count: 8192)
            records[0] = r0
            try writePDB(records, to: out)
        }

        func writePDB(_ records: [Bytes], to out: URL) throws {
            var name = Bytes(KF.sub(title, "[^A-Za-z0-9]+") { _ in "_" }.trimmingCharacters(in: CharacterSet(charactersIn: "_")).utf8.prefix(31))
            if name.isEmpty { name = KF.utf8("book") }
            let now = Int(Date().timeIntervalSince1970)
            var head = name + Bytes(repeating: 0, count: 32 - name.count)
            head += KF.be16(0) + KF.be16(0)
            head += KF.be32(now) + KF.be32(now)
            head += Bytes(repeating: 0, count: 16)
            head += KF.utf8("BOOKMOBI")
            head += KF.be32(2 * records.count - 1) + KF.be32(0)
            head += KF.be16(records.count)
            var offset = head.count + 8 * records.count + 2
            var table: Bytes = []
            for (i, r) in records.enumerated() {
                table += KF.be32(offset)
                table.append(0)
                table += Array(KF.be32(2 * i)[1...])
                offset += r.count
            }
            var file = Data(head + table + [0, 0])
            for r in records { file.append(contentsOf: r) }
            try file.write(to: out)
        }
    }

    /// Converts an EPUB to AZW3. asin: the id the Kindle files the book (and its cover thumbnail) under;
    /// cover: an image to use instead of the EPUB's own cover.
    static func write(epub: URL, to out: URL, asin: String?, cover: Data? = nil, title: String? = nil, authors: [String]? = nil) throws {
        let dir: URL
        do { dir = try KF.unzip(epub) } catch { throw Failure(message: "This EPUB can't be opened.") }
        defer { try? FileManager.default.removeItem(at: dir) }
        let source = try Source(dir)
        try Writer(epub: source, asin: asin, cover: cover, title: title, authors: authors).write(to: out)
    }
}

// MARK: - Kindle books → EPUB

enum KindleRead {
    struct DRMLocked: LocalizedError {
        var errorDescription: String? { "This book is locked with DRM." }
    }

    struct Failure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    static let langs: [Int: String] = [0x09: "en", 0x21: "id", 0x11: "ja", 0x12: "ko", 0x804: "zh-Hans", 0x404: "zh-Hant",
                                       0xc04: "zh-Hant", 0x04: "zh", 0x1e: "th", 0x2a: "vi", 0x0c: "fr", 0x07: "de",
                                       0x0a: "es", 0x10: "it", 0x16: "pt", 0x13: "nl", 0x19: "ru", 0x15: "pl", 0x1f: "tr", 0x01: "ar"]

    // MARK: decompression

    static func palmdoc(_ data: Bytes) -> Bytes {
        var out = Bytes()
        out.reserveCapacity(data.count * 2)
        var i = 0
        while i < data.count {
            let c = Int(data[i])
            i += 1
            if c == 0 || (9...0x7f).contains(c) {
                out.append(UInt8(c))
            } else if c <= 8 {
                out += data[i..<min(i + c, data.count)]
                i += c
            } else if c >= 0xc0 {
                out.append(0x20)
                out.append(UInt8(c ^ 0x80))
            } else if i < data.count {
                let pair = (c << 8) | Int(data[i])
                i += 1
                let dist = (pair >> 3) & 0x7ff, length = (pair & 7) + 3
                guard dist > 0 && dist <= out.count else { continue }
                for _ in 0..<length { out.append(out[out.count - dist]) }
            }
        }
        return out
    }

    /// HUFF/CDIC compression: a code table, then phrase dictionaries that may themselves be compressed.
    final class Huff {
        var table: [(Int, Bool, UInt64)] = []
        var lowest: [UInt64] = [0], highest: [UInt64] = [0]
        var phrases: [(Bytes, Bool)] = []

        init(_ huff: Bytes, _ cdics: [Bytes]) throws {
            guard huff.count > 24, Array(huff[0..<4]) == KF.utf8("HUFF") else { throw Failure(message: "Bad HUFF record.") }
            let t1 = KF.u32(huff, 8), t2 = KF.u32(huff, 12)
            for k in 0..<256 {
                let v = UInt64(KF.u32(huff, t1 + 4 * k))
                let bits = Int(v & 0x1f), term = v & 0x80 != 0, high = v >> 8
                table.append((bits, term, ((high + 1) << UInt64(32 - bits)) &- 1))
            }
            for k in 0..<32 {
                let lo = UInt64(KF.u32(huff, t2 + 8 * k)), hi = UInt64(KF.u32(huff, t2 + 8 * k + 4))
                lowest.append(lo << UInt64(32 - (k + 1)))
                highest.append(((hi + 1) << UInt64(32 - (k + 1))) &- 1)
            }
            for c in cdics {
                guard c.count >= 16, Array(c[0..<4]) == KF.utf8("CDIC") else { throw Failure(message: "Bad CDIC record.") }
                let count = KF.u32(c, 8), bits = KF.u32(c, 12)
                let n = min(1 << bits, count - phrases.count)
                guard n > 0 else { continue }
                for k in 0..<n {
                    let off = KF.u16(c, 16 + 2 * k)
                    let len = KF.u16(c, 16 + off)
                    let start = 18 + off, end = min(start + (len & 0x7fff), c.count)
                    phrases.append((start < end ? Array(c[start..<end]) : [], len & 0x8000 != 0))
                }
            }
        }

        func unpack(_ input: Bytes) -> Bytes {
            var left = input.count * 8
            let data = input + Bytes(repeating: 0, count: 8)
            func q(_ p: Int) -> UInt64 {
                var v: UInt64 = 0
                for k in 0..<8 { v = (v << 8) | UInt64(data[p + k]) }
                return v
            }
            var pos = 0, x = q(0), n = 32
            var out = Bytes()
            while true {
                if n <= 0 {
                    pos += 4
                    x = q(pos)
                    n += 32
                }
                let code = (x >> UInt64(n)) & 0xffff_ffff
                var (bits, term, high) = table[Int(code >> 24)]
                if !term {
                    while bits < 32 && code < lowest[bits] { bits += 1 }
                    high = highest[bits]
                }
                n -= bits
                left -= bits
                if left < 0 || bits == 0 { break }
                let r = Int((high &- code) >> UInt64(32 - bits))
                guard r < phrases.count else { break }
                if !phrases[r].1 {
                    // a compressed phrase: expand it once (marked first, so a phrase can't recurse into itself)
                    phrases[r].1 = true
                    phrases[r].0 = unpack(phrases[r].0)
                }
                out += phrases[r].0
            }
            return out
        }
    }

    // MARK: the Palm database

    /// One book inside the file: the MOBI 6 part, or the KF8 part (the whole file for AZW3).
    struct Section {
        let records: [Bytes]
        let base: Int
        var compression = 0, textLength = 0, textCount = 0, encryption = 0, version = 0
        var utf8 = false
        var extraFlags = 0
        var exth: [Int: [Bytes]] = [:]
        var name = "", locale = 0
        var firstResource = KF.null, huff = (KF.null, 0), fdst = KF.null, ncx = KF.null, frag = KF.null, skel = KF.null

        init(_ records: [Bytes], _ base: Int) {
            self.records = records
            self.base = base
            let r0 = records[base]
            compression = KF.u16(r0, 0)
            textLength = KF.u32(r0, 4)
            textCount = KF.u16(r0, 8)
            encryption = KF.u16(r0, 12)
            guard r0.count > 20, Array(r0[16..<20]) == KF.utf8("MOBI") else { return }
            let hlen = KF.u32(r0, 20)
            utf8 = KF.u32(r0, 28) == 65001
            version = KF.u32(r0, 36)
            let nameOff = KF.u32(r0, 84), nameLen = KF.u32(r0, 88)
            if nameOff + nameLen <= r0.count { name = decode(Array(r0[nameOff..<(nameOff + nameLen)])) }
            locale = KF.u32(r0, 92)
            firstResource = KF.u32(r0, 108)
            huff = (KF.u32(r0, 112), KF.u32(r0, 116))
            if hlen >= 0xe4 { extraFlags = KF.u16(r0, 0xf2) }
            if version >= 8 && hlen >= 0xb8 { fdst = KF.u32(r0, 0xc0) }
            if hlen >= 0xe8 { ncx = KF.u32(r0, 0xf4) }
            if version >= 8 && hlen >= 0xf0 {
                frag = KF.u32(r0, 0xf8)
                skel = KF.u32(r0, 0xfc)
            }
            if KF.u32(r0, 128) & 0x40 != 0 && r0.count >= 16 + hlen + 12 && Array(r0[(16 + hlen)..<(20 + hlen)]) == KF.utf8("EXTH") {
                var p = 16 + hlen
                let count = KF.u32(r0, p + 8)
                p += 12
                for _ in 0..<count {
                    guard p + 8 <= r0.count else { break }
                    let typ = KF.u32(r0, p), size = KF.u32(r0, p + 4)
                    guard size >= 8, p + size <= r0.count else { break }
                    exth[typ, default: []].append(Array(r0[(p + 8)..<(p + size)]))
                    p += size
                }
            }
        }

        func decode(_ b: Bytes) -> String { utf8 ? String(decoding: b, as: UTF8.self) : String(bytes: b, encoding: .windowsCP1252) ?? "" }
        func rec(_ i: Int) -> Bytes { base + i >= 0 && base + i < records.count ? records[base + i] : [] }
        func str(_ code: Int) -> [String] { (exth[code] ?? []).map { decode($0).trimmingCharacters(in: .whitespacesAndNewlines) } }
        func int(_ code: Int) -> Int? { exth[code].flatMap { $0.first }.flatMap { $0.count >= 4 ? KF.u32($0, 0) : nil } }

        static func trailingSize(_ data: Bytes) -> Int {
            var size = 0, shift = 0
            var i = data.count - 1
            while i >= 0 && i >= data.count - 4 {
                let c = Int(data[i])
                size |= (c & 0x7f) << shift
                shift += 7
                if c & 0x80 != 0 { break }
                i -= 1
            }
            return size
        }

        func text() throws -> Bytes {
            if encryption != 0 { throw DRMLocked() }
            var huffman: Huff?
            switch compression {
            case 17480:
                huffman = try Huff(rec(huff.0), (1..<max(1, huff.1)).map { rec(huff.0 + $0) })
            case 1, 2:
                break
            default:
                throw Failure(message: "Unknown compression (\(compression)).")
            }
            var out = Bytes()
            for i in stride(from: 1, through: textCount, by: 1) {
                var data = rec(i)
                var flags = extraFlags >> 1
                while flags != 0 {
                    if flags & 1 != 0 {
                        let size = Self.trailingSize(data)
                        data = size <= data.count ? Array(data[0..<(data.count - size)]) : []
                    }
                    flags >>= 1
                }
                if extraFlags & 1 != 0, let last = data.last {
                    let n = Int(last & 3) + 1
                    data = n <= data.count ? Array(data[0..<(data.count - n)]) : []
                }
                out += huffman?.unpack(data) ?? (compression == 2 ? palmdoc(data) : data)
            }
            return textLength > 0 && textLength < out.count ? Array(out[0..<textLength]) : out
        }
    }

    /// [(key, [tag: values])] and the CNCX string lookup, from the INDX records at `start`.
    static func readIndex(_ sec: Section, _ start: Int) -> ([(String, [Int: [Int]])], (Int) -> String) {
        let none: (Int) -> String = { _ in "" }
        guard start != KF.null, sec.base + start < sec.records.count else { return ([], none) }
        let head = sec.rec(start)
        guard head.count > 192, Array(head[0..<4]) == KF.utf8("INDX"), let t = KF.find(head, KF.utf8("TAGX")) else { return ([], none) }
        let count = KF.u32(head, 24), ncncx = KF.u32(head, 52)
        let tlen = KF.u32(head, t + 4), cbcount = KF.u32(head, t + 8)
        var tags: [(Int, Int, Int, Int)] = []
        var k = 12
        while k + 4 <= tlen && t + k + 4 <= head.count {
            tags.append((Int(head[t + k]), Int(head[t + k + 1]), Int(head[t + k + 2]), Int(head[t + k + 3])))
            k += 4
        }
        let cncx = (0..<ncncx).map { sec.rec(start + 1 + count + $0) }
        var entries: [(String, [Int: [Int]])] = []
        for r in 0..<count {
            let rec = sec.rec(start + 1 + r)
            let idxt = KF.u32(rec, 20), n = KF.u32(rec, 24)
            guard idxt > 0, idxt < rec.count else { continue }
            let offs = (0..<n).map { KF.u16(rec, idxt + 4 + 2 * $0) } + [idxt]
            for i in 0..<n {
                var pos = offs[i]
                guard pos < rec.count else { continue }
                let klen = Int(rec[pos])
                let key = String(decoding: rec[(pos + 1)..<min(pos + 1 + klen, rec.count)], as: UTF8.self)
                pos += 1 + klen
                let cbs = Array(rec[min(pos, rec.count)..<min(pos + cbcount, rec.count)])
                pos += cbcount
                var plan: [(Int, Int?, Int?, Int)] = []
                var cbi = 0
                for (tag, vpe, mask, end) in tags {
                    if end != 0 {
                        cbi += 1
                        continue
                    }
                    var v = cbi < cbs.count ? Int(cbs[cbi]) & mask : 0
                    if v == 0 { continue }
                    if v == mask && mask.nonzeroBitCount > 1 {
                        let (nbytes, p) = KF.decint(rec, pos)
                        pos = p
                        plan.append((tag, nil, nbytes, vpe))
                    } else {
                        v >>= mask.trailingZeroBitCount
                        plan.append((tag, v, nil, vpe))
                    }
                }
                var vals: [Int: [Int]] = [:]
                for (tag, cnt, nbytes, vpe) in plan {
                    var got: [Int] = []
                    if let cnt {
                        for _ in 0..<(cnt * vpe) {
                            let (x, p) = KF.decint(rec, pos)
                            pos = p
                            got.append(x)
                        }
                    } else if let nbytes {
                        let stop = pos + nbytes
                        while pos < stop && pos < rec.count {
                            let (x, p) = KF.decint(rec, pos)
                            pos = p
                            got.append(x)
                        }
                    }
                    vals[tag] = got
                }
                entries.append((key, vals))
            }
        }
        let string: (Int) -> String = { off in
            guard off / 0x10000 < cncx.count else { return "" }
            let r = cncx[off / 0x10000]
            let (len, p) = KF.decint(r, off % 0x10000)
            guard p + len <= r.count else { return "" }
            return String(decoding: r[p..<(p + len)], as: UTF8.self)
        }
        return (entries, string)
    }

    /// NCX index entries are stored level by level; put them in reading order, children under parents.
    static func tocOrder<T>(_ entries: [([Int: [Int]], T)]) -> [([Int: [Int]], T, Int)] {
        var out: [([Int: [Int]], T, Int)] = []
        var seen = Set<Int>()
        func visit(_ i: Int, _ depth: Int) {
            guard i < entries.count, !seen.contains(i) else { return }
            seen.insert(i)
            let (v, item) = entries[i]
            out.append((v, item, depth))
            if let lo = v[22]?.first, let hi = v[23]?.first, lo <= hi {
                for c in lo...hi { visit(c, depth + 1) }
            }
        }
        for (i, e) in entries.enumerated() where e.0[21] == nil { visit(i, 0) }
        for i in entries.indices { visit(i, 0) }
        return out
    }

    // MARK: resources

    struct Resources {
        var items: [(path: String, data: Bytes, type: String)?] = []

        init(_ records: [Bytes], _ ranges: [(Int, Int)]) {
            let skip = Set(["FLIS", "FCIS", "SRCS", "BOUN", "FDST", "DATP", "AUDI", "VIDE", "RESC", "CMET", "PAGE", "INDX",
                            "CONT", "CRES", "kind"])
            for (start, end) in ranges where start < end {
                for i in start..<min(end, records.count) {
                    let d = records[i]
                    let magic = d.count >= 4 ? String(decoding: d[0..<4], as: UTF8.self) : ""
                    var item: (path: String, data: Bytes, type: String)?
                    if magic == "FONT" {
                        if let f = KindleRead.font(d) {
                            let ext = f.1
                            item = ("fonts/font\(String(format: "%04d", items.count + 1)).\(ext)", f.0,
                                    ext == "otf" ? "font/otf" : ext == "woff" ? "font/woff" : "font/ttf")
                        }
                    } else if !skip.contains(magic) && d != [0xe9, 0x8e, 0x0d, 0x0a], let ext = KF.imageType(d) {
                        let type = ext == "jpg" ? "image/jpeg" : ext == "svg" ? "image/svg+xml" : "image/\(ext)"
                        item = ("images/image\(String(format: "%04d", items.count + 1)).\(ext)", d, type)
                    }
                    items.append(item)
                }
            }
        }

        func get(_ n: Int) -> (path: String, data: Bytes, type: String)? { n > 0 && n <= items.count ? items[n - 1] : nil }
    }

    /// A FONT record's font: optionally XOR-obfuscated, optionally zlib-compressed.
    static func font(_ d: Bytes) -> (Bytes, String)? {
        guard d.count >= 24 else { return nil }
        let flags = KF.u32(d, 8), start = KF.u32(d, 12), xorLen = KF.u32(d, 16), xorStart = KF.u32(d, 20)
        guard start <= d.count else { return nil }
        var font = Array(d[start...])
        if flags & 2 != 0 && xorLen > 0 && xorStart + xorLen <= d.count {
            let key = Array(d[xorStart..<(xorStart + xorLen)])
            for i in 0..<min(1040, font.count) { font[i] ^= key[i % xorLen] }
        }
        if flags & 1 != 0 {
            guard let inflated = inflate(font) else { return nil }
            font = inflated
        }
        let head = font.count >= 4 ? String(decoding: font[0..<4], as: UTF8.self) : ""
        return (font, head == "OTTO" ? "otf" : head == "wOFF" ? "woff" : "ttf")
    }

    /// zlib data (as in FONT records), inflated with the system's zlib through NSData.
    static func inflate(_ zlib: Bytes) -> Bytes? {
        guard zlib.count > 6 else { return nil }
        // NSData's zlib decompression expects raw deflate; skip the 2-byte zlib header and the 4-byte checksum
        let raw = Data(zlib[2..<(zlib.count - 4)])
        guard let out = try? (raw as NSData).decompressed(using: .zlib) else { return nil }
        return Bytes(out as Data)
    }

    // MARK: building the EPUB

    final class Out {
        var title: String, authors: [String], language: String, publisher: String, summary: String, date: String
        var ident: String
        var files: [String: Data] = [:]
        var types: [String: String] = [:]
        var spine: [String] = []
        var toc: [(String, String, Int)] = []
        var cover: String?

        init(title: String, authors: [String], language: String, publisher: String, summary: String, date: String, ident: String?) {
            self.title = title
            self.authors = authors
            self.language = language.isEmpty ? "en" : language
            self.publisher = publisher
            self.summary = summary
            self.date = date
            self.ident = ident ?? "urn:uuid:\(UUID().uuidString.lowercased())"
        }

        func add(_ path: String, _ data: Data, _ type: String) {
            files[path] = data
            types[path] = type
        }

        func write(to out: URL) throws {
            let e = KF.escape
            var ids: [String: String] = [:], items: [String] = []
            for (n, path) in files.keys.sorted().enumerated() {
                ids[path] = "i\(n)"
                items.append("<item id=\"i\(n)\" href=\"\(e(path))\" media-type=\"\(types[path] ?? "application/octet-stream")\"\(path == cover ? " properties=\"cover-image\"" : "")/>")
            }
            let refs = spine.compactMap { ids[$0] }.map { "<itemref idref=\"\($0)\"/>" }
            var meta = ["<dc:identifier id=\"uid\">\(e(ident))</dc:identifier>", "<dc:title>\(e(title))</dc:title>",
                        "<dc:language>\(e(language))</dc:language>", "<meta property=\"dcterms:modified\">2000-01-01T00:00:00Z</meta>"]
            meta += authors.map { "<dc:creator>\(e($0))</dc:creator>" }
            if !publisher.isEmpty { meta.append("<dc:publisher>\(e(publisher))</dc:publisher>") }
            if !summary.isEmpty { meta.append("<dc:description>\(e(summary))</dc:description>") }
            if !date.isEmpty { meta.append("<dc:date>\(e(date))</dc:date>") }
            if let cover, let id = ids[cover] { meta.append("<meta name=\"cover\" content=\"\(id)\"/>") }
            let entries = toc.isEmpty ? [(title, spine.first ?? "", 0)] : toc
            var nav = "", depth = -1
            for (label, href, d0) in entries {
                let d = max(0, min(d0, depth + 1))
                while depth > d {
                    nav += "</li></ol>"
                    depth -= 1
                }
                if depth == d { nav += "</li>" } else {
                    nav += "<ol>"
                    depth = d
                }
                nav += "<li><a href=\"\(e(href))\">\(e(label))</a>"
            }
            while depth >= 0 {
                nav += "</li></ol>"
                depth -= 1
            }
            var ncx = "", stack: [Int] = [], play = 0
            for (label, href, d) in entries {
                while let top = stack.last, top >= d {
                    ncx += "</navPoint>"
                    stack.removeLast()
                }
                play += 1
                ncx += "<navPoint id=\"n\(play)\" playOrder=\"\(play)\"><navLabel><text>\(e(label))</text></navLabel><content src=\"\(e(href))\"/>"
                stack.append(d)
            }
            ncx += String(repeating: "</navPoint>", count: stack.count)
            var all = files.reduce(into: [String: Data]()) { $0["OEBPS/" + $1.key] = $1.value }
            all["META-INF/container.xml"] = Data("""
                <?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>
                """.utf8)
            all["OEBPS/content.opf"] = Data("""
                <?xml version="1.0" encoding="utf-8"?>
                <package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\(meta.joined())</metadata><manifest>\(items.joined())<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx">\(refs.joined())</spine></package>
                """.utf8)
            all["OEBPS/nav.xhtml"] = Data("""
                <?xml version="1.0" encoding="utf-8"?>
                <html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>\(e(title))</title></head><body><nav epub:type="toc" id="toc">\(nav)</nav></body></html>
                """.utf8)
            all["OEBPS/toc.ncx"] = Data("""
                <?xml version="1.0" encoding="utf-8"?>
                <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="\(e(ident))"/></head><docTitle><text>\(e(title))</text></docTitle><navMap>\(ncx)</navMap></ncx>
                """.utf8)
            try KF.zipEPUB(all, to: out)
        }
    }

    /// Any HTML (KF8's XHTML, or MOBI 6's loose HTML) as a well-formed XHTML document.
    static func tidy(_ markup: Bytes, title: String) -> Data {
        var s = String(decoding: markup, as: UTF8.self)
        s = KF.sub(s, #"\s(?:aid|filepos|(?:hi|low|media)?recindex)=("[^"]*"|'[^']*'|[^\s>]+)"#) { _ in "" }
        s = KF.sub(s, #"^\s*<\?xml[^>]*\?>"#) { _ in "" }
        var doc = try? XMLDocument(data: Data(s.utf8), options: [.nodeLoadExternalEntitiesNever])
        if doc?.rootElement().map({ KF.local($0) }) != "html" {
            doc = try? XMLDocument(data: Data(s.utf8), options: [.documentTidyHTML])
        }
        guard let doc, let root = doc.rootElement() else {
            return Data("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<html xmlns=\"\(KF.xhtmlNS)\"><head><title>\(KF.escape(title))</title></head><body></body></html>".utf8)
        }
        root.namespaces = (root.namespaces ?? []).filter { ($0.name ?? "").isEmpty || $0.name == "xlink" || $0.name == "svg" || $0.name == "epub" }
        if root.namespaces?.contains(where: { ($0.name ?? "").isEmpty }) != true {
            root.addNamespace(XMLNode.namespace(withName: "", stringValue: KF.xhtmlNS) as! XMLNode)
        }
        if KF.child(root, "head") == nil { root.insertChild(XMLElement(name: "head"), at: 0) }
        let head = KF.child(root, "head")!
        if KF.child(head, "title") == nil { head.addChild(XMLElement(name: "title", stringValue: title)) }
        if KF.child(root, "body") == nil { root.addChild(XMLElement(name: "body")) }
        var xml = KF.serialize(root)
        if !xml.prefix(300).contains("xmlns=\"\(KF.xhtmlNS)\"") {
            xml = KF.sub(xml, "^<html") { _ in "<html xmlns=\"\(KF.xhtmlNS)\"" }
        }
        return Data(("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" + xml).utf8)
    }

    final class Reader {
        let records: [Bytes]
        let plain: Bool
        let mobi6: Section
        var kf8: Section?
        let out: Out

        init(_ data: Data) throws {
            let d = Bytes(data)
            guard d.count >= 78, [KF.utf8("BOOKMOBI"), KF.utf8("TEXtREAd")].contains(Array(d[60..<68])) else {
                throw Failure(message: "Not a Kindle book.")
            }
            let n = KF.u16(d, 76)
            let offs = (0..<n).map { KF.u32(d, 78 + 8 * $0) } + [d.count]
            records = (0..<n).map { i in offs[i] < offs[i + 1] && offs[i + 1] <= d.count ? Array(d[offs[i]..<offs[i + 1]]) : [] }
            guard !records.isEmpty else { throw Failure(message: "Not a Kindle book.") }
            plain = Array(d[60..<68]) == KF.utf8("TEXtREAd")
            mobi6 = Section(records, 0)
            if mobi6.encryption != 0 { throw DRMLocked() }
            if mobi6.version >= 8 && mobi6.skel != KF.null {
                kf8 = mobi6
            } else if let k8 = mobi6.int(121), k8 > 0, k8 < records.count, records[k8 - 1] == KF.utf8("BOUNDARY") {
                kf8 = Section(records, k8)
            }
            let title = mobi6.str(503).first ?? (mobi6.name.isEmpty ? "Unknown" : mobi6.name)
            let lang = mobi6.str(524).first ?? KindleRead.langs[mobi6.locale & 0xffff] ?? KindleRead.langs[mobi6.locale & 0x3ff] ?? "en"
            let asin = mobi6.str(113).first
            let ident = asin.flatMap { KF.firstMatch($0, "^[0-9a-fA-F-]{36}$") != nil ? "urn:uuid:\($0)" : nil }
            out = Out(title: title, authors: mobi6.str(100), language: lang, publisher: mobi6.str(101).first ?? "",
                      summary: KF.sub(mobi6.str(103).first ?? "", "<[^>]+>") { _ in "" }, date: mobi6.str(106).first ?? "",
                      ident: ident)
        }

        func resources() -> Resources {
            func first(_ s: Section) -> Int {
                s.firstResource != KF.null && s.firstResource != 0 ? s.base + s.firstResource : s.base + s.textCount + 1
            }
            guard let kf8, kf8.base != mobi6.base else { return Resources(records, [(first(mobi6), records.count)]) }
            return Resources(records, [(first(mobi6), kf8.base - 1), (first(kf8), records.count)])
        }

        func addResources(_ res: Resources) {
            for item in res.items.compactMap({ $0 }) { out.add(item.path, Data(item.data), item.type) }
            if let c = mobi6.int(201), let item = res.get(c + 1) { out.cover = item.path }
        }

        func convert(to url: URL) throws {
            if plain { try fromPlain() } else if kf8 != nil { try fromKF8() } else { try fromMOBI6() }
            guard !out.spine.isEmpty else { throw Failure(message: "No text found in this book.") }
            try out.write(to: url)
        }

        func fromPlain() throws {
            let text = String(bytes: try mobi6.text(), encoding: .windowsCP1252) ?? ""
            let paras = text.components(separatedBy: "\n\n").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }.map { "<p>\(KF.escape($0))</p>" }.joined()
            out.add("text/part0001.xhtml", tidy(KF.utf8("<html><body>\(paras)</body></html>"), title: out.title), "application/xhtml+xml")
            out.spine.append("text/part0001.xhtml")
        }

        // MOBI 6: one HTML stream with filepos links and recindex images, split at page breaks

        func fromMOBI6() throws {
            let raw = try mobi6.text()
            let res = resources()
            addResources(res)
            var text = mobi6.utf8 ? String(decoding: raw, as: UTF8.self) : (String(bytes: raw, encoding: .windowsCP1252) ?? "")
            // positions are byte offsets in the raw text: work on bytes for the anchors
            var targets = Set<Int>()
            if let re = try? NSRegularExpression(pattern: #"filepos=["']?0*(\d+)"#) {
                let ns = text as NSString
                for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
                    if let v = Int(ns.substring(with: m.range(at: 1))) { targets.insert(v) }
                }
            }
            var toc: [(String, Int, Int)] = []
            let (entries, string) = readIndex(mobi6, mobi6.ncx)
            for (v, item, depth) in tocOrder(entries.map { ($0.1, (($0.1[3]?.first).map(string) ?? "", $0.1[1]?.first ?? 0)) }) {
                _ = v
                toc.append((item.0.isEmpty ? "•" : item.0, item.1, depth))
                targets.insert(item.1)
            }
            var bytes = raw
            for pos in targets.sorted(by: >) where pos <= bytes.count {
                let lt = KF.rfind(bytes, [0x3c], before: pos) ?? -1, gt = KF.rfind(bytes, [0x3e], before: pos) ?? -1
                let p = lt > gt ? lt : pos
                bytes.insert(contentsOf: KF.utf8("<a id=\"filepos\(pos)\"></a>"), at: p)
            }
            text = mobi6.utf8 ? String(decoding: bytes, as: UTF8.self) : (String(bytes: bytes, encoding: .windowsCP1252) ?? "")
            text = KF.sub(text, #"<a([^>]*?)\sfilepos=["']?0*(\d+)["']?"#) { g in "<a\(g[1]) href=\"#filepos\(g[2])\"" }
            text = KF.sub(text, #"\s(?:hi|low)?recindex=(["']?)0*(\d+)\1"#) { g in
                res.get(Int(g[2]) ?? 0).map { " src=\"../\($0.path)\"" } ?? ""
            }
            text = KF.sub(text, #"<guide>.*?</guide>"#, [.caseInsensitive, .dotMatchesLineSeparators]) { _ in "" }
            let parts = KF.sub(text, #"<mbp:pagebreak\s*/?>"#, [.caseInsensitive]) { _ in "\u{1}" }.components(separatedBy: "\u{1}")
            var files: [(String, String)] = [], idmap: [String: String] = [:]
            for p in parts {
                var part = KF.sub(p, #"</?mbp:[^>]*>"#, [.caseInsensitive]) { _ in "" }
                part = KF.sub(part, #"</?(html|head|body)\b[^>]*>"#, [.caseInsensitive]) { _ in "" }
                let bare = KF.sub(part, #"<[^>]+>|\s|&nbsp;"#) { _ in "" }
                if bare.isEmpty && !part.contains("<img") && !part.contains("id=\"filepos") { continue }
                let path = String(format: "text/part%04d.xhtml", files.count + 1)
                if let re = try? NSRegularExpression(pattern: #"id="(filepos\d+)""#) {
                    let ns = part as NSString
                    for m in re.matches(in: part, range: NSRange(location: 0, length: ns.length)) { idmap[ns.substring(with: m.range(at: 1))] = path }
                }
                files.append((path, part))
            }
            for (path, part) in files {
                let linked = KF.sub(part, ##"href="#(filepos\d+)""##) { g in
                    guard let w = idmap[g[1]] else { return "href=\"#\"" }
                    return "href=\"\(w == path ? "" : (w as NSString).lastPathComponent)#\(g[1])\""
                }
                let doc = tidy(KF.utf8("<html><head><meta charset=\"utf-8\"/></head><body>\(linked)</body></html>"), title: out.title)
                out.add(path, doc, "application/xhtml+xml")
                out.spine.append(path)
            }
            for (label, pos, depth) in toc {
                if let w = idmap["filepos\(pos)"] { out.toc.append((label, "\(w)#filepos\(pos)", depth)) }
            }
        }

        // KF8: skeletons and fragments, kindle:pos links, kindle:embed and kindle:flow references

        func fromKF8() throws {
            guard let sec = kf8 else { return }
            let text = try sec.text()
            let res = resources()
            addResources(res)
            var flows = [text]
            let f = sec.rec(sec.fdst)
            if sec.fdst != KF.null, f.count >= 12, Array(f[0..<4]) == KF.utf8("FDST") {
                let n = KF.u32(f, 8)
                flows = (0..<n).map { i in
                    let a = min(KF.u32(f, 12 + 8 * i), text.count), b = min(KF.u32(f, 16 + 8 * i), text.count)
                    return a < b ? Array(text[a..<b]) : []
                }
            }
            let html0 = flows.first ?? []
            let (skels, _) = readIndex(sec, sec.skel)
            let (frags, _) = readIndex(sec, sec.frag)
            var files: [(start: Int, doc: Bytes, placed: [(Int, Int)])] = []
            var fi = 0
            for (_, v) in skels {
                let count = v[1]?.first ?? 0
                guard let g = v[6], g.count >= 2 else { continue }
                let start = g[0], length = g[1]
                guard start + length <= html0.count else { continue }
                var doc = Array(html0[start..<(start + length)])
                var basePos = start + length
                var placed: [(Int, Int)] = []
                for _ in 0..<count where fi < frags.count {
                    let (fkey, fv) = frags[fi]
                    let insertAt = Int(fkey) ?? 0
                    let flen = fv[6]?.last ?? 0
                    let end = min(basePos + flen, html0.count)
                    let data = basePos < end ? Array(html0[basePos..<end]) : []
                    let ins = insertAt - start
                    if ins >= 0 && ins <= doc.count { doc.insert(contentsOf: data, at: ins) } else { doc += data }
                    placed.append((fi, insertAt))
                    basePos += flen
                    fi += 1
                }
                files.append((start, doc, placed))
            }
            if files.isEmpty { files = [(0, html0, [])] }
            var fragPos: [Int: Int] = [:]
            for file in files { for (n, pos) in file.placed { fragPos[n] = pos } }
            let names = files.indices.map { String(format: "text/part%04d.xhtml", $0 + 1) }
            func locate(_ fid: Int, _ off: Int) -> (Int, Int) {
                let pos = (fragPos[fid] ?? 0) + off
                for i in stride(from: files.count - 1, through: 0, by: -1) where files[i].start <= pos { return (i, pos - files[i].start) }
                return (0, 0)
            }
            func parsePos(_ b: Bytes, _ i: Int) -> (Int, Int)? {
                // "kindle:pos:fid:XXXX:off:YYYYYYYYYY" at i
                guard i + 34 <= b.count else { return nil }
                let fid = String(decoding: b[(i + 15)..<(i + 19)], as: UTF8.self)
                let off = String(decoding: b[(i + 24)..<(i + 34)], as: UTF8.self)
                guard let a = Int(fid, radix: 32), let o = Int(off, radix: 32) else { return nil }
                return (a, o)
            }
            let posMark = KF.utf8("kindle:pos:fid:")
            var want = Set<String>()
            func key(_ l: (Int, Int)) -> String { "\(l.0):\(l.1)" }
            for file in files {
                var from = 0
                while let i = KF.find(file.doc, posMark, from: from) {
                    if let p = parsePos(file.doc, i) { want.insert(key(locate(p.0, p.1))) }
                    from = i + posMark.count
                }
            }
            let (ncx, ncxString) = readIndex(sec, sec.ncx)
            var tocPoints: [(String, (Int, Int), Int)] = []
            for (v, label, depth) in tocOrder(ncx.map { ($0.1, ($0.1[3]?.first).map(ncxString) ?? "") }) {
                if let g = v[6], g.count >= 2 {
                    let loc = locate(g[0], g[1])
                    want.insert(key(loc))
                    tocPoints.append((label, loc, depth))
                }
            }
            // give each target element an id
            var anchors: [String: String] = [:]
            for i in files.indices {
                var doc = files[i].doc
                let offs = want.compactMap { k -> Int? in
                    let p = k.split(separator: ":").compactMap { Int($0) }
                    return p.count == 2 && p[0] == i ? p[1] : nil
                }.sorted(by: >)
                for off in offs {
                    var lt = KF.find(doc, [0x3c], from: min(off, doc.count))
                    if lt == nil || lt! + 1 >= doc.count || [0x2f, 0x21, 0x3f].contains(doc[lt! + 1]) {
                        lt = KF.rfind(doc, [0x3c], before: max(off, 1))
                        while let l = lt, l > 0, l + 1 < doc.count, [0x2f, 0x21, 0x3f].contains(doc[l + 1]) { lt = KF.rfind(doc, [0x3c], before: l) }
                    }
                    guard let l = lt, let gt = KF.find(doc, [0x3e], from: l) else {
                        anchors["\(i):\(off)"] = ""
                        continue
                    }
                    let tag = String(decoding: doc[l..<gt], as: UTF8.self)
                    if let m = KF.firstMatch(tag, #"\sid=["']([^"']+)["']"#) {
                        anchors["\(i):\(off)"] = m[1]
                        continue
                    }
                    let aid = "pos\(i)_\(off)"
                    var nameEnd = l + 1
                    while nameEnd < gt && ![0x20, 0x09, 0x0a, 0x0d, 0x2f, 0x3e].contains(doc[nameEnd]) { nameEnd += 1 }
                    doc.insert(contentsOf: KF.utf8(" id=\"\(aid)\""), at: nameEnd)
                    anchors["\(i):\(off)"] = aid
                }
                files[i].doc = doc
            }
            func href(_ loc: (Int, Int), from here: Int) -> String {
                let a = anchors[key(loc)] ?? ""
                let name = (names[loc.0] as NSString).lastPathComponent
                let s = (loc.0 == here ? "" : name) + (a.isEmpty ? "" : "#" + a)
                return s.isEmpty ? name : s
            }
            func embeds(_ s: String) -> String {
                KF.sub(s, #"kindle:embed:([0-9A-V]{4})(\?mime=[^\s"')]*)?"#) { g in
                    res.get(Int(g[1], radix: 32) ?? 0).map { "../" + $0.path } ?? ""
                }
            }
            var flowNames: [Int: (String, String)] = [:]
            for (n, flow) in flows.enumerated().dropFirst() {
                let start = String(decoding: flow.prefix(500), as: UTF8.self)
                flowNames[n] = start.contains("<svg") ? (String(format: "images/flow%04d.svg", n), "image/svg+xml")
                    : (String(format: "styles/flow%04d.css", n), "text/css")
                out.add(flowNames[n]!.0, Data(embeds(String(decoding: flow, as: UTF8.self)).utf8), flowNames[n]!.1)
            }
            for (i, file) in files.enumerated() {
                var doc = file.doc
                var from = 0
                while let p = KF.find(doc, posMark, from: from) {
                    guard let fp = parsePos(doc, p) else {
                        from = p + posMark.count
                        continue
                    }
                    let rep = KF.utf8(href(locate(fp.0, fp.1), from: i))
                    doc.replaceSubrange(p..<(p + 34), with: rep)
                    from = p + rep.count
                }
                var s = embeds(String(decoding: doc, as: UTF8.self))
                s = KF.sub(s, #"kindle:flow:([0-9A-V]{4})(\?mime=[^\s"')]*)?"#) { g in
                    flowNames[Int(g[1], radix: 32) ?? -1].map { "../" + $0.0 } ?? ""
                }
                out.add(names[i], tidy(KF.utf8(s), title: out.title), "application/xhtml+xml")
                out.spine.append(names[i])
            }
            for (label, loc, depth) in tocPoints {
                let a = anchors[key(loc)] ?? ""
                out.toc.append((label.isEmpty ? "•" : label, names[loc.0] + (a.isEmpty ? "" : "#" + a), depth))
            }
        }
    }

    /// Converts a Kindle book to EPUB. Throws DRMLocked for store books locked with DRM.
    static func toEPUB(_ file: URL, to out: URL) throws {
        let data = try Data(contentsOf: file)
        try Reader(data).convert(to: out)
    }
}
