#!/usr/bin/env python3
"""charts_sellos — charts a partir del nombre de un sello.

Catálogo: MusicBrainz + Discogs. Información: Last.fm. Vídeos: yt-dlp.
Abrir: http://localhost:8792
"""
import concurrent.futures
import contextlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import requests

PORT = int(os.environ.get('PORT', 8792))
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('CHARTS_DATA_DIR') or os.path.join(APP_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'charts_sellos.db')
GROUPS_FILE = os.path.join(DATA_DIR, 'groups.json')
COOKIES = os.path.join(DATA_DIR, 'cookies.txt')
STATIC_DIR = os.path.join(APP_DIR, 'static')
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/theme-palettes.css': ('theme-palettes.css', 'text/css; charset=utf-8'),
    '/theme-picker.js': ('theme-picker.js', 'application/javascript; charset=utf-8'),
    '/settings-panel.js': ('settings-panel.js', 'application/javascript; charset=utf-8'),
    '/charts-switch.js': ('charts-switch.js', 'application/javascript; charset=utf-8'),
}


def switch_items():
    """Servicios del selector, de CHARTS_SWITCH=Nombre:puerto,Nombre:puerto (vacío: sin selector)."""
    items = []
    for part in os.environ.get('CHARTS_SWITCH', '').split(','):
        label, _, port = part.strip().rpartition(':')
        if label and port.isdigit():
            items.append({'label': label, 'port': int(port), 'current': int(port) == PORT})
    return items

SETTINGS_ENV_PATH = os.path.join(APP_DIR, '.env')


def _read_env_file(path):
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('#') or '=' not in s:
                continue
            k, v = s.split('=', 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1]
            values[k.strip()] = v
    return values


# Fuera de Docker no hay env_file: lo que no venga del entorno se lee del .env
for _k, _v in _read_env_file(SETTINGS_ENV_PATH).items():
    os.environ.setdefault(_k, _v)

LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY', '')
try:
    # Búsquedas de YouTube simultáneas. Cada una suma ~1700 búsquedas/h, desde la misma IP
    YT_WORKERS = max(1, int(os.environ.get('YT_WORKERS') or 2))
except ValueError:
    YT_WORKERS = 2
LASTFM_USER = os.environ.get('LASTFM_USERNAME', '')
DISCOGS_TOKEN = os.environ.get('DISCOGS_TOKEN', '')
SETTINGS_PASSWORD = os.environ.get('SETTINGS_PASSWORD', '')
VARS_SPEC = [
    {'name': 'LASTFM_API_KEY', 'secret': True, 'help': 'API key de Last.fm'},
    {'name': 'LASTFM_USERNAME', 'secret': False, 'help': 'Usuario de Last.fm (opcional: añade tus escuchas de cada disco)'},
    {'name': 'DISCOGS_TOKEN', 'secret': True, 'help': 'Token de Discogs (sin él solo se usa MusicBrainz)'},
    {'name': 'YT_WORKERS', 'secret': False, 'help': 'Búsquedas de YouTube simultáneas (2 por defecto). Más rápido, pero más riesgo de bloqueo'},
]

UA = 'charts_sellos/1.0 ( https://github.com/volteret4/repeat-one )'
LASTFM = 'https://ws.audioscrobbler.com/2.0/'
MB = 'https://musicbrainz.org/ws/2'
DISCOGS = 'https://api.discogs.com'
# Imagen que Last.fm devuelve cuando no tiene portada
LASTFM_NO_COVER = '2a96cbd8b46e442fc41c2b86b821562f'
LASTFM_TTL = 14 * 86400
NO_VIDEO_TTL = 14 * 86400
VARIOUS = 'various artists'
RTYPES = ('Album', 'EP', 'Single', 'Compilation', 'Live album', 'Remix', 'Soundtrack', 'DJ Mix', 'Mixtape', 'Demo',
          'Interview', 'Unknown')
LONG_TYPES = {'Album', 'Compilation', 'Live album', 'Soundtrack', 'DJ Mix', 'Mixtape', 'Unknown'}


def norm(s):
    """Minúsculas, sin acentos ni puntuación, espacios simples."""
    s = unicodedata.normalize('NFD', s or '')
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()
    s = re.sub(r'[^\w]+', ' ', s.replace('&', ' and '))
    return re.sub(r'\s+', ' ', s).strip()


def album_key(artist, album):
    artist = norm(artist)
    if artist == 'various':
        artist = VARIOUS
    return artist + '|' + norm(album)


def slugify(name):
    return norm(name).replace(' ', '-') or 'sello'


