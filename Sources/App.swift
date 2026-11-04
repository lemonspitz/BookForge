import SwiftUI
import PDFKit
import UniformTypeIdentifiers

@MainActor @Observable
final class BookJob {
    enum Phase: Equatable { case idle, reading, building, translating, sending, done, failed }
    enum SendMethod: String { case usb, wifi }

    var source: URL?
    var libraryBook: LibraryBook?   // the source or result, once it's in the library
    var isPDF = false
    var cover: NSImage?
    var title = ""
    var author = ""
    var language = "id"
    var translateTo = ""          // "" = keep the book's language
    var forceOCR = false
    var sendAfter = true

    // remembered between launches
    var sendMethod = SendMethod(rawValue: UserDefaults.standard.string(forKey: "sendMethod") ?? "") ?? .usb {
        didSet { UserDefaults.standard.set(sendMethod.rawValue, forKey: "sendMethod") }
    }
    var kindleEmail = UserDefaults.standard.string(forKey: "kindleEmail") ?? "" {
        didSet { UserDefaults.standard.set(kindleEmail, forKey: "kindleEmail") }
    }
    var senderEmail = UserDefaults.standard.string(forKey: "senderEmail") ?? "" {
        didSet { UserDefaults.standard.set(senderEmail, forKey: "senderEmail") }
    }

    var phase: Phase = .idle
    var progress = 0.0
    var status = "Ready."
    var epub: URL?
    var chapters: [String] = []
    var askQuitCalibre = false
    var askConfirmCost = false
    var estimatedCost = 0.0
    var styleSheet = ""

    var engine = Translator.Engine(rawValue: UserDefaults.standard.string(forKey: "engine") ?? "") ?? .claude {
        didSet { UserDefaults.standard.set(engine.rawValue, forKey: "engine") }
    }
    var apiKey = KeychainKey.load() ?? "" {
        didSet { KeychainKey.save(apiKey.trimmingCharacters(in: .whitespacesAndNewlines)) }
    }

    var busy: Bool { [.reading, .building, .translating, .sending].contains(phase) }
    var translating: Bool { !translateTo.isEmpty && translateTo != language }

