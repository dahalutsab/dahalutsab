"""Generate assets/telemetry-{dark,light}.svg for the profile README.

Live mode  : python .github/scripts/telemetry.py          (needs GITHUB_TOKEN, run by the workflow)
Seed mode  : python .github/scripts/telemetry.py --seed   (placeholder numbers, no network calls)

Text is converted to outlines with Barlow, so the SVGs look identical everywhere
(GitHub does not load web fonts inside <img> SVGs).
"""
import datetime as dt
import json
import os
import sys
import urllib.request

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ROOT_DIR') or os.path.abspath(os.path.join(HERE, '..', '..'))
ASSETS = os.path.join(ROOT, 'assets')
FONT_DIR = os.environ.get('FONT_DIR') or os.path.join(HERE, 'fonts')
USER = os.environ.get('GH_USER', 'dahalutsab')
TOKEN = os.environ.get('STATS_TOKEN') or os.environ.get('GITHUB_TOKEN')

FONT_URLS = {
    'BarlowCondensed-SemiBold.ttf': 'ofl/barlowcondensed/BarlowCondensed-SemiBold.ttf',
    'Barlow-Regular.ttf': 'ofl/barlow/Barlow-Regular.ttf',
    'Barlow-Medium.ttf': 'ofl/barlow/Barlow-Medium.ttf',
}


def ensure_fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for name, path in FONT_URLS.items():
        dest = os.path.join(FONT_DIR, name)
        if not os.path.exists(dest):
            urllib.request.urlretrieve(f'https://raw.githubusercontent.com/google/fonts/main/{path}', dest)


def nt(v):
    s = f'{v:.2f}'.rstrip('0').rstrip('.')
    return s if s not in ('-0', '') else '0'


class Font:
    def __init__(self, path):
        self.tt = TTFont(path)
        self.gs = self.tt.getGlyphSet()
        self.cmap = self.tt.getBestCmap()
        self.upm = self.tt['head'].unitsPerEm
        self.hmtx = self.tt['hmtx']

    def g(self, ch):
        n = self.cmap.get(ord(ch))
        if n is None:
            n = self.cmap.get(ord('?'))
        return n

    def width(self, text, size, ls=0.0):
        if not text:
            return 0.0
        return sum(self.hmtx[self.g(c)][0] * size / self.upm + ls for c in text) - ls

    def path(self, text, x, y, size, ls=0.0, anchor='start'):
        w = self.width(text, size, ls)
        if anchor == 'middle':
            x -= w / 2
        elif anchor == 'end':
            x -= w
        s = size / self.upm
        cx, d = x, []
        for ch in text:
            n = self.g(ch)
            pen = SVGPathPen(self.gs, ntos=nt)
            self.gs[n].draw(TransformPen(pen, (s, 0, 0, -s, cx, y)))
            c = pen.getCommands()
            if c:
                d.append(c)
            cx += self.hmtx[n][0] * s + ls
        return ' '.join(d)


PAL = {
    'dark': dict(text='#E8EAED', muted='#9AA1AB', dim='#737B86', label='#7F9DC9', border='#2C3139',
                 cross='#B9C0CA', accent='#8DAED8',
                 shades=['#8DAED8', '#6F93BF', '#54759F', '#3F5C80', '#2A4360']),
    'light': dict(text='#1F2328', muted='#57606A', dim='#6E7781', label='#3F6BA5', border='#D0D7DE',
                  cross='#57606A', accent='#3F6BA5',
                  shades=['#3F6BA5', '#5F86B9', '#88A8D0', '#B0C7E3', '#D5E2F2']),
}


# ------------------------------------------------------------------ data
def gql(query, variables):
    req = urllib.request.Request(
        'https://api.github.com/graphql',
        data=json.dumps({'query': query, 'variables': variables}).encode(),
        headers={'Authorization': f'Bearer {TOKEN}', 'User-Agent': 'profile-telemetry'})
    out = json.load(urllib.request.urlopen(req, timeout=60))
    if 'errors' in out:
        raise RuntimeError(out['errors'])
    return out['data']