# ── Base de datos ─────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, sources TEXT NOT NULL, info TEXT,
    status TEXT NOT NULL DEFAULT 'queued', error TEXT, created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS releases (
    label_slug TEXT NOT NULL REFERENCES labels(slug) ON DELETE CASCADE,
    album_key TEXT NOT NULL, rtype TEXT, year INTEGER, date TEXT, catno TEXT,
    mb_rgid TEXT, discogs_id INTEGER, thumb TEXT,
    PRIMARY KEY (label_slug, album_key)
);
CREATE TABLE IF NOT EXISTS albums (
    album_key TEXT PRIMARY KEY, artist TEXT NOT NULL, album TEXT NOT NULL,
    lastfm TEXT, lastfm_at REAL, listeners INTEGER, playcount INTEGER, tags TEXT, cover TEXT,
    videos TEXT, videos_at REAL, custom TEXT
);
CREATE TABLE IF NOT EXISTS artists (artist_key TEXT PRIMARY KEY, info TEXT, fetched_at REAL);
"""


@contextlib.contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        with con:
            yield con
    finally:
        con.close()


def init_db():
    with db() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript(SCHEMA)


# ── HTTP con reintentos y ritmo por servicio ──────────────────────────────────
class Throttle:
    def __init__(self, interval):
        self.interval, self.last, self.lock = interval, 0, threading.Lock()

    def wait(self):
        with self.lock:
            pause = self.last + self.interval - time.time()
            if pause > 0:
                time.sleep(pause)
            self.last = time.time()


_mb_rate = Throttle(1.1)        # MusicBrainz: 1 petición/s
_discogs_rate = Throttle(1.05)  # Discogs con token: 60 peticiones/min
_lastfm_rate = Throttle(0.2)    # Last.fm: 5 peticiones/s


def _get_json(url, rate, params=None, headers=None):
    """JSON de la URL, o excepción si tras los reintentos sigue fallando."""
    last = None
    for attempt in range(4):
        rate.wait()
        try:
            r = requests.get(url, params=params, headers={'User-Agent': UA, **(headers or {})}, timeout=25)
        except requests.RequestException as e:
            last = e
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = RuntimeError(f'HTTP {r.status_code} en {url}')
            time.sleep(min(int(r.headers.get('Retry-After') or 0) or 5 * 2 ** attempt, 65))
            continue
        r.raise_for_status()
        return r.json()
    raise last


def _discogs_headers():
    return {'Authorization': f'Discogs token={DISCOGS_TOKEN}'}


# ── Búsqueda de sellos ────────────────────────────────────────────────────────
def search_labels(q):
    """Candidatos de ambas fuentes; marca por defecto la coincidencia exacta de cada una."""
    out, errors = [], []
    target = norm(q)

    def mb():
        data = _get_json(f'{MB}/label', _mb_rate, {'query': q, 'fmt': 'json', 'limit': 8})
        res = []
        for l in data.get('labels', []):
            span = l.get('life-span') or {}
            res.append({'source': 'mb', 'id': l['id'], 'name': l.get('name', ''),
                        'detail': ' · '.join(x for x in [
                            l.get('type'), l.get('country'),
                            (span.get('begin') or '')[:4], l.get('disambiguation')] if x),
                        'url': f"https://musicbrainz.org/label/{l['id']}"})
        return res

    def discogs():
        if not DISCOGS_TOKEN:
            return []
        data = _get_json(f'{DISCOGS}/database/search', _discogs_rate,
                         {'type': 'label', 'q': q, 'per_page': 8}, _discogs_headers())
        return [{'source': 'discogs', 'id': r['id'], 'name': r.get('title', ''), 'detail': '',
                 'url': f"https://www.discogs.com/label/{r['id']}"} for r in data.get('results', [])]

    with concurrent.futures.ThreadPoolExecutor(2) as ex:
        for name, fut in (('MusicBrainz', ex.submit(mb)), ('Discogs', ex.submit(discogs))):
            try:
                results = fut.result()
            except Exception as e:
                errors.append(f'{name}: {e}')
                continue
            for i, c in enumerate(results):
                exact = norm(re.sub(r'\s*\(\d+\)$', '', c['name'])) == target
                c['preselected'] = exact and not any(
                    x['preselected'] for x in results[:i])
            out += results
    return {'candidates': out, 'errors': errors}


# ── Catálogo ──────────────────────────────────────────────────────────────────
_MB_SECONDARY = {'Compilation': 'Compilation', 'Live': 'Live album', 'Remix': 'Remix', 'Soundtrack': 'Soundtrack',
                 'DJ-mix': 'DJ Mix', 'Mixtape/Street': 'Mixtape', 'Demo': 'Demo', 'Interview': 'Interview'}


def _mb_type(rg):
    for s in rg.get('secondary-types') or []:
        if s in _MB_SECONDARY:
            return _MB_SECONDARY[s]
    return {'Album': 'Album', 'EP': 'EP', 'Single': 'Single'}.get(rg.get('primary-type'), 'Unknown')


def _year(date):
    m = re.match(r'\d{4}', str(date or ''))
    y = int(m.group()) if m else 0
    return y or None


def mb_catalog(mbid, log):
    """Una entrada por release-group del sello, y datos del propio sello."""
    info = _get_json(f'{MB}/label/{mbid}', _mb_rate, {'fmt': 'json'})
    span = info.get('life-span') or {}
    label = {'name': info.get('name'), 'type': info.get('type'), 'country': info.get('country'),
             'begin': span.get('begin'), 'end': span.get('end'), 'mb_url': f'https://musicbrainz.org/label/{mbid}'}
    groups, offset = {}, 0
    while True:
        data = _get_json(f'{MB}/release', _mb_rate, {'label': mbid, 'inc': 'release-groups+artist-credits+labels',
                                                     'fmt': 'json', 'limit': 100, 'offset': offset})
        page = data.get('releases', [])
        for rel in page:
            rg = rel.get('release-group') or {}
            if not rg.get('id'):
                continue
            artist = ''.join(c.get('name', '') + c.get('joinphrase', '') for c in rel.get('artist-credit', []))
            catno = next((li.get('catalog-number') for li in rel.get('label-info') or []
                          if (li.get('label') or {}).get('id') == mbid and li.get('catalog-number')), None)
            date = rg.get('first-release-date') or rel.get('date') or ''
            prev = groups.get(rg['id'])
            if prev:
                prev['catno'] = prev['catno'] or catno
                continue
            groups[rg['id']] = {'artist': artist or 'Unknown Artist', 'album': rg.get('title') or rel.get('title', ''),
                                'rtype': _mb_type(rg), 'date': date, 'year': _year(date), 'catno': catno,
                                'mb_rgid': rg['id'], 'discogs_id': None, 'thumb': None}
        offset += len(page)
        log(f'MusicBrainz: {offset} / {data.get("release-count", offset)} ediciones')
        if not page or offset >= data.get('release-count', 0):
            return label, list(groups.values())


def _discogs_type(fmt):
    tokens = {t.strip() for t in (fmt or '').split(',')}
    joined = (fmt or '')
    for token, rtype in (('Comp', 'Compilation'), ('Smplr', 'Compilation'), ('Mixed', 'DJ Mix'), ('Album', 'Album'),
                         ('LP', 'Album'), ('Mini-Album', 'EP'), ('EP', 'EP'), ('Single', 'Single'),
                         ('Maxi', 'Single')):
        if token in tokens:
            return rtype
    # Sin descripción: el formato físico es lo único que orienta
    if re.search(r'(^|\D)7"', joined):
        return 'Single'
    if re.search(r'(^|\D)1[02]"', joined):
        return 'EP'
    return 'Unknown'


def _discogs_name(artist):
    # «Autechre (2)» y «Autechre*» son desambiguaciones internas de Discogs
    return re.sub(r'\s*\(\d+\)', '', artist or '').replace('*', '').strip()


def discogs_catalog(label_id, log):
    info = _get_json(f'{DISCOGS}/labels/{label_id}', _discogs_rate, headers=_discogs_headers())
    label = {'name': info.get('name'), 'profile': info.get('profile'), 'contact': info.get('contact_info'),
             'discogs_url': info.get('uri'), 'urls': info.get('urls') or [],
             'image': (info.get('images') or [{}])[0].get('uri'),
             'sublabels': [s.get('name') for s in info.get('sublabels') or []]}
    out, page = {}, 1
    while True:
        data = _get_json(f'{DISCOGS}/labels/{label_id}/releases', _discogs_rate,
                         {'per_page': 100, 'page': page, 'sort': 'year'}, _discogs_headers())
        for rel in data.get('releases', []):
            artist = _discogs_name(rel.get('artist')) or 'Unknown Artist'
            title = rel.get('title') or ''
            if not title:
                continue
            key = album_key(artist, title)
            rtype, year = _discogs_type(rel.get('format')), rel.get('year') or None
            prev = out.get(key)
            if prev:
                if prev['rtype'] == 'Unknown':
                    prev['rtype'] = rtype
                if year and (not prev['year'] or year < prev['year']):
                    prev.update(year=year, date=str(year), catno=rel.get('catno') or prev['catno'])
                continue
            out[key] = {'artist': artist, 'album': title, 'rtype': rtype, 'date': str(year or ''), 'year': year,
                        'catno': rel.get('catno'), 'mb_rgid': None, 'discogs_id': rel.get('id'),
                        'thumb': rel.get('thumb') or None}
        pages = data.get('pagination', {}).get('pages', 1)
        log(f'Discogs: página {page} / {pages}')
        if page >= pages:
            return label, list(out.values())
        page += 1


def merge_catalogs(catalogs):
    """Junta por artista+título. MusicBrainz manda en el tipo; se conserva el año más antiguo."""
    merged = {}
    for items in catalogs:
        for it in items:
            key = album_key(it['artist'], it['album'])
            prev = merged.get(key)
            if not prev:
                merged[key] = dict(it)
                continue
            if it['mb_rgid'] and not prev['mb_rgid']:
                prev['mb_rgid'], prev['rtype'] = it['mb_rgid'], it['rtype']
                prev['artist'], prev['album'] = it['artist'], it['album']
            elif prev['rtype'] == 'Unknown':
                prev['rtype'] = it['rtype']
            for f in ('discogs_id', 'thumb', 'catno'):
                prev[f] = prev[f] or it[f]
            if it['year'] and (not prev['year'] or it['year'] < prev['year']):
                prev['year'], prev['date'] = it['year'], it['date']
    return merged


# ── Last.fm ───────────────────────────────────────────────────────────────────
class LastfmError(Exception):
    pass


def _lfm(params):
    """None si Last.fm no conoce la entidad; LastfmError si es un fallo que merece reintento."""
    try:
        data = _get_json(LASTFM, _lastfm_rate, {**params, 'api_key': LASTFM_API_KEY, 'format': 'json',
                                                'autocorrect': 1})
    except requests.HTTPError as e:
        # La API responde 400/404 con {"error": 6} a lo que no encuentra
        try:
            if e.response.json().get('error') == 6:
                return None
        except ValueError:
            pass
        raise LastfmError(str(e)) from e
    except Exception as e:
        raise LastfmError(str(e)) from e
    if 'error' in data:
        if data['error'] == 6:
            return None
        raise LastfmError(data.get('message', str(data['error'])))
    return data


def _as_list(v):
    if not v:
        return []
    return v if isinstance(v, list) else [v]


def _clean_text(s):
    """Quita el «Read more on Last.fm» y el HTML de wikis y biografías."""
    s = re.sub(r'<a href=.*?</a>\.?', '', s or '', flags=re.S)
    s = re.sub(r'User-contributed text is available under.*$', '', s, flags=re.S)
    return re.sub(r'<[^>]+>', '', s).strip() or None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _names(section, key='tag'):
    return [t.get('name') for t in _as_list((section or {}).get(key)) if isinstance(t, dict) and t.get('name')]


def _album_variants(artist, album):
    yield artist, album
    first = re.split(r'\s+(?:&|and|vs\.?|feat\.?|ft\.?|with)\s+|\s*[,/]\s*', artist, maxsplit=1, flags=re.I)[0]
    bare = re.sub(r'\s*[(\[][^)\]]*[)\]]\s*$', '', album).strip()
    if first and first != artist:
        yield first, album
    if bare and bare != album:
        yield artist, bare


def lastfm_album(artist, album):
    params = {'method': 'album.getinfo'}
    if LASTFM_USER:
        params['username'] = LASTFM_USER
    al = None
    for a, b in _album_variants(artist, album):
        data = _lfm({**params, 'artist': a, 'album': b})
        if data and data.get('album'):
            al = data['album']
            break
    if not al:
        return {}
    wiki = al.get('wiki') if isinstance(al.get('wiki'), dict) else {}
    tracks = []
    for t in _as_list((al.get('tracks') or {}).get('track') if isinstance(al.get('tracks'), dict) else None):
        tracks.append({'name': t.get('name'), 'duration': _int(t.get('duration')), 'url': t.get('url')})
    images = [i.get('#text') for i in _as_list(al.get('image')) if i.get('#text')]
    image = images[-1] if images else None
    if image and LASTFM_NO_COVER in image:
        image = None
    return {'name': al.get('name'), 'artist': al.get('artist') if isinstance(al.get('artist'), str) else None,
            'url': al.get('url'), 'mbid': al.get('mbid') or None, 'image': image,
            'listeners': _int(al.get('listeners')), 'playcount': _int(al.get('playcount')),
            'userplaycount': _int(al.get('userplaycount')) if 'userplaycount' in al else None,
            'tags': _names(al.get('tags') if isinstance(al.get('tags'), dict) else {}),
            'tracks': tracks, 'published': wiki.get('published'),
            'wiki_summary': _clean_text(wiki.get('summary')), 'wiki': _clean_text(wiki.get('content'))}


def lastfm_artist(name):
    data = _lfm({'method': 'artist.getinfo', 'artist': name})
    a = (data or {}).get('artist')
    if not a:
        return {}
    bio, stats = a.get('bio') or {}, a.get('stats') or {}
    return {'name': a.get('name'), 'url': a.get('url'), 'mbid': a.get('mbid') or None,
            'listeners': _int(stats.get('listeners')), 'playcount': _int(stats.get('playcount')),
            'tags': _names(a.get('tags') if isinstance(a.get('tags'), dict) else {}),
            'similar': [{'name': s.get('name'), 'url': s.get('url')}
                        for s in _as_list((a.get('similar') or {}).get('artist'))],
            'bio_summary': _clean_text(bio.get('summary')), 'bio': _clean_text(bio.get('content')),
            'published': bio.get('published')}


def fill_lastfm(key, artist, album, force=False):
    """Pide a Last.fm disco y artista si faltan o han caducado. Devuelve True si consultó."""
    with db() as con:
        row = con.execute('SELECT lastfm_at FROM albums WHERE album_key=?', (key,)).fetchone()
    if not LASTFM_API_KEY or (row and row['lastfm_at'] and not force):
        return False
    # «Various Artists — 2025» mezcla en Last.fm cualquier disco con ese título
    generic = is_various(artist) and len(norm(album).split()) < 2
    info = {} if generic else lastfm_album(artist, album)
    with db() as con:
        con.execute("""UPDATE albums SET lastfm=?, lastfm_at=?, listeners=?, playcount=?, tags=?, cover=?
                       WHERE album_key=?""",
                    (json.dumps(info, ensure_ascii=False), time.time(), info.get('listeners'), info.get('playcount'),
                     json.dumps(info.get('tags') or [], ensure_ascii=False), info.get('image'), key))
    if not is_various(artist):
        fill_artist(artist, force)
    return True


def fill_artist(artist, force=False):
    akey = norm(artist)
    with db() as con:
        row = con.execute('SELECT fetched_at FROM artists WHERE artist_key=?', (akey,)).fetchone()
    if row and not force:
        return
    info = lastfm_artist(artist)
    with db() as con:
        con.execute('INSERT OR REPLACE INTO artists VALUES (?,?,?)',
                    (akey, json.dumps(info, ensure_ascii=False), time.time()))


# ── YouTube (yt-dlp) ──────────────────────────────────────────────────────────
class YtBlocked(Exception):
    """YouTube pide confirmar que no somos un bot: reintentar ahora no serviría."""


_yt_slots = threading.BoundedSemaphore(YT_WORKERS)   # cuenta también las búsquedas de clic
YT_BLOCK_PAUSE, YT_BLOCK_PAUSE_MAX = 30 * 60, 4 * 3600
YT_RETRY_WAITS = (3, 8, 20)
_GENERIC_WORDS = {'ep', 'lp', 'remix', 'remixes', 'reworks', 'edits', 'the', 'a'}
_BAD_WORDS = {'reaction', 'review', 'unboxing', 'interview', 'trailer', 'tutorial', 'karaoke', 'lesson', 'teaser'}


def _token_ratio(needle, hay):
    tokens = needle.split()
    return sum(t in hay for t in tokens) / len(tokens) if tokens else 0


def is_various(artist):
    return norm(artist) in (VARIOUS, 'various')


def _title_core(album):
    """El título sin palabras genéricas: «Epiphany Remixes» también es «Epiphany (X Remix)»."""
    tokens = norm(album).split()
    core = [t for t in tokens if t not in _GENERIC_WORDS]
    return ' '.join(core or tokens)


def rank_videos(entries, artist, album, rtype, total_secs=0, label='', tracks=(), catno=''):
    """Solo vídeos cuyo título o canal nombran disco y artista (o sello si es un
    recopilatorio de varios: el título solo no basta), los mejores primero. Muchos EP
    solo están subidos pista a pista: valen también, detrás, los que nombran una de sus
    canciones o su número de catálogo."""
    target = total_secs or (2400 if rtype in LONG_TYPES else 1200 if rtype == 'EP' else 300)
    n_artist, n_album, n_label = norm(artist), _title_core(album), norm(label)
    various = is_various(artist)
    n_tracks = [t for t in (norm(x) for x in tracks) if len(t) >= 4]
    n_catno = re.sub(r'\W+', '', (catno or '').lower())
    ranked = []
    for e in entries:
        if not e or not e.get('id'):
            continue
        title, channel = norm(e.get('title')), norm(e.get('channel') or e.get('uploader'))
        hay = set(f'{title} {channel}'.split())
        duration = e.get('duration') or 0
        if duration and duration < 30:
            continue
        album_ok = _token_ratio(n_album, hay) >= (0.75 if len(n_album.split()) > 3 else 1)
        if not album_ok and not any(f' {t} ' in f' {title} ' for t in n_tracks) and not (
                len(n_catno) >= 5 and n_catno in title.replace(' ', '')):
            continue
        if _token_ratio(n_label if various else n_artist, hay) < 0.5:
            continue
        if (set(title.split()) & _BAD_WORDS) - set(n_album.split()):
            continue
        score = -abs(duration - target) / max(target, 60) if duration else -1
        if 'full album' in title or 'full ep' in title:
            score += 1 if rtype in LONG_TYPES or rtype == 'EP' else 0
        if channel.endswith(' topic') or (not various and n_artist and n_artist in channel):
            score += 0.5
        if not album_ok:
            score -= 3
        ranked.append((score, {'id': e['id'], 'title': e.get('title') or '', 'channel': e.get('channel') or e.get('uploader'),
                               'duration': duration or None, 'views': e.get('view_count')}))
    ranked.sort(key=lambda x: -x[0])
    return [v for _, v in ranked[:5]]


def yt_run(cmd):
    """Lanza yt-dlp. YouTube rechaza con 403 búsquedas sueltas al azar y yt-dlp no reintenta
    los 403; sin dormir con la plaza ocupada, para no frenar a los otros workers."""
    for wait in (*YT_RETRY_WAITS, None):
        with _yt_slots:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if not proc.returncode or wait is None or 'HTTP Error 403' not in proc.stderr:
            return proc
        time.sleep(wait)


def yt_search(artist, album, rtype, total_secs=0, label='', tracks=(), catno=''):
    who = label if is_various(artist) else artist
    query = f'{who} {album}'.strip() + (' full album' if rtype in LONG_TYPES else '')
    cmd = ['yt-dlp', f'ytsearch10:{query}', '--dump-single-json', '--flat-playlist', '--quiet', '--no-warnings',
           '--socket-timeout', '20']
    if os.path.exists(COOKIES):
        cmd += ['--cookies', COOKIES]
    proc = yt_run(cmd)
    if proc.returncode:
        err = proc.stderr.strip()
        if 'confirm you' in err or 'Sign in' in err:
            raise YtBlocked(err.splitlines()[-1])
        raise RuntimeError(err.splitlines()[-1] if err else f'yt-dlp salió con {proc.returncode}')
    return rank_videos(json.loads(proc.stdout).get('entries') or [], artist, album, rtype, total_secs, label, tracks, catno)


def fill_videos(key, artist, album, rtype, force=False, label=''):
    with db() as con:
        row = con.execute('SELECT videos, videos_at, lastfm FROM albums WHERE album_key=?', (key,)).fetchone()
    if row and row['videos'] is not None and not force:
        stale_empty = row['videos'] == '[]' and time.time() - (row['videos_at'] or 0) > NO_VIDEO_TTL
        if not stale_empty:
            return False
    tracks = (json.loads(row['lastfm']) if row and row['lastfm'] else {}).get('tracks') or []
    with db() as con:
        rel = con.execute('SELECT catno FROM releases WHERE album_key=? AND catno IS NOT NULL', (key,)).fetchone()
    videos = yt_search(artist, album, rtype, sum(t.get('duration') or 0 for t in tracks), label,
                       [t.get('name') for t in tracks], rel['catno'] if rel else '')
    with db() as con:
        con.execute('UPDATE albums SET videos=?, videos_at=? WHERE album_key=?',
                    (json.dumps(videos, ensure_ascii=False), time.time(), key))
    return True


# ── Trabajos en segundo plano ─────────────────────────────────────────────────
_job = {'finished': 0, 'log': ''}
_job_lock = threading.Lock()


def _new_stage():
    return {'running': False, 'slug': None, 'name': None, 'phase': None, 'done': 0, 'total': 0, 'current': [],
            'queue': [], 'error': None, 'warning': None, 'cancel': False, 'paused_until': None}


# Dos colas independientes: un sello pasa por la primera y luego por la segunda, porque
# YouTube elige mejor sabiendo las canciones y la duración que da Last.fm. Así YouTube,
# que es lo lento, no hace esperar a Last.fm de los sellos siguientes.
_data = _new_stage()   # catálogo + Last.fm
_yt = _new_stage()     # búsquedas de vídeos
_yt_wake = threading.Event()   # interrumpe la pausa tras un bloqueo
_yt_backoff = 0


def job_log(line):
    print(line, flush=True)
    with _job_lock:
        _job['log'] = '\n'.join((_job['log'].split('\n') + [line])[-40:]).lstrip('\n')


def job_state():
    with _job_lock:
        snapshot = lambda s: {**s, 'queue': list(s['queue']), 'current': list(s['current'])}
        return {**_job, 'running': _data['running'] or _yt['running'], 'workers': YT_WORKERS,
                'data': snapshot(_data), 'youtube': snapshot(_yt)}


def set_status(slug, status, error=None):
    with db() as con:
        con.execute('UPDATE labels SET status=?, error=?, updated=? WHERE slug=?', (status, error, time.time(), slug))


def _push(stage, run, slug):
    with _job_lock:
        if slug in stage['queue']:
            return
        stage['queue'].append(slug)
        if stage['running']:
            return
        stage['running'] = True
    threading.Thread(target=_stage_loop, args=(stage, run), daemon=True).start()


def _stage_loop(stage, run):
    while True:
        with _job_lock:
            if not stage['queue']:
                stage.update(running=False, slug=None, current=[])
                return
            slug = stage['queue'].pop(0)
            stage.update(slug=slug, done=0, total=0, current=[], error=None, warning=None, cancel=False,
                         paused_until=None)
        try:
            run(slug)
        except Exception as e:
            job_log(f'Error: {e}')
            with _job_lock:
                stage['error'] = str(e)
            set_status(slug, 'error', str(e))
        with _job_lock:
            _job['finished'] += 1
            stage.update(slug=None, current=[])


def enqueue(slug):
    """(Re)empieza por el catálogo; si estaba buscando vídeos, deja de hacerlo."""
    with _job_lock:
        if slug in _yt['queue']:
            _yt['queue'].remove(slug)
        if _yt['slug'] == slug:
            _yt['cancel'] = True
            _yt_wake.set()
    _push(_data, _run_catalog, slug)


def _to_youtube(slug):
    set_status(slug, 'youtube')
    with _job_lock:
        again = slug in _data['queue']   # han pulsado ↻ mientras tanto
    if not again:
        _push(_yt, _run_videos, slug)


def _run_catalog(slug):
    with db() as con:
        row = con.execute('SELECT * FROM labels WHERE slug=?', (slug,)).fetchone()
    if not row:
        return
    sources = json.loads(row['sources'])
    with _job_lock:
        _data.update(name=row['name'], phase='Catálogo')
    set_status(slug, 'running')

    infos, catalogs = {}, []
    for mbid in sources.get('mb', []):
        info, items = mb_catalog(mbid, job_log)
        infos.update({k: v for k, v in info.items() if v})
        catalogs.append(items)
    for did in sources.get('discogs', []):
        if not DISCOGS_TOKEN:
            job_log('Sin DISCOGS_TOKEN: se omite Discogs')
            break
        info, items = discogs_catalog(did, job_log)
        infos.update({k: v for k, v in info.items() if v and (k != 'name' or 'name' not in infos)})
        catalogs.append(items)
    merged = merge_catalogs(catalogs)
    if not merged:
        raise RuntimeError('Ninguna fuente ha devuelto discos de este sello')
    total = len(merged)
    if sources.get('types'):
        merged = {k: v for k, v in merged.items() if v['rtype'] in sources['types']}
        if not merged:
            raise RuntimeError(f'El sello tiene {total} discos, pero ninguno en las secciones elegidas')
    job_log(f'{len(merged)} de {total} discos distintos' if len(merged) != total else f'{total} discos distintos')

    with db() as con:
        con.execute('UPDATE labels SET info=? WHERE slug=?', (json.dumps(infos, ensure_ascii=False), slug))
        con.execute('DELETE FROM releases WHERE label_slug=?', (slug,))
        for key, it in merged.items():
            con.execute('INSERT OR IGNORE INTO albums (album_key, artist, album) VALUES (?,?,?)',
                        (key, it['artist'], it['album']))
            con.execute("""INSERT INTO releases (label_slug, album_key, rtype, year, date, catno, mb_rgid, discogs_id, thumb)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (slug, key, it['rtype'], it['year'], it['date'], it['catno'], it['mb_rgid'],
                         it['discogs_id'], it['thumb']))
        rows = con.execute("""SELECT a.album_key, a.artist, a.album, a.lastfm_at, r.rtype, r.year FROM releases r
                              JOIN albums a USING (album_key) WHERE r.label_slug=?
                              ORDER BY r.year DESC""", (slug,)).fetchall()

    # Last.fm: rápido, en paralelo; el ritmo lo marca el Throttle
    now = time.time()
    pending = [r for r in rows if not r['lastfm_at'] or now - r['lastfm_at'] > LASTFM_TTL]
    with _job_lock:
        _data.update(phase='Last.fm', done=0, total=len(pending))

    def lastfm_one(r):
        with _job_lock:
            _data['current'] = [f"{r['artist']} — {r['album']}"]
        try:
            fill_lastfm(r['album_key'], r['artist'], r['album'], force=bool(r['lastfm_at']))
        except LastfmError as e:
            job_log(f"Last.fm: {r['artist']} — {r['album']}: {e}")
        with _job_lock:
            _data['done'] += 1

    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        list(ex.map(lastfm_one, pending))

    _to_youtube(slug)


