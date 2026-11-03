import Foundation
import PDFKit
import ImageIO
import UniformTypeIdentifiers

struct BookInfo {
    var title: String
    var author: String
    var language: String   // BCP-47, e.g. "id" or "en"
    var publisher: String = ""
}

/// Cleans OCR'd paragraph text: rejoins lines, undoes typesetting hyphens, fixes quotes.
struct TextFixer {
    private var vocab: [String: Int] = [:]

    init(blocks: [Block]) {
        let re = try! NSRegularExpression(pattern: "[\\p{L}]+(?:-[\\p{L}]+)*")
        for case .para(let t, _, _) in blocks {
            let s = t.replacingOccurrences(of: "\n", with: " ")
            for m in re.matches(in: s, range: NSRange(s.startIndex..., in: s)) {
                vocab[String(s[Range(m.range, in: s)!]).lowercased(), default: 0] += 1
            }
        }
    }

    func joinLines(_ t: String) -> String {
        let parts = t.components(separatedBy: "\n")
        var out = parts[0]
        for next in parts.dropFirst() {
            if out.hasSuffix("-"), let aR = out.range(of: #"\p{L}+-$"#, options: .regularExpression) {
                let a = String(out[aR].dropLast())
                let b = next.prefix { $0.isLetter }
                let hy = "\(a)-\(b)".lowercased(), joined = (a + b).lowercased()
                let la = a.lowercased(), lb = b.lowercased()
                let redup = !b.isEmpty && (lb.hasPrefix(la.prefix(3)) || la.hasPrefix(lb.prefix(3)))
                if vocab[hy, default: 0] > vocab[joined, default: 0]
                    || (vocab[joined, default: 0] == 0 && redup) || (b.first?.isUppercase ?? false) {
                    out += next            // real hyphen (reduplication / compound)
                } else {
                    out = String(out.dropLast()) + next  // typesetting hyphen
                }
            } else if let a = out.last, let b = next.first, a.isCJK || b.isCJK {
                out += next   // Japanese/Chinese lines join without a space
            } else {
                out += " " + next
            }
        }
        return out
    }

    /// Applies `fix` to every paragraph and centered line.
    func finalize(_ blocks: [Block]) -> [Block] {
        blocks.map { b in
            switch b {
            case .para(let t, let f, let s): return .para(text: fix(t), first: f, spaced: s)
            case .centered(let ls): return .centered(ls.map(fix))
            default: return b
            }
        }
    }

