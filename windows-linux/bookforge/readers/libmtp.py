"""MTP readers (Kindles since 2021, Boox, some Kobos) through libmtp, on Linux and macOS.

The device stays open for as long as Book Forge runs. Releasing an MTP connection makes libmtp reset the USB
port, and some readers (Kindles among them) then drop off the computer until they're replugged, so it's
only released when the user ejects the reader."""
import ctypes, ctypes.util, os, sys, tempfile, threading

from .meta import skip_folder

ROOT = 0xFFFFFFFF          # LIBMTP_FILES_AND_FOLDERS_ROOT
FILETYPE_FOLDER, FILETYPE_JPEG, FILETYPE_UNKNOWN = 0, 14, 44


class DeviceEntry(ctypes.Structure):
    _fields_ = [('vendor', ctypes.c_char_p), ('vendor_id', ctypes.c_uint16), ('product', ctypes.c_char_p),
                ('product_id', ctypes.c_uint16), ('device_flags', ctypes.c_uint32)]


class RawDevice(ctypes.Structure):
    _fields_ = [('device_entry', DeviceEntry), ('bus_location', ctypes.c_uint32), ('devnum', ctypes.c_uint8)]


class Storage(ctypes.Structure):
    pass


Storage._fields_ = [('id', ctypes.c_uint32), ('StorageType', ctypes.c_uint16), ('FilesystemType', ctypes.c_uint16),
                    ('AccessCapability', ctypes.c_uint16), ('MaxCapacity', ctypes.c_uint64),
                    ('FreeSpaceInBytes', ctypes.c_uint64), ('FreeSpaceInObjects', ctypes.c_uint64),
                    ('StorageDescription', ctypes.c_char_p), ('VolumeIdentifier', ctypes.c_char_p),
                    ('next', ctypes.POINTER(Storage)), ('prev', ctypes.POINTER(Storage))]


class Device(ctypes.Structure):
    # only the leading fields Book Forge reads
    _fields_ = [('object_bitsize', ctypes.c_uint8), ('params', ctypes.c_void_p), ('usbinfo', ctypes.c_void_p),
                ('storage', ctypes.POINTER(Storage))]


class File(ctypes.Structure):
    pass


File._fields_ = [('item_id', ctypes.c_uint32), ('parent_id', ctypes.c_uint32), ('storage_id', ctypes.c_uint32),
                 ('filename', ctypes.c_void_p), ('filesize', ctypes.c_uint64), ('modificationdate', ctypes.c_int64),
                 ('filetype', ctypes.c_int), ('next', ctypes.POINTER(File))]

_lib = None
_libc = None
LOCK = threading.RLock()     # libmtp isn't thread-safe


def library_path():
    """libmtp: BOOKFORGE_LIBMTP, one bundled next to Book Forge, or the system's."""
    if os.environ.get('BOOKFORGE_LIBMTP'):
        return os.environ['BOOKFORGE_LIBMTP']
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for name in ('libmtp.9.dylib', 'libmtp.so.9'):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return ctypes.util.find_library('mtp') or ('libmtp.so.9' if sys.platform.startswith('linux') else None)


def lib():
    global _lib, _libc
    if _lib is None:
        path = library_path()
        if not path:
            raise OSError('libmtp is not installed')
        L = ctypes.CDLL(path)
        L.LIBMTP_Init.restype = None
        L.LIBMTP_Detect_Raw_Devices.argtypes = [ctypes.POINTER(ctypes.POINTER(RawDevice)), ctypes.POINTER(ctypes.c_int)]
        L.LIBMTP_Open_Raw_Device_Uncached.argtypes = [ctypes.POINTER(RawDevice)]
        L.LIBMTP_Open_Raw_Device_Uncached.restype = ctypes.POINTER(Device)
        for f in ('LIBMTP_Get_Modelname', 'LIBMTP_Get_Friendlyname', 'LIBMTP_Get_Manufacturername', 'LIBMTP_Get_Serialnumber'):
            getattr(L, f).argtypes = [ctypes.POINTER(Device)]
            getattr(L, f).restype = ctypes.c_void_p
        L.LIBMTP_Get_Storage.argtypes = [ctypes.POINTER(Device), ctypes.c_int]
        L.LIBMTP_Get_Files_And_Folders.argtypes = [ctypes.POINTER(Device), ctypes.c_uint32, ctypes.c_uint32]
        L.LIBMTP_Get_Files_And_Folders.restype = ctypes.POINTER(File)
        L.LIBMTP_Get_File_To_File.argtypes = [ctypes.POINTER(Device), ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]
        L.LIBMTP_Send_File_From_File.argtypes = [ctypes.POINTER(Device), ctypes.c_char_p, ctypes.POINTER(File), ctypes.c_void_p, ctypes.c_void_p]
        L.LIBMTP_new_file_t.restype = ctypes.POINTER(File)
        L.LIBMTP_destroy_file_t.argtypes = [ctypes.POINTER(File)]
        L.LIBMTP_Delete_Object.argtypes = [ctypes.POINTER(Device), ctypes.c_uint32]
        L.LIBMTP_Create_Folder.argtypes = [ctypes.POINTER(Device), ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32]
        L.LIBMTP_Create_Folder.restype = ctypes.c_uint32
        L.LIBMTP_GetPartialObject.argtypes = [ctypes.POINTER(Device), ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32,
                                              ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)), ctypes.POINTER(ctypes.c_uint)]
        L.LIBMTP_Release_Device.argtypes = [ctypes.POINTER(Device)]
        L.LIBMTP_Clear_Errorstack.argtypes = [ctypes.POINTER(Device)]
        L.LIBMTP_Init()
        _lib = L
        _libc = ctypes.CDLL(ctypes.util.find_library('c'))
        _libc.free.argtypes = [ctypes.c_void_p]
        _libc.strdup.argtypes = [ctypes.c_char_p]
        _libc.strdup.restype = ctypes.c_void_p
    return _lib


