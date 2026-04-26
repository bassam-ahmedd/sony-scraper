import os
import re
import json
import time
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ─── Env ─────────────────────────────────────────────────────────────────────
ZENROWS_KEY = os.environ.get('ZENROWS_API_KEY', '')
GSHEET_ID   = os.environ.get('GSHEET_ID', '')
SA_JSON     = os.environ.get('GOOGLE_SERVICE_ACCOUNT', '')

# ─── Constants ───────────────────────────────────────────────────────────────
SOURCES = ['Our Site (ksa.amt.tv)', 'Qomra', 'Me Stores', 'Abdulwahed']
COMPETITORS = ['Qomra', 'Me Stores', 'Abdulwahed']

URLS = {
    'lenses': {
        'our_site':    'https://ksa.amt.tv/camera-accessories/photography/lenses.html?product_brand=1',
        'qomra':       'https://qomra.pro/en/search?q=le&filters[category_id]=750050316&filters[brand_id]=174800383',
        'mestores':    'https://mestores.com/en_sa/cameras-accessories/lenses?page={page}&brand%5Bfilter%5D=SONY%2C1722',
        'abdulwahed':  'https://www.abdulwahed.com/en/photography-c-868/lenses-c-879',
    },
    'cameras': {
        'our_site':    'https://ksa.amt.tv/camcorders-digital-cameras/photography/digital-camera.html?product_brand=1',
        'qomra':       'https://qomra.pro/en/category/jKQvBD?filters[category_id]=1061595081&filters[brand_id]=174800383',
        'mestores':    'https://mestores.com/en_sa/cameras-accessories/cameras?page={page}&brand%5Bfilter%5D=SONY%2C1722',
        'abdulwahed':  'https://www.abdulwahed.com/en/photography-c-868/cameras-c-869/digital-cameras-c-870',
    },
}

# ─── Product Validation ──────────────────────────────────────────────────────
NON_LENS_KEYWORDS = [
    'lens cap', 'lens hood', 'uv filter', 'cpl filter', 'nd filter',
    'cleaning kit', 'tripod', 'flash', 'battery', 'charger', 'bag',
    'strap', 'memory card', 'sd card', 'cable', 'adapter ring',
    'camera body', 'bundle', 'cine lens', 'cinema lens', 'body only',
    'sigma brush', 'beauty', 'skin', 'cream',
]

LENS_KEYWORDS = [
    'mm', 'f/', 'f1.', 'f2.', 'f4', 'f5.', 'f6.',
    'g master', 'g lens', 'zeiss', 'fe ', 'e-mount', 'e mount',
    'sel', 'macro', 'fisheye', 'zoom lens', 'prime lens',
]

NON_CAMERA_KEYWORDS = [
    'lens', 'tripod', 'bag', 'strap', 'battery', 'charger',
    'memory card', 'sd card', 'flash', 'adapter', 'filter',
    'cleaning', 'bundle with lens', 'lens cap', 'hood',
]

CAMERA_KEYWORDS = [
    'alpha', 'a7', 'a9', 'a6', 'a1 ', 'zv-', 'fx3', 'fx6', 'fx30',
    'ilce', 'ilc-', 'dsc-', 'cyber-shot', 'mirrorless',
    'digital camera', 'full frame', 'aps-c', 'a7r', 'a7s', 'a7c',
    'a5100', 'a6000', 'a6100', 'a6400', 'a6600', 'a6700',
]


def normalize(s):
    return s.lower().strip()


def translate_eastern(s):
    eastern = '٠١٢٣٤٥٦٧٨٩'
    for i, ch in enumerate(eastern):
        s = s.replace(ch, str(i))
    return s.replace('٬', ',')


def is_sony_lens(name):
    n = normalize(name)
    for kw in NON_LENS_KEYWORDS:
        if kw in n:
            return False
    has_focal = bool(re.search(r'\d+\s*mm', n)) or bool(re.search(r'\d{2,3}[-/]\d', n))
    has_lens_kw = any(kw in n for kw in LENS_KEYWORDS)
    return has_focal or has_lens_kw


def is_sony_camera(name):
    n = normalize(name)
    for kw in NON_CAMERA_KEYWORDS:
        if kw in n:
            return False
    return any(kw in n for kw in CAMERA_KEYWORDS)