    func fix(_ text: String) -> String {
        var t = joinLines(text)
        func sub(_ p: String, _ r: String) { t = t.replacingOccurrences(of: p, with: r, options: .regularExpression) }
        sub(#"\[\s?\]"#, "")
        sub(#"(\w{3,})ii(?=[\s,.]|$)"#, "$1\"")          // closing quote read as "ii"
        sub(#"\s+([,.!?;:])(?=\s|$|")"#, "$1")
        sub(#"\.\s?\.\s?\.(\s?\.)?"#, "…")
        sub(#"…+"#, "…")
        t = t.replacingOccurrences(of: "®", with: "\"").replacingOccurrences(of: "›", with: "")
        sub(#"^[«#]"#, "\"")
        sub(#"\s*I?\]\s*$"#, "")                          // end-of-chapter box
        sub(#"\s{2,}"#, " ")
        t = t.trimmingCharacters(in: .whitespaces)
        // smart quotes
        sub(#"(^|[\s(\[—–-])""#, "$1“")
        t = t.replacingOccurrences(of: "\"", with: "”")
        sub(#"(^|[\s(“])'"#, "$1‘")
        t = t.replacingOccurrences(of: "'", with: "’")
        return t
    }
}

extension Character {
    var isCJK: Bool {
        guard let v = unicodeScalars.first?.value else { return false }
        return (0x3000...0x30FF).contains(v) || (0x4E00...0x9FFF).contains(v) || (0xFF00...0xFFEF).contains(v)
    }
}

/// Fixed page labels in the book's language.
struct Labels {
    let cover, backCover, contents, opening: String
    let page: (Int) -> String

    init(_ lang: String) {
        switch lang {
        case "id": (cover, backCover, contents, opening, page) = ("Sampul", "Sampul Belakang", "Daftar Isi", "Pembuka", { "Halaman \($0)" })
        case "ja": (cover, backCover, contents, opening, page) = ("表紙", "裏表紙", "目次", "はじめに", { "\($0)ページ" })
        default: (cover, backCover, contents, opening, page) = ("Cover", "Back Cover", "Contents", "Opening", { "Page \($0)" })
        }
    }
}

enum EPUBWriter {
    static func esc(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }

    static func jpeg(_ img: CGImage, quality: Double) -> Data {
        let data = NSMutableData()
        let dest = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(dest, img, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        CGImageDestinationFinalize(dest)
        return data as Data
    }

    /// Writes the EPUB and returns its URL. Page 1 of the PDF is the cover.
    static func write(layout: LayoutResult, doc: PDFDocument, info: BookInfo, to url: URL) throws {
        let fm = FileManager.default
        let tmp = fm.temporaryDirectory.appendingPathComponent("bookforge-\(UUID().uuidString)")
        let oebps = tmp.appendingPathComponent("OEBPS")
        try fm.createDirectory(at: oebps.appendingPathComponent("images"), withIntermediateDirectories: true)
        try fm.createDirectory(at: tmp.appendingPathComponent("META-INF"), withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: tmp) }

        let lang = info.language
        let L = Labels(lang)
        func page(_ title: String, _ body: String, cls: String = "") -> String {
            """
            <?xml version="1.0" encoding="utf-8"?>
            <!DOCTYPE html>
            <html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="\(lang)" lang="\(lang)">
            <head><meta charset="utf-8"/><title>\(esc(title))</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
            <body\(cls.isEmpty ? "" : " class=\"\(cls)\"")>
            \(body)
            </body></html>
            """
        }

        struct Item { var id: String; var file: String; var title: String; var xhtml: String; var inTOC: Bool }
        var items: [Item] = []
        var images: [(file: String, id: String)] = []

        func addImagePage(_ i: Int, title: String, inTOC: Bool, isCover: Bool = false) throws {
            guard let p = doc.page(at: i), let img = PDFReader.render(p, dpi: isCover ? 200 : 150) else { return }
            let file = isCover ? "cover.jpg" : String(format: "page%03d.jpg", i)
            try jpeg(img, quality: 0.85).write(to: oebps.appendingPathComponent("images/\(file)"))
            images.append((file, isCover ? "cover-image" : "img\(i)"))
            let itemID = isCover ? "cover" : String(format: "p%03d", i)
            items.append(Item(id: itemID, file: "\(itemID).xhtml", title: title,
                              xhtml: page(title, "<div class=\"pageimg\"><img src=\"images/\(file)\" alt=\"\(esc(title))\"/></div>",
                                          cls: "imagepage"), inTOC: inTOC))
        }

        try addImagePage(0, title: L.cover, inTOC: false, isCover: true)

        // split blocks into sections
        var sections: [(title: String, html: [String])] = []
        var afterBreak = true
        func ensureSection(_ t: String) { if sections.isEmpty { sections.append((t, [])) } }
        var trailingImages: [Int] = []
        var leadingImages: [Int] = []
        var inlineImages = Set<Int>()  // picture-only pages inside the story

        for b in layout.blocks {
            switch b {
            case .pageImage(let i):
                if sections.isEmpty { leadingImages.append(i) }
                else if i > layout.bodyEnd { trailingImages.append(i) }
                else {
                    sections[sections.count - 1].html.append("<div class=\"pageimg\"><img src=\"images/\(String(format: "page%03d.jpg", i))\" alt=\"\"/></div>")
                    inlineImages.insert(i)
                }
            case .heading(let h):
                sections.append((h, ["<h2 class=\"chapter\">\(esc(h))</h2>"]))
                afterBreak = true
            case .scene:
                ensureSection(L.opening)
                sections[sections.count - 1].html.append("<p class=\"scene\">* * *</p>")
                afterBreak = true
            case .centered(let ls):
                ensureSection(L.opening)
                sections[sections.count - 1].html.append("<div class=\"centered\">" + ls.map { "<p>\(esc($0))</p>" }.joined() + "</div>")
                afterBreak = true
            case .para(let text, let first, let spaced):
                ensureSection(L.opening)
                var cls: [String] = []
                if first { cls.append("first") } else if afterBreak { cls.append("noindent") }
                if spaced { cls.append("spaced") }
                var body = esc(text)
                if first, let c = body.first { body = "<span class=\"dropcap\">\(c)</span>" + body.dropFirst() }
                sections[sections.count - 1].html.append("<p\(cls.isEmpty ? "" : " class=\"\(cls.joined(separator: " "))\"")>\(body)</p>")
                afterBreak = false
            }
        }

        for i in leadingImages { try addImagePage(i, title: L.page(i + 1), inTOC: false) }
        let navIndex = items.count
        for (n, s) in sections.enumerated() {
            let fid = String(format: "ch%02d", n + 1)
            items.append(Item(id: fid, file: "\(fid).xhtml", title: s.title, xhtml: page(s.title, s.html.joined(separator: "\n")), inTOC: true))
        }
        for i in inlineImages.sorted() {
            guard let p = doc.page(at: i), let img = PDFReader.render(p, dpi: 150) else { continue }
            let file = String(format: "page%03d.jpg", i)
            try jpeg(img, quality: 0.85).write(to: oebps.appendingPathComponent("images/\(file)"))
            images.append((file, "img\(i)"))
        }
        for (k, i) in trailingImages.enumerated() {
            let last = k == trailingImages.count - 1
            try addImagePage(i, title: last ? L.backCover : (L.page(i + 1)), inTOC: k == 0 || last)
        }

        // table of contents
        let toc = items.filter(\.inTOC)
        let firstBody = items.first { $0.id.hasPrefix("ch") }?.file ?? toc.first?.file ?? "cover.xhtml"
        let nav = page(L.contents,
            "<nav epub:type=\"toc\" id=\"toc\"><h2 class=\"chapter\">\(L.contents)</h2><ol>"
            + toc.map { "<li><a href=\"\($0.file)\">\(esc($0.title))</a></li>" }.joined()
            + "</ol></nav><nav epub:type=\"landmarks\" hidden=\"\"><ol><li><a epub:type=\"cover\" href=\"cover.xhtml\">Cover</a></li>"
            + "<li><a epub:type=\"bodymatter\" href=\"\(firstBody)\">Start</a></li></ol></nav>")
        items.insert(Item(id: "nav", file: "nav.xhtml", title: "TOC", xhtml: nav, inTOC: false), at: navIndex)

        let bookID = "urn:uuid:\(UUID().uuidString.lowercased())"
        let ncx = """
            <?xml version="1.0" encoding="utf-8"?>
            <ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="\(bookID)"/></head>
            <docTitle><text>\(esc(info.title))</text></docTitle><navMap>
            """ + toc.enumerated().map { n, it in
                "<navPoint id=\"np\(n + 1)\" playOrder=\"\(n + 1)\"><navLabel><text>\(esc(it.title))</text></navLabel><content src=\"\(it.file)\"/></navPoint>"
            }.joined() + "</navMap></ncx>"

        var manifest = items.map { "<item id=\"\($0.id)\" href=\"\($0.file)\" media-type=\"application/xhtml+xml\"\($0.id == "nav" ? " properties=\"nav\"" : "")/>" }.joined()
        manifest += "<item id=\"css\" href=\"style.css\" media-type=\"text/css\"/><item id=\"ncx\" href=\"toc.ncx\" media-type=\"application/x-dtbncx+xml\"/>"
        manifest += images.map { "<item id=\"\($0.id)\" href=\"images/\($0.file)\" media-type=\"image/jpeg\"\($0.id == "cover-image" ? " properties=\"cover-image\"" : "")/>" }.joined()
        let spine = items.map { "<itemref idref=\"\($0.id)\"/>" }.joined()
        let stamp = ISO8601DateFormatter().string(from: Date()).prefix(19) + "Z"
        let opf = """
            <?xml version="1.0" encoding="utf-8"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="\(lang)">
            <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
            <dc:identifier id="bookid">\(bookID)</dc:identifier>
            <dc:title>\(esc(info.title))</dc:title>
            <dc:creator>\(esc(info.author))</dc:creator>
            \(info.publisher.isEmpty ? "" : "<dc:publisher>\(esc(info.publisher))</dc:publisher>")
            <dc:language>\(lang)</dc:language>
            <meta property="dcterms:modified">\(stamp)</meta>
            <meta name="cover" content="cover-image"/>
            </metadata>
            <manifest>\(manifest)</manifest>
            <spine toc="ncx">\(spine)</spine>
            <guide><reference type="cover" title="Cover" href="cover.xhtml"/></guide>
            </package>
            """

        try "application/epub+zip".write(to: tmp.appendingPathComponent("mimetype"), atomically: true, encoding: .ascii)
        try """
            <?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>
            """.write(to: tmp.appendingPathComponent("META-INF/container.xml"), atomically: true, encoding: .utf8)
        try opf.write(to: oebps.appendingPathComponent("content.opf"), atomically: true, encoding: .utf8)
        try ncx.write(to: oebps.appendingPathComponent("toc.ncx"), atomically: true, encoding: .utf8)
        try css.write(to: oebps.appendingPathComponent("style.css"), atomically: true, encoding: .utf8)
        for it in items { try it.xhtml.write(to: oebps.appendingPathComponent(it.file), atomically: true, encoding: .utf8) }

        // mimetype must be first and stored uncompressed
        try? fm.removeItem(at: url)
        try Shell.run("/usr/bin/zip", ["-X0q", url.path, "mimetype"], cwd: tmp)
        try Shell.run("/usr/bin/zip", ["-Xr9Dq", url.path, "META-INF", "OEBPS"], cwd: tmp)
    }

    static let css = """
        body { font-family: serif; line-height: 1.45; margin: 0 0.4em; }
        p { margin: 0; text-indent: 1.4em; text-align: justify; }
        p.noindent, p.first { text-indent: 0; }
        p.spaced { text-indent: 0; margin-top: 0.8em; }
        h2.chapter { font-weight: normal; text-align: center; page-break-before: always; font-size: 1.4em; letter-spacing: 0.05em; margin: 2.5em 0 1.8em; }
        .dropcap { font-size: 1.9em; line-height: 1; font-weight: bold; }
        p.scene { text-indent: 0; text-align: center; margin: 1em 0; }
        .centered { margin: 1em 0; }
        .centered p { text-indent: 0; text-align: center; }
        .pageimg { text-align: center; }
        .pageimg img { max-width: 100%; max-height: 100%; }
        body.imagepage { margin: 0; }
        """
}

enum Shell {
    struct Failure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    @discardableResult
    static func run(_ exe: String, _ args: [String], cwd: URL? = nil) throws -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        if let cwd { p.currentDirectoryURL = cwd }
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        try p.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let out = String(decoding: data, as: UTF8.self)
        if p.terminationStatus != 0 {
            throw Failure(message: "\((exe as NSString).lastPathComponent) failed: \(out.suffix(400))")
        }
        return out
    }
}
