import Foundation
import Translation

/// Translates EPUBs. Works on the book's own text, so no page scanning is involved.
enum Translator {
    enum Engine: String { case claude, apple }

    static let names = StyleGuides.languageNames
    /// Shown after the title so a translated copy doesn't replace the original on the Kindle.
    static let titleSuffix = ["id": "Bahasa Indonesia", "en": "English", "ja": "日本語"]

    struct Report {
        var paragraphs = 0
        var fellBack = 0              // paragraphs Claude couldn't do, translated by Apple instead
        var cost: Double?             // dollars, Claude only
        var styleSheet = ""
    }

    // MARK: Apple (offline, literal)

    static func apple(_ texts: [String], from: String, to: String,
                      progress: @escaping @Sendable (Double) -> Void) async throws -> [String] {
        let src = Locale.Language(identifier: from), dst = Locale.Language(identifier: to)
        switch await LanguageAvailability().status(from: src, to: dst) {
        case .installed: break
        case .supported:
            throw Shell.Failure(message: "Download \(names[from] ?? from) and \(names[to] ?? to) first: System Settings › General › Language & Region › Translation Languages.")
        default:
            throw Shell.Failure(message: "Translating \(names[from] ?? from) to \(names[to] ?? to) isn't supported on this Mac.")
        }
        let session = TranslationSession(installedSource: src, target: dst)
        var out = texts
        let todo = texts.indices.filter { !texts[$0].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        var done = 0
        for chunk in stride(from: 0, to: todo.count, by: 40).map({ Array(todo[$0..<min($0 + 40, todo.count)]) }) {
            let reqs = chunk.map { TranslationSession.Request(sourceText: texts[$0], clientIdentifier: String($0)) }
            for r in try await session.translations(from: reqs) {
                if let id = r.clientIdentifier, let i = Int(id) { out[i] = r.targetText }
            }
            done += chunk.count
            progress(Double(done) / Double(max(1, todo.count)))
        }
        return out
    }

    // MARK: EPUB

    /// A translatable piece of the book: a paragraph, heading or TOC label, or a single text run
    /// inside an element with links or images (which must keep its structure).
    private struct Unit {
        var node: XMLNode
        var text: String          // with inline <i>, <b>, <br/> kept
        var plain: String
        var shells: [XMLElement]? = nil   // elements behind <xN> placeholders
    }

    /// Each text run of an element as its own unit (for Apple, which can't keep tags).
    private static func textRuns(_ el: XMLElement) -> [Unit] {
        ((try? el.nodes(forXPath: ".//text()")) ?? []).compactMap { t in
            let s = (t.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !s.isEmpty, !["rt", "rp"].contains(t.parent?.localName?.lowercased() ?? "") else { return nil }
            return Unit(node: t, text: esc(s), plain: s)
        }
    }

    /// Like `inline`, but other elements become numbered placeholders whose shells are collected.
    private static func tagged(_ node: XMLNode, _ shells: inout [XMLElement]) -> String {
        var out = ""
        for child in node.children ?? [] {
            guard let el = child as? XMLElement else {
                if child.kind == .text { out += esc(child.stringValue ?? "") }
                continue
            }
            let name = el.localName?.lowercased() ?? ""
            if flattenable.contains(name) {
                let inner = tagged(el, &shells)
                switch name {
                case "i", "em", "cite": out += inner.isEmpty ? "" : "<i>\(inner)</i>"
                case "b", "strong": out += inner.isEmpty ? "" : "<b>\(inner)</b>"
                case "br": out += "<br/>"
                case "rt", "rp": break
                default: out += inner
                }
            } else {
                let shell = el.copy() as! XMLElement
                shells.append(shell)
                let n = shells.count
                let inner = tagged(el, &shells)
                if plainText(inner).isEmpty {
                    out += "<x\(n)/>"          // image or empty anchor: keep the original as is
                } else {
                    shell.setChildren(nil)
                    out += "<x\(n)>\(inner)</x\(n)>"
                }
            }
        }
        return out
    }

    /// Rebuilds a placeholder unit; returns false if Claude didn't keep every placeholder.
    private static func rebuild(_ el: XMLElement, _ text: String, shells: [XMLElement]) -> Bool {
        guard let wrapper = try? XMLElement(xmlString: "<x>\(text)</x>") else { return false }
        var used = Set<Int>()
        func convert(_ node: XMLNode) -> XMLNode {
            guard let e = node as? XMLElement, let name = e.name else { return node.copy() as! XMLNode }
            let kids = (e.children ?? []).map(convert)
            if name.hasPrefix("x"), let n = Int(name.dropFirst()), n >= 1, n <= shells.count {
                used.insert(n)
                let shell = shells[n - 1].copy() as! XMLElement
                if !kids.isEmpty { shell.setChildren(kids) }
                return shell
            }
            let copy = XMLElement(name: name)
            copy.setChildren(kids)
            return copy
        }
        let rebuilt = (wrapper.children ?? []).map(convert)
        guard used.count == shells.count else { return false }
        el.setChildren(rebuilt)
        return true
    }

    /// Inline elements that can be rebuilt from text with <i>/<b>/<br/>; anything else (links, images…) is kept in place.
    private static let flattenable: Set<String> = ["i", "em", "cite", "b", "strong", "br", "span", "small", "u", "ruby", "rb", "rt", "rp", "font"]

    static func detectLanguage(epub: URL) -> String? {
        guard let tmp = try? unzip(epub) else { return nil }
        defer { try? FileManager.default.removeItem(at: tmp) }
        guard let opfURL = opfPath(in: tmp), let opf = try? XMLDocument(contentsOf: opfURL, options: [.nodePreserveAll]) else { return nil }
        return (try? opf.nodes(forXPath: "//*[local-name()='language']").first?.stringValue).flatMap { $0 }
            .map { String($0.prefix(2)).lowercased() }
    }

    static func translate(epub: URL, from: String, to: String, output: URL,
                          engine: Engine, apiKey: String?,
                          status: @escaping @Sendable (String, Double) -> Void) async throws -> Report {
        guard from != to else { throw Shell.Failure(message: "The book is already in \(names[to] ?? to).") }
        let fm = FileManager.default
        let tmp = try unzip(epub)
        defer { try? fm.removeItem(at: tmp) }
        guard let opfURL = opfPath(in: tmp) else { throw Shell.Failure(message: "This EPUB has no package file (OPF).") }
        let opf = try XMLDocument(contentsOf: opfURL, options: [.nodePreserveAll])

        // documents in reading order: spine first, then any other XHTML (nav, notes)
        let base = opfURL.deletingLastPathComponent()
        var hrefByID: [String: String] = [:]
        for case let item as XMLElement in (try? opf.nodes(forXPath: "//*[local-name()='item']")) ?? [] {
            if let id = item.attribute(forName: "id")?.stringValue, let href = item.attribute(forName: "href")?.stringValue {
                hrefByID[id] = href.removingPercentEncoding ?? href
            }
        }
        var ordered: [URL] = ((try? opf.nodes(forXPath: "//*[local-name()='itemref']")) ?? []).compactMap {
            ($0 as? XMLElement)?.attribute(forName: "idref")?.stringValue.flatMap { hrefByID[$0] }.map { base.appendingPathComponent($0) }
        }
        let all = (fm.enumerator(at: tmp, includingPropertiesForKeys: nil)?.allObjects as? [URL] ?? [])
        for f in all where ["xhtml", "html", "htm"].contains(f.pathExtension.lowercased())
            && !ordered.contains(where: { $0.standardizedFileURL == f.standardizedFileURL }) {
            ordered.append(f)
        }

        let blockTags = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "figcaption", "td", "th", "blockquote", "title"]
        let anyBlock = blockTags.map { "local-name()='\($0)'" }.joined(separator: " or ")
        var docs: [(URL, XMLDocument)] = []
        var units: [Unit] = []
        for f in ordered {
            guard let doc = try? XMLDocument(contentsOf: f, options: [.nodePreserveAll]) else { continue }
            docs.append((f, doc))
            for case let el as XMLElement in (try? doc.nodes(forXPath: "//*[\(anyBlock)]")) ?? [] {
                // innermost text blocks only, e.g. a <p> inside an <li> is translated, not the <li>
                guard ((try? el.nodes(forXPath: ".//*[\(anyBlock)]")) ?? []).isEmpty else { continue }
                let descendants = ((try? el.nodes(forXPath: ".//*")) ?? []).compactMap { $0.localName?.lowercased() }
                if descendants.allSatisfy(flattenable.contains) {
                    let text = inline(el).trimmingCharacters(in: .whitespacesAndNewlines)
                    let plain = plainText(text)
                    if !plain.isEmpty { units.append(Unit(node: el, text: text, plain: plain)) }
                } else if engine == .claude {
                    // links, images, footnote markers become placeholders <x1>…</x1> / <x2/> so the
                    // sentence is translated whole and the elements are put back afterwards
                    var shells: [XMLElement] = []
                    let text = tagged(el, &shells).trimmingCharacters(in: .whitespacesAndNewlines)
                    if !plainText(text).isEmpty { units.append(Unit(node: el, text: text, plain: plainText(text), shells: shells)) }
                } else {
                    units += textRuns(el)
                }
            }
        }
        // table of contents labels for older readers (toc.ncx)
        var ncx: (URL, XMLDocument)?
        if let ncxURL = all.first(where: { $0.pathExtension.lowercased() == "ncx" }),
           let doc = try? XMLDocument(contentsOf: ncxURL, options: [.nodePreserveAll]) {
            ncx = (ncxURL, doc)
            for case let el as XMLElement in (try? doc.nodes(forXPath: "//*[local-name()='text']")) ?? [] {
                if let s = el.stringValue, !s.isEmpty { units.append(Unit(node: el, text: esc(s), plain: s)) }
            }
        }

        var report = Report(paragraphs: units.count)
        var results: [String?]
        switch engine {
        case .apple:
            status("Translating with Apple Translate…", 0)
            results = try await apple(units.map(\.plain), from: from, to: to) { status("Translating… \(Int($0 * 100))%", $0) }
            for i in units.indices { results[i] = esc(results[i] ?? units[i].plain) }
        case .claude:
            guard let apiKey, !apiKey.isEmpty else { throw Shell.Failure(message: "Add your Claude API key first.") }
            let claude = ClaudeTranslator(apiKey: apiKey, from: from, to: to)
            var usage = ClaudeTranslator.Usage()
            status("Reading the book to learn its style…", 0)
            report.styleSheet = try await claude.learnStyle(samples: samples(units.map(\.plain)), usage: &usage)
            let learned = usage
            let (translated, used) = try await claude.translate(units.map(\.text), styleSheet: report.styleSheet) { p, u in
                let spent = learned.dollars + u.dollars
                status(String(format: "Translating with Claude… %d%% ($%.2f so far)", Int(p * 100), spent), p)
            }
            results = translated
            report.cost = learned.dollars + used.dollars
            // anything Claude couldn't return cleanly goes through Apple Translate instead
            let missing = units.indices.filter { results[$0] == nil && units[$0].shells == nil }
            if !missing.isEmpty {
                report.fellBack = missing.count
                status("Finishing \(missing.count) paragraphs with Apple Translate…", 1)
                let fixed = (try? await apple(missing.map { units[$0].plain }, from: from, to: to) { _ in }) ?? missing.map { units[$0].plain }
                for (k, i) in missing.enumerated() { results[i] = esc(fixed[k]) }
            }
        }

        status("Writing the translated EPUB…", 1)
        var broken: [XMLElement] = []
        for (i, u) in units.enumerated() {
            if let shells = u.shells, let el = u.node as? XMLElement {
                if let r = results[i], rebuild(el, r, shells: shells) { continue }
                broken.append(el)    // placeholders lost: keep the structure, translate its text runs instead
            } else {
                setInline(u.node, results[i] ?? esc(u.plain))
            }
        }
        if !broken.isEmpty {
            let runs = broken.flatMap(textRuns)
            let tr = (try? await apple(runs.map(\.plain), from: from, to: to) { _ in }) ?? runs.map(\.plain)
            for (k, r) in runs.enumerated() { setInline(r.node, esc(tr[k])) }
            report.fellBack += broken.count
        }
        for (f, doc) in docs {
            // XMLDocument writes "<!DOCTYPE html" without its closing ">"
            let xml = doc.xmlString(options: [.nodePreserveAll])
                .replacingOccurrences(of: #"<!DOCTYPE html\s*(?=<)"#, with: "<!DOCTYPE html>\n", options: .regularExpression)
            try xml.write(to: f, atomically: true, encoding: .utf8)
        }
        if let (f, doc) = ncx { try doc.xmlData(options: [.nodePreserveAll]).write(to: f) }
        for n in (try? opf.nodes(forXPath: "//*[local-name()='language']")) ?? [] { n.stringValue = to }
        if let t = (try? opf.nodes(forXPath: "//*[local-name()='title']"))?.first {
            t.stringValue = "\(t.stringValue ?? "") (\(titleSuffix[to] ?? to))"
        }
        try opf.xmlData(options: [.nodePreserveAll]).write(to: opfURL)

        try? fm.removeItem(at: output)
        try Shell.run("/usr/bin/zip", ["-X0q", output.path, "mimetype"], cwd: tmp)
        try Shell.run("/usr/bin/zip", ["-Xr9Dq", output.path, ".", "-x", "mimetype"], cwd: tmp)
        return report
    }

    // MARK: helpers

    private static func unzip(_ epub: URL) throws -> URL {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("bookforge-tr-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        try Shell.run("/usr/bin/unzip", ["-q", epub.path, "-d", tmp.path])
        return tmp
    }

    private static func opfPath(in root: URL) -> URL? {
        if let c = try? XMLDocument(contentsOf: root.appendingPathComponent("META-INF/container.xml"), options: []),
           let p = (try? c.nodes(forXPath: "//*[local-name()='rootfile']"))?.first as? XMLElement,
           let path = p.attribute(forName: "full-path")?.stringValue {
            return root.appendingPathComponent(path)
        }
        return (FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)?.allObjects as? [URL])?
            .first { $0.pathExtension.lowercased() == "opf" }
    }

    static func esc(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;").replacingOccurrences(of: ">", with: "&gt;")
    }

    /// Element content as text with only <i>, <b> and <br/> kept (em/strong folded into i/b).
    private static func inline(_ node: XMLNode) -> String {
        (node.children ?? []).map { child -> String in
            guard let el = child as? XMLElement else {
                return child.kind == .text ? esc(child.stringValue ?? "") : ""
            }
            let inner = inline(el)
            switch el.localName?.lowercased() ?? "" {
            case "i", "em", "cite": return inner.isEmpty ? "" : "<i>\(inner)</i>"
            case "b", "strong": return inner.isEmpty ? "" : "<b>\(inner)</b>"
            case "br": return "<br/>"
            case "rt", "rp": return ""   // furigana readings
            default: return inner
            }
        }.joined()
    }

    /// Replaces an element's content with translated text, rebuilding <i>/<b>/<br/> when present.
    private static func setInline(_ node: XMLNode, _ text: String) {
        guard let el = node as? XMLElement else {
            // a lone text run keeps its surrounding spaces
            let s = node.stringValue ?? ""
            let lead = s.prefix { $0.isWhitespace }, trail = String(s.reversed().prefix { $0.isWhitespace })
            node.stringValue = lead + plainText(text) + trail
            return
        }
        el.setChildren(nil)
        if text.contains("<"), let wrapper = try? XMLElement(xmlString: "<x>\(text)</x>") {
            for child in wrapper.children ?? [] {
                child.detach()
                el.addChild(child)
            }
        } else {
            let unescaped = text.replacingOccurrences(of: "&lt;", with: "<").replacingOccurrences(of: "&gt;", with: ">")
                .replacingOccurrences(of: "&amp;", with: "&")
            el.stringValue = unescaped
        }
    }

    /// About a dozen excerpts spread across the book, favouring passages with dialogue.
    private static func samples(_ paras: [String]) -> [String] {
        let body = paras.filter { $0.count > 40 }
        guard !body.isEmpty else { return [] }
        let windows = 12, per = 1400
        var out: [String] = []
        for w in 0..<windows {
            let center = body.count * (2 * w + 1) / (2 * windows)
            // slide a little to land on dialogue if there is some nearby
            let start = (max(0, center - 6)..<min(body.count, center + 6)).first { body[$0].contains("\"") || body[$0].contains("“") || body[$0].contains("「") } ?? center
            var text = ""
            var i = start
            while i < body.count, text.count < per { text += body[i] + "\n"; i += 1 }
            out.append(text)
        }
        return out
    }
}

extension Translator {
    /// Inline-tagged text back to plain text.
    static func plainText(_ tagged: String) -> String {
        tagged.replacingOccurrences(of: "<br/>", with: " ").replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
            .replacingOccurrences(of: "&lt;", with: "<").replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
    }
}