def fix_arabic_name(name, url, validator):
    if any('\u0600' <= c <= '\u06FF' for c in name):
        slug = url.rstrip('/').split('/')[-1]
        slug_name = slug.replace('-', ' ').title().replace('.Html', '').replace('.html', '').strip()
        if validator(slug_name):
            return slug_name
    return name


def parse_price(text):
    text = translate_eastern(text)
    text = re.sub(r'[^\d.,]', '', text).replace(',', '')
    try:
        return float(text)
    except Exception:
        return None


# ─── ZenRows ────────────────────────────────────────────────────────────────
def zenrows_get(url, wait=8000, retries=2):
    params = {
        'apikey': ZENROWS_KEY,
        'url': url,
        'antibot': 'true',
        'premium_proxy': 'true',
        'js_render': 'true',
        'proxy_country': 'sa',
        'wait': str(wait),
    }
    for attempt in range(retries + 1):
        try:
            resp = requests.get('https://api.zenrows.com/v1/', params=params, timeout=120)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            log.warning(f'ZenRows attempt {attempt+1} failed for {url}: {e}')
            if attempt < retries:
                time.sleep(5)
    return None


# ─── Parsers ─────────────────────────────────────────────────────────────────
def parse_our_site(product_type):
    """Scrape ksa.amt.tv — Magento store"""
    base_url = URLS[product_type]['our_site']
    products = []
    seen = set()
    page = 1
    while page <= 20:
        url = f"{base_url}&p={page}" if page > 1 else base_url
        log.info(f'[Our Site] Fetching page {page}: {url}')
        html = zenrows_get(url, wait=8000)
        if not html:
            break
        soup = BeautifulSoup(html, 'lxml')
        items = soup.select('li.product-item')
        if not items:
            items = soup.select('.product-item-info')
        if not items:
            break
        new_found = 0
        for item in items:
            try:
                name_el = item.select_one('.product-item-name a, .product-item-link')
                price_el = item.select_one('.price')
                link_el  = item.select_one('a.product-item-link, .product-item-name a')
                if not name_el or not price_el:
                    continue
                name  = name_el.get_text(strip=True)
                price = parse_price(price_el.get_text(strip=True))
                link  = link_el['href'] if link_el and link_el.get('href') else ''
                if link in seen:
                    continue
                seen.add(link)
                validator = is_sony_lens if product_type == 'lenses' else is_sony_camera
                name = fix_arabic_name(name, link, validator)
                if not validator(name):
                    continue
                avail_el = item.select_one('.stock, .availability')
                avail = 'In Stock' if avail_el and 'in-stock' in (avail_el.get('class') or []) else 'Check Site'
                products.append({'name': name, 'price': price, 'availability': avail, 'url': link})
                new_found += 1
            except Exception as e:
                log.debug(f'[Our Site] item parse error: {e}')
        if new_found == 0:
            break
        page += 1
    log.info(f'[Our Site] {product_type}: {len(products)} products')
    return products


def parse_qomra(product_type):
    """Scrape qomra.pro"""
    base_url = URLS[product_type]['qomra']
    products = []
    seen = set()
    page = 1
    while page <= 20:
        url = f"{base_url}&page={page}" if page > 1 else base_url
        log.info(f'[Qomra] Fetching page {page}: {url}')
        html = zenrows_get(url, wait=10000)
        if not html:
            break
        soup = BeautifulSoup(html, 'lxml')
        items = soup.select('.product-card, .product-item, [class*="product"]')
        if not items:
            break
        new_found = 0
        for item in items:
            try:
                name_el  = item.select_one('h2, h3, .product-title, .product-name, [class*="title"], [class*="name"]')
                price_el = item.select_one('.price, [class*="price"]')
                link_el  = item.select_one('a[href]')
                if not name_el or not price_el:
                    continue
                name  = name_el.get_text(strip=True)
                price = parse_price(price_el.get_text(strip=True))
                link  = link_el['href'] if link_el else ''
                if not link.startswith('http'):
                    link = 'https://qomra.pro' + link
                if link in seen:
                    continue
                seen.add(link)
                validator = is_sony_lens if product_type == 'lenses' else is_sony_camera
                name = fix_arabic_name(name, link, validator)
                if not validator(name):
                    continue
                avail_el = item.select_one('[class*="stock"], [class*="avail"]')
                avail = 'Out of Stock' if avail_el and 'out' in avail_el.get_text(strip=True).lower() else 'In Stock'
                products.append({'name': name, 'price': price, 'availability': avail, 'url': link})
                new_found += 1
            except Exception as e:
                log.debug(f'[Qomra] item parse error: {e}')
        if new_found == 0:
            break
        page += 1
    log.info(f'[Qomra] {product_type}: {len(products)} products')
    return products


