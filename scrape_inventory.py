#!/usr/bin/env python3
"""
JaysonSellsCars inventory sync
Reads Valley Mitsubishi's public inventory pages and writes inventory.json,
which the Test Drive and Contact Us forms load automatically.

Uses only Python's standard library. Run it yourself with:
    python scrape_inventory.py            (writes inventory.json and inventory-report.txt)
    python scrape_inventory.py --dry-run  (prints what it found, writes nothing)

Safety: if a run finds far fewer vehicles than the last good file (or none),
it refuses to overwrite inventory.json, so a website change can never wipe
out the list on your forms.
"""
import argparse, datetime, json, os, re, sys, time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BASE = os.environ.get('INVENTORY_BASE', 'https://www.valleymitsubishi.ca').rstrip('/')
LIST_PAGES = [
    '/new/inventory/Mitsubishi-Outlander.html',
    '/new/inventory/Mitsubishi-Outlander_PHEV.html',
    '/new/inventory/Mitsubishi-Eclipse_Cross.html',
    '/new/inventory/Mitsubishi-RVR.html',
    '/new/inventory/search.html',
    '/used/search.html',
]
EXCLUDE_SLUG = re.compile(r'G_?310|Motorcycle', re.I)       # motorcycles can't be test driven
USER_AGENT = 'JaysonSellsCars-inventory-sync/1.0 (+info@jaysonsellscarsandtrucks.ca)'
DELAY = float(os.environ.get('INVENTORY_DELAY', '1.0'))      # seconds between page loads, be polite
MAX_LIST_PAGES = 15

LISTING_RE = re.compile(r'/(new/inventory|used)/(\d{4})-([A-Za-z0-9_\-]+?)-id(\d+)\.html', re.I)
VIN_RE = re.compile(r'\b([A-HJ-NPR-Z0-9]{17})\b')
COLOURS = ['white', 'black', 'silver', 'grey', 'gray', 'red', 'blue', 'orange', 'green', 'brown', 'beige', 'gold', 'yellow', 'purple', 'bronze', 'copper', 'maroon', 'burgundy', 'tan']
MODEL_FIX = {'MX 30 EV': 'MX-30 EV', 'CR V': 'CR-V', 'CX 9': 'CX-9', 'CX 5': 'CX-5', 'HR V': 'HR-V'}
UPPER = {'es', 'se', 'sel', 'le', 'gt', 'gts', 'awc', 's-awc', 'fwd', 'awd', 'rwd', '4wd', '2wd', 'cvt', 'sx', 'lx', 'ex', 'rs', 'sxt', 'sr', 'xle', 'xse', 'ev', 'phev', 'suv', 'at4', 'z71', 'gls', 'gl', 'sv', 'sl', 'se', 'ltz', 'lt', 'ls', 'rt', 'gt-line'}


