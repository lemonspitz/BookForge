"""Paths, settings, external tools and the stored API key, for Windows, Linux (and macOS, for testing)."""
import json, os, shutil, subprocess, sys, threading

from calibre.constants import iswindows, ismacos

APP = 'Book Forge'


class Failure(Exception):
    """An error meant to be shown to the user as it is."""


def config_dir():
    if iswindows:
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        d = os.path.join(base, APP)
    elif ismacos:
        d = os.path.expanduser('~/Library/Application Support/Book Forge (Qt)')
    else:
        d = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'book-forge')
    os.makedirs(d, exist_ok=True)
    return d


def cache_dir():
    if iswindows:
        base = os.environ.get('LOCALAPPDATA') or config_dir()
        d = os.path.join(base, APP, 'Cache')
    elif ismacos:
        d = os.path.expanduser('~/Library/Caches/Book Forge (Qt)')
    else:
        d = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'book-forge')
    os.makedirs(d, exist_ok=True)
    return d


def documents_dir():
    from qt.core import QStandardPaths
    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return d or os.path.expanduser('~')


class Settings:
    """Small preferences kept in settings.json (never secrets)."""

    def __init__(self):
        self.path = os.path.join(config_dir(), 'settings.json')
        try:
            with open(self.path, encoding='utf-8') as f:
                self.data = json.load(f)
        except Exception:
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        if self.data.get(key) == value:
            return
        self.data[key] = value
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)


settings = Settings()


# MARK: - External programs

def calibre_exe(name):
    """A Calibre command-line tool (calibre-debug, ebook-convert, ebook-meta)."""
    from calibre.utils.ipc.launch import exe_path
    p = exe_path(name)
    if isinstance(p, (list, tuple)):
        p = p[0]
    if os.path.isabs(p) and os.path.exists(p):
        return p
    if iswindows:
        for base in (os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramFiles(x86)', '')):
            cand = os.path.join(base, 'Calibre2', name + '.exe')
            if base and os.path.exists(cand):
                return cand
    return shutil.which(name) or p


def pdf_tools():
    """(pdfinfo, pdftoppm, pdftohtml) from Calibre's bundle."""
    from calibre.ebooks.metadata.pdf import get_tools
    from calibre.ebooks.pdf.pdftohtml import PDFTOHTML
    pdfinfo, pdftoppm = get_tools()
    return pdfinfo, pdftoppm, PDFTOHTML


def tesseract():
    """The Tesseract OCR program, or None. BOOKFORGE_TESSERACT points at another one (for testing)."""
    p = os.environ.get('BOOKFORGE_TESSERACT') or shutil.which('tesseract')
    if p:
        return p
    if iswindows:
        for base in (os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramFiles(x86)', ''),
                     os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs')):
            cand = os.path.join(base, 'Tesseract-OCR', 'tesseract.exe')
            if base and os.path.exists(cand):
                return cand
    return None


def no_window():
    """Keeps Windows from flashing a console window for each helper program."""
    if iswindows:
        return {'creationflags': 0x08000000}   # CREATE_NO_WINDOW
    return {}


def run(args, cwd=None, binary=False, input=None, errors=True):
    """Runs a program and returns its output; raises Failure with the end of its output if it fails."""
    p = subprocess.run(args, cwd=cwd, input=input, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT if errors else subprocess.DEVNULL, **no_window())
    if p.returncode != 0:
        tail = p.stdout.decode('utf-8', 'replace')[-400:]
        raise Failure('%s failed: %s' % (os.path.basename(args[0]), tail))
    return p.stdout if binary else p.stdout.decode('utf-8', 'replace')


def reveal(path):
    """Shows a file in Explorer / the file manager."""
    if iswindows:
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
    elif ismacos:
        subprocess.Popen(['open', '-R', path])
    else:
        from qt.core import QDesktopServices, QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


# MARK: - Calibre's own window holds the e-reader connection

def calibre_app_running():
    try:
        if iswindows:
            out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq calibre.exe', '/NH'], capture_output=True,
                                 text=True, **no_window()).stdout
            return 'calibre.exe' in out.lower()
        if ismacos:
            return subprocess.run(['pgrep', '-f', 'calibre.app/Contents/MacOS/calibre$'], capture_output=True).returncode == 0
        return subprocess.run(['pgrep', '-x', 'calibre'], capture_output=True).returncode == 0
    except Exception:
        return False


def quit_calibre():
    """Asks the Calibre app to shut down; returns True once it has."""
    import time
    try:
        subprocess.run([calibre_exe('calibre-debug'), '-s'], capture_output=True, timeout=30, **no_window())
    except Exception:
        pass
    for _ in range(15):
        if not calibre_app_running():
            return True
        time.sleep(1)
    return not calibre_app_running()


# MARK: - The Claude API key (Windows Credential Manager, the Linux keyring, or the macOS Keychain)

SERVICE, ACCOUNT = 'com.widagdos.bookforge', 'anthropic-api-key'


def _win_cred():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
                    ('Comment', wintypes.LPWSTR), ('LastWritten', wintypes.FILETIME),
                    ('CredentialBlobSize', wintypes.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_char)),
                    ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                    ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]
    return ctypes, CREDENTIAL


