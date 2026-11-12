import Foundation

// A copy of Sources/EPUBWriter.swift's Shell, so the checker builds without the rest of the app.
enum Shell {
    struct Failure: LocalizedError {
        var message: String
        var errorDescription: String? { message }
    }

    @discardableResult
    static func run(_ exe: String, _ args: [String], cwd: URL? = nil) throws -> String {
        String(decoding: try data(exe, args, cwd: cwd), as: UTF8.self)
    }

    /// Like `run`, but returns raw output. Pass `errors: false` for binary data (such as `unzip -p` of an image)
    /// so warnings on stderr don't end up in it.
    static func data(_ exe: String, _ args: [String], cwd: URL? = nil, errors: Bool = true) throws -> Data {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        if let cwd { p.currentDirectoryURL = cwd }
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = errors ? pipe : FileHandle.nullDevice
        try p.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        if p.terminationStatus != 0 {
            throw Failure(message: "\((exe as NSString).lastPathComponent) failed: \(String(decoding: data, as: UTF8.self).suffix(400))")
        }
        return data
    }
}