def parse_mestores(product_type):
    """Scrape mestores.com — paginated with page= param"""
    base_url = URLS[product_type]['mestores']
    products = []
    seen = set()
    page = 1
    while page <= 20:
        url = base_url.format(page=page)
        log.info(f'[Me Stores] Fetching page {page}: {url}')
        html = zenrows_get(url, wait=8000)
        if not html:
            break
        soup = BeautifulSoup(html, 'lxml')
        items = soup.select('.product-item, .product-card, [class*="product-item"]')
        if not items:
            break
        new_found = 0
        for item in items:
            try:
                name_el  = item.select_one('.product-item__title, .product-name, h2, h3, [class*="title"]')
                price_el = item.select_one('.price, .product-price, [class*="price"]')
                link_el  = item.select_one('a[href]')
                if not name_el or not price_el:
                    continue
                name  = name_el.get_text(strip=True)
                price = parse_price(price_el.get_text(strip=True))
                link  = link_el['href'] if link_el else ''
                if not link.startswith('http'):
                    link = 'https://mestores.com' + link
                if link in seen:
                    continue
                seen.add(link)
                validator = is_sony_lens if product_type == 'lenses' else is_sony_camera
                name = fix_arabic_name(name, link, validator)
                if not validator(name):
                    continue
                avail_el = item.select_one('[class*="stock"], [class*="avail"], [class*="sold"]')
                avail = 'Out of Stock' if avail_el and ('out' in avail_el.get_text(strip=True).lower() or 'sold' in avail_el.get_text(strip=True).lower()) else 'In Stock'
                products.append({'name': name, 'price': price, 'availability': avail, 'url': link})
                new_found += 1
            except Exception as e:
                log.debug(f'[Me Stores] item parse error: {e}')
        if new_found == 0:
            break
        page += 1
    log.info(f'[Me Stores] {product_type}: {len(products)} products')
    return products


def parse_abdulwahed(product_type):
    """Scrape abdulwahed.com — filter Sony products by name"""
    base_url = URLS[product_type]['abdulwahed']
    products = []
    seen = set()
    page = 1
    while page <= 20:
        url = f"{base_url}?page={page}" if page > 1 else base_url
        log.info(f'[Abdulwahed] Fetching page {page}: {url}')
        html = zenrows_get(url, wait=8000)
        if not html:
            break
        soup = BeautifulSoup(html, 'lxml')
        items = soup.select('.product-item, .product-card, .product, [class*="product-item"]')
        if not items:
            break
        new_found = 0
        for item in items:
            try:
                name_el  = item.select_one('h2, h3, .product-name, .product-title, [class*="name"], [class*="title"]')
                price_el = item.select_one('.price, [class*="price"]')
                link_el  = item.select_one('a[href]')
                if not name_el or not price_el:
                    continue
                name  = name_el.get_text(strip=True)
                price = parse_price(price_el.get_text(strip=True))
                link  = link_el['href'] if link_el else ''
                if not link.startswith('http'):
                    link = 'https://www.abdulwahed.com' + link
                if link in seen:
                    continue
                seen.add(link)
                # Must be Sony
                if 'sony' not in normalize(name) and 'sony' not in normalize(link):
                    # check brand element
                    brand_el = item.select_one('[class*="brand"], [class*="manufacturer"]')
                    if not brand_el or 'sony' not in normalize(brand_el.get_text()):
                        continue
                validator = is_sony_lens if product_type == 'lenses' else is_sony_camera
                name = fix_arabic_name(name, link, validator)
                if not validator(name):
                    continue
                avail_el = item.select_one('[class*="stock"], [class*="avail"]')
                avail = 'Out of Stock' if avail_el and 'out' in avail_el.get_text(strip=True).lower() else 'In Stock'
                products.append({'name': name, 'price': price, 'availability': avail, 'url': link})
                new_found += 1
            except Exception as e:
                log.debug(f'[Abdulwahed] item parse error: {e}')
        if new_found == 0:
            break
        page += 1
    log.info(f'[Abdulwahed] {product_type}: {len(products)} products')
    return products


