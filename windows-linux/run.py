"""Starts Book Forge. Run it with Calibre's Python:  calibre-debug -e run.py
Command-line modes (no window):  --import-new, --send-new, --device, --convert in.pdf out.epub [id|en|ja]"""
import os, sys

here = os.path.dirname(os.path.abspath(__file__))
if here not in sys.path:
    sys.path.insert(0, here)


def detach_on_windows(args):
    """calibre-debug is a console program. On Windows the shortcut starts it minimised, and it starts the app
    again as a process with no console window at all, then leaves. Returns True if it did."""
    if sys.platform != 'win32' or '--child' in args or '--console' in args:
        return False
    import subprocess
    from bookforge.util import calibre_exe
    flags = 0x08000000 | 0x00000200     # CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([calibre_exe('calibre-debug'), '-e', os.path.abspath(__file__), '--', '--child'] + args,
                     creationflags=flags, close_fds=True)
    return True


def log_to_file():
    """Without a console there's nowhere for errors to go; keep them in a log file instead."""
    if sys.stdout is None or sys.stderr is None or '--child' in sys.argv:
        from bookforge.util import cache_dir
        f = open(os.path.join(cache_dir(), 'book-forge.log'), 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = f


def cli(args):
    """Runs a command-line mode; returns an exit code, or None to start the window."""
    from qt.core import QCoreApplication
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
        from bookforge import devices
        r = devices.script('info')
        print('status', r.get('status'), r.get('error') or '')
        if r.get('status') == 'ok':
            d = devices.reader_from(r)
            print('%s [%s, %s] free %s of %s' % (d['name'], d['kind'], d['driver'], d['free'], d['total']))
            for b in d['books']:
                print('  %s — %s (%s) uuid %s' % (b['title'], ', '.join(b['authors']), b['lpath'], b['uuid']))
        return 0
    for flag in ('--import-new', '--send-new'):
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
            titles = [devices.display_title(b) for b in D.to_import()] if flag == '--import-new' else [b['title'] for b in D.to_send()]
            print('To import:' if flag == '--import-new' else 'To send:', ' | '.join(titles) or 'nothing')
            (D.import_new if flag == '--import-new' else D.send_new)()
            print(D.message or '')
            return 0
    return None


def main():
    args = sys.argv[1:]
    code = cli(args)
    if code is not None:
        return code
    if detach_on_windows(args):
        return 0
    log_to_file()
    from qt.core import QApplication, QIcon, Qt
    from qt.webengine import QWebEngineView  # noqa: must be imported before the application starts
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication([sys.argv[0]])
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
