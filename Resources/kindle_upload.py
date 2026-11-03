# Run with: calibre-debug -e kindle_upload.py <book.azw3>
# Uploads over MTP like Calibre's "Send to device", so the Kindle home screen shows the cover.
import sys, os, json
from calibre.devices.scanner import DeviceScanner
from calibre.devices.mtp.driver import MTP_DEVICE
from calibre.ebooks.metadata.meta import get_metadata
from calibre.utils.img import scale_image


def result(**kw):
    print('RESULT ' + json.dumps(kw), flush=True)


path = sys.argv[-1]
dev = MTP_DEVICE(None)
dev.startup()
try:
    scanner = DeviceScanner()
    scanner.scan()
    cd = dev.detect_managed_devices(scanner.devices)
    if cd is None:
        result(status='no_device')
        sys.exit(0)
    try:
        dev.open(cd, 'bookforge')
    except Exception as e:
        result(status='open_failed', error=str(e)[:300])
        sys.exit(0)

    with open(path, 'rb') as f:
        mi = get_metadata(f, 'azw3')
    if mi.cover_data and mi.cover_data[1]:
        mi.thumbnail = scale_image(mi.cover_data[1], width=10000, height=dev.THUMBNAIL_HEIGHT * 2)

    def thumbs():
        names = set()
        for storage in dev.filesystem_cache.entries:
            d = storage.find_path(['system', 'thumbnails'])
            if d is not None:
                names |= {f.name for f in d.files}
        return names

    # replace an older copy of the same book (same "Title - Author" file name)
    prefix = '%s - ' % mi.title
    for storage in dev.filesystem_cache.entries:
        docs = storage.find_path(['documents'])
        if docs is None:
            continue
        for f in list(docs.files):
            if f.name.startswith(prefix) and f.name.lower().endswith(('.azw3', '.mobi')):
                try:
                    dev.delete_kindle_cover_thumbnail_for(storage, f.mtp_relpath)
                except Exception:
                    pass
                dev.delete_file_or_folder(f)
                print('removed old copy', f.name, flush=True)

    before = thumbs()
    res = dev.upload_books([path], [os.path.basename(path)], end_session=False, metadata=[mi])
    after = thumbs()
    uploaded = res[0][0]
    new_thumbs = sorted(n for n in after - before if 'portrait' in n)
    result(status='uploaded', path='/'.join(uploaded.mtp_relpath) if hasattr(uploaded, 'mtp_relpath') else str(uploaded),
           thumbnail=bool(new_thumbs), thumbnails=new_thumbs, device=dev.current_friendly_name)
finally:
    try:
        dev.shutdown()
    except Exception:
        pass
