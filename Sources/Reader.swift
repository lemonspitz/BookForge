import SwiftUI
import WebKit
import PDFKit

struct TOCEntry: Identifiable, Hashable {
    let id: Int
    let title: String
    let chapter: Int
    let anchor: String?
    let depth: Int
}

/// An EPUB unpacked into the cache, with its reading order and table of contents.
final class EPUBPackage: Sendable {
    let root: URL
    let spine: [URL]
    let starts: [Double]        // share of the book before each spine item
    let shares: [Double]        // share of the book in each spine item
    let toc: [TOCEntry]
    let rtl: Bool

    private init(root: URL, spine: [URL], starts: [Double], shares: [Double], toc: [TOCEntry], rtl: Bool) {
        self.root = root
        self.spine = spine
        self.starts = starts
        self.shares = shares
        self.toc = toc
        self.rtl = rtl
    }

    static func open(_ epub: URL, id: String) throws -> EPUBPackage {
        let fm = FileManager.default
        let root = fm.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("com.widagdos.bookforge/Reader/\(id)", isDirectory: true)
        let stamp = root.appendingPathComponent(".bookforge-stamp")
        let mtime = (try? epub.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate?.timeIntervalSince1970 ?? 0
        if (try? String(contentsOf: stamp, encoding: .utf8)) != "\(mtime)" {
            try? fm.removeItem(at: root)
            try fm.createDirectory(at: root, withIntermediateDirectories: true)
            try Shell.run("/usr/bin/unzip", ["-q", "-o", epub.path, "-d", root.path])
            try "\(mtime)".write(to: stamp, atomically: true, encoding: .utf8)
        }
        guard let opfRel = EPUBArchive.opfPath(epub) else { throw Shell.Failure(message: "This EPUB has no package file.") }
        let opfURL = root.appendingPathComponent(opfRel)
        let opf = try XMLDocument(contentsOf: opfURL, options: [])
        let opfDir = opfURL.deletingLastPathComponent()

        func attr(_ n: XMLNode, _ name: String) -> String { (n as? XMLElement)?.attribute(forName: name)?.stringValue ?? "" }
        func file(_ href: String, in dir: URL) -> URL {
            let path = href.components(separatedBy: "#")[0]
            return dir.appendingPathComponent(path.removingPercentEncoding ?? path).standardizedFileURL
        }
        var manifest: [String: (url: URL, props: String, type: String)] = [:]
        for item in (try? opf.nodes(forXPath: "//*[local-name()='manifest']/*[local-name()='item']")) ?? [] {
            manifest[attr(item, "id")] = (file(attr(item, "href"), in: opfDir), attr(item, "properties"), attr(item, "media-type"))
        }
        let spineEl = (try? opf.nodes(forXPath: "//*[local-name()='spine']"))?.first
        let spine = ((try? opf.nodes(forXPath: "//*[local-name()='spine']/*[local-name()='itemref']")) ?? [])
            .compactMap { manifest[attr($0, "idref")]?.url }
            .filter { fm.fileExists(atPath: $0.path) }
        guard !spine.isEmpty else { throw Shell.Failure(message: "This EPUB has no pages.") }
        let rtl = spineEl.map { attr($0, "page-progression-direction") } == "rtl"

        let sizes = spine.map { Double((try? $0.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 1) }
        let total = max(1, sizes.reduce(0, +))
        var starts: [Double] = [], acc = 0.0
        for s in sizes { starts.append(acc / total); acc += s }

        // table of contents: EPUB 3 nav, else the EPUB 2 NCX
        func chapter(_ url: URL) -> Int? { spine.firstIndex { $0.path == url.path } }
        var toc: [TOCEntry] = []
        func addEntry(_ title: String, href: String, base: URL, depth: Int) {
            let t = title.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            guard !t.isEmpty, let ch = chapter(file(href, in: base)) else { return }
            let anchor = href.contains("#") ? href.components(separatedBy: "#").last : nil
            toc.append(TOCEntry(id: toc.count, title: t, chapter: ch, anchor: anchor, depth: depth))
        }
        func depth(_ node: XMLNode, _ name: String) -> Int {
            var d = 0, p = node.parent
            while let n = p { if n.localName == name { d += 1 }; p = n.parent }
            return d
        }
        if let nav = manifest.values.first(where: { $0.props.split(separator: " ").contains("nav") }),
           let doc = (try? XMLDocument(contentsOf: nav.url, options: [])) ?? (try? XMLDocument(contentsOf: nav.url, options: .documentTidyHTML)) {
            let navs = (try? doc.nodes(forXPath: "//*[local-name()='nav']")) ?? []
            let tocNav = navs.first { n in ((n as? XMLElement)?.attributes ?? []).contains { $0.localName == "type" && $0.stringValue == "toc" } } ?? navs.first
            for a in (try? tocNav?.nodes(forXPath: ".//*[local-name()='a']")) ?? [] {
                addEntry(a.stringValue ?? "", href: attr(a, "href"), base: nav.url.deletingLastPathComponent(), depth: max(0, depth(a, "ol") - 1))
            }
        }
        if toc.isEmpty, let ncxID = spineEl.map({ attr($0, "toc") }), let ncx = manifest[ncxID] ?? manifest.values.first(where: { $0.type == "application/x-dtbncx+xml" }),
           let doc = try? XMLDocument(contentsOf: ncx.url, options: []) {
            for p in (try? doc.nodes(forXPath: "//*[local-name()='navPoint']")) ?? [] {
                let title = (try? p.nodes(forXPath: "./*[local-name()='navLabel']/*[local-name()='text']"))?.first?.stringValue ?? ""
                let src = (try? p.nodes(forXPath: "./*[local-name()='content']"))?.first.map { attr($0, "src") } ?? ""
                addEntry(title, href: src, base: ncx.url.deletingLastPathComponent(), depth: depth(p, "navPoint"))
            }
        }
        return EPUBPackage(root: root, spine: spine, starts: starts, shares: sizes.map { $0 / total }, toc: toc, rtl: rtl)
    }
}

struct ReaderSettings: Codable, Equatable {
    var scale: Int
    var theme: String       // light, sepia, dark
    var font: String        // book, serif, sans
    var spread: Bool        // two pages side by side in wide windows
}

/// Drives one EPUB in a web view: the page script lays chapters out in columns and reports page turns back.
@MainActor
final class EPUBReader: NSObject, ObservableObject, WKNavigationDelegate, WKScriptMessageHandler {
    let book: LibraryBook
    let pkg: EPUBPackage
    let webView: WKWebView
    @Published private(set) var chapter = 0
    @Published private(set) var page = 0
    @Published private(set) var pages = 1
    @Published private(set) var loading = true
    var settings: ReaderSettings {
        didSet {
            guard settings != oldValue else { return }
            applyBackground()
            webView.evaluateJavaScript("bf.restyle(\(json(settings)))")
        }
    }
    private var landing: (fraction: Double, anchor: String?) = (0, nil)

    init(book: LibraryBook, pkg: EPUBPackage, settings: ReaderSettings) {
        self.book = book
        self.pkg = pkg
        self.settings = settings
        let config = WKWebViewConfiguration()
        let content = WKUserContentController()
        content.addUserScript(WKUserScript(source: Self.script, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        config.userContentController = content
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        content.add(WeakHandler(self), name: "bf")
        webView.navigationDelegate = self
        webView.allowsMagnification = false
        applyBackground()
        let pos = book.position
        load(min(pos?.chapter ?? 0, pkg.spine.count - 1), fraction: pos?.fraction ?? 0)
    }

    var progress: Double {
        let f = pages > 1 ? Double(page) / Double(pages - 1) : 0
        return min(1, pkg.starts[chapter] + pkg.shares[chapter] * f)
    }

    /// The table-of-contents entry the reader is in.
    var currentEntry: TOCEntry? { pkg.toc.last { $0.chapter <= chapter } }

    func load(_ index: Int, fraction: Double = 0, anchor: String? = nil) {
        guard pkg.spine.indices.contains(index) else { return }
        chapter = index
        landing = (fraction, anchor)
        loading = true
        webView.alphaValue = 0
        webView.loadFileURL(pkg.spine[index], allowingReadAccessTo: pkg.root)
    }

    func next() { webView.evaluateJavaScript("bf.next()") }
    func previous() { webView.evaluateJavaScript("bf.prev()") }

    func go(to entry: TOCEntry) {
        if entry.chapter == chapter, let a = entry.anchor {
            webView.evaluateJavaScript("bf.anchor(\(json(a)))")
        } else {
            load(entry.chapter, anchor: entry.anchor)
        }
    }

    /// Jumps to a point in the whole book (0…1).
    func go(toProgress p: Double) {
        let i = (pkg.starts.lastIndex { $0 <= p } ?? 0)
        let f = pkg.shares[i] > 0 ? (p - pkg.starts[i]) / pkg.shares[i] : 0
        if i == chapter {
            webView.evaluateJavaScript("bf.fraction(\(min(1, max(0, f))))")
        } else {
            load(i, fraction: min(1, max(0, f)))
        }
    }

    private func applyBackground() {
        let bg = Self.themes[settings.theme]?.0 ?? .white
        webView.underPageBackgroundColor = bg
    }

    static let themes: [String: (NSColor, NSColor)] = [
        "light": (NSColor(white: 1, alpha: 1), NSColor(white: 0.11, alpha: 1)),
        "sepia": (NSColor(red: 0.957, green: 0.925, blue: 0.847, alpha: 1), NSColor(red: 0.26, green: 0.2, blue: 0.13, alpha: 1)),
        "dark": (NSColor(white: 0.12, alpha: 1), NSColor(white: 0.85, alpha: 1)),
    ]

    private func json<T: Encodable>(_ v: T) -> String {
        String(decoding: (try? JSONEncoder().encode(v)) ?? Data("null".utf8), as: UTF8.self)
    }

    // MARK: WKNavigationDelegate

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let anchor = landing.anchor.map(json) ?? "null"
        webView.evaluateJavaScript("bf.setup(\(json(settings)), \(landing.fraction), \(anchor), \(pkg.rtl))")
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { showAnyway() }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { showAnyway() }
    private func showAnyway() { loading = false; webView.alphaValue = 1 }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        guard action.navigationType == .linkActivated, let url = action.request.url else {
            decisionHandler(.allow)
            return
        }
        decisionHandler(.cancel)
        if url.isFileURL {
            let target = url.standardizedFileURL.path
            if let i = pkg.spine.firstIndex(where: { $0.path == target }) {
                if i == chapter, let frag = url.fragment {
                    webView.evaluateJavaScript("bf.anchor(\(json(frag)))")
                } else {
                    load(i, anchor: url.fragment)
                }
            }
        } else {
            NSWorkspace.shared.open(url)
        }
    }

    // MARK: WKScriptMessageHandler

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "page":
            page = body["page"] as? Int ?? 0
            pages = max(1, body["pages"] as? Int ?? 1)
            if loading {
                loading = false
                webView.alphaValue = 1
                webView.window?.makeFirstResponder(webView)
            }
            let f = pages > 1 ? Double(page) / Double(pages - 1) : 0
            Library.shared.setPosition(book.id, ReadingPosition(chapter: chapter, fraction: f, progress: progress))
        case "edge":
            if body["dir"] as? String == "next" {
                if chapter + 1 < pkg.spine.count { load(chapter + 1) }
            } else if chapter > 0 {
                load(chapter - 1, fraction: 1)
            }
        default:
            break
        }
    }

    /// The page script. Chapters are laid out in CSS columns exactly one window wide (one window tall for
    /// vertical Japanese text, whose columns stack downwards), and pages turn by jumping a whole window.
    static let script = #"""
    (function () {
      if (window.bf) return;
      const style = document.createElement('style');
      document.documentElement.appendChild(style);
      const themes = { light: ['#ffffff', '#1d1d1f', '#0a58ca'], sepia: ['#f4ecd8', '#433422', '#8a5a2b'], dark: ['#1e1e1e', '#d9d9d9', '#8ab4ff'] };
      let s = null, page = 0, pages = 1, W = 1, H = 1, vertical = false, rtl = false;
      const post = (m) => window.webkit.messageHandlers.bf.postMessage(m);
      const se = () => document.scrollingElement || document.documentElement;

      function layout() {
        W = window.innerWidth;
        H = window.innerHeight;
        const pad = Math.max(24, Math.round(W * 0.06)), vpad = 40;
        const cols = (s.spread && W >= 1000 && !vertical) ? 2 : 1;
        const colW = (W - 2 * pad * cols) / cols;
        const [bg, fg, link] = themes[s.theme] || themes.light;
        const family = s.font === 'serif' ? '"Iowan Old Style", "Hiragino Mincho ProN", Georgia, serif'
                     : s.font === 'sans' ? '-apple-system, "Hiragino Sans", "Helvetica Neue", sans-serif' : null;
        let css = `
          html { background: ${bg} !important; font-size: ${s.scale}% !important; overflow: hidden !important;
                 margin: 0 !important; padding: 0 !important; height: ${H}px !important; -webkit-text-size-adjust: none; }
          body { background: transparent !important; color: ${fg} !important; margin: 0 !important;
                 box-sizing: border-box !important; height: ${H}px !important; width: ${W}px !important;
                 max-width: none !important; max-height: none !important; padding: ${vpad}px ${pad}px !important;
                 column-fill: auto !important; overflow-wrap: break-word; }
          a, a * { color: ${link} !important; }
          img, svg, video { max-width: 100% !important; max-height: ${H - 2 * vpad}px !important; object-fit: contain;
                            break-inside: avoid; page-break-inside: avoid; }`;
        if (family) css += `body, body p, body div, body span, body li, body blockquote, body td { font-family: ${family} !important; }`;
        if (s.theme !== 'light') css += `body *:not(a) { color: inherit !important; background-color: transparent !important; }`;
        if (vertical) css += `body { column-width: ${H - 2 * vpad}px !important; column-gap: ${2 * vpad}px !important; }`;
        else css += `body { column-width: ${colW}px !important; column-gap: ${2 * pad}px !important; }`;
        style.textContent = css;
      }
      function count() {
        pages = Math.max(1, vertical ? Math.ceil((se().scrollHeight - 4) / H) : Math.ceil((se().scrollWidth - 4) / W));
      }
      function show() {
        if (vertical) se().scrollTop = page * H; else se().scrollLeft = page * W;
        post({ type: 'page', page: page, pages: pages });
      }
      function settle(fn) { requestAnimationFrame(() => requestAnimationFrame(fn)); }
      function pageOf(el) {
        const r = el.getBoundingClientRect();
        const p = vertical ? Math.floor((r.top + se().scrollTop) / H) : Math.floor((r.left + se().scrollLeft) / W);
        return Math.max(0, Math.min(pages - 1, p));
      }

      window.bf = {
        setup(settings, fraction, anchor, pageRTL) {
          s = settings;
          rtl = pageRTL;
          const wm = getComputedStyle(document.body || document.documentElement).writingMode || '';
          vertical = wm.startsWith('vertical');
          layout();
          const go = () => settle(() => {
            count();
            if (anchor) { this.anchor(anchor); return; }
            page = Math.round(fraction * (pages - 1));
            show();
          });
          if (document.readyState === 'complete') go(); else window.addEventListener('load', go, { once: true });
        },
        restyle(settings) {
          if (!s) return;
          const f = pages > 1 ? page / (pages - 1) : 0;
          s = settings;
          layout();
          settle(() => { count(); page = Math.round(f * (pages - 1)); show(); });
        },
        next() { if (page < pages - 1) { page++; show(); } else post({ type: 'edge', dir: 'next' }); },
        prev() { if (page > 0) { page--; show(); } else post({ type: 'edge', dir: 'prev' }); },
        fraction(f) { count(); page = Math.round(f * (pages - 1)); show(); },
        anchor(id) {
          const el = document.getElementById(id) || document.getElementsByName(id)[0];
          page = el ? pageOf(el) : 0;
          show();
        },
      };

      const backwards = () => vertical || rtl;
      document.addEventListener('keydown', (e) => {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const k = e.key;
        if (k === ' ' || k === 'PageDown' || k === 'ArrowDown') { e.shiftKey && k === ' ' ? bf.prev() : bf.next(); }
        else if (k === 'PageUp' || k === 'ArrowUp') bf.prev();
        else if (k === 'ArrowRight') backwards() ? bf.prev() : bf.next();
        else if (k === 'ArrowLeft') backwards() ? bf.next() : bf.prev();
        else return;
        e.preventDefault();
      });
      document.addEventListener('click', (e) => {
        if (e.target.closest && e.target.closest('a')) return;
        if (String(window.getSelection())) return;
        const x = e.clientX / W;
        if (x < 0.3) backwards() ? bf.next() : bf.prev();
        else if (x > 0.7) backwards() ? bf.prev() : bf.next();
      });
      let acc = 0, locked = false;
      window.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (locked) return;
        const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? (backwards() ? -e.deltaX : e.deltaX) : e.deltaY;
        acc += d;
        if (Math.abs(acc) > 40) {
          acc > 0 ? bf.next() : bf.prev();
          locked = true;
          setTimeout(() => { locked = false; acc = 0; }, 450);
        }
      }, { passive: false });
      let timer = null;
      window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(() => s && bf.restyle(s), 120); });
    })();
    """#
}

/// Keeps the user content controller from retaining the reader.
private final class WeakHandler: NSObject, WKScriptMessageHandler {
    weak var target: (any WKScriptMessageHandler)?
    init(_ target: any WKScriptMessageHandler) { self.target = target }
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        target?.userContentController(c, didReceive: m)
    }
}

private struct WebView: NSViewRepresentable {
    let view: WKWebView
    func makeNSView(context: Context) -> WKWebView { view }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

// MARK: - Windows

struct ReaderWindow: View {
    let bookID: String

    var body: some View {
        if let book = Library.shared.book(bookID) {
            if book.format == "pdf" { PDFReaderView(book: book) } else { EPUBReaderView(book: book) }
        } else {
            ContentUnavailableView("Book not found", systemImage: "book.closed",
                                   description: Text("It may have been removed from the library."))
        }
    }
}

struct EPUBReaderView: View {
    let book: LibraryBook
    @State private var reader: EPUBReader?
    @State private var failure: String?
    @State private var showContents = false
    @State private var showStyle = false
    @State private var scrub: Double?
    @AppStorage("readerScale") private var scale = 100
    @AppStorage("readerTheme") private var theme = "auto"
    @AppStorage("readerFont") private var font = "book"
    @AppStorage("readerSpread") private var spread = true
    @Environment(\.colorScheme) private var colorScheme

    private var settings: ReaderSettings {
        ReaderSettings(scale: scale, theme: theme == "auto" ? (colorScheme == .dark ? "dark" : "light") : theme,
                       font: font, spread: spread)
    }

    var body: some View {
        Group {
            if let reader {
                content(reader)
            } else if let failure {
                ContentUnavailableView("Can't open this book", systemImage: "exclamationmark.triangle", description: Text(failure))
            } else {
                ProgressView("Opening…").frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .navigationTitle(book.title)
        .navigationSubtitle(book.author)
        .frame(minWidth: 480, minHeight: 480)
        .task {
            let url = Library.shared.url(book), id = book.id
            do {
                let pkg = try await Task.detached { try EPUBPackage.open(url, id: id) }.value
                reader = EPUBReader(book: Library.shared.book(id) ?? book, pkg: pkg, settings: settings)
            } catch {
                failure = error.localizedDescription
            }
        }
        .onChange(of: settings) { reader?.settings = settings }
    }

    private func content(_ r: EPUBReader) -> some View {
        let bg = Color(nsColor: EPUBReader.themes[settings.theme]?.0 ?? .white)
        let fg = Color(nsColor: EPUBReader.themes[settings.theme]?.1 ?? .black)
        return VStack(spacing: 0) {
            ZStack {
                WebView(view: r.webView)
                if r.loading { ProgressView() }
            }
            HStack(spacing: 12) {
                Button { r.previous() } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(.borderless).help("Previous page")
                Text(r.currentEntry?.title ?? book.title).lineLimit(1)
                Spacer()
                Slider(value: Binding(get: { scrub ?? r.progress }, set: { scrub = $0 }), in: 0...1) { editing in
                    if !editing, let p = scrub { r.go(toProgress: p); scrub = nil }
                }
                .frame(maxWidth: 220)
                .controlSize(.small)
                Text("\(Int(((scrub ?? r.progress) * 100).rounded()))%").monospacedDigit().frame(width: 40, alignment: .trailing)
                Text("Page \(r.page + 1) of \(r.pages)").monospacedDigit().foregroundStyle(fg.opacity(0.6))
                Button { r.next() } label: { Image(systemName: "chevron.right") }
                    .buttonStyle(.borderless).help("Next page")
            }
            .font(.callout)
            .foregroundStyle(fg.opacity(0.8))
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(bg)
        }
        .background(bg)
        .toolbar {
            ToolbarItem {
                Button { showContents.toggle() } label: { Label("Contents", systemImage: "list.bullet") }
                    .disabled(r.pkg.toc.isEmpty)
                    .popover(isPresented: $showContents, arrowEdge: .bottom) { contents(r) }
            }
            ToolbarItem {
                Button { showStyle.toggle() } label: { Label("Text", systemImage: "textformat.size") }
                    .popover(isPresented: $showStyle, arrowEdge: .bottom) { styleControls }
            }
        }
    }

    private func contents(_ r: EPUBReader) -> some View {
        ScrollViewReader { proxy in
            List(r.pkg.toc) { entry in
                Button {
                    r.go(to: entry)
                    showContents = false
                } label: {
                    Text(entry.title)
                        .fontWeight(entry.id == r.currentEntry?.id ? .semibold : .regular)
                        .padding(.leading, CGFloat(entry.depth) * 14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .id(entry.id)
            }
            .frame(width: 320, height: 420)
            .onAppear { if let c = r.currentEntry { proxy.scrollTo(c.id, anchor: .center) } }
        }
    }

    private var styleControls: some View {
        Form {
            LabeledContent("Text size") {
                HStack {
                    Button { scale = max(60, scale - 10) } label: { Image(systemName: "textformat.size.smaller") }
                    Text("\(scale)%").monospacedDigit().frame(width: 44)
                    Button { scale = min(250, scale + 10) } label: { Image(systemName: "textformat.size.larger") }
                }
            }
            Picker("Theme", selection: $theme) {
                Text("Auto").tag("auto")
                Text("Light").tag("light")
                Text("Sepia").tag("sepia")
                Text("Dark").tag("dark")
            }
            .pickerStyle(.segmented)
            Picker("Font", selection: $font) {
                Text("Book's own").tag("book")
                Text("Serif").tag("serif")
                Text("Sans serif").tag("sans")
            }
            Toggle("Two pages in wide windows", isOn: $spread)
        }
        .formStyle(.grouped)
        .frame(width: 340)
    }
}

struct PDFReaderView: View {
    let book: LibraryBook

    var body: some View {
        PDFKitView(book: book)
            .navigationTitle(book.title)
            .navigationSubtitle(book.author)
            .frame(minWidth: 480, minHeight: 480)
    }
}

private struct PDFKitView: NSViewRepresentable {
    let book: LibraryBook

    func makeNSView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.displaysPageBreaks = true
        if let doc = PDFDocument(url: Library.shared.url(book)) {
            view.document = doc
            if let p = book.position?.chapter, let page = doc.page(at: min(p, doc.pageCount - 1)) { view.go(to: page) }
        }
        context.coordinator.observe(view)
        return view
    }

    func updateNSView(_ nsView: PDFView, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(id: book.id) }

    @MainActor
    final class Coordinator {
        let id: String
        var token: NSObjectProtocol?
        init(id: String) { self.id = id }

        func observe(_ view: PDFView) {
            token = NotificationCenter.default.addObserver(forName: .PDFViewPageChanged, object: view, queue: .main) { [id] note in
                MainActor.assumeIsolated {
                    guard let v = note.object as? PDFView, let doc = v.document, let page = v.currentPage else { return }
                    let i = doc.index(for: page)
                    Library.shared.setPosition(id, ReadingPosition(chapter: i, fraction: 0,
                                                                   progress: doc.pageCount > 1 ? Double(i) / Double(doc.pageCount - 1) : 1))
                }
            }
        }
    }
}