def _videos_pass(rows, owner):
    """Una pasada por los discos. Devuelve (bloqueo, fallo repetido); lo guardado no se repite."""
    global _yt_backoff
    stop = threading.Event()
    state = {'failures': 0, 'blocked': None, 'broken': None}
    with _job_lock:
        _yt['done'] = 0

    def one(r):
        global _yt_backoff
        with _job_lock:
            if stop.is_set() or _yt['cancel']:
                return
            who = f"{r['artist']} — {r['album']}"
            _yt['current'].append(who)
        try:
            if fill_videos(r['album_key'], r['artist'], r['album'], r['rtype'], label=owner):
                with _job_lock:
                    state['failures'] = 0
                    _yt_backoff = 0
                time.sleep(1)   # cada worker hace ~1700 búsquedas/h; ver YT_WORKERS
        except YtBlocked as e:
            state['blocked'] = e
            stop.set()
            return
        except Exception as e:
            job_log(f'yt-dlp: {who}: {e}')
            with _job_lock:
                state['failures'] += 1
                if state['failures'] >= 8:
                    state['broken'] = e
                    stop.set()
        finally:
            with _job_lock:
                _yt['current'].remove(who)
        with _job_lock:
            _yt['done'] += 1

    with concurrent.futures.ThreadPoolExecutor(YT_WORKERS) as ex:
        list(ex.map(one, rows))
    return state['blocked'], state['broken']