# ---------------------------------------------------------------- fetching
def fetch(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml'})
            with urlopen(req, timeout=30) as r:
                return r.read().decode(r.headers.get_content_charset() or 'utf-8', 'replace')
        except Exception as e:                      # noqa
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError('Could not load %s (%s)' % (url, last))


_pw = {}
def fetch_rendered(url):
    """Fallback for pages built by JavaScript. Needs: pip install playwright && playwright install chromium"""
    from playwright.sync_api import sync_playwright
    if 'p' not in _pw:
        _pw['p'] = sync_playwright().start()
        _pw['b'] = _pw['p'].chromium.launch()
    page = _pw['b'].new_page(user_agent=USER_AGENT)
    try:
        page.goto(url, wait_until='networkidle', timeout=45000)
        for _ in range(6):                          # scroll to trigger lazy loading
            page.mouse.wheel(0, 4000); page.wait_for_timeout(500)
        return page.content()
    finally:
        page.close()


def get_html(url, render):
    return fetch_rendered(url) if render else fetch(url)


# ---------------------------------------------------------------- parsing
class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.text, self.ld, self.h1, self.title = [], [], [], [], ''
        self._skip = 0; self._in_ld = False; self._in_h1 = False; self._in_title = False; self._a = None; self._buf = ''

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style', 'noscript'):
            self._skip += 1
            self._in_ld = tag == 'script' and 'ld+json' in (a.get('type') or '')
            self._buf = ''
        elif tag == 'h1': self._in_h1 = True; self.h1.append('')
        elif tag == 'title': self._in_title = True
        elif tag == 'a' and a.get('href'): self._a = [a['href'], '']
        if tag in ('br', 'p', 'div', 'li', 'tr', 'td', 'th', 'dt', 'dd', 'span', 'h1', 'h2', 'h3', 'h4'):
            self.text.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self._skip = max(0, self._skip - 1)
            if self._in_ld and self._buf.strip(): self.ld.append(self._buf)
            self._in_ld = False
        elif tag == 'h1': self._in_h1 = False
        elif tag == 'title': self._in_title = False
        elif tag == 'a' and self._a: self.links.append(tuple(self._a)); self._a = None

    def handle_data(self, d):
        if self._skip:
            if self._in_ld: self._buf += d
            return
        self.text.append(d)
        if self._in_h1 and self.h1: self.h1[-1] += d
        if self._in_title: self.title += d
        if self._a: self._a[1] += d

    def plain(self):
        return re.sub(r'\s+', ' ', ''.join(self.text)).strip()


def parse(html):
    p = PageParser(); p.feed(html); return p


def listing_links(page, base_url):
    found = {}
    for href, _ in page.links:
        u = urljoin(base_url, href.split('#')[0])
        m = LISTING_RE.search(urlparse(u).path)
        if m:
            found[m.group(4)] = (u.split('?')[0], m.group(1).lower(), m.group(2), m.group(3), m.group(4))
    return found


def next_pages(page, base_url, seen):
    out = []
    for href, text in page.links:
        if re.fullmatch(r'\s*(next|next page|›|»|>)\s*', text or '', re.I) or re.search(r'[?&]page=\d+', href):
            u = urljoin(base_url, href)
            if urlparse(u).netloc == urlparse(BASE).netloc and u not in seen: out.append(u)
    return out


def num(s):
    try: return int(re.sub(r'[^\d]', '', str(s)) or 0)
    except ValueError: return 0


def walk_ld(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk_ld(v)
    elif isinstance(o, list):
        for v in o: yield from walk_ld(v)


def from_ld(page):
    out = {}
    for raw in page.ld:
        try: data = json.loads(raw)
        except ValueError: continue
        for o in walk_ld(data):
            vin = o.get('vehicleIdentificationNumber')
            if vin and 'vin' not in out: out['vin'] = str(vin).upper()
            if o.get('sku') and 'stock' not in out: out['stock'] = str(o['sku'])
            col = o.get('color') or o.get('vehicleColor')
            if col and 'colour' not in out: out['colour'] = str(col)
            off = o.get('offers')
            for of in (off if isinstance(off, list) else [off] if isinstance(off, dict) else []):
                pv = str(of.get('price') or '').replace(',', '').replace('$', '').strip()
                if re.fullmatch(r'\d+(\.\d+)?', pv) and 'price' not in out: out['price'] = int(round(float(pv)))
            mil = o.get('mileageFromOdometer')
            if isinstance(mil, dict) and mil.get('value') is not None and 'km' not in out:
                mv = str(mil['value']).replace(',', '').strip()
                if re.fullmatch(r'\d+(\.\d+)?', mv): out['km'] = int(round(float(mv)))
    return out


def norm_colour(raw):
    raw = re.sub(r'\s+', ' ', raw or '').strip(' :-/')
    if not raw: return ''
    low = raw.lower()
    for c in COLOURS:
        if re.search(r'\b%s\b' % c, low):
            pre = 'Dark ' if low.startswith('dark') else 'Light ' if low.startswith('light') else ''
            return (pre + c).capitalize() if not pre else pre + c
    return raw.title()[:20]


def norm_trim(t):
    out = []
    for w in t.split():
        out.append(w.upper() if w.lower() in UPPER else w if (w.isupper() or re.search(r'\d', w)) else w.capitalize())
    return ' '.join(out).strip(' -,')


def parse_detail(html, url, kind, year, slug, lid):
    page = parse(html)
    text = page.plain()
    d = from_ld(page)

    vin = d.get('vin')
    if not vin:
        m = re.search(r'VIN\s*(?:#|number|no\.?)?\s*[:\-]?\s*([A-HJ-NPR-Z0-9]{17})\b', text, re.I)
        vin = m.group(1).upper() if m else (VIN_RE.search(text.upper()).group(1) if VIN_RE.search(text.upper()) else '')
    stock = d.get('stock')
    m = re.search(r'Stock\s*(?:#|number|no\.?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]{2,11})\b', text, re.I)
    if m and (not stock or stock.upper() != m.group(1).upper()):
        stock = m.group(1)
    stock = (stock or '').upper()

    price = d.get('price', 0)
    if not price:
        m = re.search(r'(?:Internet Price|Our Price|Sale Price|Selling Price|Price)\s*:?\s*\$\s?(\d{1,3}(?:,\d{3})+|\d{4,6})', text, re.I)
        price = num(m.group(1)) if m else 0

    km = 0
    if kind == 'U':
        km = d.get('km', 0)
        if not km:
            m = re.search(r'(?:Mileage|Odometer|Kilometres|Kilometers|KMs?)\s*:?\s*([\d,]{1,9})\s*(?:km|kms)?', text, re.I) or re.search(r'([\d,]{3,9})\s*km\b', text, re.I)
            km = num(m.group(1)) if m else 0

    colour = d.get('colour', '')
    if not colour:
        m = re.search(r'(?:Exterior(?:\s+Colou?r)?|Ext\.?\s*Colou?r)\s*:?\s*([A-Za-z][A-Za-z /\-]{2,28}?)(?=\s+(?:Interior|Int\.|Trans|Engine|Drive|Fuel|Stock|VIN|Body|Mileage|Odometer|$)|\s*[|,]|$)', text, re.I)
        colour = m.group(1) if m else ''
    colour = norm_colour(colour)

    make_raw, _, model_raw = slug.partition('-')
    make = make_raw.replace('_', '-')
    model = model_raw.replace('_', ' ')
    model = MODEL_FIX.get(model, model)
    orig_model = model
    if make == 'Mitsubishi' and model == 'Outlander' and vin.startswith('JA4T5'):
        model = 'Outlander PHEV'           # plug-in hybrid VIN, even when the site lists it as "Outlander"

    title = (page.h1[0] if page.h1 else page.title or '').strip()
    title = re.split(r'\s[|\-–]\s(?:Valley|Kelowna)', title)[0]
    t = re.sub(r'\b%s\b' % year, '', title, count=1)
    t = re.sub(re.escape(make).replace('\\-', '[- ]'), '', t, count=1, flags=re.I)
    t = re.sub(re.escape(orig_model), '', t, count=1, flags=re.I)
    for w in orig_model.replace('-', ' ').split():
        t = re.sub(r'\b%s\b' % re.escape(w), '', t, count=1, flags=re.I)
    t = re.sub(r'\b(new|used|certified|pre-owned|for sale)\b', '', t, flags=re.I)
    trim = norm_trim(re.sub(r'\s+', ' ', t).strip())

    return [stock, vin, int(year), make, model, trim, colour, price, km, kind, slug, int(lid)]


def valid(r):
    stock, vin, year = r[0], r[1], r[2]
    return bool(stock) and bool(re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin or '')) and 1990 <= year <= datetime.date.today().year + 2


# ---------------------------------------------------------------- main
def collect_listings(render):
    listings, seen = {}, set()
    queue = [BASE + p for p in LIST_PAGES]
    n = 0
    while queue and n < MAX_LIST_PAGES + len(LIST_PAGES):
        url = queue.pop(0)
        if url in seen: continue
        seen.add(url); n += 1
        try:
            html = get_html(url, render)
        except Exception as e:                       # noqa
            print('  skipped', url, '-', e); continue
        page = parse(html)
        got = listing_links(page, url)
        if not got and not render:                   # maybe this page is built by JavaScript: try a real browser once
            try:
                page = parse(fetch_rendered(url)); got = listing_links(page, url)
                if got: print('  (needed a real browser for this page)')
            except Exception:                        # noqa  (browser not installed: carry on)
                pass
        print('  %-62s %3d vehicles' % (url.replace(BASE, ''), len(got)))
        listings.update(got)
        queue.extend(next_pages(page, url, seen))
        time.sleep(DELAY)
    return listings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--out', default='inventory.json')
    ap.add_argument('--render', action='store_true', help='load pages in a real browser (for JavaScript-built pages)')
    a = ap.parse_args()

    print('Finding vehicles...')
    render = a.render
    listings = collect_listings(render)
    if not listings and not render:
        print('No vehicle links found in the plain page. Trying again with a real browser...')
        try:
            render = True; listings = collect_listings(True)
        except Exception as e:                       # noqa
            print('Browser mode unavailable:', e)
    if not listings:
        print('ERROR: found no vehicles. The website layout may have changed. Leaving inventory.json untouched.')
        sys.exit(2)

    print('\nReading %d vehicle pages...' % len(listings))
    rows, skipped = [], []
    for i, (lid, (url, k, year, slug, _)) in enumerate(sorted(listings.items()), 1):
        if EXCLUDE_SLUG.search(slug):
            skipped.append((url, 'excluded (motorcycle)')); continue
        try:
            r = parse_detail(get_html(url, render), url, 'N' if k.startswith('new') else 'U', year, slug, lid)
        except Exception as e:                       # noqa
            skipped.append((url, str(e))); continue
        if valid(r): rows.append(r)
        else: skipped.append((url, 'missing stock number or VIN: %r' % (r[:2],)))
        time.sleep(DELAY)

    seen_ids, uniq = set(), []
    for r in rows:
        if r[0] not in seen_ids: seen_ids.add(r[0]); uniq.append(r)
    rows = sorted(uniq, key=lambda r: (r[9] != 'N', r[4], r[7] or 10**9, r[0]))

    new_n = sum(1 for r in rows if r[9] == 'N'); used_n = len(rows) - new_n
    models = {}
    for r in rows: models[(r[9], r[4])] = models.get((r[9], r[4]), 0) + 1
    report = ['Inventory sync report', 'Run: %s' % datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
              'Vehicles: %d  (new %d, used %d)' % (len(rows), new_n, used_n), '']
    for (k, m), c in sorted(models.items(), key=lambda x: (x[0][0] != 'N', -x[1])): report.append('  %-5s %-22s %d' % ('New' if k == 'N' else 'Used', m, c))
    nop = [r[0] for r in rows if not r[7]]; nocol = [r[0] for r in rows if not r[6]]
    if nop: report += ['', 'Without a price (%d): %s' % (len(nop), ', '.join(nop[:30]))]
    if nocol: report += ['Without a colour (%d): %s' % (len(nocol), ', '.join(nocol[:30]))]
    if skipped: report += ['', 'Skipped (%d):'] + ['  %s  %s' % s for s in skipped[:40]]
    if skipped: report[report.index('Skipped (%d):')] = 'Skipped (%d):' % len(skipped)
    print('\n' + '\n'.join(report))

    old = 0
    if os.path.exists(a.out):
        try:
            d = json.load(open(a.out)); old = len(d['units'] if isinstance(d, dict) else d)
        except Exception:                            # noqa
            old = 0
    if len(rows) < 20 or (old and len(rows) < 0.6 * old):
        print('\nERROR: found only %d vehicles (last good file had %d). Refusing to overwrite %s.' % (len(rows), old, a.out))
        sys.exit(3)
    if a.dry_run:
        print('\nDry run: nothing written.'); return
    out = {'asOf': datetime.date.today().isoformat(), 'source': BASE, 'count': len(rows), 'units': rows}
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(os.path.dirname(os.path.abspath(a.out)), 'inventory-report.txt'), 'w') as f:
        f.write('\n'.join(report) + '\n')
    print('\nWrote %s (%d vehicles).' % (a.out, len(rows)))


if __name__ == '__main__':
    main()
