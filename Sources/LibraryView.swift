import SwiftUI
import UniformTypeIdentifiers

enum SidebarItem: Hashable { case library, device, forge }

struct MainView: View {
    @MainActor private static var openedLaunchBook = false
    @Environment(\.openWindow) private var openWindow
    @State private var selection: SidebarItem? = .library
    @State private var job = BookJob()
    private var devices = Devices.shared
    private var library = Library.shared

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                Section("Library") {
                    Label("All Books", systemImage: "books.vertical")
                        .badge(library.books.count)
                        .tag(SidebarItem.library)
                }
                Section("Devices") {
                    DeviceRow().tag(SidebarItem.device)
                }
                Section("Tools") {
                    Label("Convert & Translate", systemImage: "wand.and.stars").tag(SidebarItem.forge)
                }
            }
            .navigationSplitViewColumnWidth(min: 200, ideal: 220)
        } detail: {
            switch selection ?? .library {
            case .library:
                LibraryView { book in
                    job.load(library.url(book), libraryBook: book)
                    selection = .forge
                }
            case .device: DeviceView()
            case .forge: ForgeView(job: job)
            }
        }
        .frame(minWidth: 860, minHeight: 560)
        .task {
            devices.start()
            // `--read <book id>` opens a book on launch (used for testing the reader)
            let args = CommandLine.arguments
            if !Self.openedLaunchBook, let i = args.firstIndex(of: "--read"), i + 1 < args.count {
                Self.openedLaunchBook = true
                openWindow(id: "reader", value: args[i + 1])
            }
        }
        .onOpenURL { url in
            guard ["pdf", "epub"].contains(url.pathExtension.lowercased()) else { return }
            job.load(url)
            selection = .forge
        }
        .alert("Close Calibre?", isPresented: Binding(get: { devices.askQuitCalibre }, set: { devices.askQuitCalibre = $0 })) {
            Button("Quit Calibre and Continue") { devices.quitCalibreAndContinue() }
            Button("Cancel", role: .cancel) { devices.cancelPending() }
        } message: {
            Text("The Calibre app is open and holds the e-reader connection. It has to be closed for Book Forge to change what's on the reader.")
        }
    }
}

private struct DeviceRow: View {
    private var devices = Devices.shared

    var body: some View {
        HStack(spacing: 8) {
            DeviceImage(kind: devices.reader?.kind ?? (devices.state == .none ? "generic" : devices.busyKind))
                .frame(width: 18, height: 24)
                .opacity(devices.reader == nil ? 0.45 : 1)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).lineLimit(1)
                if let sub = subtitle { Text(sub).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            }
            Spacer(minLength: 0)
            if devices.scanning || devices.working != nil { ProgressView().controlSize(.small) }
        }
    }

    private var title: String {
        switch devices.state {
        case .connected: devices.reader?.name ?? "E-reader"
        case .busy(let name): name
        case .ejected(let name): name
        default: "E-reader"
        }
    }

    private var subtitle: String? {
        switch devices.state {
        case .connected: devices.reader.map { "\($0.books.count) books" }
        case .busy: "In use by Calibre"
        case .ejected: "Safe to unplug"
        case .noCalibre: "Needs Calibre"
        case .failed: "Couldn't connect"
        case .none: "Not connected"
        }
    }
}

// MARK: - Library

struct LibraryView: View {
    var onTranslate: (LibraryBook) -> Void
    @Environment(\.openWindow) private var openWindow
    @State private var search = ""
    @AppStorage("librarySort") private var sort = "added"
    @AppStorage("libraryShow") private var show = "all"     // all, on, off (on the e-reader or not)
    @State private var selected: Set<String> = []
    @State private var targeted = false
    @State private var editing: LibraryBook?
    @State private var removing: Set<String> = []
    @State private var alert: String?
    private var library = Library.shared
    private var devices = Devices.shared

    init(onTranslate: @escaping (LibraryBook) -> Void) { self.onTranslate = onTranslate }