def _key_file(account=ACCOUNT):
    return os.path.join(config_dir(), account)


def load_secret(account=ACCOUNT):
    target = SERVICE + '/' + account if account != ACCOUNT else SERVICE
    try:
        if iswindows:
            ctypes, CREDENTIAL = _win_cred()
            p = ctypes.POINTER(CREDENTIAL)()
            if not ctypes.windll.advapi32.CredReadW(target, 1, 0, ctypes.byref(p)):
                return ''
            c = p.contents
            key = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode('utf-8')
            ctypes.windll.advapi32.CredFree(p)
            return key
        if ismacos:
            r = subprocess.run(['security', 'find-generic-password', '-s', SERVICE, '-a', account, '-w'],
                               capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else ''
        if shutil.which('secret-tool'):
            r = subprocess.run(['secret-tool', 'lookup', 'service', SERVICE, 'account', account],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return r.stdout.strip()
        with open(_key_file(account), encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''


def save_secret(key, account=ACCOUNT):
    """Returns where the secret was put, for the settings screen."""
    key = key.strip()
    target = SERVICE + '/' + account if account != ACCOUNT else SERVICE
    if iswindows:
        ctypes, CREDENTIAL = _win_cred()
        if not key:
            ctypes.windll.advapi32.CredDeleteW(target, 1, 0)
            return 'Windows Credential Manager'
        blob = key.encode('utf-8')
        c = CREDENTIAL()
        c.Type = 1      # CRED_TYPE_GENERIC
        c.TargetName = target
        c.UserName = account
        c.CredentialBlobSize = len(blob)
        c.CredentialBlob = ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.POINTER(ctypes.c_char))
        c.Persist = 2   # CRED_PERSIST_LOCAL_MACHINE
        ctypes.windll.advapi32.CredWriteW(ctypes.byref(c), 0)
        return 'Windows Credential Manager'
    if ismacos:
        subprocess.run(['security', 'delete-generic-password', '-s', SERVICE, '-a', account], capture_output=True)
        if key:
            subprocess.run(['security', 'add-generic-password', '-s', SERVICE, '-a', account, '-w', key], capture_output=True)
        return 'Keychain'
    if shutil.which('secret-tool'):
        if key:
            r = subprocess.run(['secret-tool', 'store', '--label=Book Forge ' + account, 'service', SERVICE,
                                'account', account], input=key, text=True, capture_output=True)
        else:
            r = subprocess.run(['secret-tool', 'clear', 'service', SERVICE, 'account', account], capture_output=True)
        if r.returncode == 0:
            try:
                os.remove(_key_file(account))
            except OSError:
                pass
            return 'keyring'
    # no keyring service: a file only this user can read
    if not key:
        try:
            os.remove(_key_file(account))
        except OSError:
            pass
        return 'settings folder'
    fd = os.open(_key_file(account), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(key)
    return 'settings folder'


def load_api_key():
    return load_secret()


def save_api_key(key):
    return save_secret(key)


# MARK: - Running work off the GUI thread

def in_background(fn, done=None, failed=None):
    """Runs fn() on a thread; done(result) or failed(exception) run back on the GUI thread."""
    from qt.core import QObject, pyqtSignal

    class Relay(QObject):
        ok = pyqtSignal(object)
        bad = pyqtSignal(object)

    relay = Relay()
    if done:
        relay.ok.connect(done)
    if failed:
        relay.bad.connect(failed)
    _keep.add(relay)

    def work():
        try:
            r = fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            relay.bad.emit(e)
        else:
            relay.ok.emit(r)
        finally:
            _keep.discard(relay)

    threading.Thread(target=work, daemon=True).start()


_keep = set()


class Poster:
    """Calls a function on the GUI thread from any thread (progress updates)."""

    def __init__(self, fn):
        from qt.core import QObject, pyqtSignal

        class Relay(QObject):
            sig = pyqtSignal(object)

        self.relay = Relay()
        self.relay.sig.connect(lambda args: fn(*args))

    def __call__(self, *args):
        self.relay.sig.emit(args)
