import Foundation

/// Literary translation through the Claude API (raw HTTPS; there is no official Swift SDK).
/// First it reads samples from across the book and writes a style sheet (voices, register,
/// fixed renderings for names and terms); every batch of paragraphs is then translated with
/// the language-pair guide plus that style sheet.
struct ClaudeTranslator {
    static let model = "claude-opus-5-5"
    // BOOKFORGE_API_URL points at a local mock server for testing
    static let endpoint = URL(string: ProcessInfo.processInfo.environment["BOOKFORGE_API_URL"] ?? "https://api.anthropic.com/v1/messages")!
    // per million tokens, claude-opus-5-5
    static let inputPrice = 4.0, outputPrice = 20.0, cacheReadPrice = 0.20

    let apiKey: String
    let from: String
    let to: String

    struct Usage {
        var input = 0, output = 0, cacheRead = 0, cacheWrite = 0
        var dollars: Double {
            (Double(input) * ClaudeTranslator.inputPrice + Double(output) * ClaudeTranslator.outputPrice
                + Double(cacheRead) * ClaudeTranslator.cacheReadPrice + Double(cacheWrite) * ClaudeTranslator.inputPrice * 1.25) / 1_000_000
        }
    }

    enum Failure: LocalizedError {
        case badKey, refused, truncated, http(Int, String), malformed(String)
        var errorDescription: String? {
            switch self {
            case .badKey: "The Claude API key was rejected. Check it in Book Forge."
            case .refused: "Claude declined this passage."
            case .truncated: "The reply was cut off."
            case .http(let code, let body): "Claude API error \(code): \(body.prefix(300))"
            case .malformed(let s): "Unexpected reply from Claude: \(s.prefix(200))"
            }
        }
    }

    /// Rough cost before starting: source text in, translation (plus some reasoning) out.
    static func estimate(sourceChars: Int, from: String, to: String) -> Double {
        let charsPerToken = ["ja": 1.3][from] ?? 3.6
        let srcTokens = Double(sourceChars) / charsPerToken
        let expansion = to == "ja" ? (from == "ja" ? 1.0 : 1.4) : (from == "ja" ? 0.9 : 1.1)
        let chunks = Double(sourceChars) / 3000 + 1
        let input = srcTokens * 1.25 + chunks * 600          // text + context + prompt per batch
        let cached = chunks * 3000                           // style guide + sheet, read from cache
        let output = srcTokens * expansion * 1.35             // translation + reasoning
        return (input * inputPrice + cached * cacheReadPrice + output * outputPrice) / 1_000_000
    }

    // MARK: style learning

    /// Asks Claude to study excerpts and return a style sheet for this book and language pair.
    func learnStyle(samples: [String], usage: inout Usage) async throws -> String {
        let schema: [String: Any] = [
            "type": "object", "additionalProperties": false,
            "required": ["narration", "dialogue", "characters", "glossary", "notes"],
            "properties": [
                "narration": ["type": "string", "description": "Narrative voice: person, tense, tone, sentence rhythm, and how to reproduce it in the target language."],
                "dialogue": ["type": "string", "description": "Overall dialogue register (slang, particles, politeness) and how to render it."],
                "characters": ["type": "array", "items": [
                    "type": "object", "additionalProperties": false, "required": ["name", "voice", "rendering"],
                    "properties": [
                        "name": ["type": "string"],
                        "voice": ["type": "string", "description": "How this character speaks in the source."],
                        "rendering": ["type": "string", "description": "How to voice them in the target language (pronouns, endings, register)."],
                    ]]],
                "glossary": ["type": "array", "items": [
                    "type": "object", "additionalProperties": false, "required": ["source", "target"],
                    "properties": ["source": ["type": "string"], "target": ["type": "string"]]]],
                "notes": ["type": "string", "description": "Anything else a translator must keep consistent."],
            ],
        ]
        let src = StyleGuides.languageNames[from] ?? from, dst = StyleGuides.languageNames[to] ?? to
        let system = StyleGuides.base(from: from, to: to) + "\n\n" + StyleGuides.pair(from: from, to: to)
        let prompt = """
            Below are excerpts from across a \(src) novel that is about to be translated into \(dst). \
            Before translating, study them and write a translation style sheet: the narrative voice; the dialogue register; \
            every recurring character's way of speaking and how to voice them in \(dst); and a glossary of names, titles, places \
            and recurring terms with the exact \(dst) rendering to use every time (for Japanese, katakana for names). Be specific and concise.

            \(samples.enumerated().map { "<excerpt \($0 + 1)>\n\($1)\n</excerpt>" }.joined(separator: "\n\n"))
            """
        let json = try await call(system: system, prompt: prompt, schema: schema, maxTokens: 16000, usage: &usage)
        guard let obj = json as? [String: Any] else { throw Failure.malformed("style sheet") }
        var sheet = "Style sheet learned from this book:\n"
        sheet += "Narration: \(obj["narration"] as? String ?? "")\n"
        sheet += "Dialogue: \(obj["dialogue"] as? String ?? "")\n"
        for c in obj["characters"] as? [[String: String]] ?? [] {
            sheet += "Character \(c["name"] ?? ""): \(c["voice"] ?? "") → \(c["rendering"] ?? "")\n"
        }
        let glossary = (obj["glossary"] as? [[String: String]] ?? []).map { "\($0["source"] ?? "") = \($0["target"] ?? "")" }
        if !glossary.isEmpty { sheet += "Glossary: " + glossary.joined(separator: "; ") + "\n" }
        sheet += "Notes: \(obj["notes"] as? String ?? "")"
        return sheet
    }