    private var books: [LibraryBook] {
        let q = search.trimmingCharacters(in: .whitespaces)
        var found = q.isEmpty ? library.books : library.books.filter {
            $0.title.localizedStandardContains(q) || $0.author.localizedStandardContains(q)
        }
        if show != "all" && deviceName != nil {
            found = found.filter { (status($0) != .none) == (show == "on") }
        }
        switch sort {
        case "title": return found.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
        case "author": return found.sorted { ($0.author, $0.title) < ($1.author, $1.title) }
        case "recent": return found.sorted { ($0.position == nil ? 0 : 1, $0.added) > ($1.position == nil ? 0 : 1, $1.added) }
        default: return found.sorted { $0.added > $1.added }
        }
    }

    var body: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 130, maximum: 170), spacing: 22, alignment: .top)], spacing: 26) {
                ForEach(books) { book in
                    BookCell(book: book, selected: selected.contains(book.id), onDevice: status(book),
                             deviceName: deviceName ?? "the e-reader", seen: devices.lastSeen?.date)
                        .onTapGesture(count: 2) { open(book) }
                        .simultaneousGesture(TapGesture().onEnded { select(book) })
                        .contextMenu { menu(for: book) }
                }
            }
            .padding(24)
        }
        .background(Color(nsColor: .textBackgroundColor).opacity(0.001).onTapGesture { selected = [] })
        .overlay {
            if library.books.isEmpty {
                ContentUnavailableView {
                    Label("Your library is empty", systemImage: "books.vertical")
                } description: {
                    Text("Drag EPUB or PDF files or folders here, or click Add Books. Books you make in Convert & Translate are added automatically.")
                } actions: {
                    Button("Add Books…") { choose() }
                }
            } else if books.isEmpty && !search.isEmpty {
                ContentUnavailableView.search(text: search)
            } else if books.isEmpty {
                ContentUnavailableView(show == "on" ? "None of your books are on the \(deviceName ?? "e-reader")"
                                                    : "All your books are on the \(deviceName ?? "e-reader")",
                                       systemImage: "checkmark.circle")
            }
        }
        .overlay {
            if targeted { RoundedRectangle(cornerRadius: 10).strokeBorder(Color.accentColor, lineWidth: 3).padding(6) }
        }
        .onDrop(of: [.fileURL], isTargeted: $targeted) { providers in
            Task { await library.add(await Self.urls(from: providers)) }
            return true
        }
        .safeAreaInset(edge: .bottom) { statusBar }
        .searchable(text: $search, placement: .toolbar, prompt: "Title or author")
        .navigationTitle("Library")
        .toolbar {
            ToolbarItem {
                Menu {
                    Picker("Sort by", selection: $sort) {
                        Text("Date Added").tag("added")
                        Text("Recently Read").tag("recent")
                        Text("Title").tag("title")
                        Text("Author").tag("author")
                    }
                    .pickerStyle(.inline)
                    if let name = deviceName {
                        Picker("Show", selection: $show) {
                            Text("All Books").tag("all")
                            Text("On the \(name)").tag("on")
                            Text("Not on the \(name)").tag("off")
                        }
                        .pickerStyle(.inline)
                    }
                } label: { Label("Sort", systemImage: "arrow.up.arrow.down") }
            }
            if let reader = devices.reader, devices.state == .connected {
                ToolbarItem {
                    let n = devices.toImport.count
                    Button { devices.message = nil; devices.perform { await devices.importNew() } } label: {
                        Label("Import from \(reader.shortName)", systemImage: "square.and.arrow.down")
                    }
                    .help(n == 0 ? "Every book on the \(reader.shortName) you can copy is already in your library"
                                 : "Copy \(n) book\(n == 1 ? "" : "s") that \(n == 1 ? "is" : "are") only on the \(reader.shortName) into your library")
                    .disabled(devices.working != nil)
                }
                ToolbarItem {
                    let n = devices.toSend.count
                    Button { devices.message = nil; devices.perform { await devices.sendNew() } } label: {
                        Label("Send to \(reader.shortName)", systemImage: "square.and.arrow.up")
                    }
                    .help(n == 0 ? "Every book in your library is already on the \(reader.shortName)"
                                 : "Copy \(n) book\(n == 1 ? "" : "s") that \(n == 1 ? "isn't" : "aren't") on the \(reader.shortName) yet")
                    .disabled(devices.working != nil)
                }
            }
            ToolbarItem {
                Button { choose() } label: { Label("Add Books", systemImage: "plus") }
                    .help("Add EPUB or PDF files, or whole folders")
            }
        }
        .sheet(item: $editing) { EditBookSheet(book: $0) }
        .alert("Move to the Trash?", isPresented: Binding(get: { !removing.isEmpty }, set: { if !$0 { removing = [] } })) {
            Button("Move to Trash", role: .destructive) {
                library.remove(removing)
                selected.subtract(removing)
                removing = []
            }
            Button("Cancel", role: .cancel) { removing = [] }
        } message: {
            Text(removing.count == 1 ? "The book's file goes to the Trash. Copies on e-readers stay where they are."
                                     : "\(removing.count) books' files go to the Trash. Copies on e-readers stay where they are.")
        }
        .alert(alert ?? "", isPresented: Binding(get: { alert != nil }, set: { if !$0 { alert = nil } })) {
            Button("OK") { alert = nil }
        }
    }

    /// The e-reader the library's badges are about: the connected one, or the one last connected.
    private var deviceName: String? {
        if let r = devices.reader { return r.shortName }
        return devices.lastSeen.map { $0.kind.hasPrefix("kindle") ? "Kindle" : $0.name }
    }

    private func status(_ book: LibraryBook) -> DeviceStatus {
        if devices.reader != nil { return devices.match(book) != nil ? .on : .none }
        return devices.wasOnReader(book) ? .wasOn : .none
    }

    /// How the library and the connected reader compare.
    private var summary: String? {
        guard let reader = devices.reader, devices.state == .connected else { return nil }
        let on = library.books.filter { devices.match($0) != nil }.count
        let only = devices.onlyOnReader.count
        var parts = ["\(on) of \(library.books.count) books are on your \(reader.shortName)"]
        if only > 0 { parts.append("\(only) book\(only == 1 ? " is" : "s are") only on the \(reader.shortName)") }
        return parts.joined(separator: " · ")
    }

    private var statusBar: some View {
        let text = devices.working ?? library.message ?? devices.message ?? summary
        return Group {
            if let text {
                HStack(spacing: 8) {
                    if library.importing || devices.working != nil { ProgressView().controlSize(.small) }
                    Text(text).lineLimit(2)
                    Spacer()
                }
                .font(.callout)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(.bar)
            }
        }
    }

    @ViewBuilder
    private func menu(for book: LibraryBook) -> some View {
        let ids = selected.contains(book.id) ? selected : [book.id]
        let targets = library.books.filter { ids.contains($0.id) }
        if targets.count == 1 {
            Button("Read") { open(book) }
            Divider()
        }
        if let reader = devices.reader {
            let missing = targets.filter { devices.match($0) == nil }
            let present = targets.compactMap { devices.match($0) }
            if !missing.isEmpty {
                Button("Send to \(reader.name)") { devices.perform { await devices.sync(add: missing, remove: []) } }
            }
            if !present.isEmpty {
                Button("Remove from \(reader.name)") { devices.perform { await devices.sync(add: [], remove: present) } }
            }
        }
        if targets.count == 1 {
            Button("Email to Kindle") { email(book) }
            if book.format == "epub" { Button("Translate…") { onTranslate(book) } }
            Button("Edit Info…") { editing = book }
        }
        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting(targets.map { library.url($0) }) }
        Divider()
        Button("Move to Trash…", role: .destructive) { removing = Set(targets.map(\.id)) }
    }

    private func select(_ book: LibraryBook) {
        if NSEvent.modifierFlags.contains(.command) {
            if selected.contains(book.id) { selected.remove(book.id) } else { selected.insert(book.id) }
        } else {
            selected = [book.id]
        }
    }

    private func open(_ book: LibraryBook) {
        selected = [book.id]
        openWindow(id: "reader", value: book.id)
    }

    private func email(_ book: LibraryBook) {
        let to = (UserDefaults.standard.string(forKey: "kindleEmail") ?? "").trimmingCharacters(in: .whitespaces)
        guard to.contains("@") else {
            alert = "Add your Kindle's email address first: Convert & Translate › Send by Wi-Fi."
            return
        }
        let from = (UserDefaults.standard.string(forKey: "senderEmail") ?? "").trimmingCharacters(in: .whitespaces)
        library.message = "Sending “\(book.title)” through Mail…"
        Task {
            try? await Task.sleep(for: .milliseconds(100))
            do {
                try Kindle.email(epub: library.url(book), to: to, from: from)
                library.message = "Emailed “\(book.title)” to \(to). It arrives over Wi-Fi in a few minutes."
            } catch {
                library.message = error.localizedDescription
            }
        }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf, UTType(filenameExtension: "epub")!, .folder]
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = true
        panel.prompt = "Add"
        if panel.runModal() == .OK { Task { await library.add(panel.urls) } }
    }

    static func urls(from providers: [NSItemProvider]) async -> [URL] {
        var out: [URL] = []
        for p in providers {
            if let url = await withCheckedContinuation({ (c: CheckedContinuation<URL?, Never>) in
                _ = p.loadObject(ofClass: URL.self) { url, _ in c.resume(returning: url) }
            }) {
                out.append(url)
            }
        }
        return out
    }
}

