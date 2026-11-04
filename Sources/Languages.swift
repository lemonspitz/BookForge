import Foundation

/// The languages Book Forge reads and translates, from Resources/languages.json
/// (shared with the Windows and Linux app).
struct BookLanguage: Decodable {
    struct Labels: Decodable { let cover, backCover, contents, opening, page: String }
    let code: String            // BCP-47, as written into the EPUB
    let name: String            // shown in menus
    let prose: String           // in the translator's instructions ("Simplified Chinese")
    let native: String          // after a translated book's title
    let vision: [String]        // text recognition languages
    let labels: Labels
    let charsPerToken: Double
    let weight: Double          // tokens for the same text relative to English
    let names: String           // how the style sheet should write names, if it matters
    let reading: String         // notes on translating from this language
    let writing: String         // notes on translating into it
}

enum Languages {
    static let all: [BookLanguage] = {
        struct File: Decodable { let languages: [BookLanguage] }
        if let url = Bundle.main.url(forResource: "languages", withExtension: "json"),
           let data = try? Data(contentsOf: url),
           let file = try? JSONDecoder().decode(File.self, from: data), !file.languages.isEmpty {
            return file.languages
        }
        return [BookLanguage(code: "en", name: "English", prose: "English", native: "English", vision: ["en-US"],
                             labels: .init(cover: "Cover", backCover: "Back Cover", contents: "Contents", opening: "Opening", page: "Page %d"),
                             charsPerToken: 3.6, weight: 1, names: "", reading: "", writing: "")]
    }()
    static let codes = all.map(\.code)

    static subscript(_ code: String) -> BookLanguage? { all.first { $0.code == code } }
    static func name(_ code: String) -> String { self[code]?.name ?? code }
    static func prose(_ code: String) -> String { self[code]?.prose ?? code }
    /// A language Book Forge knows, or English.
    static func known(_ code: String) -> String { codes.contains(code) ? code : "en" }

    /// "ind", "zh-TW", "zh_CN", "ko-KR"… → the code Book Forge uses.
    static func normalize(_ code: String) -> String {
        let parts = code.lowercased().split(whereSeparator: { $0 == "-" || $0 == "_" }).map(String.init)
        guard let first = parts.first else { return "" }
        let base = ["in": "id", "ind": "id", "eng": "en", "jp": "ja", "jpn": "ja", "kor": "ko", "vie": "vi",
                    "tha": "th", "chi": "zh", "zho": "zh", "cmn": "zh"][first] ?? first
        if base == "zh" {
            return Set(parts.dropFirst()).isDisjoint(with: ["hant", "tw", "hk", "mo"]) ? "zh-Hans" : "zh-Hant"
        }
        return String(base.prefix(2))
    }
}
