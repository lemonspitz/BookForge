"""A reader that shows up as a drive or folder: older Kindles, Kobo, PocketBook, and MTP readers that the
Linux desktop has already mounted (gvfs)."""
import os, shutil, sys

from .meta import skip_folder


class FolderStorage:
    def __init__(self, root, name, kind, mount=None):
        self.root, self.name, self.kind = root, name, kind
        self.mount = mount or root      # what to eject
        self.model = name

    def _p(self, lpath):
        return os.path.join(self.root, *lpath.split('/'))

    @property
    def space(self):
        try:
            u = shutil.disk_usage(self.root)
            return u.total, u.free
        except OSError:
            return 0, 0

    def files(self):
        """[(lpath, size)] of every file outside the reader's system folders."""
        out = []
        for dirpath, dirs, files in os.walk(self.root):
            rel = os.path.relpath(dirpath, self.root).replace(os.sep, '/')
            rel = '' if rel == '.' else rel + '/'
            dirs[:] = [d for d in dirs if not skip_folder(rel + d)]
            for f in files:
                try:
                    out.append((rel + f, os.path.getsize(os.path.join(dirpath, f))))
                except OSError:
                    pass
        return out

    def exists(self, lpath):
        return os.path.exists(self._p(lpath))

    def read_small(self, lpath):
        try:
            with open(self._p(lpath), 'rb') as f:
                return f.read()
        except OSError:
            return None

    def write_small(self, lpath, data):
        p = self._p(lpath)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(data)

    def head(self, lpath, n):
        with open(self._p(lpath), 'rb') as f:
            return f.read(n)

    def get(self, lpath, local):
        shutil.copyfile(self._p(lpath), local)

    def put(self, local, lpath):
        p = self._p(lpath)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copyfile(local, p)

    def remove(self, lpath):
        p = self._p(lpath)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)

    def eject(self):
        """Unmounts the drive so it's safe to unplug."""
        import subprocess
        try:
            if sys.platform == 'darwin':
                subprocess.run(['diskutil', 'eject', self.mount], capture_output=True, timeout=60)
            elif sys.platform == 'win32':
                letter = os.path.splitdrive(self.mount)[0]
                ps = ("$d = (New-Object -ComObject Shell.Application).Namespace(17).ParseName('%s');"
                      "if ($d) { $d.InvokeVerb('Eject') }" % letter)
                subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=60,
                               creationflags=0x08000000)
            elif self.mount.startswith('/run/user/') and '/gvfs/' in self.mount:
                subprocess.run(['gio', 'mount', '-u', self.mount], capture_output=True, timeout=60)
            else:
                subprocess.run(['udisksctl', 'unmount', '-b', self._block_device()], capture_output=True, timeout=60)
        except Exception:
            pass

    def _block_device(self):
        with open('/proc/mounts') as f:
            for line in f:
                dev, mnt = line.split()[:2]
                if mnt.replace('\\040', ' ') == self.mount:
                    return dev
        return self.mount

    def close(self):
        pass
