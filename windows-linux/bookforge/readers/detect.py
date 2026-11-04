"""Finding e-readers: drives with a reader's folders on them, MTP readers mounted by the Linux desktop, and MTP
readers reached directly (libmtp on Linux/macOS, Windows Portable Devices on Windows)."""
import glob, os, sys

from .folder import FolderStorage

iswindows, ismacos = sys.platform == 'win32', sys.platform == 'darwin'


def kind_of(*names):
    n = ' '.join(x for x in names if x).lower()
    if 'kindle' in n or 'amazon' in n:
        if 'scribe' in n:
            return 'kindle-scribe'
        if 'oasis' in n:
            return 'kindle-oasis'
        return 'kindle'
    for k, words in (('kobo', ('kobo', 'rakuten')), ('pocketbook', ('pocketbook',)), ('boox', ('boox', 'onyx')),
                     ('tolino', ('tolino',)), ('nook', ('nook', 'barnes'))):
        if any(w in n for w in words):
            return k
    return 'generic'


def kind_from(name, manufacturer, fallback):
    """The reader's own model name decides; libmtp's device table can be wrong (it calls a Paperwhite a Scribe)."""
    k = kind_of(name, manufacturer)
    return k if k != 'generic' else kind_of(fallback)


def clean_name(name):
    """'Kindle Paperwhite Signature Edition GN433W11534501' -> without the serial number."""
    import re
    return re.sub(r'\s+[A-Z0-9]{10,}$', '', name.strip()) or name


def _has(root, *parts):
    """Case-insensitive check that root/parts exists."""
    cur = root
    for p in parts:
        try:
            match = next((e for e in os.listdir(cur) if e.lower() == p.lower()), None)
        except OSError:
            return False
        if match is None:
            return False
        cur = os.path.join(cur, match)
    return True


def volume_kind(root):
    if _has(root, 'documents') and _has(root, 'system'):
        return 'kindle', 'Kindle'
    if _has(root, '.kobo'):
        return 'kobo', 'Kobo eReader'
    if _has(root, 'system', 'config') and _has(root, 'system', 'profiles'):
        return 'pocketbook', 'PocketBook'
    return None, None


def volumes():
    """Mounted drives that could be readers. BOOKFORGE_TEST_READER adds a folder that stands in for one (testing)."""
    if os.environ.get('BOOKFORGE_TEST_READER'):
        return [os.environ['BOOKFORGE_TEST_READER']]
    if iswindows:
        import ctypes, string
        bits = ctypes.windll.kernel32.GetLogicalDrives()
        out = []
        for i, letter in enumerate(string.ascii_uppercase):
            root = letter + ':\\'
            if bits >> i & 1 and ctypes.windll.kernel32.GetDriveTypeW(root) == 2:     # DRIVE_REMOVABLE
                out.append(root)
        return out
    if ismacos:
        return [v for v in glob.glob('/Volumes/*') if not os.path.islink(v)]
    user = os.environ.get('USER') or ''
    return glob.glob('/media/%s/*' % user) + glob.glob('/run/media/%s/*' % user) + glob.glob('/media/*/') + glob.glob('/mnt/*')


def gvfs_mtp():
    """MTP readers the Linux desktop (gvfs) has mounted: [(storage folder, mount, device name)]."""
    out = []
    for mount in glob.glob('/run/user/%d/gvfs/mtp:host=*' % os.getuid()) if hasattr(os, 'getuid') else []:
        name = mount.split('mtp:host=', 1)[1].replace('_', ' ')
        for storage in sorted(glob.glob(os.path.join(mount, '*'))):
            if os.path.isdir(storage):
                out.append((storage, mount, name))
    return out


def candidates():
    """Cheap list of what's attached, as (key, label) pairs; used to notice plugging and unplugging."""
    out = []
    for v in volumes():
        k, name = volume_kind(v)
        if k:
            out.append((('volume', v), name))
    gv = gvfs_mtp()
    for storage, mount, name in gv:
        if volume_kind(storage)[0] or kind_of(name) != 'generic':
            out.append((('gvfs', storage), name))
    if iswindows:
        try:
            from . import wpd
            out += [(('wpd', pnp), name) for pnp, name in wpd.detect()]
        except Exception:
            # Windows Portable Devices didn't work here: let Calibre's drivers look, if Calibre is installed
            from ..util import calibre_exe
            if calibre_exe('calibre-debug'):
                out.append((('calibre', 'mtp'), 'E-reader'))
    elif not gv:
        try:
            from . import libmtp
            out += [(('mtp', (bus, dev)), '%s %s' % (vendor, product)) for bus, dev, _, _, vendor, product in libmtp.detect()]
        except Exception:
            pass
    return out


class Busy(Exception):
    """Another program holds the reader."""


def open_reader(key, label):
    """Opens a candidate; returns a storage (FolderStorage, MTPStorage, WPDStorage). Raises Busy if it's held."""
    kind_, where = key
    if kind_ == 'volume':
        k, name = volume_kind(where)
        return FolderStorage(where, name, k)
    if kind_ == 'gvfs':
        mount = where.rsplit('/', 1)[0]
        name = mount.split('mtp:host=', 1)[1].replace('_', ' ')
        return FolderStorage(where, label, kind_of(name) if kind_of(name) != 'generic' else (volume_kind(where)[0] or 'generic'), mount=mount)
    if kind_ == 'mtp':
        import time
        from . import libmtp
        dev = libmtp.open_device(where)
        for _ in range(3):
            # another program may be reading it for a moment
            if dev is not None:
                break
            time.sleep(1.5)
            dev = libmtp.open_device(where)
        if dev is None:
            raise Busy(label)
        name = clean_name(dev.model or dev.friendly or label)
        storages = dev.storages()
        if not storages:
            raise Busy(name)
        # the storage with the books: internal storage first
        st = next((s for s in storages if 'internal' in s[1].lower()), storages[0])
        return libmtp.MTPStorage(dev, st, name, kind_from(name, dev.manufacturer, label))
    if kind_ == 'wpd':
        from . import wpd
        try:
            return wpd.open_storage(where, label)
        except Busy:
            raise
        except Exception:
            from ..util import calibre_exe
            if not calibre_exe('calibre-debug'):
                raise
            kind_ = 'calibre'
    if kind_ == 'calibre':
        from . import calibre_bridge
        r = calibre_bridge.open_reader()
        if r is None:
            raise Busy(label)
        return r
    raise ValueError(kind_)
