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
SOURCES     = ['Our Site (ksa.amt.tv)', 'Qomra', 'Me Stores', 'Abdulwahed']
COMPETITORS = ['Qomra', 'Me Stores', 'Abdulwahed']

URLS = {
    'lenses': {
        'our_site':   'https://ksa.amt.tv/camera-accessories/photography/lenses.html?product_brand=1',
        'qomra':      'https://qomra.pro/en/search?q=le&filters[category_id]=750050316&filters[brand_id]=174800383',
        'mestores':   'https://mestores.com/en_sa/cameras-accessories/lenses?page={page}&brand%5Bfilter%5D=SONY%2C1722',
        'abdulwahed': 'https://www.abdulwahed.com/en/photography-c-868/lenses-c-879',
    },
    'cameras': {
        'our_site':   'https://ksa.amt.tv/camcorders-digital-cameras/photography/digital-camera.html?product_brand=1',
        'qomra':      'https://qomra.pro/en/category/jKQvBD?filters[category_id]=1061595081&filters[brand_id]=174800383',
        'mestores':   'https://mestores.com/en_sa/cameras-accessories/cameras?page={page}&brand%5Bfilter%5D=SONY%2C1722',
        'abdulwahed': 'https://www.abdulwahed.com/en/photography-c-868/cameras-c-869/digital-cameras-c-870',
    },
}

# ─── Product Validation ──────────────────────────────────────────────────────
NON_LENS_KEYWORDS = [
    'lens cap', 'lens hood', 'uv filter', 'cpl filter', 'nd filter',
    'cleaning kit', 'tripod', 'flash', 'battery', 'charger', 'bag',
    'strap', 'memory card', 'sd card', 'cable', 'adapter ring',
    'camera body', 'bundle', 'cine lens', 'cinema lens', 'body only',
    'camcorder', 'action cam', 'vlogging camera',
]

LENS_KEYWORDS = [
    'mm', 'f/', 'f1.', 'f2.', 'f4', 'f5.', 'f6.',
    'g master', 'g lens', 'zeiss', 'fe ', 'e-mount', 'e mount',
    'sel', 'macro', 'fisheye', 'zoom lens', 'prime lens', 'gm lens',
]

NON_CAMERA_KEYWORDS = [
    'tripod', 'bag', 'strap', 'battery', 'charger',
    'memory card', 'sd card', 'flash', 'filter',
    'cleaning', 'lens cap', 'hood', 'g master', 'gm lens',
    'zoom lens', 'prime lens', 'macro lens', 'fisheye',
    ' lens ', 'lens|', '| lens',
]