    func load(_ url: URL, libraryBook: LibraryBook? = nil) {
        guard !busy else { return }
        source = url
        self.libraryBook = libraryBook
        styleSheet = ""
        epub = nil
        chapters = []
        phase = .idle
        progress = 0
        isPDF = url.pathExtension.lowercased() == "pdf"
        let guess = Self.guessFromFilename(url)
        if isPDF, let doc = PDFDocument(url: url) {
            let attrs = doc.documentAttributes ?? [:]
            title = (attrs[PDFDocumentAttribute.titleAttribute] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? guess.title
            author = (attrs[PDFDocumentAttribute.authorAttribute] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? guess.author
            if let p = doc.page(at: 0), let img = PDFReader.render(p, dpi: 60) {
                cover = NSImage(cgImage: img, size: .zero)
            }
            status = "\(doc.pageCount) pages. Check the title and author, then make the EPUB."
        } else {
            epub = url
            let meta = Library.readMeta(url)
            title = libraryBook?.title ?? meta.title
            author = libraryBook?.author ?? (meta.author.isEmpty ? guess.author : meta.author)
            language = libraryBook?.language ?? meta.language
            language = Languages.known(Languages.normalize(language))
            if translateTo == language { translateTo = "" }
            cover = meta.cover.flatMap { NSImage(data: $0) }
            status = "EPUB ready to translate or send to your e-reader."
        }
    }

    /// "Title -- Author -- ..." (Anna's Archive style) or "Title - Author".
    nonisolated static func guessFromFilename(_ url: URL) -> (title: String, author: String) {
        let name = url.deletingPathExtension().lastPathComponent
        let parts = name.components(separatedBy: " -- ")
        if parts.count >= 2 { return (parts[0], parts[1]) }
        let dash = name.components(separatedBy: " - ")
        if dash.count == 2 { return (dash[0], dash[1]) }
        return (name, "")
    }

    /// New books go straight into the library folder.
    private func outputURL(title: String) -> URL {
        Library.shared.uniqueURL(base: Library.fileName(title, author.trimmingCharacters(in: .whitespaces)), ext: "epub")
    }

    /// PDF → EPUB. Translation happens afterwards, on the EPUB's text.
    func makeEPUB() {
        guard let src = source, isPDF, !busy else { return }
        let info = BookInfo(title: title.trimmingCharacters(in: .whitespaces),
                            author: author.trimmingCharacters(in: .whitespaces), language: language)
        let languages = Languages[language]?.vision ?? ["en-US"]
        let force = forceOCR
        let out = outputURL(title: info.title)
        phase = .reading
        progress = 0
        status = "Reading pages…"
        Task {
            do {
                guard let doc = PDFDocument(url: src) else { throw Shell.Failure(message: "Couldn't open the PDF.") }
                let total = Double(doc.pageCount)
                let pages = await PDFReader.read(doc, languages: languages, forceOCR: force) { done in
                    Task { @MainActor in
                        self.progress = Double(done) / total
                        self.status = "Reading page \(done) of \(Int(total))…"
                    }
                }
                phase = .building
                status = "Rebuilding chapters and paragraphs…"
                let layout = try await Task.detached {
                    let d = PDFDocument(url: src)!
                    var layout = Layout.build(pages: pages, doc: d, languages: languages)
                    layout.blocks = TextFixer(blocks: layout.blocks).finalize(layout.blocks)
                    try EPUBWriter.write(layout: layout, doc: d, info: info, to: out)
                    return layout
                }.value
                chapters = layout.blocks.compactMap { if case .heading(let h) = $0 { h } else { nil } }
                epub = out
                libraryBook = await Library.shared.add([out], quiet: true).first
                phase = .done
                status = "Added “\(info.title)” to your library — \(chapters.count) chapters."
                if translating { translateEPUB() } else if sendAfter { sendToKindle() }
            } catch {
                phase = .failed
                status = error.localizedDescription
            }
        }
    }

    /// Translates the current EPUB (the one dropped in, or the one just made from a PDF).
    func translateEPUB(confirmed: Bool = false) {
        guard let src = epub, translating, !busy else { return }
        if engine == .claude {
            if apiKey.trimmingCharacters(in: .whitespaces).isEmpty {
                phase = .failed
                status = "Paste your Claude API key (console.anthropic.com › API Keys), or switch to Apple Translate."
                return
            }
            if !confirmed {
                // show the rough cost before spending anything
                let chars = (try? Shell.run("/usr/bin/unzip", ["-p", src.path, "*.xhtml", "*.html"]))
                    .map { $0.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression).count } ?? 0
                estimatedCost = ClaudeTranslator.estimate(sourceChars: chars, from: language, to: translateTo)
                askConfirmCost = true
                return
            }
        }
        let target = translateTo, from = language, eng = engine, key = apiKey
        let out = outputURL(title: "\(title) (\(Languages[target]?.native ?? target))")
        phase = .translating
        progress = 0
        styleSheet = ""
        Task {
            do {
                let report = try await Translator.translate(epub: src, from: from, to: target, output: out,
                                                            engine: eng, apiKey: key) { line, p in
                    Task { @MainActor in self.status = line; self.progress = p }
                }
                styleSheet = report.styleSheet
                epub = out
                libraryBook = await Library.shared.add([out], quiet: true).first
                phase = .done
                var msg = "Added the translation to your library."
                if let c = report.cost { msg += String(format: " Claude cost about $%.2f.", c) }
                if report.fellBack > 0 { msg += " \(report.fellBack) of \(report.paragraphs) paragraphs used Apple Translate instead." }
                status = msg
                if sendAfter { sendToKindle() }
            } catch {
                phase = .failed
                status = error.localizedDescription
            }
        }
    }

    func sendToKindle(skipCalibreCheck: Bool = false) {
        guard let book = epub, !busy else { return }
        if sendMethod == .wifi { sendByEmail(book); return }
        if !skipCalibreCheck && Kindle.calibreAppRunning {
            askQuitCalibre = true
            return
        }
        let item = libraryBook.map(SendItem.init)
            ?? SendItem(file: book, title: title, author: author, uuid: UUID().uuidString, language: language)
        phase = .sending
        progress = 0
        status = "Preparing…"
        Task {
            do {
                status = try await Devices.sendNow([item]) { line in Task { @MainActor in self.status = line } }
                phase = .done
            } catch {
                phase = .failed
                status = error.localizedDescription
            }
            await Devices.shared.scan()
        }
    }

    private func sendByEmail(_ book: URL) {
        let to = kindleEmail.trimmingCharacters(in: .whitespaces)
        guard to.contains("@") else {
            phase = .failed
            status = "Enter your Kindle's email address (Amazon › Manage Your Content and Devices › Devices)."
            return
        }
        phase = .sending
        status = "Sending through Mail…"
        // let the status redraw before Mail takes over
        Task {
            try? await Task.sleep(for: .milliseconds(100))
            do {
                try Kindle.email(epub: book, to: to, from: senderEmail.trimmingCharacters(in: .whitespaces))
                phase = .done
                status = "Emailed to \(to). It arrives over Wi-Fi in a few minutes. Amazon may email you to confirm it first."
            } catch {
                phase = .failed
                status = error.localizedDescription
            }
        }
    }

    func quitCalibreAndSend() {
        status = "Closing Calibre…"
        Task {
            if await Kindle.quitCalibre() {
                sendToKindle(skipCalibreCheck: true)
            } else {
                phase = .failed
                status = "Calibre didn't close (a dialog may be open in it). Close it yourself, then press Send to Kindle."
            }
        }
    }
}

struct ForgeView: View {
    @Bindable var job: BookJob
    @State private var targeted = false
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 16) {
            dropZone
            if job.source != nil { details }
            statusArea
        }
        .padding(20)
        .navigationTitle("Convert & Translate")
        .alert("Close Calibre?", isPresented: $job.askQuitCalibre) {
            Button("Quit Calibre and Send") { job.quitCalibreAndSend() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The Calibre app is open and holds the e-reader connection. It has to be closed for Book Forge to send the book.")
        }
        .alert("Translate with Claude?", isPresented: $job.askConfirmCost) {
            Button("Translate") { job.translateEPUB(confirmed: true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(String(format: "This sends the book's text to Anthropic and costs roughly $%.2f on your API account. Paragraphs Claude declines are translated by Apple instead.", job.estimatedCost))
        }
    }

    private var dropZone: some View {
        HStack(spacing: 16) {
            Group {
                if let c = job.cover {
                    Image(nsImage: c).resizable().aspectRatio(contentMode: .fit)
                } else {
                    Image(systemName: job.source == nil ? "doc.badge.plus" : "book.closed")
                        .font(.system(size: 36)).foregroundStyle(.secondary)
                }
            }
            .frame(width: 80, height: 110)
            VStack(alignment: .leading, spacing: 6) {
                Text(job.source?.lastPathComponent ?? "Drop a PDF or EPUB")
                    .font(.headline).lineLimit(2).truncationMode(.middle)
                Text("Scanned PDFs are read with on-device text recognition.")
                    .font(.callout).foregroundStyle(.secondary)
                Button("Choose File…") { choose() }.disabled(job.busy)
            }
            Spacer()
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12)
            .strokeBorder(targeted ? Color.accentColor : Color.secondary.opacity(0.4),
                          style: StrokeStyle(lineWidth: 2, dash: [6])))
        .onDrop(of: [.fileURL], isTargeted: $targeted) { providers in
            _ = providers.first?.loadObject(ofClass: URL.self) { url, _ in
                guard let url, ["pdf", "epub"].contains(url.pathExtension.lowercased()) else { return }
                Task { @MainActor in job.load(url) }
            }
            return true
        }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 12) {
            Form {
                Section("Book") {
                    TextField("Title", text: $job.title)
                    TextField("Author", text: $job.author)
                    Picker("Written in", selection: $job.language) {
                        ForEach(Languages.all, id: \.code) { Text($0.name).tag($0.code) }
                    }
                    if job.isPDF {
                        Toggle("Always use text recognition (ignore the PDF's own text)", isOn: $job.forceOCR)
                    }
                }
                Section("Translate") {
                    Picker("Translate to", selection: $job.translateTo) {
                        Text("Don't translate").tag("")
                        ForEach(Languages.codes.filter { $0 != job.language }, id: \.self) {
                            Text(Languages.name($0)).tag($0)
                        }
                    }
                    if job.translating {
                        Picker("Translator", selection: $job.engine) {
                            Text("Claude (natural style)").tag(Translator.Engine.claude)
                            Text("Apple (quick, offline)").tag(Translator.Engine.apple)
                        }
                        .pickerStyle(.segmented)
                        if job.engine == .claude {
                            SecureField("Claude API key", text: $job.apiKey, prompt: Text("sk-ant-…  (kept in your Keychain)"))
                            Text("Claude first studies the book's narration, each character's way of speaking and its names, then translates with that style sheet. It uses your Anthropic API account (about $2–5 for a novel).")
                                .font(.caption).foregroundStyle(.secondary)
                        } else {
                            Text("Free and offline, sentence by sentence. Faster, but literal: slang, tone and speech styles are not adapted.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if job.isPDF && job.epub == nil {
                            Text("The PDF is turned into an EPUB first, then its text is translated (no second scan).")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Section("Send") {
                    Picker("Send by", selection: $job.sendMethod) {
                        Text("USB cable").tag(BookJob.SendMethod.usb)
                        Text("Wi-Fi (Kindle email)").tag(BookJob.SendMethod.wifi)
                    }
                    .pickerStyle(.segmented)
                    if job.sendMethod == .wifi {
                        TextField("Kindle email", text: $job.kindleEmail, prompt: Text("name@kindle.com"))
                        TextField("Send from", text: $job.senderEmail, prompt: Text("Mail's default account"))
                        Text("The sender must be on Amazon's Approved Personal Document Email List.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text("Works with Kindle, Kobo, PocketBook and other e-readers over USB. With Calibre installed, EPUBs become Kindle books with covers in the Kindle library.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Toggle("Send when done", isOn: $job.sendAfter)
                }
            }
            .formStyle(.columns)
            .disabled(job.busy)
            .onChange(of: job.language) { if job.translateTo == job.language { job.translateTo = "" } }
            HStack {
                if job.isPDF {
                    Button(job.translating && job.epub == nil ? "Make EPUB and Translate" : "Make EPUB") { job.makeEPUB() }
                        .keyboardShortcut(.defaultAction)
                        .disabled(job.busy || job.title.isEmpty)
                }
                if job.translating && job.epub != nil {
                    Button("Translate EPUB") { job.translateEPUB() }
                        .disabled(job.busy)
                }
                Button(job.sendMethod == .wifi ? "Email to Kindle" : "Send to E-reader") { job.sendToKindle() }
                    .disabled(job.busy || job.epub == nil)
                Spacer()
                if let b = job.libraryBook {
                    Button("Read") { openWindow(id: "reader", value: b.id) }
                }
                if let e = job.epub, FileManager.default.fileExists(atPath: e.path) {
                    Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([e]) }
                }
            }
        }
    }

    private var statusArea: some View {
        VStack(alignment: .leading, spacing: 8) {
            if job.busy {
                if [.reading, .translating].contains(job.phase) {
                    ProgressView(value: job.progress)
                } else {
                    ProgressView().progressViewStyle(.linear)
                }
            }
            Label(job.status, systemImage: icon).foregroundStyle(job.phase == .failed ? .red : .primary)
                .textSelection(.enabled)
            if !job.styleSheet.isEmpty {
                DisclosureGroup("Style Claude learned from this book") {
                    ScrollView {
                        Text(job.styleSheet).font(.callout).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(maxHeight: 160)
                }
            }
            if !job.chapters.isEmpty {
                DisclosureGroup("Chapters found (\(job.chapters.count))") {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(Array(job.chapters.enumerated()), id: \.offset) { Text($0.element).font(.callout) }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(maxHeight: 120)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var icon: String {
        switch job.phase {
        case .done: "checkmark.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        case .idle: "info.circle"
        default: "hourglass"
        }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf, UTType(filenameExtension: "epub")!]
        if panel.runModal() == .OK, let url = panel.url { job.load(url) }
    }
}

struct BookForgeApp: App {
    var body: some Scene {
        Window("Book Forge", id: "main") { MainView() }
            .windowResizability(.contentMinSize)
            .defaultSize(width: 1080, height: 720)
        WindowGroup("Reader", id: "reader", for: String.self) { $id in
            ReaderWindow(bookID: id ?? "")
        }
        .defaultSize(width: 900, height: 820)
    }
}