# ─── Matching ────────────────────────────────────────────────────────────────
def extract_focal(name):
    m = re.search(r'(\d+)(?:-(\d+))?\s*mm', name, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)) if m.group(2) else int(m.group(1)))
    return None


def extract_aperture(name):
    m = re.search(r'f/?(\d+\.?\d*)', name, re.IGNORECASE)
    return float(m.group(1)) if m else None


def match_lens_score(a, b):
    na, nb = normalize(a), normalize(b)
    score = 0
    fa, fb = extract_focal(na), extract_focal(nb)
    if fa and fb:
        if fa != fb:
            return 0
        score += 80
    aa, ab = extract_aperture(na), extract_aperture(nb)
    if aa and ab:
        if abs(aa - ab) < 0.1:
            score += 30
    mounts = ['fe ', 'e-mount', 'e mount', 'a-mount', 'full frame', 'aps-c']
    for mt in mounts:
        if mt in na and mt in nb:
            score += 25
            break
        elif mt in na or mt in nb:
            pass
    series = ['g master', 'gm', 'g lens', 'zeiss']
    for s in series:
        if s in na and s in nb:
            score += 15
    return score


def match_camera_score(a, b):
    na, nb = normalize(a), normalize(b)
    # extract model numbers
    models_a = set(re.findall(r'a\d[a-z0-9]*|zv-\w+|fx\d+|ilce-\w+|dsc-\w+', na))
    models_b = set(re.findall(r'a\d[a-z0-9]*|zv-\w+|fx\d+|ilce-\w+|dsc-\w+', nb))
    if models_a and models_b and models_a & models_b:
        return 100
    if models_a and models_b:
        return 0
    # fallback word overlap
    words_a = set(na.split())
    words_b = set(nb.split())
    overlap = len(words_a & words_b)
    return min(70, overlap * 15)


def find_match(our_product, competitor_products, product_type):
    best_score = 0
    best_match = None
    scorer = match_lens_score if product_type == 'lenses' else match_camera_score
    for cp in competitor_products:
        score = scorer(our_product['name'], cp['name'])
        if score > best_score:
            best_score = score
            best_match = cp
    if best_score >= 80:
        return best_match
    return None


# ─── Build Comparison Rows ───────────────────────────────────────────────────
def build_rows(our_products, competitor_data, product_type):
    """
    competitor_data = {
        'Qomra': [...],
        'Me Stores': [...],
        'Abdulwahed': [...],
    }
    Returns list of row dicts.
    """
    rows = []
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    used_competitor_urls = {src: set() for src in COMPETITORS}

    for our in our_products:
        row = {
            'timestamp': timestamp,
            'name': our['name'],
            'our_price': our['price'],
            'our_availability': our['availability'],
            'our_url': our['url'],
        }
        prices_for_lowest = []
        if our['price'] and our['availability'] == 'In Stock':
            prices_for_lowest.append(('Our Site (ksa.amt.tv)', our['price'], our['url']))

        for src in COMPETITORS:
            match = find_match(our, competitor_data.get(src, []), product_type)
            if match:
                used_competitor_urls[src].add(match['url'])
                diff = round(match['price'] - our['price'], 2) if match['price'] and our['price'] else None
                if diff is None:
                    status = 'Not listed'
                elif diff > 0:
                    status = 'Cheaper than competitor'
                elif diff < 0:
                    status = 'More expensive'
                else:
                    status = 'Same price'
                row[src] = {
                    'url': match['url'],
                    'price': match['price'],
                    'availability': match['availability'],
                    'diff': diff,
                    'status': status,
                }
                if match['price'] and match['availability'] == 'In Stock':
                    prices_for_lowest.append((src, match['price'], match['url']))
            else:
                row[src] = {'url': '', 'price': None, 'availability': '', 'diff': None, 'status': 'Not listed'}

        # Summary
        if prices_for_lowest:
            cheapest = min(prices_for_lowest, key=lambda x: x[1])
            row['lowest_price']     = cheapest[1]
            row['cheapest_brand']   = cheapest[0]
            row['cheapest_link']    = cheapest[2]
            our_p = our['price'] or 0
            row['our_diff_vs_cheapest'] = round(our_p - cheapest[1], 2)
        else:
            row['lowest_price']          = None
            row['cheapest_brand']        = ''
            row['cheapest_link']         = ''
            row['our_diff_vs_cheapest']  = None

        rows.append(row)

    # Competitor-only products (not matched to any of our products)
    for src in COMPETITORS:
        for cp in competitor_data.get(src, []):
            if cp['url'] in used_competitor_urls[src]:
                continue
            row = {
                'timestamp': timestamp,
                'name': cp['name'],
                'our_price': None,
                'our_availability': 'Not listed',
                'our_url': '',
            }
            for other_src in COMPETITORS:
                if other_src == src:
                    row[other_src] = {
                        'url': cp['url'], 'price': cp['price'],
                        'availability': cp['availability'], 'diff': None, 'status': 'Not listed',
                    }
                else:
                    row[other_src] = {'url': '', 'price': None, 'availability': '', 'diff': None, 'status': 'Not listed'}
            row['lowest_price']         = cp['price']
            row['cheapest_brand']       = src
            row['cheapest_link']        = cp['url']
            row['our_diff_vs_cheapest'] = None
            rows.append(row)

    return rows


