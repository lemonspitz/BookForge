"""Starts Book Forge:  python run.py  (the packaged app runs this too).
Command-line modes (no window):  --import-new, --send-new, --device, --convert in.pdf out.epub [id|en|ja]"""
import os, sys

here = os.path.dirname(os.path.abspath(__file__))
if here not in sys.path:
    sys.path.insert(0, here)


def log_to_file():
    """Without a console there's nowhere for errors to go; keep them in a log file instead."""
    if sys.stdout is None or sys.stderr is None:
        from bookforge.util import cache_dir
        f = open(os.path.join(cache_dir(), 'book-forge.log'), 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = f


def cli(args):
    """Runs a command-line mode; returns an exit code, or None to start the window."""
    from bookforge.qt import QCoreApplication
    if '--convert' in args:
        i = args.index('--convert')
        src, out = args[i + 1], args[i + 2]
        lang = args[i + 3] if len(args) > i + 3 else 'id'
        app = QCoreApplication(sys.argv[:1])
        from bookforge import epubwriter, layout, library, pdfread
        title, author, _, _ = library.read_meta(src)
        pages = pdfread.read(src, lang, False, lambda d, n: print('page %d of %d' % (d, n), end='\r'))
        blocks, start, end = layout.build(pages, src, lang)
        blocks = layout.TextFixer(blocks).finalize(blocks)
        epubwriter.write(blocks, end, src, title, author, lang, out)
        heads = [b[1] for b in blocks if b[0] == 'heading']
        print('\nbody pages %d-%d, %d chapters: %s' % (start + 1, end + 1, len(heads), ' | '.join(heads[:12])))
        return 0
    if '--device' in args:
        app = QCoreApplication(sys.argv[:1])
        from bookforge import devices, library
        library.shared()
        D = devices.shared()
        D._rescan_blocking()
        print('state', D.state, D.state_name)
        d = D.reader
        if d:
            print('%s [%s, %s] formats %s free %s of %s' % (d['name'], d['kind'], d['driver'], ','.join(d['formats']),
                                                           d['free'], d['total']))
            for b in d['books']:
                print('  %s — %s (%s) uuid %s' % (b['title'], ', '.join(b['authors']), b['lpath'], b['uuid']))
        return 0
    for flag in ('--import-new', '--send-new', '--plan'):
        if flag in args:
            app = QCoreApplication(sys.argv[:1])
            from bookforge import devices, library
            L, D = library.shared(), devices.shared()
            L.rescan()
            D._rescan_blocking()
            if not D.reader:
                print('No e-reader: %s %s' % (D.state, D.state_name))
                return 1
            both = [b for b in L.books if D.match(b)]
            print('%s: %d in both, %d only in the library, %d only on the reader'
                  % (D.reader['name'], len(both), len(L.books) - len(both), len(D.only_on_reader())))
            if flag == '--plan':
                # what Import and Send would copy, without copying anything
                print('To import:', ' | '.join(devices.display_title(b) for b in D.to_import()) or 'nothing')
                print('To send:', ' | '.join(b['title'] for b in D.to_send()) or 'nothing')
                print('Left on the reader (store books):', sum(1 for b in D.only_on_reader() if D.likely_locked(b)))
                return 0
            titles = [devices.display_title(b) for b in D.to_import()] if flag == '--import-new' else [b['title'] for b in D.to_send()]
            print('To import:' if flag == '--import-new' else 'To send:', ' | '.join(titles) or 'nothing')
            (D.import_new if flag == '--import-new' else D.send_new)()
            print(D.message or '')
            return 0
    return None


def main():
    args = sys.argv[1:]
    log_to_file()       # the packaged Windows app has no console: its output goes to the log
    code = cli(args)
    if code is not None:
        return code
    from bookforge.qt import QApplication, QIcon, Qt, QWebEngineView  # noqa: web engine before the application starts
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication([sys.argv[0]])
    if sys.platform == 'darwin':
        app.setStyle('Fusion')      # the Mac style doesn't draw ticks in lists
    app.setApplicationName('Book Forge')
    app.setOrganizationName('Book Forge')
    app.setDesktopFileName('book-forge')
    icon = os.path.join(here, 'bookforge', 'icon.png')
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    from bookforge import devices, library
    from bookforge.ui_main import MainWindow
    library.shared().rescan()
    w = MainWindow()
    w.show()
    devices.shared().start()
    for a in args:
        if a.startswith('--'):
            continue
        if a.lower().endswith(('.pdf', '.epub')) and os.path.exists(a):
            w.forge.load(os.path.abspath(a))
            w.side.setCurrentItem(w.forge_item)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
