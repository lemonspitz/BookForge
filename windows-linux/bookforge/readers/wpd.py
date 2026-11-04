"""MTP readers on Windows through Windows Portable Devices (WPD), the same service Explorer uses.
Every WPD call runs on one worker thread set up for COM."""
import ctypes, os, re, threading
from concurrent.futures import ThreadPoolExecutor
from ctypes import POINTER, c_int8, c_uint, c_uint16, c_uint32, c_ubyte, c_ulong, c_ushort, c_void_p, c_wchar_p, cast, pointer

from .meta import skip_folder

# USB vendor ids of e-reader makers (the device id contains "vid_xxxx")
READER_VENDORS = {'1949': 'Kindle', '2237': 'Kobo eReader', 'fdd': 'PocketBook', '2207': 'Onyx Boox'}

_pool = None
_api = _types = None


def _init():
    import sys
    # comtypes sets COM up for the thread that first imports it, in the mode named here (multithreaded)
    sys.coinit_flags = 0
    import comtypes
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass        # already set up on this thread


def call(fn, *a):
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(1, thread_name_prefix='wpd', initializer=_init)
    return _pool.submit(fn, *a).result()


def libs():
    global _api, _types
    if _api is None:
        import comtypes.client
        comtypes.client.GetModule('portabledeviceapi.dll')
        comtypes.client.GetModule('portabledevicetypes.dll')
        import comtypes.gen.PortableDeviceApiLib as api
        import comtypes.gen.PortableDeviceTypesLib as types
        _api, _types = api, types
    return _api, _types


def guid(text):
    import comtypes
    return comtypes.GUID(text)


def key(fmtid, pid):
    api, _ = libs()
    k = api._tagpropertykey()
    k.fmtid = guid(fmtid)
    k.pid = c_ulong(pid)
    return pointer(k)


OBJ = '{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}'
DEV = '{26D4979A-E643-4626-9E2B-736DC0C92FDC}'
STOR = '{01A3057A-74D6-4E80-BEA7-DC4C212CE50A}'
FOLDER = '{27E2E392-A111-48E0-AB0C-E17705A05F85}'
FUNCTIONAL = '{99ED0160-17FF-4C44-9D98-1D7A6F941921}'
GENERIC_FILE = '{0085E0A6-8D34-45D7-BC5C-447E59C73D48}'
FORMAT_UNSPECIFIED = '{30000000-AE6C-4804-98BA-C57B46965FE7}'
FORMAT_PROPERTIES_ONLY = '{30010000-AE6C-4804-98BA-C57B46965FE7}'
RESOURCE_DEFAULT = ('{E81E79BE-34F0-41BF-B53F-F1A06AE87842}', 0)


def K(name):
    table = {'parent': (OBJ, 3), 'name': (OBJ, 4), 'format': (OBJ, 6), 'content_type': (OBJ, 7), 'size': (OBJ, 11),
             'file_name': (OBJ, 12), 'manufacturer': (DEV, 7), 'model': (DEV, 8), 'friendly': (DEV, 12),
             'capacity': (STOR, 4), 'free': (STOR, 5), 'resource': RESOURCE_DEFAULT}
    return key(*table[name])


def _create(coclass, interface):
    import comtypes, comtypes.client
    return comtypes.client.CreateObject(coclass().IPersist_GetClassID(), clsctx=comtypes.CLSCTX_INPROC_SERVER,
                                        interface=interface)


def _devices():
    api, types = libs()
    mng = _create(api.PortableDeviceManager, api.IPortableDeviceManager)
    try:
        mng.RefreshDeviceList()
    except Exception:
        pass
    n = pointer(c_ulong())
    mng.GetDevices(POINTER(c_wchar_p)(), n)
    if not n.contents.value:
        return []
    ids = (c_wchar_p * n.contents.value)()
    mng.GetDevices(cast(ids, POINTER(c_wchar_p)), n)
    return [i for i in ids if i]


def detect():
    """[(device id, label)] of attached e-readers that use MTP (drives are found as drives)."""
    out = []
    for pnp in call(_devices):
        low = pnp.lower()
        if 'usbstor' in low or low.startswith('\\\\?\\storage') or 'wpdbusenum' in low:
            continue        # a USB drive: handled as a drive
        m = re.search(r'vid_([0-9a-f]{4})', low)
        vid = m.group(1).lstrip('0') if m else ''
        label = READER_VENDORS.get(vid) or ('Kindle' if 'kindle' in low else 'Kobo eReader' if 'kobo' in low else None)
        if label:
            out.append((pnp, label))
    return out