def _str(ptr, free=True):
    if not ptr:
        return ''
    s = ctypes.string_at(ptr).decode('utf-8', 'replace')
    if free:
        _libc.free(ptr)
    return s


def detect():
    """[(bus_location, devnum, vendor_id, product_id, vendor name, product name)] of attached MTP devices."""
    L = lib()
    with LOCK:
        raw = ctypes.POINTER(RawDevice)()
        n = ctypes.c_int(0)
        if L.LIBMTP_Detect_Raw_Devices(ctypes.byref(raw), ctypes.byref(n)) != 0 or n.value == 0:
            return []
        out = []
        for i in range(n.value):
            r = raw[i]
            e = r.device_entry
            out.append((r.bus_location, r.devnum, e.vendor_id, e.product_id,
                        (e.vendor or b'').decode('utf-8', 'replace'), (e.product or b'').decode('utf-8', 'replace')))
        _libc.free(raw)
        return out


def open_device(where):
    """Opens the device at (bus_location, devnum). Returns an MTPDevice, or None if another program holds it."""
    L = lib()
    with LOCK:
        raw = ctypes.POINTER(RawDevice)()
        n = ctypes.c_int(0)
        if L.LIBMTP_Detect_Raw_Devices(ctypes.byref(raw), ctypes.byref(n)) != 0:
            return None
        try:
            for i in range(n.value):
                if (raw[i].bus_location, raw[i].devnum) == where:
                    dev = L.LIBMTP_Open_Raw_Device_Uncached(ctypes.byref(raw[i]))
                    if not dev:
                        return None
                    return MTPDevice(dev, where, raw[i].device_entry.vendor_id)
        finally:
            _libc.free(raw)
    return None


class MTPDevice:
    def __init__(self, dev, where, vendor_id):
        L = lib()
        self.dev, self.where, self.vendor_id = dev, where, vendor_id
        self.manufacturer = _str(L.LIBMTP_Get_Manufacturername(dev))
        self.model = _str(L.LIBMTP_Get_Modelname(dev)).strip()
        self.friendly = _str(L.LIBMTP_Get_Friendlyname(dev)).strip()
        self.serial = _str(L.LIBMTP_Get_Serialnumber(dev)).strip()
        self.alive = True

    def storages(self):
        """[(id, description, total, free)]"""
        with LOCK:
            lib().LIBMTP_Get_Storage(self.dev, 0)
            out = []
            s = self.dev.contents.storage
            while s:
                c = s.contents
                out.append((c.id, (c.StorageDescription or b'').decode('utf-8', 'replace'), c.MaxCapacity, c.FreeSpaceInBytes))
                nxt = ctypes.cast(c.next, ctypes.c_void_p).value
                s = ctypes.cast(nxt, ctypes.POINTER(Storage)) if nxt else None
            return out

    def listing(self, storage_id, parent):
        """[(id, name, size, is_folder)] in one folder."""
        L = lib()
        with LOCK:
            f = L.LIBMTP_Get_Files_And_Folders(self.dev, storage_id, parent)
            out = []
            while f:
                c = f.contents
                out.append((c.item_id, _str(c.filename, free=False), c.filesize, c.filetype == FILETYPE_FOLDER))
                # copy the link's address before freeing: ctypes reads pointer fields from the struct's memory
                nxt = ctypes.cast(c.next, ctypes.c_void_p).value
                L.LIBMTP_destroy_file_t(f)
                f = ctypes.cast(nxt, ctypes.POINTER(File)) if nxt else None
            L.LIBMTP_Clear_Errorstack(self.dev)
            return out

    def release(self):
        with LOCK:
            if self.alive:
                lib().LIBMTP_Release_Device(self.dev)
                self.alive = False