PROFILE_Q = '''query($login:String!){ user(login:$login){ createdAt
  allRepos: repositories(privacy:PUBLIC){ totalCount }
  own: repositories(first:100, ownerAffiliations:OWNER, isFork:false, privacy:PUBLIC){ nodes{ stargazerCount
    languages(first:10, orderBy:{field:SIZE, direction:DESC}){ edges{ size node{ name } } } } } } }'''

CAL_Q = '''query($login:String!,$from:DateTime!,$to:DateTime!){ user(login:$login){
  contributionsCollection(from:$from,to:$to){ contributionCalendar{ weeks{ contributionDays{ date contributionCount } } } } } }'''


def collect_live():
    now = dt.datetime.now(dt.timezone.utc)
    prof = gql(PROFILE_Q, {'login': USER})['user']
    created = dt.datetime.fromisoformat(prof['createdAt'].replace('Z', '+00:00'))
    days = {}
    for y in range(created.year, now.year + 1):
        a = f'{y}-01-01T00:00:00Z'
        b = min(now, dt.datetime(y, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ')
        cal = gql(CAL_Q, {'login': USER, 'from': a, 'to': b})['user']['contributionsCollection']['contributionCalendar']
        for w in cal['weeks']:
            for d in w['contributionDays']:
                days[d['date']] = d['contributionCount']
    langs = {}
    for r in prof['own']['nodes']:
        for e in r['languages']['edges']:
            langs[e['node']['name']] = langs.get(e['node']['name'], 0) + e['size']
    return dict(days=days, repos=prof['allRepos']['totalCount'], langs=langs, today=now.date())


def streaks(days, today):
    ds = sorted(d for d in days if dt.date.fromisoformat(d) <= today)
    total = sum(days[d] for d in ds)
    first = next((d for d in ds if days[d] > 0), None)
    # longest
    best, best_rng, run, start = 0, None, 0, None
    prev = None
    for d in ds:
        if days[d] > 0:
            if run == 0 or prev is None or (dt.date.fromisoformat(d) - dt.date.fromisoformat(prev)).days != 1:
                run, start = 0, d
            run += 1
            prev = d
            if run > best:
                best, best_rng = run, (start, d)
        else:
            run, prev = 0, None
    # current (today may still be empty)
    cur, cur_end = 0, None
    i = len(ds) - 1
    if i >= 0 and days[ds[i]] == 0:
        i -= 1
    while i >= 0 and days[ds[i]] > 0:
        cur += 1
        cur_end = cur_end or ds[i]
        cur_start = ds[i]
        i -= 1
    cur_rng = (cur_start, cur_end) if cur else None
    return total, first, cur, cur_rng, best, best_rng


def fmt(d, year=True):
    x = dt.date.fromisoformat(d)
    s = x.strftime('%b ') + str(x.day)
    return (s + f', {x.year}') if year else s


# ------------------------------------------------------------------ render
def render(theme, F, data):
    p = PAL[theme]
    COND, REG, MED = F
    W, H = 900, 268
    def T(font, text, x, y, size, fill, ls=0.0, anchor='start', upper=False):
        if upper:
            text = text.upper()
        return f'<path d="{font.path(text, x, y, size, ls, anchor)}" fill="{fill}"/>'
    def plus(x, y, l=6):
        return f'<path d="M{x-l} {y}H{x+l}M{x} {y-l}V{y+l}" stroke="{p["cross"]}" stroke-width="1" fill="none"/>'

    b = [f'<rect x="8.5" y="8.5" width="883" height="251" fill="none" stroke="{p["border"]}"/>']
    for x, y in ((8, 8), (892, 8), (8, 260), (892, 260)):
        b.append(plus(x, y))

    colw = 221
    for i in (1, 2, 3):
        b.append(f'<path d="M{8 + colw * i + 0.5} 30V150" stroke="{p["border"]}"/>')
    cols = data['kpis']
    for i, (label, value, sub) in enumerate(cols):
        cx = 8 + colw * i + colw / 2
        b.append(T(MED, label, cx, 46, 11, p['label'], ls=1.6, anchor='middle', upper=True))
        b.append(T(COND, value, cx, 102, 50, p['text'], ls=0.3, anchor='middle'))
        b.append(T(MED, sub, cx, 128, 10.5, p['dim'], ls=1.2, anchor='middle', upper=True))

    b.append(f'<path d="M8 158.5H892" stroke="{p["border"]}"/>')
    b.append(T(MED, 'Top languages', 28, 184, 11, p['label'], ls=1.6, upper=True))
    bx, bw, by = 28, 844, 196
    langs = data['langs']
    if langs:
        x = bx
        for i, (n, pct) in enumerate(langs):
            w = bw * pct / 100
            b.append(f'<rect x="{x:.1f}" y="{by}" width="{max(w - 2, 1):.1f}" height="10" fill="{p["shades"][i]}"/>')
            x += w
        slot = bw / len(langs)
        for i, (n, pct) in enumerate(langs):
            lx = bx + i * slot
            b.append(f'<rect x="{lx:.1f}" y="226" width="8" height="8" fill="{p["shades"][i]}"/>')
            b.append(T(REG, n, lx + 16, 234, 14, p['text']))
            wv = REG.width(n, 14)
            b.append(T(MED, f'{pct:.1f}%', lx + 16 + wv + 8, 234, 11, p['dim'], ls=0.6))
    else:
        b.append(f'<rect x="{bx + 0.5}" y="{by + 0.5}" width="{bw - 1}" height="9" fill="none" stroke="{p["border"]}"/>')
        b.append(T(MED, 'Syncing on first workflow run', bx, 234, 11, p['dim'], ls=1.4, upper=True))

    label = 'GitHub telemetry for Utsab Dahal: ' + ', '.join(f'{v} {l.lower()}' for l, v, _ in cols)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="{label}"><title>{label}</title>{"".join(b)}</svg>')


def main():
    ensure_fonts()
    F = (Font(os.path.join(FONT_DIR, 'BarlowCondensed-SemiBold.ttf')),
         Font(os.path.join(FONT_DIR, 'Barlow-Regular.ttf')),
         Font(os.path.join(FONT_DIR, 'Barlow-Medium.ttf')))
    if '--seed' in sys.argv:
        data = dict(kpis=[
            ('Total contributions', '1,588', 'Apr 14, 2023 – present'),
            ('Current streak', '1', 'Sep 19'),
            ('Longest streak', '13', 'Jan 20, 2024 – Feb 1, 2024'),
            ('Public repositories', '12', 'On GitHub'),
        ], langs=[])
    else:
        raw = collect_live()
        total, first, cur, cur_rng, best, best_rng = streaks(raw['days'], raw['today'])
        tl = sum(raw['langs'].values()) or 1
        top = sorted(raw['langs'].items(), key=lambda kv: -kv[1])[:5]
        langs = [(n, v * 100 / tl) for n, v in top]
        cur_sub = ('—' if not cur_rng else (fmt(cur_rng[0], False) if cur_rng[0] == cur_rng[1]
                   else f'{fmt(cur_rng[0], False)} – {fmt(cur_rng[1], False)}'))
        data = dict(kpis=[
            ('Total contributions', f'{total:,}', f'{fmt(first)} – present' if first else 'No activity yet'),
            ('Current streak', str(cur), cur_sub),
            ('Longest streak', str(best), f'{fmt(best_rng[0])} – {fmt(best_rng[1])}' if best_rng else '—'),
            ('Public repositories', str(raw['repos']), 'On GitHub'),
        ], langs=langs)
    os.makedirs(ASSETS, exist_ok=True)
    for theme in PAL:
        with open(os.path.join(ASSETS, f'telemetry-{theme}.svg'), 'w', encoding='utf-8') as f:
            f.write(render(theme, F, data))
    print('telemetry written', data['kpis'][0][1])


if __name__ == '__main__':
    main()
