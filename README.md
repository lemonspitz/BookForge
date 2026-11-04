# Book Forge

A macOS/Windows/Linux app for your e-book library: it reads EPUBs and PDFs, turns PDF books into clean EPUBs, translates them between Indonesian, English and Japanese, and syncs them to a Kindle or other e-reader with covers showing.

## What it does

- **Library.** Keeps your EPUBs and PDFs in one place (`~/Documents/Book Forge Library`) with their covers, reading progress, search and sorting. Drag in files or whole folders; books you make or translate are added automatically.
- **Built-in reader.** Double-click a book to read it. EPUBs turn pages like an e-reader: one or two pages, arrow keys, clicks, trackpad swipes, contents, text size, fonts and light, sepia or dark themes. Vertical Japanese books read right to left. PDFs open in Apple's PDF viewer. Book Forge remembers where you stopped.
- **Devices.** When a Kindle, Kobo, PocketBook, Boox or other e-reader is plugged in by USB, it appears in the sidebar with its name, a drawing of the device, free space and the books on it. Tick the library books you want on it and press **Sync**: missing ones are copied (converted to Kindle format for Kindles, with covers in the Kindle library) and unticked ones are removed. Books bought from the store stay unless you untick them.
- **PDF → EPUB.** Uses the PDF's text layer when it has one, otherwise reads scanned pages with Apple's on-device text recognition (Indonesian, English, Japanese). It rebuilds chapters, paragraphs, scene breaks and drop caps, removes running heads and page numbers, and keeps the cover and any image pages.
- **Translate an EPUB.** Translates the whole book while keeping italics, bold, links, images and the table of contents.
  - **Claude** (default, needs an Anthropic API key): reads passages from across the book first and writes a style sheet (the narrator's voice, how each character speaks, fixed spellings for names and titles), then translates with per-language-pair guidance on slang, honorifics, politeness and dialogue punctuation. The app shows a cost estimate and asks before sending anything (roughly $2–5 for a novel). Your key is kept in the macOS Keychain.
  - **Apple Translate**: free and offline, but literal.
- **Kindle by Wi-Fi.** Emails a book to your Kindle's `@kindle.com` address through the Mail app. Add your sending address to the approved list in your Amazon account first.

## Requirements

- macOS 26 on Apple silicon.
- For e-readers over USB: [Calibre](https://calibre-ebook.com) installed in `/Applications` (Book Forge uses its device drivers). Close Calibre's window while syncing, because it holds the connection.
- For Apple translation: the language pair downloaded under System Settings → General → Language & Region → Translation Languages.
- For Claude translation: an API key from [console.anthropic.com](https://console.anthropic.com). The book's text is sent to Anthropic for translation.

## Install

Download `Book-Forge.zip` from the [latest release](../../releases/latest), unzip it and move **Book Forge** to Applications.

The app is not notarized, so the first time macOS will refuse to open it. Right-click the app and choose **Open**, or go to System Settings → Privacy & Security and click **Open Anyway**. Alternatively, remove the quarantine flag:

```bash
xattr -dr com.apple.quarantine "/Applications/Book Forge.app"
```

## Build from source

No Xcode project is needed, only the command-line tools:

```bash
./build.sh            # builds build/Book Forge.app
./build.sh --install  # also copies it to /Applications
```

## Command-line modes

The app binary also runs without a window, which is handy for testing:

```bash
BookForge --convert book.pdf book.epub [id|en|ja]
BookForge --translate in.epub out.epub id en [apple|claude]   # key from ANTHROPIC_API_KEY or the Keychain
BookForge --send book.epub                                    # send to the connected e-reader
BookForge --device                                            # show what's on the connected e-reader
```

The binary is at `Book Forge.app/Contents/MacOS/BookForge`.

## Notes

Only convert and translate books you own, for your own reading.