class MTPStorage:
    """One storage area (usually "Internal Storage") of an MTP reader, addressed by '/'-separated paths."""

    def __init__(self, device, storage, name, kind):
        self.device, self.name, self.kind = device, name, kind
        self.model = device.model
        self.sid, self.description, self._total, self._free = storage
        self._folders = {}       # lpath of a folder ('' = root) -> {name: (id, size, is_folder)}

    @property
    def space(self):
        for sid, _, total, free in self.device.storages():
            if sid == self.sid:
                return total, free
        return self._total, self._free

    def _folder_id(self, folder):
        if folder == '':
            return ROOT
        parent, _, name = folder.rpartition('/')
        e = self._entries(parent).get(name)
        return e[0] if e and e[2] else None

    def _entries(self, folder):
        if folder not in self._folders:
            fid = self._folder_id(folder)
            self._folders[folder] = {} if fid is None else {
                n: (i, s, d) for i, n, s, d in self.device.listing(self.sid, fid)}
        return self._folders[folder]

    def _find(self, lpath):
        folder, _, name = lpath.strip('/').rpartition('/')
        return self._entries(folder).get(name)

    def files(self):
        self._folders = {}
        out = []

        def walk(folder):
            for name, (i, size, is_dir) in list(self._entries(folder).items()):
                lp = (folder + '/' if folder else '') + name
                if is_dir:
                    if not skip_folder(lp):
                        walk(lp)
                else:
                    out.append((lp, size))
        walk('')
        return out

    def exists(self, lpath):
        return self._find(lpath) is not None

    def read_small(self, lpath):
        if not self._find(lpath):
            return None
        fd, tmp = tempfile.mkstemp()
        os.close(fd)
        try:
            self.get(lpath, tmp)
            with open(tmp, 'rb') as f:
                return f.read()
        except Exception:
            return None
        finally:
            os.remove(tmp)

    def write_small(self, lpath, data):
        fd, tmp = tempfile.mkstemp()
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        try:
            self.put(tmp, lpath)
        finally:
            os.remove(tmp)

    def head(self, lpath, n):
        e = self._find(lpath)
        if not e:
            raise OSError('not on the reader: ' + lpath)
        L = lib()
        with LOCK:
            data = ctypes.POINTER(ctypes.c_ubyte)()
            size = ctypes.c_uint(0)
            if L.LIBMTP_GetPartialObject(self.device.dev, e[0], 0, min(n, e[1] or n), ctypes.byref(data), ctypes.byref(size)) == 0 and data:
                out = ctypes.string_at(data, size.value)
                _libc.free(data)
                return out
            L.LIBMTP_Clear_Errorstack(self.device.dev)
        # the reader can't send part of a file: fetch it all
        fd, tmp = tempfile.mkstemp()
        os.close(fd)
        try:
            self.get(lpath, tmp)
            with open(tmp, 'rb') as f:
                return f.read(n)
        finally:
            os.remove(tmp)

    def get(self, lpath, local):
        e = self._find(lpath)
        if not e:
            raise OSError('not on the reader: ' + lpath)
        with LOCK:
            r = lib().LIBMTP_Get_File_To_File(self.device.dev, e[0], os.fsencode(local), None, None)
            lib().LIBMTP_Clear_Errorstack(self.device.dev)
        if r != 0:
            raise OSError("couldn't copy %s from the reader" % lpath)

    def _make_folder(self, folder):
        if folder == '':
            return ROOT
        fid = self._folder_id(folder)
        if fid is not None:
            return fid
        parent, _, name = folder.rpartition('/')
        pid = self._make_folder(parent)
        with LOCK:
            new = lib().LIBMTP_Create_Folder(self.device.dev, ctypes.create_string_buffer(name.encode('utf-8')),
                                             0 if pid == ROOT else pid, self.sid)
        if not new:
            raise OSError("couldn't make the folder %s on the reader" % folder)
        self._entries(parent)[name] = (new, 0, True)
        self._folders[folder] = {}
        return new

    def put(self, local, lpath):
        if self._find(lpath):
            self.remove(lpath)
        folder, _, name = lpath.strip('/').rpartition('/')
        pid = self._make_folder(folder)
        L = lib()
        with LOCK:
            f = L.LIBMTP_new_file_t()
            c = f.contents
            c.filename = _libc.strdup(name.encode('utf-8'))
            c.filesize = os.path.getsize(local)
            c.filetype = FILETYPE_JPEG if name.lower().endswith('.jpg') else FILETYPE_UNKNOWN
            c.parent_id = 0 if pid == ROOT else pid
            c.storage_id = self.sid
            r = L.LIBMTP_Send_File_From_File(self.device.dev, os.fsencode(local), f, None, None)
            new_id = c.item_id
            L.LIBMTP_destroy_file_t(f)
            L.LIBMTP_Clear_Errorstack(self.device.dev)
        if r != 0:
            raise OSError("couldn't copy %s to the reader" % name)
        self._entries(folder)[name] = (new_id, os.path.getsize(local), False)

    def remove(self, lpath):
        e = self._find(lpath)
        if not e:
            return
        if e[2]:
            folder = lpath.strip('/')
            for name in list(self._entries(folder)):
                self.remove(folder + '/' + name)
            self._folders.pop(folder, None)
        with LOCK:
            lib().LIBMTP_Delete_Object(self.device.dev, e[0])
            lib().LIBMTP_Clear_Errorstack(self.device.dev)
        folder, _, name = lpath.strip('/').rpartition('/')
        self._entries(folder).pop(name, None)

    def eject(self):
        self.device.release()

    def close(self):
        pass
