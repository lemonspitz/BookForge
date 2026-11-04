"""The languages Book Forge reads and translates, from languages.json (shared with the Mac app's Resources)."""
import json, os, re

_here = os.path.dirname(os.path.abspath(__file__))


def _load():
    # packaged: next to this file; from source: the repository's Resources folder
    for p in (os.path.join(_here, 'languages.json'), os.path.join(_here, '..', '..', 'Resources', 'languages.json')):
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                return json.load(f)['languages']
    raise RuntimeError('languages.json is missing')


ALL = _load()
BY_CODE = {l['code']: l for l in ALL}
CODES = [l['code'] for l in ALL]
CHOICES = [(l['code'], l['name']) for l in ALL]


def get(code):
    return BY_CODE.get(code)


def name(code):
    return BY_CODE[code]['name'] if code in BY_CODE else code


def prose(code):
    return BY_CODE[code]['prose'] if code in BY_CODE else code


def known(code):
    """A language Book Forge knows, or English."""
    return code if code in BY_CODE else 'en'


def normalize(code):
    """'ind', 'zh-TW', 'zh_CN', 'ko-KR'… → the code Book Forge uses."""
    parts = [p for p in re.split(r'[-_]', (code or '').lower()) if p]
    if not parts:
        return ''
    base = {'in': 'id', 'ind': 'id', 'eng': 'en', 'jp': 'ja', 'jpn': 'ja', 'kor': 'ko', 'vie': 'vi',
            'tha': 'th', 'chi': 'zh', 'zho': 'zh', 'cmn': 'zh'}.get(parts[0], parts[0])
    if base == 'zh':
        return 'zh-Hant' if {'hant', 'tw', 'hk', 'mo'} & set(parts[1:]) else 'zh-Hans'
    return base[:2]