def _run_videos(slug):
    global _yt_backoff
    with db() as con:
        row = con.execute('SELECT name FROM labels WHERE slug=?', (slug,)).fetchone()
        if not row:
            return
        rows = con.execute("""SELECT a.album_key, a.artist, a.album, r.rtype FROM releases r
                              JOIN albums a USING (album_key) WHERE r.label_slug=?
                              ORDER BY r.year DESC""", (slug,)).fetchall()
    with _job_lock:
        _yt.update(name=row['name'], phase='YouTube', total=len(rows))
    while True:
        blocked, broken = _videos_pass(rows, row['name'])
        with _job_lock:
            if _yt['cancel']:
                return
        if not blocked:
            break
        # Insistir empeora el bloqueo y afecta a toda la IP: se espera, y cada vez más
        with _job_lock:
            _yt_backoff = min(max(YT_BLOCK_PAUSE, _yt_backoff * 2), YT_BLOCK_PAUSE_MAX)
            pause = _yt_backoff
            _yt.update(warning=f'YouTube ha bloqueado las búsquedas ({blocked}). Si no se resuelve, '
                               'añade data/cookies.txt y pulsa ↻.', paused_until=time.time() + pause)
        job_log(f'YouTube bloqueado: pausa de {pause // 60} min')
        _yt_wake.clear()
        _yt_wake.wait(pause)
        with _job_lock:
            _yt.update(warning=None, paused_until=None)
            if _yt['cancel']:
                return
    if broken:
        msg = f'yt-dlp falla repetidamente: {broken}'
        with _job_lock:
            _yt['warning'] = msg
        set_status(slug, 'partial', msg)
        return
    set_status(slug, 'done')
    job_log('Completado')