    // MARK: translation

    /// Translates `paragraphs` (inline <i>/<b>/<br/> allowed) in parallel batches.
    /// Paragraphs Claude can't return cleanly come back as nil so the caller can fall back.
    func translate(_ paragraphs: [String], styleSheet: String,
                   progress: @escaping @Sendable (Double, Usage) -> Void) async throws -> ([String?], Usage) {
        let system = StyleGuides.base(from: from, to: to) + "\n\n" + StyleGuides.pair(from: from, to: to) + "\n\n" + styleSheet

        // batches of ~3000 characters, never splitting a paragraph
        var batches: [Range<Int>] = []
        var start = 0, size = 0
        for (i, p) in paragraphs.enumerated() {
            if size > 0, size + p.count > 3000 || i - start >= 40 {
                batches.append(start..<i)
                start = i
                size = 0
            }
            size += p.count
        }
        if start < paragraphs.count { batches.append(start..<paragraphs.count) }

        var out = [String?](repeating: nil, count: paragraphs.count)
        var usage = Usage()
        var done = 0
        let total = paragraphs.count
        try await withThrowingTaskGroup(of: (Range<Int>, [String]?, Usage).self) { group in
            var next = 0
            func add() {
                let range = batches[next]
                next += 1
                // the source text just before this batch, so each batch can run independently
                let context = String(paragraphs[..<range.lowerBound].suffix(4).joined(separator: "\n").suffix(800))
                group.addTask {
                    var u = Usage()
                    let result = try await self.translateBatch(Array(paragraphs[range]), context: context, system: system, usage: &u)
                    return (range, result, u)
                }
            }
            for _ in 0..<min(4, batches.count) { add() }
            while let (range, result, u) = try await group.next() {
                if let result { for (k, t) in result.enumerated() { out[range.lowerBound + k] = t } }
                usage.input += u.input; usage.output += u.output
                usage.cacheRead += u.cacheRead; usage.cacheWrite += u.cacheWrite
                done += range.count
                progress(Double(done) / Double(max(1, total)), usage)
                if next < batches.count { add() }
            }
        }
        return (out, usage)
    }

