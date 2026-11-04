import Foundation
import AppKit

/// Kindle email delivery, and the Calibre install used (when present) to convert books to and from Kindle formats.
enum Kindle {
    static let calibreBin = "/Applications/calibre.app/Contents/MacOS"
    static let calibreBundleID = "net.kovidgoyal.calibre"

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

    /// Calibre is only needed to convert books to and from Kindle formats. BOOKFORGE_NO_CALIBRE hides it (testing).
    static var calibreInstalled: Bool {
        ProcessInfo.processInfo.environment["BOOKFORGE_NO_CALIBRE"] == nil
            && FileManager.default.isExecutableFile(atPath: "\(calibreBin)/ebook-convert")
    }

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
}