def resume_jobs():
    """Un reinicio a medias no pierde nada: lo guardado no se vuelve a pedir."""
    with db() as con:
        rows = con.execute("""SELECT slug, status FROM labels WHERE status IN ('queued','running','youtube')
                              ORDER BY status = 'running' DESC, updated""").fetchall()
    for r in rows:
        if r['status'] == 'youtube':
            _push(_yt, _run_videos, r['slug'])
        else:
            _push(_data, _run_catalog, r['slug'])


# ── Lecturas para la web ──────────────────────────────────────────────────────
def list_labels():
    with db() as con:
        labels = con.execute('SELECT * FROM labels ORDER BY name COLLATE NOCASE').fetchall()
        counts = con.execute('SELECT label_slug, rtype, COUNT(*) n FROM releases GROUP BY 1, 2').fetchall()
    types = {}
    for c in counts:
        types.setdefault(c['label_slug'], {})[c['rtype'] or 'Unknown'] = c['n']
    return [{'key': l['slug'], 'name': l['name'], 'status': l['status'], 'error': l['error'],
             'updated': time.strftime('%Y-%m-%d', time.gmtime(l['updated'])) if l['updated'] else '',
             'types': types.get(l['slug'], {}), 'count': sum(types.get(l['slug'], {}).values())} for l in labels]