enum DeviceStatus { case on, wasOn, none }

private struct BookCell: View {
    let book: LibraryBook
    let selected: Bool
    let onDevice: DeviceStatus
    let deviceName: String
    let seen: Date?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            BookCover(book: book)
                .overlay(alignment: .topTrailing) {
                    if onDevice != .none {
                        Image(systemName: "checkmark.circle.fill")
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.white, onDevice == .on ? .green : .gray)
                            .font(.title3)
                            .shadow(radius: 2)
                            .padding(5)
                            .help(onDevice == .on ? "On your \(deviceName)"
                                  : "Was on your \(deviceName) when it was last connected\(seen.map { " (\($0.formatted(date: .abbreviated, time: .shortened)))" } ?? "")")
                    }
                }
                .overlay(alignment: .bottomLeading) {
                    if book.format == "pdf" {
                        Text("PDF").font(.caption2.bold()).padding(.horizontal, 5).padding(.vertical, 2)
                            .background(.black.opacity(0.6), in: RoundedRectangle(cornerRadius: 3))
                            .foregroundStyle(.white).padding(5)
                    }
                }
            if let p = book.position?.progress, p > 0 {
                ProgressView(value: min(1, p)).controlSize(.mini).tint(p >= 0.99 ? .green : .accentColor)
            }
            Text(book.title).font(.callout.weight(.semibold)).lineLimit(2)
            Text(book.author.isEmpty ? " " : book.author).font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(6)
        .background(RoundedRectangle(cornerRadius: 8).fill(selected ? Color.accentColor.opacity(0.22) : .clear))
        .contentShape(Rectangle())
    }
}