CAMERA_KEYWORDS = [
    'alpha', 'a7', 'a9', 'a6', 'a1 ', 'zv-', 'fx3', 'fx6', 'fx30',
    'ilce', 'ilc-', 'dsc-', 'cyber-shot', 'mirrorless',
    'digital camera', 'full frame', 'aps-c', 'a7r', 'a7s', 'a7c',
    'a5100', 'a6000', 'a6100', 'a6400', 'a6600', 'a6700',
    'camera, ilce', 'camera body',
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
    has_focal   = bool(re.search(r'\d+\s*mm', n)) or bool(re.search(r'\d{2,3}[-/]\d', n))
    has_lens_kw = any(kw in n for kw in LENS_KEYWORDS)
    return has_focal or has_lens_kw


def is_sony_camera(name):
    n = normalize(name)
    # must not contain strong lens signals
    lens_signals = ['mm f', 'f/1.', 'f/2.', 'f/4', 'f/5.', 'g master', 'gm |', '| gm',
                    'zoom lens', 'prime lens', 'macro lens', 'fisheye lens']
    for sig in lens_signals:
        if sig in n:
            return False
    for kw in NON_CAMERA_KEYWORDS:
        if kw in n:
            return False
    return any(kw in n for kw in CAMERA_KEYWORDS)


def fix_arabic_name(name, url, validator):
    if any('\u0600' <= c <= '\u06FF' for c in name):
        slug = url.rstrip('/').split('/')[-1]
        slug_name = re.sub(r'[_-]', ' ', slug).title()
        slug_name = re.sub(r'\.html?$', '', slug_name, flags=re.IGNORECASE).strip()
        if validator(slug_name):
            return slug_name
    return name


def parse_price(text):
    text = translate_eastern(text)
    text = re.sub(r'[^\d.,]', '', text)
    # remove thousands separator if followed by 3 digits
    text = re.sub(r',(\d{3})', r'\1', text)
    text = text.replace(',', '')
    try:
        return float(text)
    except Exception:
        return None


def clean_price_text(el):
    """Extract price from element, preferring SAR prices."""
    if not el:
        return None
    texts = el.get_text(separator=' ', strip=True)
    # look for patterns like 4849 or 4,849
    matches = re.findall(r'[\d,،٠-٩]+(?:[.,][\d]+)?', translate_eastern(texts))
    for m in matches:
        p = parse_price(m)
        if p and p > 50:   # ignore tiny numbers (ratings, etc.)
            return p
    return None


# ─── ZenRows ────────────────────────────────────────────────────────────────
def zenrows_get(url, wait=8000, retries=2):
    params = {
        'apikey':        ZENROWS_KEY,
        'url':           url,
        'antibot':       'true',
        'premium_proxy': 'true',
        'js_render':     'true',
        'proxy_country': 'sa',
        'wait':          str(wait),
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


# ─── Our Site (ksa.amt.tv) — Magento ────────────────────────────────────────
def parse_our_site(product_type):
    base_url = URLS[product_type]['our_site']
    products = []
    seen     = set()
    page     = 1
    while page <= 20:
        url = f"{base_url}&p={page}" if page > 1 else base_url
        log.info(f'[Our Site] page {page}: {url}')
        html = zenrows_get(url, wait=8000)
        if not html:
            break
        soup      = BeautifulSoup(html, 'lxml')
        items     = soup.select('li.product-item, .product-item-info')
        new_found = 0
        for item in items:
            try:
                name_el  = item.select_one('.product-item-name a, .product-item-link')
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
                avail    = 'In Stock'
                if avail_el:
                    avail = 'Out of Stock' if 'out' in avail_el.get_text().lower() else 'In Stock'
                products.append({'name': name, 'price': price, 'availability': avail, 'url': link})
                new_found += 1
            except Exception as e:
                log.debug(f'[Our Site] item error: {e}')
        if new_found == 0:
            break
        page += 1
    log.info(f'[Our Site] {product_type}: {len(products)} products')
    return products


# ─── Qomra (Salla platform) ──────────────────────────────────────────────────
def parse_qomra(product_type):
    """
    Salla platform.
    Container: custom-salla-product-card  (or s-product-card-entry)
    Name:      h1.s-product-card-content-title > a  (text content)
    Link:      same <a> href
    Price:     .s-product-card-sale-price h4  (first number = sale price)
    Avail:     presence of 's-product-card-out-of-stock' class or 'نفد' text
    """
    base_url  = URLS[product_type]['qomra']
    products  = []
    seen      = set()
    validator = is_sony_lens if product_type == 'lenses' else is_sony_camera
    page      = 1

    while page <= 20:
        url  = f"{base_url}&page={page}" if page > 1 else base_url
        log.info(f'[Qomra] page {page}: {url}')
        html = zenrows_get(url, wait=12000)
        if not html:
            break
        soup = BeautifulSoup(html, 'lxml')

        # Each product is a <custom-salla-product-card> custom element
        items = soup.select('custom-salla-product-card, s-product-card-entry')
        if not items:
            # fallback: find all elements with s-product-card-entry class
            items = soup.select('[class~="s-product-card-entry"]')
        if not items:
            log.warning(f'[Qomra] No items on page {page}')
            break

        new_found = 0
        for item in items:
            try:
                # Name from h1.s-product-card-content-title > a
                title_el = item.select_one('h1.s-product-card-content-title a, h2.s-product-card-content-title a')
                if not title_el:
                    # fallback: any <a> with meaningful text
                    title_el = item.select_one('.s-product-card-content a[href]')
                if not title_el:
                    continue

                name = title_el.get_text(strip=True)
                link = title_el.get('href', '').strip()
                if not link.startswith('http'):
                    link = 'https://qomra.pro' + link
                if link in seen:
                    continue
                seen.add(link)

                if not name:
                    slug = link.rstrip('/').split('/')[-1].split('?')[0]
                    name = re.sub(r'[_-]', ' ', slug).title()

                name = fix_arabic_name(name, link, validator)
                if not validator(name):
                    continue

                # Price: first h4 inside .s-product-card-sale-price
                price    = None
                price_el = item.select_one('.s-product-card-sale-price h4, .s-product-card-sale-price span')
                if price_el:
                    price = parse_price(translate_eastern(price_el.get_text(strip=True)))
                if not price:
                    # broader fallback
                    for el in item.select('h4, [class*="price"]'):
                        txt = translate_eastern(el.get_text(strip=True))
                        p   = parse_price(txt)
                        if p and p > 100:
                            price = p
                            break

                # Availability
                avail    = 'In Stock'
                card_txt = item.get_text()
                if ('out of stock' in card_txt.lower() or 'نفد' in card_txt
                        or item.select_one('[class*="out-of-stock"], [class*="sold-out"]')):
                    avail = 'Out of Stock'

                products.append({'name': name, 'price': price, 'availability': avail, 'url': link})
                new_found += 1
            except Exception as e:
                log.debug(f'[Qomra] item error: {e}')

        if new_found == 0:
            break
        page += 1

    log.info(f'[Qomra] {product_type}: {len(products)} products')
    return products


# ─── Me Stores ───────────────────────────────────────────────────────────────
def parse_mestores(product_type):
    """
    Me Stores custom React storefront.
    Grid:   div.gallery-root-yVO > div.gallery-items-4Gj > a[href]
    Name:   img[alt] inside the card  (full product name in alt text)
    Price:  span.productCard-module-priceAmount-vpp  (contains "SAR 5,999")
    Avail:  'Out of stock' button text or 'Notify Me' button
    """
    base_url  = URLS[product_type]['mestores']
    products  = []
    seen      = set()
    validator = is_sony_lens if product_type == 'lenses' else is_sony_camera
    page      = 1

    while page <= 20:
        url  = base_url.format(page=page)
        log.info(f'[Me Stores] page {page}: {url}')
        html = zenrows_get(url, wait=12000)
        if not html:
            break
        soup = BeautifulSoup(html, 'lxml')

        # Find all product anchor tags in the gallery grid
        # The gallery uses class names with hashed suffixes like gallery-root-yVO
        anchors = soup.select('a[href*="/en_sa/sony"]')
        if not anchors:
            # broader: any <a> inside gallery-like container
            gallery = soup.select_one('[class*="gallery-root"], [class*="infinite-scroll"]')
            if gallery:
                anchors = [a for a in gallery.select('a[href]')
                           if a.get('href', '').startswith('/en_sa/') or 'mestores.com/en_sa/' in a.get('href', '')]

        if not anchors:
            log.warning(f'[Me Stores] No anchors on page {page}')
            break

        new_found = 0
        for a in anchors:
            try:
                link = a.get('href', '').strip()
                if not link:
                    continue
                if not link.startswith('http'):
                    link = 'https://mestores.com' + link
                # skip non-product pages (category pages have fewer path segments)
                path_parts = [p for p in link.replace('https://mestores.com', '').split('/') if p]
                if len(path_parts) < 3:
                    continue
                if link in seen:
                    continue
                seen.add(link)

                # Name: prefer the longest img[alt] inside the card
                name = ''
                best_alt_len = 0
                for img in a.select('img[alt]'):
                    alt = img.get('alt', '').strip()
                    # skip payment logos (tabby, tamara, star icons)
                    if alt.lower() in ('tabby', 'tamara', 'sar', '') or len(alt) < 10:
                        continue
                    if len(alt) > best_alt_len:
                        name = alt
                        best_alt_len = len(alt)

                if not name:
                    # fallback: tooltip span has full name
                    tip = a.select_one('[class*="tooltipText"]')
                    if tip:
                        name = tip.get_text(strip=True)

                if not name:
                    slug = link.rstrip('/').split('/')[-1].split('?')[0]
                    name = re.sub(r'[_-]', ' ', slug).title()

                name = fix_arabic_name(name, link, validator)
                if not validator(name):
                    continue

                # Price: span with class containing 'priceAmount'
                price    = None
                price_el = a.select_one('[class*="priceAmount"], [class*="priceValue"]')
                if price_el:
                    price = parse_price(translate_eastern(price_el.get_text(strip=True)))
                if not price:
                    for el in a.select('span'):
                        txt = translate_eastern(el.get_text(strip=True))
                        p   = parse_price(txt)
                        if p and p > 100:
                            price = p
                            break

                # Availability
                avail    = 'In Stock'
                card_txt = a.get_text().lower()
                if 'out of stock' in card_txt or 'notify me' in card_txt or 'نفد' in a.get_text():
                    avail = 'Out of Stock'

                products.append({'name': name, 'price': price, 'availability': avail, 'url': link})
                new_found += 1
            except Exception as e:
                log.debug(f'[Me Stores] item error: {e}')

        if new_found == 0:
            break
        page += 1

    log.info(f'[Me Stores] {product_type}: {len(products)} products')
    return products


# ─── Abdulwahed ───────────────────────────────────────────────────────────────
def parse_abdulwahed(product_type):
    """
    Abdulwahed uses a Tailwind grid.
    Product cards are div.relative inside div[class*="grid-cols-2"] or similar grid.
    Name from <img alt="...">.
    Price from a span containing digits after removing non-numeric chars.
    Filter Sony by name or alt text.
    """
    base_url  = URLS[product_type]['abdulwahed']
    products  = []
    seen      = set()
    validator = is_sony_lens if product_type == 'lenses' else is_sony_camera
    page      = 1

    while page <= 20:
        url  = f"{base_url}?page={page}" if page > 1 else base_url
        log.info(f'[Abdulwahed] page {page}: {url}')
        html = zenrows_get(url, wait=10000)
        if not html:
            break
        soup = BeautifulSoup(html, 'lxml')

        # Find product cards: each card is a div.relative with an img and price
        # The grid container has class grid grid-cols-2 or similar
        cards = soup.select(
            'div[class*="grid-cols-2"] > div, '
            'div[class*="grid-cols-3"] > div, '
            'div[class*="grid-cols-4"] > div, '
            'div[class*="sm:grid-cols"] > div'
        )
        # fallback: any div containing both an img[alt] and a price-like span
        if not cards:
            cards = [d for d in soup.select('div') if d.select_one('img[alt]') and
                     re.search(r'\d{3,}', d.get_text())]

        if not cards:
            log.warning(f'[Abdulwahed] No cards on page {page}')
            break

        new_found = 0
        for card in cards:
            try:
                # Name from img alt
                img_el = card.select_one('img[alt]')
                if not img_el:
                    continue
                name = img_el.get('alt', '').strip()
                if not name:
                    continue

                # Link
                link_el = card.select_one('a[href]')
                link    = ''
                if link_el:
                    link = link_el.get('href', '').strip()
                    if not link.startswith('http'):
                        link = 'https://www.abdulwahed.com' + link
                if not link:
                    continue
                if link in seen:
                    continue
                seen.add(link)

                # Sony filter — must mention Sony in name or alt
                if 'sony' not in normalize(name):
                    continue

                name = fix_arabic_name(name, link, validator)
                if not validator(name):
                    continue

                # Price
                price = None
                for el in card.select('span, div, p'):
                    txt = translate_eastern(el.get_text(strip=True))
                    if re.search(r'\d{3,}', txt):
                        p = parse_price(txt)
                        if p and 100 < p < 200000:
                            price = p
                            break

                # Availability
                avail    = 'In Stock'
                card_txt = card.get_text().lower()
                if 'out of stock' in card_txt or 'notify' in card_txt or 'unavailable' in card_txt:
                    avail = 'Out of Stock'

                products.append({'name': name, 'price': price, 'availability': avail, 'url': link})
                new_found += 1
            except Exception as e:
                log.debug(f'[Abdulwahed] item error: {e}')

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
    fa, fb = extract_focal(na), extract_focal(nb)
    if not fa or not fb or fa != fb:
        return 0
    score = 80
    aa, ab = extract_aperture(na), extract_aperture(nb)
    if aa and ab and abs(aa - ab) < 0.1:
        score += 30
    for mt in ['fe ', 'e-mount', 'e mount', 'a-mount']:
        if mt in na and mt in nb:
            score += 25
            break
    for s in ['g master', 'gm', 'g lens', 'zeiss']:
        if s in na and s in nb:
            score += 15
    return score


def match_camera_score(a, b):
    na, nb = normalize(a), normalize(b)
    models_a = set(re.findall(r'a\d[a-z0-9]*|zv-\w+|fx\d+|ilce-[\w-]+|dsc-[\w-]+', na))
    models_b = set(re.findall(r'a\d[a-z0-9]*|zv-\w+|fx\d+|ilce-[\w-]+|dsc-[\w-]+', nb))
    if models_a and models_b:
        return 100 if models_a & models_b else 0
    words_a = set(na.split())
    words_b = set(nb.split())
    overlap = len(words_a & words_b)
    return min(70, overlap * 15)


def find_match(our_product, competitor_products, product_type):
    scorer     = match_lens_score if product_type == 'lenses' else match_camera_score
    best_score = 0
    best_match = None
    for cp in competitor_products:
        score = scorer(our_product['name'], cp['name'])
        if score > best_score:
            best_score = score
            best_match = cp
    return best_match if best_score >= 80 else None


# ─── Build Comparison Rows ───────────────────────────────────────────────────
def build_rows(our_products, competitor_data, product_type):
    rows      = []
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    used_urls = {src: set() for src in COMPETITORS}

    for our in our_products:
        row = {
            'timestamp':        timestamp,
            'name':             our['name'],
            'our_price':        our['price'],
            'our_availability': our['availability'],
            'our_url':          our['url'],
        }
        prices_for_lowest = []
        if our['price'] and our['availability'] == 'In Stock':
            prices_for_lowest.append(('Our Site (ksa.amt.tv)', our['price'], our['url']))

        for src in COMPETITORS:
            match = find_match(our, competitor_data.get(src, []), product_type)
            if match:
                used_urls[src].add(match['url'])
                diff   = round(match['price'] - our['price'], 2) if match['price'] and our['price'] else None
                status = ('Cheaper than competitor' if diff and diff > 0 else
                          'More expensive'          if diff and diff < 0 else
                          'Same price'              if diff == 0          else 'Not listed')
                row[src] = {'url': match['url'], 'price': match['price'],
                            'availability': match['availability'], 'diff': diff, 'status': status}
                if match['price'] and match['availability'] == 'In Stock':
                    prices_for_lowest.append((src, match['price'], match['url']))
            else:
                row[src] = {'url': '', 'price': None, 'availability': '', 'diff': None, 'status': 'Not listed'}

        if prices_for_lowest:
            cheapest                    = min(prices_for_lowest, key=lambda x: x[1])
            row['lowest_price']         = cheapest[1]
            row['cheapest_brand']       = cheapest[0]
            row['cheapest_link']        = cheapest[2]
            row['our_diff_vs_cheapest'] = round((our['price'] or 0) - cheapest[1], 2)
        else:
            row['lowest_price'] = row['cheapest_brand'] = row['cheapest_link'] = None
            row['our_diff_vs_cheapest'] = None

        rows.append(row)

    # Competitor-only products
    for src in COMPETITORS:
        for cp in competitor_data.get(src, []):
            if cp['url'] in used_urls[src]:
                continue
            row = {'timestamp': timestamp, 'name': cp['name'],
                   'our_price': None, 'our_availability': 'Not listed', 'our_url': ''}
            for other in COMPETITORS:
                if other == src:
                    row[other] = {'url': cp['url'], 'price': cp['price'],
                                  'availability': cp['availability'], 'diff': None, 'status': 'Not listed'}
                else:
                    row[other] = {'url': '', 'price': None, 'availability': '', 'diff': None, 'status': 'Not listed'}
            row['lowest_price']         = cp['price']
            row['cheapest_brand']       = src
            row['cheapest_link']        = cp['url']
            row['our_diff_vs_cheapest'] = None
            rows.append(row)

    return rows


# ─── Google Sheets ───────────────────────────────────────────────────────────
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

SUMMARY_HEADERS = ['Source', 'Total Products', 'Cheaper Than Us', 'More Expensive',
                   'Same Price', 'Not Listed', 'Updated']


def get_gspread_client():
    sa_info = json.loads(SA_JSON)
    scopes  = ['https://www.googleapis.com/auth/spreadsheets',
               'https://www.googleapis.com/auth/drive']
    creds   = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return gspread.authorize(creds)


def row_to_list(row):
    out = [row['timestamp'], row['name'],
           row.get('our_price', ''), row.get('our_availability', ''), row.get('our_url', '')]
    for src in COMPETITORS:
        d = row.get(src, {})
        out += [d.get('url', ''), d.get('price', ''), d.get('availability', ''),
                d.get('diff', ''), d.get('status', '')]
    out += [row.get('lowest_price', ''), row.get('cheapest_brand', ''),
            row.get('cheapest_link', ''), row.get('our_diff_vs_cheapest', '')]
    return out


def compute_summary(rows, timestamp):
    summary = []
    for src in SOURCES:
        if src == 'Our Site (ksa.amt.tv)':
            total = sum(1 for r in rows if r.get('our_price'))
            cheaper = more_exp = same = not_listed = 0
        else:
            total      = sum(1 for r in rows if r.get(src, {}).get('price'))
            cheaper    = sum(1 for r in rows if r.get(src, {}).get('status') == 'Cheaper than competitor')
            more_exp   = sum(1 for r in rows if r.get(src, {}).get('status') == 'More expensive')
            same       = sum(1 for r in rows if r.get(src, {}).get('status') == 'Same price')
            not_listed = sum(1 for r in rows if r.get(src, {}).get('status') == 'Not listed')
        summary.append([src, total, cheaper, more_exp, same, not_listed, timestamp])
    return summary


def color_status_cells(ws, rows, spreadsheet):
    color_map = {
        'Cheaper than competitor': {'red': 0.20, 'green': 0.73, 'blue': 0.40},
        'More expensive':          {'red': 0.91, 'green': 0.27, 'blue': 0.27},
        'Same price':              {'red': 1.0,  'green': 0.90, 'blue': 0.20},
        'Not listed':              {'red': 0.85, 'green': 0.85, 'blue': 0.85},
    }
    # Status columns are at positions 10, 15, 20 (1-indexed) → 0-indexed: 9, 14, 19
    status_col_indices = [9, 14, 19]
    requests_body = []
    for row_idx, row in enumerate(rows):
        sheet_row = row_idx + 2  # data starts at row 3 (0-indexed row 2)
        for src_idx, src in enumerate(COMPETITORS):
            status = row.get(src, {}).get('status', '')
            color  = color_map.get(status)
            if not color:
                continue
            col = status_col_indices[src_idx]
            requests_body.append({
                'repeatCell': {
                    'range': {
                        'sheetId':          ws.id,
                        'startRowIndex':    sheet_row,
                        'endRowIndex':      sheet_row + 1,
                        'startColumnIndex': col,
                        'endColumnIndex':   col + 1,
                    },
                    'cell':   {'userEnteredFormat': {'backgroundColor': color}},
                    'fields': 'userEnteredFormat.backgroundColor',
                }
            })
    if requests_body:
        spreadsheet.batch_update({'requests': requests_body})


def write_sheet(client, product_type, rows):
    tab_name     = 'Lenses'          if product_type == 'lenses' else 'Cameras'
    summary_name = 'Lenses Summary'  if product_type == 'lenses' else 'Cameras Summary'
    timestamp    = datetime.now().strftime('%Y-%m-%d %H:%M')
    sh           = client.open_by_key(GSHEET_ID)

    # Main tab
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=500, cols=30)
    ws.clear()
    data = [GROUP_HEADERS, COL_HEADERS] + [row_to_list(r) for r in rows]
    ws.update(values=data, range_name='A1', value_input_option='USER_ENTERED')
    color_status_cells(ws, rows, sh)
    log.info(f'Written {len(rows)} rows to [{tab_name}]')

    # Summary tab
    try:
        ws_s = sh.worksheet(summary_name)
    except gspread.WorksheetNotFound:
        ws_s = sh.add_worksheet(title=summary_name, rows=20, cols=10)
    ws_s.clear()
    ws_s.update(values=[SUMMARY_HEADERS] + compute_summary(rows, timestamp),
                range_name='A1', value_input_option='USER_ENTERED')
    log.info(f'Written summary to [{summary_name}]')


# ─── Main ────────────────────────────────────────────────────────────────────
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

        our_products = scrape_source(lambda pt=product_type: parse_our_site(pt),    'Our Site')
        qomra        = scrape_source(lambda pt=product_type: parse_qomra(pt),       'Qomra')
        mestores     = scrape_source(lambda pt=product_type: parse_mestores(pt),     'Me Stores')
        abdulwahed   = scrape_source(lambda pt=product_type: parse_abdulwahed(pt),   'Abdulwahed')

        competitor_data = {
            'Qomra':      qomra,
            'Me Stores':  mestores,
            'Abdulwahed': abdulwahed,
        }

        rows = build_rows(our_products, competitor_data, product_type)
        write_sheet(client, product_type, rows)
        log.info(f'[{product_type}] Done — {len(rows)} rows written.')

    log.info('=== Scraper Finished ===')


if __name__ == '__main__':
    main()