# ─── Google Sheets ───────────────────────────────────────────────────────────
def get_gspread_client():
    sa_info = json.loads(SA_JSON)
    scopes  = ['https://www.googleapis.com/auth/spreadsheets',
               'https://www.googleapis.com/auth/drive']
    creds  = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return gspread.authorize(creds)


def row_to_list(row):
    out = [
        row['timestamp'],
        row['name'],
        row.get('our_price', ''),
        row.get('our_availability', ''),
        row.get('our_url', ''),
    ]
    for src in COMPETITORS:
        d = row.get(src, {})
        out += [
            d.get('url', ''),
            d.get('price', ''),
            d.get('availability', ''),
            d.get('diff', ''),
            d.get('status', ''),
        ]
    out += [
        row.get('lowest_price', ''),
        row.get('cheapest_brand', ''),
        row.get('cheapest_link', ''),
        row.get('our_diff_vs_cheapest', ''),
    ]
    return out


GROUP_HEADERS = (
    ['Timestamp', 'Product Name', '', '', ''] +
    ['Our Site (ksa.amt.tv)', '', '', '', ''] +
    ['Qomra', '', '', '', ''] +
    ['Me Stores', '', '', '', ''] +
    ['Abdulwahed', '', '', '', ''] +
    ['Summary', '', '', '']
)

COL_HEADERS = (
    ['Timestamp', 'Product Name', 'Our Price (SAR)', 'Our Availability', 'Our Product URL'] +
    ['Product URL', 'Price (SAR)', 'Availability', 'Price Diff (SAR)', 'Status'] * 3 +
    ['Lowest Price (SAR)', 'Cheapest Brand', 'Cheapest Link', 'Our Price Diff vs Cheapest']
)

SUMMARY_HEADERS = ['Source', 'Total Products', 'Cheaper Than Us', 'More Expensive', 'Same Price', 'Not Listed', 'Updated']


def compute_summary(rows, timestamp):
    summary = []
    for src in SOURCES:
        if src == 'Our Site (ksa.amt.tv)':
            total   = sum(1 for r in rows if r.get('our_price'))
            cheaper = more_exp = same = not_listed = 0
        else:
            total      = sum(1 for r in rows if r.get(src, {}).get('price'))
            cheaper    = sum(1 for r in rows if r.get(src, {}).get('status') == 'Cheaper than competitor')
            more_exp   = sum(1 for r in rows if r.get(src, {}).get('status') == 'More expensive')
            same       = sum(1 for r in rows if r.get(src, {}).get('status') == 'Same price')
            not_listed = sum(1 for r in rows if r.get(src, {}).get('status') == 'Not listed')
        summary.append([src, total, cheaper, more_exp, same, not_listed, timestamp])
    return summary