def open_storage(pnp, label):
    from .detect import Busy, clean_name, kind_from
    try:
        dev = call(WPDDevice, pnp)
    except Exception as e:
        raise Busy(label) from e
    name = clean_name(dev.model or dev.friendly or label)
    st = call(dev.storages)
    if not st:
        raise Busy(name)
    s = next((x for x in st if 'internal' in x[1].lower()), st[0])
    return WPDStorage(dev, s, name, kind_from(name, dev.manufacturer, label))


class WPDDevice:
    def __init__(self, pnp):
        api, types = libs()
        info = _create(types.PortableDeviceValues, types.IPortableDeviceValues)
        self.dev = _create(api.PortableDevice, api.IPortableDevice)
        self.dev.Open(pnp, info)
        self.content = self.dev.Content()
        self.props = self.content.Properties()
        self.transfer = self.content.Transfer()
        v = self.values('DEVICE', 'friendly', 'model', 'manufacturer')
        self.friendly, self.model, self.manufacturer = v['friendly'] or '', v['model'] or '', v['manufacturer'] or ''

    def values(self, oid, *names):
        api, types = libs()
        keys = _create(types.PortableDeviceKeyCollection, api.IPortableDeviceKeyCollection)
        for n in names:
            keys.Add(K(n))
        v = self.props.GetValues(oid, keys)
        out = {}
        for n in names:
            try:
                if n in ('content_type', 'format'):
                    out[n] = str(v.GetGuidValue(K(n)))
                elif n in ('size', 'capacity', 'free'):
                    out[n] = int(v.GetUnsignedLargeIntegerValue(K(n)))
                else:
                    out[n] = v.GetStringValue(K(n))
            except Exception:
                out[n] = None
        return out

    def children(self, oid):
        """[(object id, file name, size, is folder)]"""
        out = []
        for child in self.content.EnumObjects(c_ulong(0), oid, None):
            v = self.values(child, 'file_name', 'name', 'size', 'content_type')
            ct = (v['content_type'] or '').upper()
            is_dir = ct in (FOLDER, FUNCTIONAL)
            out.append((child, v['file_name'] or v['name'] or child, v['size'] or 0, is_dir))
        return out

    def storages(self):
        """[(object id, description, total, free)]"""
        out = []
        for oid, name, _, is_dir in self.children('DEVICE'):
            v = self.values(oid, 'capacity', 'free', 'name')
            if v['capacity']:
                out.append((oid, v['name'] or name, v['capacity'], v['free'] or 0))
        return out

    def read(self, oid, local, limit=None):
        api, _ = libs()
        optimal = pointer(c_ulong(0))
        optimal, pstream = self.transfer.GetStream(oid, K('resource'), c_uint(0), optimal)
        size = max(4096, optimal.contents.value)
        stream = getattr(pstream, 'value', pstream)
        buf = (c_ubyte * size)()
        got = pointer(c_ulong(0))
        total = 0
        with open(local, 'wb') as f:
            while True:
                stream._ISequentialStream__com_RemoteRead(buf, c_ulong(size), got)
                n = got.contents.value
                if n == 0:
                    break
                f.write(bytes(buf[:n]))
                total += n
                if limit and total >= limit:
                    break

    def write(self, parent, name, local):
        api, types = libs()
        size = os.path.getsize(local)
        v = _create(types.PortableDeviceValues, types.IPortableDeviceValues)
        v.SetStringValue(K('parent'), parent)
        v.SetUnsignedLargeIntegerValue(K('size'), size)
        v.SetStringValue(K('file_name'), name)
        v.SetStringValue(K('name'), name)
        v.SetGuidValue(K('format'), guid(FORMAT_UNSPECIFIED))
        v.SetGuidValue(K('content_type'), guid(GENERIC_FILE))
        res = self.content.CreateObjectWithPropertiesAndData(v, pointer(c_ulong(0)), POINTER(c_wchar_p)())
        stream = res[0] if isinstance(res, tuple) else res
        stream = getattr(stream, 'value', stream)
        written = pointer(c_ulong(0))
        with open(local, 'rb') as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                buf = (c_ubyte * len(chunk)).from_buffer_copy(chunk)
                stream._ISequentialStream__com_RemoteWrite(buf, c_ulong(len(chunk)), written)
        stream.Commit(0)

    def make_folder(self, parent, name):
        api, types = libs()
        v = _create(types.PortableDeviceValues, types.IPortableDeviceValues)
        v.SetStringValue(K('parent'), parent)
        v.SetStringValue(K('name'), name)
        v.SetStringValue(K('file_name'), name)
        v.SetGuidValue(K('content_type'), guid(FOLDER))
        v.SetGuidValue(K('format'), guid(FORMAT_PROPERTIES_ONLY))
        r = self.content.CreateObjectWithPropertiesOnly(v, POINTER(c_wchar_p)())
        r = r[0] if isinstance(r, tuple) else r
        return getattr(r, 'value', r)

    def delete(self, oid, recursive=False):
        api, types = libs()

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [('vt', c_ushort), ('r1', c_ushort), ('r2', c_ushort), ('r3', c_ushort),
                        ('pwszVal', c_wchar_p), ('pad', c_void_p)]
        coll = _create(types.PortableDevicePropVariantCollection, api.IPortableDevicePropVariantCollection)
        pv = PROPVARIANT(vt=31, pwszVal=oid)       # VT_LPWSTR
        coll.Add(cast(pointer(pv), POINTER(api.tag_inner_PROPVARIANT)))
        self.content.Delete(c_ulong(1 if recursive else 0), coll, None)

    def close(self):
        try:
            self.dev.Close()
        except Exception:
            pass