    /// One batch; on a count mismatch, refusal or cut-off it retries the two halves separately.
    private func translateBatch(_ paras: [String], context: String, system: String, usage: inout Usage, depth: Int = 0) async throws -> [String]? {
        let schema: [String: Any] = [
            "type": "object", "additionalProperties": false, "required": ["paragraphs"],
            "properties": ["paragraphs": ["type": "array", "items": ["type": "string"],
                                          "description": "One translated paragraph per numbered input paragraph, same order, without the numbers."]],
        ]
        let numbered = paras.enumerated().map { "[\($0 + 1)] \($1)" }.joined(separator: "\n\n")
        let prompt = (context.isEmpty ? "" : "<context>\n\(context)\n</context>\n\n")
            + "Translate these \(paras.count) paragraphs:\n\n\(numbered)"
        do {
            let json = try await call(system: system, prompt: prompt, schema: schema,
                                      maxTokens: 16000, usage: &usage)
            if let list = (json as? [String: Any])?["paragraphs"] as? [String], list.count == paras.count {
                return list
            }
        } catch Failure.refused, Failure.truncated, Failure.malformed {
            // fall through to splitting
        }
        guard paras.count > 1, depth < 4 else { return nil }
        let mid = paras.count / 2
        let a = try await translateBatch(Array(paras[..<mid]), context: context, system: system, usage: &usage, depth: depth + 1)
        let b = try await translateBatch(Array(paras[mid...]), context: paras[..<mid].suffix(2).joined(separator: "\n"),
                                         system: system, usage: &usage, depth: depth + 1)
        if let a, let b { return a + b }
        // keep whatever half succeeded; the caller falls back for the rest
        return nil
    }

    // MARK: HTTP

    /// POST /v1/messages with a cached system prompt and JSON-schema output; retries rate limits and overloads.
    private func call(system: String, prompt: String, schema: [String: Any], maxTokens: Int, usage: inout Usage) async throws -> Any {
        let body: [String: Any] = [
            "model": Self.model,
            "max_tokens": maxTokens,
            // re-run on Anthropic's recommended model if a safety classifier declines (e.g. violent scenes)
            "fallbacks": "default",
            "output_config": ["effort": "medium", "format": ["type": "json_schema", "schema": schema]],
            "system": [["type": "text", "text": system, "cache_control": ["type": "ephemeral"]]],
            "messages": [["role": "user", "content": prompt]],
        ]
        var req = URLRequest(url: Self.endpoint)
        req.httpMethod = "POST"
        req.timeoutInterval = 600
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        req.setValue("2026-10-21", forHTTPHeaderField: "anthropic-version")
        req.setValue("server-side-fallback-2026-07-01", forHTTPHeaderField: "anthropic-beta")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        var attempt = 0
        while true {
            attempt += 1
            let (data, resp): (Data, URLResponse)
            do {
                (data, resp) = try await URLSession.shared.data(for: req)
            } catch {
                if attempt < 5 { try await Task.sleep(for: .seconds(Double(attempt) * 3)); continue }
                throw error
            }
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let text = String(decoding: data, as: UTF8.self)
            switch code {
            case 200: break
            case 401, 403: throw Failure.badKey
            case 408, 409, 429, 500...599:
                guard attempt < 6 else { throw Failure.http(code, text) }
                let wait = (resp as? HTTPURLResponse)?.value(forHTTPHeaderField: "retry-after").flatMap(Double.init)
                    ?? min(60, pow(2, Double(attempt)) * 2)
                try await Task.sleep(for: .seconds(wait))
                continue
            default: throw Failure.http(code, text)
            }
            guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw Failure.malformed(text) }
            if let u = obj["usage"] as? [String: Any] {
                usage.input += u["input_tokens"] as? Int ?? 0
                usage.output += u["output_tokens"] as? Int ?? 0
                usage.cacheRead += u["cache_read_input_tokens"] as? Int ?? 0
                usage.cacheWrite += u["cache_creation_input_tokens"] as? Int ?? 0
            }
            switch obj["stop_reason"] as? String {
            case "refusal": throw Failure.refused
            case "max_tokens": throw Failure.truncated
            default: break
            }
            // the answer is the last text block (fallback turns may add other blocks before it)
            let blocks = obj["content"] as? [[String: Any]] ?? []
            guard let json = blocks.last(where: { $0["type"] as? String == "text" })?["text"] as? String,
                  let parsed = try? JSONSerialization.jsonObject(with: Data(json.utf8)) else {
                throw Failure.malformed(text)
            }
            return parsed
        }
    }
}

/// The API key lives in the login Keychain, not in preferences.
enum KeychainKey {
    static let service = "com.widagdos.bookforge", account = "anthropic-api-key"

    static func load() -> String? {
        var item: CFTypeRef?
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account, kSecReturnData as String: true]
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let d = item as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }

    static func save(_ key: String) {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account]
        SecItemDelete(q as CFDictionary)
        guard !key.isEmpty else { return }
        var add = q
        add[kSecValueData as String] = Data(key.utf8)
        SecItemAdd(add as CFDictionary, nil)
    }
}