def _covers(row):
    """Portadas por orden de preferencia; la web prueba la siguiente si una falla."""
    urls = [row['cover']]
    if row['mb_rgid']:
        urls.append(f"https://coverartarchive.org/release-group/{row['mb_rgid']}/front-500")
    urls.append(row['thumb'])
    return [u for u in urls if u]


def _links(row):
    return {'musicbrainz': f"https://musicbrainz.org/release-group/{row['mb_rgid']}" if row['mb_rgid'] else None,
            'discogs': f"https://www.discogs.com/release/{row['discogs_id']}" if row['discogs_id'] else None}


def label_data(slug):
    with db() as con:
        label = con.execute('SELECT * FROM labels WHERE slug=?', (slug,)).fetchone()
        if not label:
            return None
        rows = con.execute("""SELECT r.*, a.artist, a.album, a.listeners, a.playcount, a.tags, a.cover, a.videos,
                                     a.custom, a.lastfm_at
                              FROM releases r JOIN albums a USING (album_key) WHERE r.label_slug=?""",
                           (slug,)).fetchall()
    return {
        'label': {'slug': slug, 'name': label['name'], 'status': label['status'], 'error': label['error'],
                  'types': json.loads(label['sources']).get('types'),
                  'info': json.loads(label['info'] or '{}')},
        'albums': [{'key': r['album_key'], 'artist': r['artist'], 'album': r['album'], 'rtype': r['rtype'] or 'Unknown',
                    'year': r['year'], 'date': r['date'], 'catno': r['catno'], 'covers': _covers(r),
                    'listeners': r['listeners'], 'playcount': r['playcount'], 'tags': json.loads(r['tags'] or '[]'),
                    'videos': json.loads(r['videos']) if r['videos'] is not None else None,
                    'custom': json.loads(r['custom'] or '[]'), 'links': _links(r),
                    'has_lastfm': bool(r['lastfm_at'])} for r in rows],
    }