class WPDStorage:
    """The storage area of a WPD reader, addressed by '/'-separated paths (same interface as MTPStorage)."""

    def __init__(self, device, storage, name, kind):
        self.device, self.name, self.kind = device, name, kind
        self.model = device.model
        self.sid, self.description, self._total, self._free = storage
        self._folders = {}
        self.lock = threading.RLock()

    @property
    def space(self):
        v = call(self.device.values, self.sid, 'capacity', 'free')
        return v['capacity'] or self._total, v['free'] or self._free

    def _folder_id(self, folder):
        if folder == '':
            return self.sid
        parent, _, name = folder.rpartition('/')
        e = self._entries(parent).get(name)
        return e[0] if e and e[2] else None

    def _entries(self, folder):
        if folder not in self._folders:
            fid = self._folder_id(folder)
            self._folders[folder] = {} if fid is None else {
                n: (i, s, d) for i, n, s, d in call(self.device.children, fid)}
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
        import tempfile
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
        import tempfile
        fd, tmp = tempfile.mkstemp()
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        try:
            self.put(tmp, lpath)
        finally:
            os.remove(tmp)

    def head(self, lpath, n):
        import tempfile
        e = self._find(lpath)
        if not e:
            raise OSError('not on the reader: ' + lpath)
        fd, tmp = tempfile.mkstemp()
        os.close(fd)
        try:
            call(self.device.read, e[0], tmp, n)
            with open(tmp, 'rb') as f:
                return f.read(n)
        finally:
            os.remove(tmp)

    def get(self, lpath, local):
        e = self._find(lpath)
        if not e:
            raise OSError('not on the reader: ' + lpath)
        call(self.device.read, e[0], local)

    def _make_folder(self, folder):
        fid = self._folder_id(folder)
        if fid is not None:
            return fid
        parent, _, name = folder.rpartition('/')
        pid = self._make_folder(parent)
        new = call(self.device.make_folder, pid, name)
        self._entries(parent)[name] = (new, 0, True)
        self._folders[folder] = {}
        return new

    def put(self, local, lpath):
        if self._find(lpath):
            self.remove(lpath)
        folder, _, name = lpath.strip('/').rpartition('/')
        pid = self._make_folder(folder)
        call(self.device.write, pid, name, local)
        self._folders.pop(folder, None)     # pick up the new object's id next time

    def remove(self, lpath):
        e = self._find(lpath)
        if not e:
            return
        call(self.device.delete, e[0], e[2])
        folder, _, name = lpath.strip('/').rpartition('/')
        self._entries(folder).pop(name, None)
        if e[2]:
            self._folders.pop(lpath.strip('/'), None)

    def eject(self):
        call(self.device.close)

    def close(self):
        pass