struct BookCover: View {
    let book: LibraryBook

    var body: some View {
        // a 2:3 frame the cover fills and is cropped to
        Color.clear
            .aspectRatio(2 / 3, contentMode: .fit)
            .overlay {
                if let img = Library.shared.cover(book) {
                    Image(nsImage: img).resizable().scaledToFill()
                } else {
                    ZStack {
                        LinearGradient(colors: [Color(hue: hue, saturation: 0.45, brightness: 0.55),
                                                Color(hue: hue, saturation: 0.55, brightness: 0.35)],
                                       startPoint: .top, endPoint: .bottom)
                        VStack(spacing: 6) {
                            Text(book.title).font(.system(.callout, design: .serif).weight(.semibold))
                            if !book.author.isEmpty { Text(book.author).font(.caption2) }
                        }
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.white)
                        .padding(10)
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 4))
            .shadow(color: .black.opacity(0.25), radius: 4, y: 2)
    }

    // stable across launches, unlike hashValue
    private var hue: Double { Double(book.title.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0xffff } % 360) / 360 }
}

private struct EditBookSheet: View {
    @State var book: LibraryBook
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Edit Info").font(.headline)
            Form {
                TextField("Title", text: $book.title)
                TextField("Author", text: $book.author)
                Picker("Language", selection: $book.language) {
                    ForEach(Languages.all, id: \.code) { Text($0.name).tag($0.code) }
                    if !Languages.codes.contains(book.language) {
                        Text(book.language.isEmpty ? "Unknown" : book.language).tag(book.language)
                    }
                }
            }
            Text("This changes how the book is listed here and named on e-readers. The book file itself isn't changed.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Save") {
                    Library.shared.update(book)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(book.title.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}

// MARK: - Device

struct DeviceView: View {
    private var devices = Devices.shared
    private var library = Library.shared
    @State private var wanted: Set<String> = []       // library books that should be on the reader
    @State private var keep: Set<String> = []         // device-only books to keep
    @State private var filter = ""

    var body: some View {
        Group {
            if devices.state == .connected, let reader = devices.reader {
                connected(reader)
            } else {
                unavailable
            }
        }
        .navigationTitle(devices.reader?.name ?? "E-reader")
        .onAppear(perform: reset)
        .onChange(of: devices.reader) { reset() }
        .onChange(of: library.books.map(\.id)) { old, new in
            // books that just came into the library (imported from the reader, say) and are on it stay ticked
            let fresh = Set(new).subtracting(old)
            wanted.formUnion(library.books.filter { fresh.contains($0.id) && devices.match($0) != nil }.map(\.id))
        }
    }

    private func reset() {
        wanted = Set(library.books.filter { devices.match($0) != nil }.map(\.id))
        keep = Set(orphans.map(\.path))
    }

    /// Books on the reader that aren't in the library.
    private var orphans: [DeviceBook] { devices.onlyOnReader }

    private var toAdd: [LibraryBook] { library.books.filter { wanted.contains($0.id) && devices.match($0) == nil } }
    private var toRemove: [DeviceBook] {
        library.books.filter { !wanted.contains($0.id) }.flatMap { devices.copies(of: $0) }
            + orphans.filter { !keep.contains($0.path) }
    }

    private func connected(_ reader: EReader) -> some View {
        let shown = library.books
            .filter { filter.isEmpty || $0.title.localizedStandardContains(filter) || $0.author.localizedStandardContains(filter) }
            .sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
        return VStack(spacing: 0) {
            header(reader)
            Divider()
            List {
                Section {
                    ForEach(shown) { book in
                        Toggle(isOn: Binding(get: { wanted.contains(book.id) },
                                             set: { if $0 { wanted.insert(book.id) } else { wanted.remove(book.id) } })) {
                            row(book, reader: reader)
                        }
                    }
                } header: {
                    HStack {
                        Text("Your library: tick the books to keep on the \(reader.shortName)")
                        Spacer()
                        Button("All") { wanted.formUnion(shown.map(\.id)) }.buttonStyle(.link)
                        Button("None") { wanted.subtract(shown.map(\.id)) }.buttonStyle(.link)
                    }
                }
                if !orphans.isEmpty {
                    Section {
                        ForEach(orphans) { b in
                            Toggle(isOn: Binding(get: { keep.contains(b.path) },
                                                 set: { if $0 { keep.insert(b.path) } else { keep.remove(b.path) } })) {
                                HStack {
                                    VStack(alignment: .leading) {
                                        Text(b.title.isEmpty ? (b.lpath as NSString).lastPathComponent : b.title)
                                        Text(b.authors.joined(separator: ", ")).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    if !keep.contains(b.path) { Text("Will be removed").font(.caption).foregroundStyle(.red) }
                                    else { StatusTag(text: "Not in library", color: .orange) }
                                    Text(ByteCountFormatter.string(fromByteCount: b.size, countStyle: .file))
                                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                                    if devices.likelyLocked(b) {
                                        Text("Store book").font(.caption).foregroundStyle(.secondary)
                                            .help(b.importable ? "Bought from the Kindle Store and locked with DRM, so Import All leaves it on the reader"
                                                               : "Bought from the Kindle Store in Amazon's KFX format, which only a Kindle can open")
                                    }
                                    if b.importable {
                                        Button { importToLibrary([b]) } label: { Label("Import", systemImage: "square.and.arrow.down") }
                                            .buttonStyle(.borderless)
                                            .help(devices.likelyLocked(b) ? "Try copying this book into your library (store books usually can't be)"
                                                                          : "Copy this book into your library")
                                            .disabled(devices.working != nil)
                                    }
                                }
                            }
                            .contextMenu {
                                Button("Import to Library") { importToLibrary([b]) }.disabled(!b.importable)
                            }
                        }
                    } header: {
                        HStack {
                            Text("Only on the \(reader.shortName) (not in your library)")
                            Spacer()
                            if !devices.toImport.isEmpty {
                                Button("Import All") { importNew() }
                                    .buttonStyle(.link)
                                    .disabled(devices.working != nil)
                            }
                        }
                    }
                }
            }
            .searchable(text: $filter, placement: .toolbar, prompt: "Filter books")
            Divider()
            footer(reader)
        }
    }

    /// Copies books from the reader into the library; they stay on the reader.
    private func importToLibrary(_ books: [DeviceBook]) {
        devices.message = nil
        devices.perform { _ = await devices.importBooks(books) }
    }

    private func importNew() {
        devices.message = nil
        devices.perform { await devices.importNew() }
    }

    private func sendNew() {
        devices.message = nil
        devices.perform { await devices.sendNew() }
    }

    private func header(_ reader: EReader) -> some View {
        let shownCover = library.books.first { devices.match($0) != nil }.flatMap { library.cover($0) }
        let used = max(0, reader.total - reader.free)
        return HStack(alignment: .center, spacing: 24) {
            DeviceImage(kind: reader.kind, cover: shownCover)
                .frame(width: 130, height: 170)
            VStack(alignment: .leading, spacing: 8) {
                Text(reader.name).font(.largeTitle.weight(.semibold))
                Text("\(DeviceImage.familyName(reader.kind)) · connected by USB")
                    .foregroundStyle(.secondary)
                if reader.total > 0 {
                    Gauge(value: Double(used), in: 0...Double(reader.total)) {
                        EmptyView()
                    } currentValueLabel: {
                        EmptyView()
                    }
                    .gaugeStyle(.linearCapacity)
                    .frame(maxWidth: 280)
                    Text("\(ByteCountFormatter.string(fromByteCount: reader.free, countStyle: .file)) free of \(ByteCountFormatter.string(fromByteCount: reader.total, countStyle: .file)) · \(reader.books.count) books")
                        .font(.callout).foregroundStyle(.secondary)
                }
                Text(comparison(reader)).font(.callout).foregroundStyle(.secondary)
                HStack {
                    let imports = devices.toImport.count, sends = devices.toSend.count
                    Button(action: importNew) {
                        Label(imports == 0 ? "Nothing New to Import" : "Import \(imports) New Book\(imports == 1 ? "" : "s")",
                              systemImage: "square.and.arrow.down")
                    }
                    .help("Copy the books that are only on the \(reader.shortName) into your library. Books already in it, extra copies and store books are skipped.")
                    .disabled(imports == 0)
                    Button(action: sendNew) {
                        Label(sends == 0 ? "Nothing New to Send" : "Send \(sends) New Book\(sends == 1 ? "" : "s")",
                              systemImage: "square.and.arrow.up")
                    }
                    .help("Copy the library books that aren't on the \(reader.shortName) yet. Nothing is removed and nothing is copied twice.")
                    .disabled(sends == 0)
                }
                .controlSize(.large)
                .disabled(devices.working != nil || devices.scanning)
                HStack {
                    Button { Task { await devices.scan() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    Button { devices.perform { await devices.eject() } } label: { Label("Eject", systemImage: "eject") }
                }
                .disabled(devices.working != nil || devices.scanning)
            }
            Spacer()
        }
        .padding(20)
    }

    /// "40 in both · 12 only in your library · 110 only on the Kindle"
    private func comparison(_ reader: EReader) -> String {
        let both = library.books.filter { devices.match($0) != nil }.count
        let libOnly = library.books.count - both
        let readerOnly = orphans.count
        return "\(both) in both · \(libOnly) only in your library · \(readerOnly) only on the \(reader.shortName)"
    }

    private func row(_ book: LibraryBook, reader: EReader) -> some View {
        let onDevice = devices.match(book) != nil
        let want = wanted.contains(book.id)
        return HStack(spacing: 10) {
            BookCover(book: book).frame(width: 28, height: 42)
            VStack(alignment: .leading) {
                Text(book.title).lineLimit(1)
                Text(book.author).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            Group {
                if onDevice && want { StatusTag(text: "On \(reader.shortName)", color: .green) }
                else if want { Text(reader.isKindle && book.format == "epub" ? "Will be added (as Kindle book)" : "Will be added").foregroundStyle(Color.accentColor) }
                else if onDevice { Text("Will be removed").foregroundStyle(.red) }
                else { StatusTag(text: "Not on \(reader.shortName)", color: .secondary) }
            }
            .font(.caption)
        }
    }

    private func footer(_ reader: EReader) -> some View {
        let add = toAdd, remove = toRemove
        var parts: [String] = []
        if !add.isEmpty { parts.append("\(add.count) to add") }
        if !remove.isEmpty { parts.append("\(remove.count) to remove") }
        return HStack(spacing: 10) {
            if let w = devices.working {
                ProgressView().controlSize(.small)
                Text(w)
            } else if let m = devices.message {
                Text(m).foregroundStyle(.secondary).lineLimit(2)
            } else {
                Text(parts.isEmpty ? "Everything ticked is on the reader." : parts.joined(separator: " · "))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Revert") { reset() }.disabled(parts.isEmpty || devices.working != nil)
            Button("Sync") {
                devices.message = nil
                devices.perform { await devices.sync(add: add, remove: remove) }
            }
            .keyboardShortcut(.defaultAction)
            .disabled(parts.isEmpty || devices.working != nil)
        }
        .font(.callout)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private var unavailable: some View {
        switch devices.state {
        case .busy(let name):
            ContentUnavailableView {
                Label { Text(name) } icon: { DeviceImage(kind: devices.busyKind).frame(width: 60, height: 80) }
            } description: {
                Text(Kindle.calibreAppRunning
                     ? "The Calibre app is using it. Close Calibre so Book Forge can reach the reader."
                     : "Another app (such as Android File Transfer) is using it. Close that app, replug the reader and try again.")
            } actions: {
                if Kindle.calibreAppRunning {
                    Button("Quit Calibre") { devices.perform { await devices.scan() } }
                }
                Button("Check Again") { Task { await devices.scan() } }
            }
        case .noCalibre:
            ContentUnavailableView {
                Label("Calibre is needed", systemImage: "puzzlepiece.extension")
            } description: {
                Text("Book Forge talks to Kindle, Kobo, PocketBook and other e-readers through Calibre's free device drivers. Install Calibre into Applications, then reopen Book Forge.")
            } actions: {
                Link("Get Calibre", destination: URL(string: "https://calibre-ebook.com/download_osx")!)
            }
        case .failed(let msg):
            ContentUnavailableView {
                Label("Couldn't read the e-reader", systemImage: "exclamationmark.triangle")
            } description: {
                Text(msg)
            } actions: {
                Button("Try Again") { Task { await devices.scan() } }
            }
        case .ejected(let name):
            ContentUnavailableView {
                Label { Text(name) } icon: { DeviceImage(kind: devices.busyKind).frame(width: 60, height: 80) }
            } description: {
                Text("It's safe to unplug your \(name).")
            } actions: {
                Button("Connect Again") {
                    devices.state = .none
                    Task { await devices.scan() }
                }
            }
        default:
            ContentUnavailableView {
                Label { Text("No e-reader connected") } icon: { DeviceImage(kind: "generic").frame(width: 60, height: 80).opacity(0.5) }
            } description: {
                Text("Connect your Kindle, Kobo, PocketBook or other e-reader to this Mac with a USB data cable and unlock it. It shows up here by itself.")
            } actions: {
                Button("Check Again") { Task { await devices.scan() } }
                    .disabled(devices.scanning)
            }
        }
    }
}

/// A small coloured label saying where a book is.
private struct StatusTag: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.caption.weight(.medium))
            .foregroundStyle(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .background(color.opacity(0.12), in: Capsule())
    }
}