def album_detail(key, rtype='Album', refresh=False, label=''):
    """Toda la información de un disco. Lo que aún no esté se busca ahora mismo."""
    with db() as con:
        row = con.execute('SELECT * FROM albums WHERE album_key=?', (key,)).fetchone()
    if not row:
        return None
    errors = []
    try:
        fill_lastfm(key, row['artist'], row['album'], force=refresh)
    except LastfmError as e:
        errors.append(f'Last.fm: {e}')
    try:
        fill_videos(key, row['artist'], row['album'], rtype, force=refresh, label=label)
    except Exception as e:
        errors.append(f'YouTube: {e}')
    with db() as con:
        row = con.execute('SELECT * FROM albums WHERE album_key=?', (key,)).fetchone()
        artist = con.execute('SELECT info FROM artists WHERE artist_key=?', (norm(row['artist']),)).fetchone()
    return {'lastfm': json.loads(row['lastfm'] or '{}'), 'artist': json.loads(artist['info'] or '{}') if artist else {},
            'videos': json.loads(row['videos']) if row['videos'] is not None else None,
            'custom': json.loads(row['custom'] or '[]'), 'errors': errors}


def load_groups():
    try:
        with open(GROUPS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def save_groups(groups):
    clean = []
    for g in groups:
        if not isinstance(g, dict) or not str(g.get('name') or '').strip():
            continue
        members = [m for m in (g.get('members') or []) if isinstance(m, str)]
        clean.append({'id': str(g.get('id') or ''), 'name': str(g['name']).strip(),
                      'members': list(dict.fromkeys(members))})
    tmp = GROUPS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    os.replace(tmp, GROUPS_FILE)
    return clean


def create_label(name, mb_ids, discogs_ids, types=None):
    slug = slugify(name)
    with db() as con:
        prev = con.execute('SELECT created FROM labels WHERE slug=?', (slug,)).fetchone()
        con.execute("""INSERT OR REPLACE INTO labels (slug, name, sources, status, created, updated)
                       VALUES (?,?,?,'queued',?,?)""",
                    (slug, name, json.dumps({'mb': mb_ids, 'discogs': discogs_ids, 'types': types}),
                     prev['created'] if prev else time.time(), time.time()))
    return slug


# ── Panel de configuración (⚙), mismo patrón que el resto de apps ─────────────
def _write_env_file(path, updates):
    lines = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            lines = f.readlines()
    seen, out = set(), []
    for line in lines:
        s = line.strip()
        k = s.split('=', 1)[0].strip() if s and not s.startswith('#') and '=' in s else None
        if k in updates:
            out.append(f'{k}={updates[k]}\n')
            seen.add(k)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            if out and not out[-1].endswith('\n'):
                out[-1] += '\n'
            out.append(f'{k}={v}\n')
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(out)


def _settings_value(name):
    return _read_env_file(SETTINGS_ENV_PATH).get(name, os.environ.get(name, ''))


# ── HTTP ──────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, body, ctype, code=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(json.dumps(data, ensure_ascii=False), 'application/json; charset=utf-8', code)

    def do_GET(self):
        url = urlparse(self.path)
        path, qs = url.path, parse_qs(url.query)
        arg = lambda k: qs.get(k, [''])[0].strip()

        if path in STATIC_FILES:
            fname, ctype = STATIC_FILES[path]
            with open(os.path.join(STATIC_DIR, fname), 'rb') as f:
                self._send(f.read(), ctype)
            return
        if path == '/api/switch':
            self._json(switch_items())
            return
        if path == '/api/search':
            if not arg('q'):
                self._json({'error': 'q requerido'}, 400)
                return
            self._json(search_labels(arg('q')))
            return
        if path == '/api/sources':
            self._json(list_labels())
            return
        if path == '/api/groups':
            self._json(load_groups())
            return
        if path.startswith('/api/data/'):
            data = label_data(unquote(path[len('/api/data/'):]))
            self._json(data if data else {'error': 'No existe'}, 200 if data else 404)
            return
        if path == '/api/album':
            data = album_detail(arg('key'), arg('rtype') or 'Album', arg('refresh') == '1', arg('label'))
            self._json(data if data else {'error': 'No existe'}, 200 if data else 404)
            return
        if path == '/api/job':
            self._json(job_state())
            return
        self._json({'error': 'No existe'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        except ValueError as e:
            self._json({'error': str(e)}, 400)
            return

        if path == '/api/fetch':
            slug = d.get('slug')
            # Sin «types» (o con null) el sello trae todas las secciones
            types = d.get('types')
            if types is not None and not (isinstance(types, list) and types and set(types) <= set(RTYPES)):
                self._json({'error': 'Elige al menos una sección'}, 400)
                return
            if not slug:
                name = str(d.get('name') or '').strip()
                mb_ids = [str(x) for x in d.get('mb') or []]
                discogs_ids = [int(x) for x in d.get('discogs') or []]
                if not name or not (mb_ids or discogs_ids):
                    self._json({'error': 'Elige al menos un sello'}, 400)
                    return
                slug = create_label(name, mb_ids, discogs_ids, types)
            else:
                _yt_wake.set()   # ↻ reintenta ya tras un bloqueo; añadir uno nuevo no
                with db() as con:
                    row = con.execute('SELECT sources FROM labels WHERE slug=?', (slug,)).fetchone()
                    if row and 'types' in d:
                        con.execute('UPDATE labels SET sources=? WHERE slug=?',
                                    (json.dumps({**json.loads(row['sources']), 'types': types}), slug))
                    con.execute("UPDATE labels SET status='queued' WHERE slug=?", (slug,))
                if not row:
                    self._json({'error': 'No existe'}, 404)
                    return
            enqueue(slug)
            self._json({'ok': True, 'slug': slug})
            return
        if path == '/api/delete':
            with _job_lock:
                busy = d.get('slug') and d['slug'] in (_data['slug'], _yt['slug'])
                if not busy:
                    for stage in (_data, _yt):
                        if d.get('slug') in stage['queue']:
                            stage['queue'].remove(d['slug'])
            if busy:
                self._json({'error': 'Se está actualizando; espera a que termine'}, 409)
                return
            with db() as con:
                con.execute('DELETE FROM labels WHERE slug=?', (d.get('slug'),))
            self._json({'ok': True})
            return
        if path == '/api/groups':
            if not isinstance(d.get('groups'), list):
                self._json({'error': 'groups requerido'}, 400)
                return
            self._json({'ok': True, 'groups': save_groups(d['groups'])})
            return
        if path == '/api/links':
            urls = [u.strip() for u in d.get('urls') or [] if isinstance(u, str) and u.strip()]
            if any(not u.startswith(('https://', 'http://')) for u in urls):
                self._json({'error': 'Enlace no válido'}, 400)
                return
            with db() as con:
                n = con.execute('UPDATE albums SET custom=? WHERE album_key=?',
                                (json.dumps(urls), d.get('key'))).rowcount
            self._json({'ok': True, 'urls': urls} if n else {'error': 'No existe'}, 200 if n else 404)
            return
        if path == '/api/settings':
            password = d.get('password') or ''
            if password != SETTINGS_PASSWORD or not SETTINGS_PASSWORD:
                error = 'Contraseña incorrecta' if password else None
                if not SETTINGS_PASSWORD:
                    error = 'Este servicio tiene credenciales pero no hay SETTINGS_PASSWORD configurada. Añádela al .env y reinicia el contenedor.'
                self._json({'requires_password': True, 'authorized': False, 'error': error})
                return
            self._json({'requires_password': True, 'authorized': True,
                        'vars': [{'name': v['name'], 'value': _settings_value(v['name']), 'secret': v['secret'],
                                  'help': v['help']} for v in VARS_SPEC]})
            return
        if path == '/api/settings/save':
            if not SETTINGS_PASSWORD or (d.get('password') or '') != SETTINGS_PASSWORD:
                self._json({'error': 'Contraseña incorrecta'}, 403)
                return
            known = {v['name'] for v in VARS_SPEC}
            updates = {k: v for k, v in (d.get('values') or {}).items() if k in known}
            if not updates:
                self._json({'error': 'Nada que guardar'}, 400)
                return
            _write_env_file(SETTINGS_ENV_PATH, updates)
            self._json({'ok': True, 'message': 'Guardado. Reinicia el contenedor para aplicar los cambios.'})
            return
        self._json({'error': 'No existe'}, 404)


if __name__ == '__main__':
    init_db()
    resume_jobs()
    print(f'Charts de sellos → http://localhost:{PORT}', flush=True)
    ThreadingHTTPServer(('', PORT), Handler).serve_forever()