def color_status_cells(ws, rows):
    """Apply conditional colors to Status columns."""
    status_cols = []  # 1-indexed col positions of Status cells
    # Status is at col 10, 15, 20 (1-indexed), i.e. after 5 our-site cols + each competitor group of 5
    for i, _ in enumerate(COMPETITORS):
        status_cols.append(5 + (i + 1) * 5)  # cols 10, 15, 20

    color_map = {
        'Cheaper than competitor': {'red': 0.20, 'green': 0.73, 'blue': 0.40},
        'More expensive':          {'red': 0.91, 'green': 0.27, 'blue': 0.27},
        'Same price':              {'red': 1.0,  'green': 0.90, 'blue': 0.20},
        'Not listed':              {'red': 0.85, 'green': 0.85, 'blue': 0.85},
    }

    requests_body = []
    for row_idx, row in enumerate(rows):
        sheet_row = row_idx + 3  # data starts at row 3
        for src_idx, src in enumerate(COMPETITORS):
            status = row.get(src, {}).get('status', '')
            color  = color_map.get(status)
            if not color:
                continue
            col = status_cols[src_idx]
            requests_body.append({
                'repeatCell': {
                    'range': {
                        'sheetId': ws.id,
                        'startRowIndex': sheet_row - 1,
                        'endRowIndex': sheet_row,
                        'startColumnIndex': col - 1,
                        'endColumnIndex': col,
                    },
                    'cell': {'userEnteredFormat': {'backgroundColor': color}},
                    'fields': 'userEnteredFormat.backgroundColor',
                }
            })
    return requests_body


def write_sheet(client, product_type, rows):
    tab_name     = 'Lenses'     if product_type == 'lenses'  else 'Cameras'
    summary_name = 'Lenses Summary' if product_type == 'lenses' else 'Cameras Summary'
    timestamp    = datetime.now().strftime('%Y-%m-%d %H:%M')

    sh = client.open_by_key(GSHEET_ID)

    # ── Main tab ──────────────────────────────────────────────────────────
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=500, cols=30)

    ws.clear()
    data = [GROUP_HEADERS, COL_HEADERS] + [row_to_list(r) for r in rows]
    ws.update('A1', data, value_input_option='USER_ENTERED')

    # Color status cells
    color_requests = color_status_cells(ws, rows)
    if color_requests:
        sh.batch_update({'requests': color_requests})

    log.info(f'Written {len(rows)} rows to [{tab_name}]')

    # ── Summary tab ───────────────────────────────────────────────────────
    try:
        ws_s = sh.worksheet(summary_name)
    except gspread.WorksheetNotFound:
        ws_s = sh.add_worksheet(title=summary_name, rows=20, cols=10)

    ws_s.clear()
    summary_rows = compute_summary(rows, timestamp)
    ws_s.update('A1', [SUMMARY_HEADERS] + summary_rows, value_input_option='USER_ENTERED')
    log.info(f'Written summary to [{summary_name}]')


# ─── Main ─────────────────────────────────────────────────────────────────────
def scrape_source(fn, label):
    try:
        return fn()
    except Exception as e:
        log.error(f'[{label}] FAILED: {e}')
        return []


def main():
    log.info('=== Sony Price Comparison Scraper Started ===')
    client = get_gspread_client()

    for product_type in ['lenses', 'cameras']:
        log.info(f'\n─── Scraping {product_type.upper()} ───')

        our_products = scrape_source(lambda: parse_our_site(product_type), 'Our Site')
        qomra        = scrape_source(lambda: parse_qomra(product_type),     'Qomra')
        mestores     = scrape_source(lambda: parse_mestores(product_type),  'Me Stores')
        abdulwahed   = scrape_source(lambda: parse_abdulwahed(product_type),'Abdulwahed')

        competitor_data = {
            'Qomra':      qomra,
            'Me Stores':  mestores,
            'Abdulwahed': abdulwahed,
        }

        rows = build_rows(our_products, competitor_data, product_type)
        write_sheet(client, product_type, rows)
        log.info(f'[{product_type}] Done — {len(rows)} comparison rows written.')

    log.info('=== Scraper Finished ===')


if __name__ == '__main__':
    main()
