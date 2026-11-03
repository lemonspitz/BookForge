import Foundation
import AppKit

/// Sends an EPUB to a USB-connected Kindle through Calibre (macOS has no built-in MTP support).
enum Kindle {
    static let calibreBin = "/Applications/calibre.app/Contents/MacOS"
    static let calibreBundleID = "net.kovidgoyal.calibre"

    enum Outcome {
        case sent(path: String, thumbnail: Bool)
        case noDevice
        case busy          // something else (usually the Calibre app) holds the device
    }

    /// Wi-Fi delivery: emails the EPUB to the Kindle's @kindle.com address through the Mail app.
    /// Amazon builds the library cover from the EPUB's own cover image.
    @MainActor
    static func email(epub: URL, to kindleAddress: String, from sender: String) throws {
        let size = (try? FileManager.default.attributesOfItem(atPath: epub.path)[.size] as? Int) ?? 0
        if size > 50_000_000 {
            throw Shell.Failure(message: "The EPUB is over 50 MB, Amazon's email limit. Use the USB cable instead.")
        }
        func q(_ s: String) -> String { "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\"" }
        let senderLine = sender.isEmpty ? "" : "set sender of m to \(q(sender))"
        let script = """
            tell application "Mail"
                set m to make new outgoing message with properties {subject:\(q(epub.deletingPathExtension().lastPathComponent)), content:" ", visible:false}
                \(senderLine)
                tell m
                    make new to recipient at end of to recipients with properties {address:\(q(kindleAddress))}
                    make new attachment with properties {file name:(POSIX file \(q(epub.path)))} at after the last paragraph
                end tell
                delay 2
                send m
            end tell
            """
        var err: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&err)
        if let err {
            let msg = err[NSAppleScript.errorMessage] as? String ?? "unknown error"
            throw Shell.Failure(message: "Mail couldn't send it: \(msg). Make sure Mail has an account set up and Book Forge may control Mail (System Settings › Privacy & Security › Automation).")
        }
    }

    static var calibreInstalled: Bool { FileManager.default.isExecutableFile(atPath: "\(calibreBin)/ebook-convert") }

    static var calibreAppRunning: Bool {
        NSRunningApplication.runningApplications(withBundleIdentifier: calibreBundleID)
            .contains { !$0.isTerminated && $0.executableURL?.lastPathComponent == "calibre" }
    }

    /// Asks the Calibre app to quit politely; returns true once it has.
    static func quitCalibre() async -> Bool {
        for app in NSRunningApplication.runningApplications(withBundleIdentifier: calibreBundleID) { app.terminate() }
        for _ in 0..<15 {
            if !calibreAppRunning { return true }
            try? await Task.sleep(for: .seconds(1))
        }
        _ = try? Shell.run("\(calibreBin)/calibre-debug", ["-s"])
        return !calibreAppRunning
    }

    /// Converts to AZW3 with the original cover and uploads it. `log` gets progress lines.
    static func send(epub: URL, log: @escaping @Sendable (String) -> Void) throws -> Outcome {
        let fm = FileManager.default
        let work = fm.temporaryDirectory.appendingPathComponent("bookforge-kindle-\(UUID().uuidString)")
        try fm.createDirectory(at: work, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: work) }

        let cover = work.appendingPathComponent("cover.jpg")
        _ = try? Shell.run("\(calibreBin)/ebook-meta", [epub.path, "--get-cover=\(cover.path)"])
        let azw3 = work.appendingPathComponent(epub.deletingPathExtension().lastPathComponent + ".azw3")
        log("Converting to Kindle format…")
        var args = [epub.path, azw3.path, "--output-profile", "kindle_pw3"]
        if fm.fileExists(atPath: cover.path) { args += ["--cover", cover.path] }
        try Shell.run("\(calibreBin)/ebook-convert", args)

        guard let script = Bundle.main.url(forResource: "kindle_upload", withExtension: "py") else {
            throw Shell.Failure(message: "kindle_upload.py is missing from the app bundle")
        }
        log("Uploading to Kindle…")
        let out = try Shell.run("\(calibreBin)/calibre-debug", ["-e", script.path, azw3.path])
        guard let line = out.split(separator: "\n").last(where: { $0.hasPrefix("RESULT ") }),
              let obj = try? JSONSerialization.jsonObject(with: Data(line.dropFirst(7).utf8)) as? [String: Any],
              let status = obj["status"] as? String else {
            throw Shell.Failure(message: "Upload gave no result:\n\(out.suffix(400))")
        }
        switch status {
        case "uploaded": return .sent(path: obj["path"] as? String ?? "", thumbnail: obj["thumbnail"] as? Bool ?? false)
        case "no_device": return .noDevice
        default: return .busy
        }
    }
}
