import os, re, json, time, logging, requests
from bs4 import BeautifulSoup
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

ZENROWS_KEY = os.environ.get('ZENROWS_API_KEY', '')
GSHEET_ID   = os.environ.get('GSHEET_ID', '')
SA_JSON     = os.environ.get('GOOGLE_SERVICE_ACCOUNT', '')

OUR_SITE    = 'Our Site (ksa.amt.tv)'
COMPETITORS = ['Qomra','Me Stores','Abdulwahed','Amazon SA','Noon','CameraMix','PClub','CamTime','AlamCam','CameraBox']
SOURCES     = [OUR_SITE] + COMPETITORS

URLS = {
    'lenses': {
        'our_site':   'https://ksa.amt.tv/camera-accessories/photography/lenses.html?product_brand=1',
        'qomra':      'https://qomra.pro/en/search?q=lens&filters[brand_id]=174800383&per_page=50',
        'mestores':   'https://mestores.com/en_sa/cameras-accessories/lenses?page={page}&brand%5Bfilter%5D=SONY%2C1722',
        'abdulwahed': 'https://www.abdulwahed.com/en/photography-c-868/lenses-c-879',
        'amazon':     'https://www.amazon.sa/s?k=sony+lens&i=electronics&language=en_AE&rh=p_89%3ASony',
        'noon':       'https://www.noon.com/saudi-en/electronics-and-mobiles/camera-and-photo-16165/lenses-16166/?q=sony',
        'cameramix':  'https://www.cameramix.com/Camera_Lenses',
        'pclub':      'https://pclub.com.sa/sony-1-10?limit=100',
        'camtime':    'https://camtime.sa/%D8%A7%D9%84%D8%B9%D8%AF%D8%B3%D8%A7%D8%AA-%D9%88%D9%85%D9%84%D8%AD%D9%82%D8%A7%D8%AA%D9%87%D8%A71778543834?fm=10',
        'alamcam':    'https://alamcam.sa/index.php?route=product/search&search=sony+fe+lens&limit=100',
        'camerabox':  'https://camerabox.com.sa/en/sony/brand-1380282655',
    },
    'cameras': {
        'our_site':   ['https://ksa.amt.tv/camcorders-digital-cameras/photography/digital-camera.html?product_brand=1',
                       'https://ksa.amt.tv/camcorders-digital-cameras/video/digital-cinematography-cameras.html?product_brand=1'],
        'qomra':      'https://qomra.pro/en/search?q=camera&filters[brand_id]=174800383&per_page=50',
        'mestores':   'https://mestores.com/en_sa/cameras-accessories/cameras?page={page}&brand%5Bfilter%5D=SONY%2C1722',
        'abdulwahed': 'https://www.abdulwahed.com/en/photography-c-868/cameras-c-869',
        'amazon':     'https://www.amazon.sa/s?k=sony+alpha+camera&i=electronics&language=en_AE&rh=p_89%3ASony',
        'noon':       'https://www.noon.com/saudi-en/electronics-and-mobiles/camera-and-photo-16165/digital-cameras-16168/?q=sony',
        'cameramix':  'https://www.cameramix.com/Sony',
        'pclub':      'https://pclub.com.sa/sony-1-10?limit=100',
        'camtime':    'https://camtime.sa/%D9%83%D8%A7%D9%85%D9%8A%D8%B1%D8%A7%D8%AA-%D8%A7%D9%84%D8%AA%D8%B5%D9%88%D9%8A%D8%B11778543751?fm=10',
        'alamcam':    'https://alamcam.sa/index.php?route=product/search&search=sony+camera&limit=100',
        'camerabox':  'https://camerabox.com.sa/en/sony/brand-1380282655',
    },
}

# ── Validators ────────────────────────────────────────────────────────────────
NON_LENS = [
    'lens cap','lens cover','front cap','rear cap','body cap',
    'lens hood','sun shade','uv filter','cpl filter','nd filter','variable nd',
    'cleaning kit','cleaning cloth','cleaning pen',
    'tripod','monopod','ballhead','flash','speedlight',
    'battery','charger','memory card','sd card','cf card','cfexpress',
    'bag','case','pouch','strap','shoulder strap',
    'cable','usb dock','dock','software','book','manual',
    'mount adapter','lens adapter','converter',
    'cage','shooting grip','microphone','condenser',
    'screen protector','carrying case','light stand',
]
NON_CAM = [
    'tripod','bag','strap','battery','charger','memory card','sd card',
    'cf card','cfexpress','flash','filter','cleaning','lens cap','lens hood',
    'usb dock','dock','screen protector','carrying case',
    'condenser','cage','smallrig','tilta',
    'monitor','hdmi','softbox','diffuser','light stand',
    'microphone','wireless mic',
    # Specific excluded models
    'zv-1a',
]
CAM_MODELS_RE = [
    r'\ba7\s*(r|s|c|cm)?\s*(ii+|iv|v|[2-9])?\b',
    r'\ba9\s*(ii+|iii|[23])?\b',
    r'\ba1\b',
    r'\ba6[0-9]{3}[a-z]?\b',
    r'\ba5[0-9]{3}\b',
    r'\bzv-[a-z0-9]+',
    r'\bzv1[a-z]?\b',
    r'\bfx[23679][a0]?\b',
    r'\bfx9\b',
    r'\bpxw-\w+',          # PXW-FX9, PXW-FS7 etc
    r'\bdsc-rx[0-9]',      # DSC-RX100, DSC-RX10 etc
    r'\brx[0-9]{3}',       # RX100 series
    r'\bilce-[\w-]+',
    r'\bilme-[\w-]+',
    r'\bdsc-[\w-]+',
    r'\brx[0-9]',
    r'\balpha\s+1\b',
    r'\balpha\s+a?\d',
    r'\balpha\s+7\s*(iv|v|iii|ii)?\b',   # Alpha 7 IV, Alpha 7 V etc
    r'\bvenice\b',          # Sony VENICE
    r'\bburano\b',          # Sony BURANO 8K
    r'\bfs[57]\b',          # FS5, FS7
]
CAM_TYPES = [
    'mirrorless camera','mirrorless digital camera','digital camera',
    'cinema camera','vlog camera','camera body',
    'interchangeable lens camera','interchangeable-lens camera',
]
LENS_ID = [' lens','g master','gm ','zeiss','vario-tessar',
           'fe pz ','e pz ',' oss','macro g','macro gm',
           'sel','dn ','dg ','dc ','hsm',
           'sony fe ','sony e ']

def norm(s):
    # Normalize Greek alpha α → a for Sony model matching (α7 → a7)
    s = s.replace('α','a').replace('Α','A')
    return s.lower().strip()

def tr_east(s):
    for i,c in enumerate('٠١٢٣٤٥٦٧٨٩'): s=s.replace(c,str(i))
    return s.replace('٬',',')

def is_lens(name):
    n=norm(name)
    if any(k in n for k in NON_LENS): return False
    if re.search(r'\b(mirrorless camera|mirrorless digital camera|digital camera|vlog camera|cinema camera|camera body)\b',n): return False
    # Reject if name is primarily a camera (has camera model + camera-type word)
    has_cam_model=any(re.search(p,n) for p in CAM_MODELS_RE)
    has_cam_word=bool(re.search(r'\b(mirrorless|cinema|vlog|digital|camera|camcorder)\b',n))
    if has_cam_model and has_cam_word: return False
    if not re.search(r'\d+\s*mm',n): return False
    has_aperture=bool(re.search(r'\bf/?[\s]?\d+\.?\d*',n))
    has_id=any(k in n for k in LENS_ID)
    return has_aperture or has_id

def is_camera(name):
    n=norm(name)
    if any(k in n for k in NON_CAM): return False
    is_pure_lens=(bool(re.search(r'\d+\s*mm',n)) and
                  bool(re.search(r'\bf/?[\s]?\d+\.?\d*',n)) and
                  any(k in n for k in [' lens','g master','vario-tessar','fe pz','e pz']) and
                  not re.search(r'\b(mirrorless|digital camera|cinema camera|vlog camera)\b',n))
    if is_pure_lens: return False
    has_model=any(re.search(p,n) for p in CAM_MODELS_RE)
    has_type=any(k in n for k in CAM_TYPES)
    return has_model or has_type

def slug_to_name(slug):
    """Convert URL slug to clean Sony product name."""
    s = slug.rstrip('/- ').split('?')[0]
    s = re.sub(r'\.html?$', '', s, flags=re.I)
    # Remove trailing numeric IDs: -p-5006409, -2-1693, -3496 etc.
    s = re.sub(r'[-_]p[-_]\d+$', '', s)
    s = re.sub(r'[-_]\d{4,}$', '', s)
    s = re.sub(r'[-_]\d+-\d+$', '', s)
    # Restore ZV model hyphens BEFORE general substitution
    s = re.sub(r'\b(zv)[-_](e?\d+[a-z0-9]*)', r'\1-\2', s, flags=re.I)
    s = re.sub(r'\b(ilce|ilme|dsc|sel)[-_](\w+)', r'\1-\2', s, flags=re.I)
    # Aperture: f-3-5-5-6 → f3.5-5.6, f-1-8 → f1.8
    s = re.sub(r'\bf[-_](\d)[-_](\d)[-_](\d)[-_](\d)\b', r'f\1.\2-\3.\4', s, flags=re.I)
    s = re.sub(r'\bf[-_](\d)[-_](\d)\b', r'f\1.\2', s, flags=re.I)
    s = re.sub(r'\bf[-_](\d{1,2})\b', r'f\1', s, flags=re.I)
    # Focal ranges: 16-50mm
    s = re.sub(r'(\d{2,3})[-_](\d{2,3})[-_]?(mm)', r'\1-\2mm', s, flags=re.I)
    # Remove ILCE/ILME/P SKU junk at end
    s = re.sub(r'[-_]?(ilce|ilme)[-_]?\w*\s*$', '', s, flags=re.I)
    s = re.sub(r'[-_][a-z]-\d+\s*$', '', s, flags=re.I)
    # Replace remaining separators
    s = re.sub(r'[-_]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    # Ensure Sony prefix
    if not re.match(r'sony\b', s, re.I):
        s = 'Sony ' + s
    # Smart title case
    words = s.split()
    result = []
    FORCE_UPPER = {'ii','iii','iv','xlr','usb','sel','fe','oss','gm','aps','dsc'}
    FORCE_LOWER = {'with','and','or','for','the','a','an','of','in','to','by'}
    # Preserve case for known prefixes
    FORCE_EXACT = {'sony':'Sony','zv':'ZV','fx':'FX','ilce':'ILCE','ilme':'ILME'}
    for i, w in enumerate(words):
        wl = w.lower()
        if wl in FORCE_EXACT: result.append(FORCE_EXACT[wl])
        elif wl in FORCE_UPPER: result.append(w.upper())
        elif wl in FORCE_LOWER and i > 0: result.append(wl)
        else: result.append(w.capitalize())
    return ' '.join(result)

def fix_arabic(name, url, val):
    """If name is Arabic or a raw slug code, replace with slug-derived name."""
    if any('\u0600'<=c<='\u06FF' for c in name):
        # Try path segments from the end, skip pure Arabic/number segments
        parts = [p for p in url.split('/') if p and not p.startswith('?')]
        for part in reversed(parts):
            part = part.split('?')[0]
            # Must contain Latin characters (actual slug)
            if not re.search(r'[a-zA-Z]{3,}', part): continue
            sn = slug_to_name(part)
            if sn and len(sn) > 10 and val(sn):
                return sn
    return name

def pparse(text):
    t=tr_east(str(text))
    nums=re.findall(r'[\d]+(?:[,،][\d]+)*(?:\.[\d]+)?',t)
    for n in nums:
        clean=n.replace(',','').replace('،','').split('.')[0]
        try:
            v=float(clean)
            if 100 < v < 200000: return v  # cap at 200k SAR — higher values are parsing artifacts
        except: continue
    return None

def detect_avail(item):
    """Robustly detect product availability from any HTML element."""
    t=item.get_text().lower()
    # Strong OUT OF STOCK signals (check these first)
    OOS=['out of stock','sold out','غير متوفر','نفد المخزون','نفذ','نفد',
         'notify me when in stock','notify me when available','notify when available',
         'notify when in stock','تنبيهي عند توفره','unavailable','not available',
         'enquire now','pre-order']
    if any(x in t for x in OOS): return 'Out of Stock'
    # Strong IN STOCK signals
    INS=['add to cart','add to bag','buy now','أضف للسلة','أضف إلى السلة',
         'in stock','متوفر','buy now']
    if any(x in t for x in INS): return 'In Stock'
    return 'In Stock'  # default

# ── HTTP helpers ──────────────────────────────────────────────────────────────
def zenrows_js(url, wait=10000, scroll=False, retries=2):
    p={'apikey':ZENROWS_KEY,'url':url,'antibot':'true','premium_proxy':'true',
       'js_render':'true','proxy_country':'sa','wait':str(wait)}
    if scroll:
        p['js_instructions']=json.dumps([
            {'scroll_y':5000},{'wait':2000},
            {'scroll_y':10000},{'wait':2000},
            {'scroll_y':16000},{'wait':2000},
            {'scroll_y':22000},{'wait':2000},
            {'scroll_y':28000},{'wait':2000},
            {'scroll_y':35000},{'wait':2000},
            {'scroll_y':42000},{'wait':2500},
            {'scroll_y':50000},{'wait':3000}])
    for a in range(retries+1):
        try:
            r=requests.get('https://api.zenrows.com/v1/',params=p,timeout=90)
            r.raise_for_status(); return r.text
        except Exception as e:
            log.warning(f'ZenRows JS attempt {a+1}: {e}')
            if a<retries: time.sleep(5)
    return None

def zenrows_std(url, retries=2):
    p={'apikey':ZENROWS_KEY,'url':url,'antibot':'true','premium_proxy':'true','proxy_country':'sa'}
    for a in range(retries+1):
        try:
            r=requests.get('https://api.zenrows.com/v1/',params=p,timeout=60)
            r.raise_for_status(); return r.text
        except Exception as e:
            log.warning(f'ZenRows std attempt {a+1}: {e}')
            if a<retries: time.sleep(5)
    return None

def plain(url, ssl=True, retries=2):
    h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
       'Accept-Language':'en-US,en;q=0.9'}
    for a in range(retries+1):
        try:
            r=requests.get(url,headers=h,timeout=30,verify=ssl)
            r.raise_for_status(); return r.text
        except Exception as e:
            log.warning(f'plain_get attempt {a+1}: {e}')
            if a<retries: time.sleep(3)
    return None

# ── Shared parsers ────────────────────────────────────────────────────────────
def opencart_parse(html, base_url, label, validator):
    soup=BeautifulSoup(html,'lxml')
    items=soup.select('.product-thumb,.product-layout,[class*="product-item"]')
    log.info(f'[{label}] found {len(items)} items')
    if not items:
        body=soup.find('body')
        log.warning(f'[{label}] snippet: {str(body)[:300] if body else html[:300]}')
    results=[]; seen=set(); logged=0
    for item in items:
        try:
            ne=(item.select_one('.caption h4 a') or item.select_one('.product-name a') or
                item.select_one('h4 a') or item.select_one('h3 a') or item.select_one('h2 a') or
                item.select_one('[class*="product-title"] a') or item.select_one('[class*="name"] a') or
                item.select_one('a[href]'))
            if not ne: continue
            name=ne.get_text(strip=True); link=ne.get('href','').strip()
            if not name or len(name)<3: continue
            if not link.startswith('http'): link=base_url+link
            if link in seen: continue
            seen.add(link)
            if logged<3: log.info(f'[{label}] candidate: {name[:80]}'); logged+=1
            name=fix_arabic(name,link,validator)
            if not validator(name): continue
            pe=item.select_one('.price-new,.price-normal,.price,[class*="price"]')
            price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
            avail=detect_avail(item)
            results.append({'name':name,'price':price,'availability':avail,'url':link})
        except Exception as e: log.debug(f'[{label}] {e}')
    return results

def salla_parse(html, base_url, label, validator):
    soup=BeautifulSoup(html,'lxml')
    items=soup.select('custom-salla-product-card,s-product-card-entry,[class~="s-product-card-entry"]')
    log.info(f'[{label}] found {len(items)} salla items')
    if not items:
        body=soup.find('body')
        log.warning(f'[{label}] snippet: {str(body)[:300] if body else html[:300]}')
    results=[]; seen=set(); logged=0
    for item in items:
        try:
            te=item.select_one('h1.s-product-card-content-title a,h2.s-product-card-content-title a,.s-product-card-content-title a')
            if not te: te=item.select_one('a[aria-label][href]')
            if not te: te=item.select_one('a[href]')
            if not te: continue
            name=te.get('aria-label','').strip() or te.get_text(strip=True)
            link=te.get('href','').strip()
            if not link.startswith('http'): link=base_url+link
            if link in seen: continue
            seen.add(link)
            if not name or re.match(r'^P\d+$',name):
                img=item.select_one('img[alt]')
                if img: name=img.get('alt','').strip()
            if not name:
                slug=link.rstrip('/').split('/')[-1].split('?')[0]
                name=slug_to_name(slug)
            name=fix_arabic(name,link,validator)
            if logged<3: log.info(f'[{label}] candidate: {name[:80]}'); logged+=1
            if not validator(name): continue
            pe=item.select_one('.s-product-card-sale-price h4,.s-product-card-sale-price span')
            price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
            if not price:
                for el in item.select('h4,[class*="price"]'):
                    p=pparse(tr_east(el.get_text(strip=True)))
                    if p and p>100: price=p; break
            avail=detect_avail(item)
            results.append({'name':name,'price':price,'availability':avail,'url':link})
        except Exception as e: log.debug(f'[{label}] {e}')
    log.info(f'[{label}] returning {len(results)} valid items')
    return results

# ── Site parsers ──────────────────────────────────────────────────────────────
def parse_our_site(pt):
    base=URLS[pt]['our_site']; val=is_lens if pt=='lenses' else is_camera
    urls_to_scrape=base if isinstance(base,list) else [base]
    products=[]; seen=set()
    for base_url in urls_to_scrape:
        page=1
        while page<=20:
            url=f"{base_url}&p={page}" if page>1 else base_url
            log.info(f'[Our Site] page {page} ({base_url.split("/")[-1][:30]})')
            html=zenrows_js(url,wait=8000)
            if not html:
                log.warning(f'[Our Site] no HTML p{page}, retrying with wait=15000')
                html=zenrows_js(url,wait=15000)
            if not html: break
            soup=BeautifulSoup(html,'lxml')
            items=soup.select('li.product-item,.product-item-info')
            if not items:
                items=soup.select('[class*="product-item"]')
            # If page returns 0 items, retry once with longer wait
            if not items:
                log.warning(f'[Our Site] 0 items on p{page}, retrying with wait=20000')
                html=zenrows_js(url,wait=20000)
                if html:
                    soup=BeautifulSoup(html,'lxml')
                    items=soup.select('li.product-item,.product-item-info')
                    if not items: items=soup.select('[class*="product-item"]')
            log.info(f'[Our Site] found {len(items)} items p{page}')
            if not items: break
            nf=0; rejected=0
            for item in items:
                try:
                    ne=item.select_one('.product-item-name a,.product-item-link')
                    le=item.select_one('a.product-item-link,.product-item-name a')
                    if not ne: continue
                    name=ne.get_text(strip=True)
                    link=le['href'] if le and le.get('href') else ''
                    if link in seen: continue
                    seen.add(link); name=fix_arabic(name,link,val)
                    if not val(name):
                        rejected+=1
                        if rejected<=3: log.info(f'[Our Site] REJECTED: {name[:60]}')
                        continue
                    # Skip DISCONTINUED products
                    item_html_lower=str(item).lower()
                    if 'discontinued' in item_html_lower:
                        log.info(f'[Our Site] SKIP DISCONTINUED: {name[:60]}')
                        continue
                    price=None
                    for sel in ['[data-price-type="finalPrice"] .price','.special-price .price','.price-box .price','.price']:
                        pe=item.select_one(sel)
                        if pe:
                            price=pparse(pe.get_text(strip=True))
                            if price: break
                    if len(products)==0:
                        pe_raw=item.select_one('.price')
                        log.info(f'[Our Site] first price raw: {pe_raw.get_text(strip=True) if pe_raw else "NOT FOUND"}')
                    # Availability
                    if ('out-of-stock' in item_html_lower or 'out of stock' in item_html_lower or
                        'notify' in item_html_lower or 'sold-out' in item_html_lower or
                        'product-item-info-unavailable' in item_html_lower or
                        'enquire now' in item_html_lower or 'enquire-now' in item_html_lower or
                        'استفسر' in str(item)):
                        avail='Out of Stock'
                    elif item.select_one('[class*="tocart"],[class*="to-cart"],[title*="Cart"]'):
                        avail='In Stock'
                    elif not price:
                        avail='Out of Stock'
                    else:
                        avail='In Stock'
                    products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
                except Exception as e: log.debug(f'[Our Site] {e}')
            log.info(f'[Our Site] p{page}: {nf} valid, {rejected} rejected')
            if nf==0 and len(items)==0: break  # Only break if no items at all
            if nf==0 and page>1: break  # Break if we got items but none valid on subsequent pages
            page+=1
    log.info(f'[Our Site] {pt}: {len(products)}'); return products

def parse_qomra(pt):
    base=URLS[pt]['qomra']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=20:
        url=f"{base}&page={page}" if page>1 else base
        log.info(f'[Qomra] page {page}')
        html=zenrows_js(url,wait=15000)
        if html:
            test=BeautifulSoup(html,'lxml')
            if not test.select('custom-salla-product-card,s-product-card-entry'):
                log.info('[Qomra] retrying with wait=20000')
                html=zenrows_js(url,wait=20000)
        if not html: break
        results=salla_parse(html,'https://qomra.pro','Qomra',val)
        new=[p for p in results if p['url'] not in seen]
        for p in new: seen.add(p['url'])
        products.extend(new)
        log.info(f'[Qomra] page {page}: {len(new)} new items (total {len(products)})')
        if not new: break  # Stop when no new items found
        page+=1
    log.info(f'[Qomra] {pt}: {len(products)}'); return products

def parse_mestores(pt):
    base=URLS[pt]['mestores'].format(page=1); val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set()
    # Me Stores uses infinite scroll but also supports ?page=N
    # Fetch page=1 (with scroll) and page=2 to catch all products
    pages_to_fetch=[base, base.replace('page=1','page=2')]
    for page_num, page_url in enumerate(pages_to_fetch, 1):
        log.info(f'[Me Stores] page {page_num}')
        html=zenrows_js(page_url,wait=15000,scroll=True)
        if not html:
            html=zenrows_js(page_url,wait=20000)
        if not html:
            continue
        soup=BeautifulSoup(html,'lxml')
        anchors=soup.select('a[href*="/en_sa/"]')
        anchors=[a for a in anchors if re.search(r'/en_sa/[a-z0-9-]+-\d+', a.get('href',''))]
        if not anchors:
            anchors=[a for a in soup.select('a[href]')
                     if '/en_sa/' in a.get('href','') and
                     re.search(r'-\d{3,}', a.get('href','')) and
                     a.get('href','').count('/') >= 3]
        log.info(f'[Me Stores] found {len(anchors)} anchors on page {page_num}')
        if not anchors:
            if page_num==1:
                body=soup.find('body'); log.warning(f'[Me Stores] snippet: {str(body)[:200] if body else ""}')
            break
        nf=0; nrej=0
        for a in anchors:
            try:
                link=a.get('href','').strip()
                if not link.startswith('http'): link='https://mestores.com'+link
                if link in ('https://mestores.com','https://mestores.com/en_sa'): continue
                if link in seen: continue
                seen.add(link)
                name=''; bl=0
                for img in a.select('img[alt]'):
                    alt=img.get('alt','').strip()
                    if alt.lower() in ('tabby','tamara','sar','') or len(alt)<10: continue
                    if len(alt)>bl: name=alt; bl=len(alt)
                if not name:
                    for el in a.select('h1,h2,h3,h4,p,[class*="name"],[class*="title"]'):
                        t=el.get_text(strip=True)
                        if len(t)>10: name=t; break
                if not name: continue
                if '|' in name: name = name.split('|')[0].strip()
                if ' + ' in name: name = name.split(' + ')[0].strip()
                name=fix_arabic(name,link,val)
                if not val(name):
                    nrej+=1
                    log.info(f'[Me Stores] REJECTED ({pt}): "{name[:80]}"')
                    continue
                pe=a.select_one('[class*="priceAmount"],[class*="priceValue"]')
                price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
                if not price:
                    for el in a.select('span'):
                        p=pparse(tr_east(el.get_text(strip=True)))
                        if p and p>100: price=p; break
                avail=detect_avail(a)
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[Me Stores] {e}')
        log.info(f'[Me Stores] found {nf} new valid on page {page_num}, {nrej} rejected')
        # If page 2 returns 0 new items, stop
        if nf==0 and page_num>1: break
    log.info(f'[Me Stores] {pt}: {len(products)}'); return products

def parse_abdulwahed(pt):
    val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set()
    log.info(f'[Abdulwahed] scraping via search ({pt})')
    import requests as _req
    from urllib.parse import quote as _quote
    headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    # Use multiple targeted search queries to get all Sony cameras/lenses
    # The main search endpoint returns 32 items per query, so use specific queries
    queries = {
        'lenses': ['sony lens fe', 'sony fe 50mm', 'sony fe 24', 'sony fe 85', 'sony fe 70', 'sony fe 16', 'sony fe 35', 'sony fe 90', 'sony fe 12', 'sony fe 135', 'sony fe 200'],
        'cameras': ['sony alpha camera', 'sony ilce camera', 'sony zv camera', 'sony a7 camera', 'sony a9 camera', 'sony fx camera', 'sony a6 camera', 'sony alpha 7', 'sony mirrorless'],
    }
    for q in queries[pt]:
        try:
            url = f'https://www.abdulwahed.com/en/search?q={_quote(q)}'
            r = _req.get(url, headers=headers, timeout=15)
            if r.status_code != 200: continue
            import re as _re, json as _json
            m = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
            if not m: continue
            data = _json.loads(m.group(1))
            prods = data['props']['pageProps']['searchResults'].get('products', [])
            for p in prods:
                name = p.get('name','')
                if isinstance(name, list): name = name[0] if name else ''
                if not name or len(name) < 5: continue
                brand = p.get('brand','')
                if isinstance(brand, list): brand = brand[0] if brand else ''
                if brand.lower() not in ('sony',''): continue
                if ' + ' in name: name = name.split(' + ')[0].strip()
                sku = p.get('sku','')
                if isinstance(sku, list): sku = sku[0] if sku else ''
                # Build correct product URL: abdulwahed.com/en/product/{url_key}
                url_key = p.get('url_key','')
                if isinstance(url_key, list): url_key = url_key[0] if url_key else ''
                if url_key:
                    link = f"https://www.abdulwahed.com/en/product/{url_key}"
                else:
                    # Fallback: extract slug from store.awahed.com URL
                    raw = p.get('url','')
                    if isinstance(raw, list): raw = raw[0] if raw else ''
                    slug = raw.split('/en/')[-1] if '/en/' in raw else ''
                    link = f"https://www.abdulwahed.com/en/product/{slug}" if slug else ''
                key = sku or name
                if key in seen: continue
                seen.add(key)
                name = fix_arabic(name, link, val)
                if not val(name): continue
                price = None
                ptax = p.get('prices_with_tax') or p.get('price_incl_tax') or {}
                if isinstance(ptax, dict):
                    price = ptax.get('discounted_price') or ptax.get('price') or ptax.get('original_price')
                elif isinstance(ptax, (int,float,str)):
                    try: price = float(ptax)
                    except: pass
                try: price = float(price) if price else None
                except: price = None
                products.append({'name':name,'price':price,'availability':'In Stock','url':link})
        except Exception as e:
            log.debug(f'[Abdulwahed] query "{q}" error: {e}')
        time.sleep(0.5)
    log.info(f'[Abdulwahed] {pt}: {len(products)}'); return products

def parse_amazon(pt):
    base=URLS[pt]['amazon']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=15:
        url=f"{base}&page={page}" if page>1 else base
        log.info(f'[Amazon SA] page {page}')
        html=zenrows_std(url)
        if not html: break
        soup=BeautifulSoup(html,'lxml')
        items=[i for i in soup.select('[data-component-type="s-search-result"]') if i.get('data-asin')]
        log.info(f'[Amazon SA] found {len(items)} items p{page}')
        if not items:
            body=soup.find('body'); log.warning(f'[Amazon SA] snippet: {str(body)[:300] if body else html[:300]}')
            break
        nf=0
        for item in items:
            try:
                ne=(item.select_one('.a-size-medium.a-color-base.a-text-normal') or
                    item.select_one('.a-size-base-plus.a-color-base.a-text-normal') or
                    item.select_one('h2 a span') or item.select_one('h2'))
                if not ne: continue
                name=ne.get_text(strip=True)
                if not name or len(name)<5: continue
                # Must be Sony brand — check name, brand row, or full item text
                brand_el=item.select_one('.a-row.a-size-base.a-color-secondary span')
                brand_text=(brand_el.get_text().lower() if brand_el else '')
                item_text=item.get_text().lower()
                if 'sony' not in name.lower() and 'sony' not in brand_text and 'sony' not in item_text[:200]:
                    continue
                pw=item.select_one('.a-price-whole'); pf=item.select_one('.a-price-fraction')
                price=None
                if pw:
                    ps=pw.get_text(strip=True).replace(',','').replace('.','')
                    ps+='.'+pf.get_text(strip=True) if pf else '.00'
                    try: price=float(ps)
                    except: pass
                avail='Out of Stock' if not price else 'In Stock'
                if 'currently unavailable' in item_text or 'out of stock' in item_text:
                    avail='Out of Stock'
                le=item.select_one('h2 a'); href=le.get('href','') if le else ''
                if '/dp/' in href:
                    asin=href.split('/dp/')[1].split('/')[0]
                    link=f'https://www.amazon.sa/dp/{asin}'
                else:
                    asin=item.get('data-asin','')
                    link=f'https://www.amazon.sa/dp/{asin}' if asin else ''
                if not link or link in seen: continue
                seen.add(link)
                if page==1 and nf<5: log.info(f'[Amazon SA] candidate: {name[:80]}')
                name=fix_arabic(name,link,val)
                if not val(name):
                    if page==1 and nf<3: log.info(f'[Amazon SA] REJECTED: {name[:80]}')
                    continue
                if not name.lower().startswith('sony'): name='Sony '+name
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[Amazon SA] {e}')
        nxt=soup.select_one('.s-pagination-next:not(.s-pagination-disabled)')
        if not nxt: break
        page+=1; time.sleep(2)
    log.info(f'[Amazon SA] {pt}: {len(products)}'); return products

def parse_noon(pt):
    base=URLS[pt]['noon']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=10:
        url=f"{base}?page={page}" if page>1 else base
        log.info(f'[Noon] page {page}')
        html=zenrows_js(url,wait=10000)
        if not html: break
        soup=BeautifulSoup(html,'lxml')
        items=(soup.select('[data-qa="product-block"]') or
               soup.select('[class*="ProductBlock"]') or
               soup.select('[class*="product-block"]') or
               soup.select('[class*="productContainer"]') or
               [a.find_parent('div') for a in soup.select('a[href*="/p/"]') if a.find_parent('div')])
        seen_ids=set(); unique=[]
        for it in items:
            if id(it) not in seen_ids: seen_ids.add(id(it)); unique.append(it)
        items=unique
        log.info(f'[Noon] found {len(items)} items p{page}')
        if not items:
            body=soup.find('body'); log.warning(f'[Noon] snippet: {str(body)[:300] if body else html[:300]}')
            break
        nf=0
        for item in items:
            try:
                ne=(item.select_one('[data-qa="product-name"]') or item.select_one('[class*="productTitle"]') or
                    item.select_one('[class*="product-title"]') or item.select_one('[class*="name"]') or
                    item.select_one('h3') or item.select_one('h2'))
                le=item.select_one('a[href*="/p/"]') or item.select_one('a[href]')
                if not ne or not le: continue
                name=ne.get_text(strip=True)
                link=le.get('href','')
                if not link.startswith('http'): link='https://www.noon.com'+link
                if link in seen: continue
                seen.add(link)
                if nf<3: log.info(f'[Noon] candidate: {name[:80]}')
                if 'sony' not in norm(name): continue
                name=fix_arabic(name,link,val)
                if not val(name): continue
                price=None
                for sel in ['[data-qa="price-amount"]','[class*="priceNow"]','[class*="selling-price"]','[class*="sellingPrice"]','[class*="price-now"]']:
                    pe=item.select_one(sel)
                    if pe:
                        price=pparse(tr_east(pe.get_text(strip=True)))
                        if price and price>100: break
                if not price:
                    for el in item.select('span,strong'):
                        txt=tr_east(el.get_text(strip=True)).replace(',','').strip()
                        if re.match(r'^\d{3,5}$',txt):
                            p=pparse(txt)
                            if p and 100<p<100000: price=p; break
                avail=detect_avail(item)
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[Noon] {e}')
        nxt=soup.select_one('[aria-label="Next"],[class*="nextPage"],[class*="next-page"]')
        if not nxt or nf==0: break
        page+=1; time.sleep(2)
    log.info(f'[Noon] {pt}: {len(products)}'); return products

def parse_cameramix(pt):
    # For lenses: use /Sony page (all-brands lenses page has no Sony filter)
    # For cameras: use /Sony page, stop when no new cameras found
    if pt=='lenses':
        base='https://www.cameramix.com/Sony'
    else:
        base=URLS[pt]['cameramix']
    val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=20:
        url=f"{base}?page={page}" if page>1 else base
        log.info(f'[CameraMix] page {page}')
        html=zenrows_std(url)
        if not html: break
        results=opencart_parse(html,'https://www.cameramix.com','CameraMix',val)
        if not results and page==1:
            log.info('[CameraMix] 0 items p1, retrying with JS render')
            html=zenrows_js(url,wait=8000)
            if html: results=opencart_parse(html,'https://www.cameramix.com','CameraMix',val)
        new=[p for p in results if p['url'] not in seen]
        for p in new: seen.add(p['url'])
        products.extend(new)
        if not html: break
        soup=BeautifulSoup(html,'lxml')
        nxt=soup.select_one('ul.pagination li.active + li a,[aria-label="Next"]')
        if not nxt: break
        if not new and pt=='cameras': break  # cameras: stop when no new cameras found
        # lenses: continue through all pages (lenses scattered across /Sony pages)
        page+=1; time.sleep(1.5)
    log.info(f'[CameraMix] {pt}: {len(products)}'); return products

def parse_pclub(pt):
    base=URLS[pt]['pclub']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set()
    log.info(f'[PClub] page 1')
    html=zenrows_js(base,wait=8000)
    if html:
        results=opencart_parse(html,'https://pclub.com.sa','PClub',val)
        for p in results:
            if p['url'] not in seen:
                seen.add(p['url']); products.append(p)
    log.info(f'[PClub] {pt}: {len(products)}'); return products

def parse_camtime(pt):
    base=URLS[pt]['camtime']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=10:
        sep='&' if '?' in base else '?'
        url=base if page==1 else f"{base}{sep}page={page}"
        log.info(f'[CamTime] page {page}')
        if pt=='lenses':
            # Parent lenses category works with plain HTTP (OpenCart structure)
            html=plain(url,ssl=False)
            if not html: html=zenrows_js(url,wait=8000)
            if not html: break
            results=opencart_parse(html,'https://camtime.sa','CamTime',val)
        else:
            # CamTime cameras page works with plain HTTP (OpenCart)
            html=plain(url,ssl=False)
            if not html: html=zenrows_js(url,wait=8000)
            if not html: break
            results=opencart_parse(html,'https://camtime.sa','CamTime',val)
        new=[p for p in results if p['url'] not in seen]
        for p in new: seen.add(p['url'])
        products.extend(new)
        if not new: break
        soup=BeautifulSoup(html,'lxml')
        nxt=soup.select_one('ul.pagination li.active + li a,[aria-label="Next"]')
        if not nxt: break
        page+=1; time.sleep(1.5)
    log.info(f'[CamTime] {pt}: {len(products)}'); return products

def parse_alamcam(pt):
    base=URLS[pt]['alamcam']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=10:
        sep='&' if '?' in base else '?'
        url=base if page==1 else f"{base}{sep}page={page}"
        log.info(f'[AlamCam] page {page}')
        html=plain(url)
        if not html: html=zenrows_js(url,wait=8000)
        if not html: break
        soup=BeautifulSoup(html,'lxml')
        items=(soup.select('.product-layout,.product-thumb') or soup.select('.product-item') or soup.select('[class*="product-card"]'))
        items=[i for i in items if i.select_one('a[href]')]
        log.info(f'[AlamCam] found {len(items)} items p{page}')
        if not items:
            body=soup.find('body'); log.warning(f'[AlamCam] snippet: {str(body)[:300] if body else ""}')
            break
        seen_u=set(); deduped=[]
        for item in items:
            a=item.select_one('a[href*="alamcam"]') or item.select_one('a[href^="/"]')
            href=a.get('href','') if a else ''
            if href and href not in seen_u: seen_u.add(href); deduped.append(item)
        items=deduped if deduped else items
        nf=0; logged=0
        for item in items:
            try:
                ne=(item.select_one('.caption h4 a') or item.select_one('h4 a') or
                    item.select_one('h3 a') or item.select_one('.name a') or item.select_one('a'))
                if not ne: continue
                name=ne.get_text(strip=True)
                link=ne.get('href','') if ne.name=='a' else ''
                if not link:
                    la=item.select_one('a[href]'); link=la.get('href','') if la else ''
                if not link: continue
                if not link.startswith('http'): link='https://alamcam.sa'+link
                if link in seen: continue
                seen.add(link)
                if logged<3: log.info(f'[AlamCam] candidate: {name[:80]}'); logged+=1
                if 'sony' not in norm(name): continue
                # For lenses: require Sony as brand, not just "for Sony E-Mount"
                if pt=='lenses':
                    n=norm(name)
                    if not n.startswith('sony') and not any(s in n for s in ['sony fe','sony e ','sony g ','sony sel','vario-tessar']):
                        continue
                name=fix_arabic(name,link,val)
                if not val(name): continue
                pe=item.select_one('.price-new,.price-normal,.price')
                price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
                avail=detect_avail(item)
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[AlamCam] {e}')
        log.info(f'[AlamCam] p{page}: {nf} Sony products')
        nxt=soup.select_one('ul.pagination li.active + li a,[aria-label="Next"],.next a')
        if not nxt: break
        page+=1; time.sleep(1.5)
    log.info(f'[AlamCam] {pt}: {len(products)}'); return products

def parse_camerabox(pt):
    url=URLS[pt]['camerabox']; val=is_lens if pt=='lenses' else is_camera
    products=[]; log.info(f'[CameraBox] fetching with scroll')
    try:
        html=zenrows_js(url,wait=8000,scroll=True)
        if html:
            results=salla_parse(html,'https://camerabox.com.sa','CameraBox',val)
            if not results:
                log.info('[CameraBox] retrying with wait=15000')
                html=zenrows_js(url,wait=15000,scroll=True)
                if html:
                    results=salla_parse(html,'https://camerabox.com.sa','CameraBox',val)
            products.extend(results)
    except Exception as e: log.error(f'[CameraBox] {e}')
    log.info(f'[CameraBox] {pt}: {len(products)}'); return products

# ── Matching ──────────────────────────────────────────────────────────────────
def focal(n):
    m=re.search(r'(\d+)(?:-(\d+))?\s*mm',n,re.I)
    return (int(m.group(1)),int(m.group(2)) if m.group(2) else int(m.group(1))) if m else None

def aperture(n):
    m=re.search(r'f/?(\d+\.?\d*)',n,re.I)
    return float(m.group(1)) if m else None

def lens_score(a,b):
    na,nb=norm(a),norm(b); fa,fb=focal(na),focal(nb)
    if not fa or not fb or fa!=fb: return 0
    sc=80; aa,ab=aperture(na),aperture(nb)
    if aa and ab and abs(aa-ab)<0.1: sc+=30
    for mt in ['fe ','e-mount','e mount','a-mount']:
        if mt in na and mt in nb: sc+=25; break
    for s in ['g master','gm','g lens','zeiss']:
        if s in na and s in nb: sc+=15
    return sc

def models_match(ma,mb):
    if ma&mb: return True
    if 'a7' in ma and any(m.startswith('a7') for m in mb): return True
    if 'a7' in mb and any(m.startswith('a7') for m in ma): return True
    # ILCE code cross-matching: ilce-7rm6 ↔ a7r6, ilce-7rm5 ↔ a7r5, etc.
    def ilce_to_model(s):
        m=re.match(r'ilce-7r?m(\d)',s)
        if m: return f'a7r{m.group(1)}'
        m=re.match(r'ilce-7sm(\d)',s)
        if m: return f'a7s{m.group(1)}'
        m=re.match(r'ilce-7cm(\d)',s)
        if m: return f'a7c{m.group(1)}'
        m=re.match(r'ilce-7m(\d)',s)
        if m: return f'a7{m.group(1)}'
        if re.match(r'ilce-7cr',s): return 'a7cr'
        return None
    for x in ma:
        eq=ilce_to_model(x)
        if eq and eq in mb: return True
    for x in mb:
        eq=ilce_to_model(x)
        if eq and eq in ma: return True
    return False

def cam_score(a,b):
    na,nb=norm(a),norm(b)

    def extract_models(n):
        # Normalize roman numerals to digits
        n=re.sub(r'\bviii\b','8',n); n=re.sub(r'\bvii\b','7',n)
        n=re.sub(r'\bvi\b','6',n); n=re.sub(r'\biv\b','4',n)
        n=re.sub(r'\biii\b','3',n); n=re.sub(r'\bii\b','2',n)
        n=re.sub(r'\bv\b(?!\w)','5',n)
        # Also handle when roman numerals are directly appended (a7v, a7iv, a7iii)
        n=re.sub(r'\ba7v\b','a75',n); n=re.sub(r'\ba7iv\b','a74',n)
        n=re.sub(r'\ba7iii\b','a73',n); n=re.sub(r'\ba7ii\b','a72',n)
        # "alpha a6400" → "a6400", "alpha 7" → "a7", handles both "alpha NNN" and "alpha aNNN"
        n=re.sub(r'\balpha\s+a?(\d)',r'a\1',n)
        # a7cm2/a7cii/a7c2 → a7c2
        n=re.sub(r'\ba7c\s*m?(\d)\b',r'a7c\1',n)
        n=re.sub(r'\ba7c\b(?!\d)','a7c1',n)
        # ── ZV normalization ─────────────────────────────────────────────────
        # Handle no-hyphen variants from URL slugs: "zv e10" → "zv-e10"
        n=re.sub(r'\bzv\s+e10\s*m2[k]?\b','zv-e10gen2',n)  # "zv e10m2k" → gen2
        n=re.sub(r'\bzv\s+e10\s+2\b','zv-e10gen2',n)        # "zv e10 2" → gen2
        n=re.sub(r'\bzv\s+e10\b','zv-e10',n)                # "zv e10" → "zv-e10"
        n=re.sub(r'\bzv\s+e1\b','zv-e1',n)                  # "zv e1" → "zv-e1"
        n=re.sub(r'\bzv\s+1\b','zv-1',n)                    # "zv 1" → "zv-1"
        # ZV-E10M2K / ZV-E10M2 = ZV-E10 gen2
        n=re.sub(r'\bzv-e10\s*m2[k]?\b','zv-e10gen2',n)
        # ZV-1 II and ZV-1M2 → zv-1gen2
        n=re.sub(r'\bzv-1\s*m2\b','zv-1gen2',n)
        n=re.sub(r'\bzv-1\s+2\b','zv-1gen2',n)
        # ZV-1F is its own model
        n=re.sub(r'\bzv-1f\b','zv-1f',n)
        # Bare ZV-1 (no suffix) → first gen
        n=re.sub(r'\bzv-1\b(?!gen|\s*[m\d])','zv-1gen1',n)
        # ZV-E10 II → zv-e10gen2
        n=re.sub(r'\bzv-e10\s*2\b','zv-e10gen2',n)
        n=re.sub(r'\bzv-e10\s+2\s','zv-e10gen2 ',n)
        # ZV-E10 / ZV-E10K (kit) → zv-e10gen1
        n=re.sub(r'\bzv-e10[klb]?\b','zv-e10gen1',n)
        models=set()
        # a7 letter variants WITH generation (a7r5, a7s3, a7c2)
        for m in re.finditer(r'\ba7([rsc])\s*(\d)\b',n): models.add(f'a7{m.group(1)}{m.group(2)}')
        # a7 + generation (a7 4, a7 5 etc — after roman numeral normalization)
        for m in re.finditer(r'\ba7\s+(\d)\b',n): models.add(f'a7{m.group(1)}')
        # a7V, a7IV written without space (common abbreviation)
        for m in re.finditer(r'\ba7([2-9])\b',n): models.add(f'a7{m.group(1)}')
        # bare a7 = wildcard matches any a7 generation
        if re.search(r'\ba7\b(?!\s*[rscm\d])',n): models.add('a7')
        # a9 models
        for m in re.finditer(r'\ba9\s*(\d?)\b',n): models.add('a9'+(m.group(1) or ''))
        # a6xxx models
        for m in re.finditer(r'\ba6[0-9]{3}[a-z]?\b',n): models.add(m.group(0))
        # a1
        for m in re.finditer(r'\ba1\b',n): models.add('a1')
        # a7CR (no generation number — standalone model)
        if re.search(r'\ba7cr\b',n): models.add('a7cr')
        if re.search(r'\bilce-7cr\b',n): models.add('a7cr')
        # ZV models (after normalization)
        for m in re.finditer(r'\bzv-1gen\d\b',n): models.add(m.group(0))
        if re.search(r'\bzv-1f\b',n): models.add('zv-1f')
        for m in re.finditer(r'\bzv-e10gen\d\b',n): models.add(m.group(0))
        for m in re.finditer(r'\bzv-[a-z]\w+\b',n):
            mv=m.group(0)
            if 'gen' not in mv: models.add(mv)  # other ZV models like zv-e1
        # FX models (cinema line)
        for m in re.finditer(r'\bfx[23679][a0]?\b',n): models.add(m.group(0))
        for m in re.finditer(r'\bfx9\b',n): models.add('fx9')
        for m in re.finditer(r'\bpxw-\w+',n): models.add(m.group(0))
        # ILCE / ILME
        for m in re.finditer(r'\bilce-[\w-]+',n): models.add(m.group(0))
        for m in re.finditer(r'\bilme-[\w-]+',n): models.add(m.group(0))
        return models

    ma=extract_models(na); mb=extract_models(nb)
    if not ma or not mb:
        return min(70,len(set(na.split())&set(nb.split()))*15)
    if not models_match(ma,mb): return 0

    # Extract kit lens from name (e.g. "with 28-60mm", "with 16-50mm", "18-135mm lens")
    def kit_lens(n):
        # Normalize OSS II / OSS generation before focal extraction
        # Only tag with gen2 when explicit "OSS II" / "OSS 2" marker present.
        # Plain "OSS" or no OSS = no tag (matches both gens), so gen1 stores
        # (Amazon, CamTime, AlamCam) that just say "16-50mm Lens" still match.
        n_oss=n
        if re.search(r'16-50.*oss\s+2\b|16-50.*oss\s*ii\b|16-50.*oss\s*mark\s*2\b|pz\s*16-50.*ii\b|e pz 16-50.*ii\b|16-50mm\s+f[/\d\.\s-]+ii\b',n_oss):
            n_oss=re.sub(r'16-50\s*mm','16-50mm_gen2',n_oss)
        # No else — plain OSS or no-OSS stays untagged so it can match either gen
        n=n_oss
        # Match focal range: 16-50mm or 16 50mm (slug-derived)
        m=re.search(r'(\d{2,3}[-\s]\d{2,3}\s*mm(?:_gen\d)?)',n)
        if m:
            raw=m.group(1).strip()
            fl=re.sub(r'(\d)\s+mm','\\1mm',raw)
            fl=re.sub(r'(\d)\s+(\d)','\\1-\\2',fl)
            fl=re.sub(r'mm\s+gen','mm_gen',fl)  # normalize spacing in gen tag
            has_lens_kw=any(x in n for x in ['with lens','with '+m.group(1)[:6],'kit','mm lens',' lens ',' oss',' gm',' g lens'])
            after=n[n.find(m.group(1))+len(m.group(1)):]
            has_aperture=bool(re.search(r'^\s*f[/\s]?\d',after))
            if has_lens_kw or has_aperture:
                return fl
        # Single focal length with lens keyword
        m2=re.search(r'(\d{2,3}\s*mm)',n)
        if m2 and any(x in n for x in ['with lens','kit',' lens ',' gm ',' oss ']):
            return re.sub(r'\s+','',m2.group(1))
        # Focal range WITHOUT mm suffix: e.g. "with 16-50 lens kit", "16-50 lens"
        m3=re.search(r'(\d{2,3})[-\s](\d{2,3})(?!\s*mm)(?=\s*(lens|kit|oss|gm|zoom))',n)
        if m3:
            return f'{m3.group(1)}-{m3.group(2)}mm'
        return None

    a_kit_lens=kit_lens(na); b_kit_lens=kit_lens(nb)
    a_body=any(x in na for x in ['body only','body-only','(body only)','body ('])
    b_body=any(x in nb for x in ['body only','body-only','(body only)','body ('])
    a_has_kit=bool(a_kit_lens)
    b_has_kit=bool(b_kit_lens)
    # "bare" name = no body/kit info at all (just model name, no lens or body-only)
    a_bare=not a_has_kit and not a_body
    b_bare=not b_has_kit and not b_body

    # HARD RULE: body-only vs kit = no match
    if a_body and b_has_kit: return 0
    if b_body and a_has_kit: return 0

    # HARD RULE: different kit lenses = no match (18-135mm != 16-50mm)
    if a_kit_lens and b_kit_lens and a_kit_lens!=b_kit_lens: return 0

    # HARD RULE: one has a hardware bundle extra, the other doesn't
    BUNDLE_EXTRAS=['xlr handle','xlr kit','shooting grip','wireless remote','microphone kit',
                   'creator kit','vlogger kit','handle unit']
    a_extras=[x for x in BUNDLE_EXTRAS if x in na]
    b_extras=[x for x in BUNDLE_EXTRAS if x in nb]
    if a_extras!=b_extras: return 0

    # HARD RULE: a1 vs a1 II (a1m2) are different generations
    a_has_a1m2=bool(re.search(r'\ba1\s*m2\b|\bilce-1m2\b',na))
    b_has_a1m2=bool(re.search(r'\ba1\s*m2\b|\bilce-1m2\b',nb))
    a_has_a1_bare=bool(re.search(r'\ba1\b',na)) and not a_has_a1m2
    b_has_a1_bare=bool(re.search(r'\ba1\b',nb)) and not b_has_a1m2
    if (a_has_a1m2 and b_has_a1_bare) or (b_has_a1m2 and a_has_a1_bare): return 0

    # HARD RULE: explicit color conflict → different products
    COLORS=['black','silver','white','blue','green','red']
    a_colors=[c for c in COLORS if c in na]
    b_colors=[c for c in COLORS if c in nb]
    if a_colors and b_colors and set(a_colors)!=set(b_colors): return 0

    # HARD RULE: kit vs bare (no body/kit info) → no match
    # A kit with a specific lens should never match a bare product name
    if a_has_kit and b_bare: return 0
    if b_has_kit and a_bare: return 0

    # Base score
    score=100
    # Bonus: exact same type (both same kit lens, or both body-only)
    if (a_has_kit and b_has_kit) or (a_body and b_body): score+=20
    # Color bonus
    for color in ['black','silver','white','blue','green']:
        if color in na and color in nb: score+=5; break

    # Lower score when one side is "bare" (no body/kit info) — so greedy assignment
    # always prefers specific-vs-specific matches over bare-vs-specific matches.
    # e.g. "a6400 with 16-50mm" vs "a6400 with 16-50mm" = 120 (wins first)
    # then "a6400 bare" vs "a6400 body only" = 90 (gets the remaining product)
    if a_bare and b_has_kit: score=min(score,85)
    if b_bare and a_has_kit: score=min(score,85)
    if a_bare and b_body: score=min(score,90)
    if b_bare and a_body: score=min(score,90)

    return score

def find_match(our,comps,pt):
    sc=lens_score if pt=='lenses' else cam_score
    bs=0; bm=None
    for cp in comps:
        s=sc(our['name'],cp['name'])
        if s>bs: bs=s; bm=cp
    return bm if bs>=80 else None

# ── Build rows ────────────────────────────────────────────────────────────────
def build_rows(our,comp_data,pt):
    rows=[]; ts=datetime.now().strftime('%Y-%m-%d %H:%M')
    sc=lens_score if pt=='lenses' else cam_score

    # Pre-compute greedy assignments: for each source, map our_url → best comp product
    assignments={src:{} for src in COMPETITORS}  # src → {our_url: comp_product}
    used_comp={src:set() for src in COMPETITORS}  # src → set of used comp URLs

    for src in COMPETITORS:
        comps=comp_data.get(src,[])
        if not comps: continue
        pairs=[]
        for o in our:
            for cp in comps:
                s=sc(o['name'],cp['name'])
                if s>=80: pairs.append((s,o['url'],cp['url'],o,cp))
        pairs.sort(key=lambda x:-x[0])
        assigned_our=set(); assigned_comp=set()
        for s,our_url,comp_url,o,cp in pairs:
            if our_url in assigned_our or comp_url in assigned_comp: continue
            assignments[src][our_url]=cp
            assigned_our.add(our_url); assigned_comp.add(comp_url)
            used_comp[src].add(comp_url)

    # Build rows for our products
    for o in our:
        row={'timestamp':ts,'name':o['name'],'our_price':o['price'],
             'our_availability':o['availability'],'our_url':o['url']}
        pfl=[(OUR_SITE,o['price'],o['url'])] if o['price'] and o['availability']=='In Stock' else []
        for src in COMPETITORS:
            m=assignments[src].get(o['url'])
            if m:
                diff=round(m['price']-o['price'],2) if m['price'] and o['price'] else None
                st=('Cheaper than competitor' if diff and diff>0 else
                    'More expensive' if diff and diff<0 else
                    'Same price' if diff==0 else 'Not listed')
                row[src]={'url':m['url'],'price':m['price'],'availability':m['availability'],'diff':diff,'status':st}
                if m['price'] and m['availability']=='In Stock': pfl.append((src,m['price'],m['url']))
            else:
                row[src]={'url':'','price':None,'availability':'','diff':None,'status':'Not listed'}
        if pfl:
            ch=min(pfl,key=lambda x:x[1])
            row['lowest_price']=ch[1]; row['cheapest_brand']=ch[0]; row['cheapest_link']=ch[2]
            row['our_diff_vs_cheapest']=round((o['price'] or 0)-ch[1],2)
        else:
            row['lowest_price']=row['cheapest_brand']=row['cheapest_link']=None
            row['our_diff_vs_cheapest']=None
        rows.append(row)

    # Consolidate unmatched competitor products:
    # Group all unmatched products across all sources by product similarity,
    # so the same product from multiple competitors appears in ONE row.
    unmatched=[]
    for src in COMPETITORS:
        for cp in comp_data.get(src,[]):
            if cp['url'] not in used_comp[src]:
                unmatched.append((src,cp))

    # Cluster unmatched products: same product = score >= 80 between them
    clusters=[]  # list of {src: cp}
    for src,cp in unmatched:
        placed=False
        for cluster in clusters:
            # Compare against any existing member of the cluster
            rep_src,rep_cp=next(iter(cluster.items()))
            if sc(cp['name'],rep_cp['name'])>=80:
                if src not in cluster:  # only one product per source per cluster
                    cluster[src]=cp
                    placed=True
                    break
        if not placed:
            clusters.append({src:cp})

    # Each cluster becomes one row
    for cluster in clusters:
        # Use the name from the first source in COMPETITORS order
        name=''; price=None
        for src in COMPETITORS:
            if src in cluster:
                name=cluster[src]['name']
                price=cluster[src]['price']
                break
        row={'timestamp':ts,'name':name,'our_price':None,'our_availability':'Not listed','our_url':''}
        pfl=[]
        for src in COMPETITORS:
            if src in cluster:
                cp=cluster[src]
                row[src]={'url':cp['url'],'price':cp['price'],'availability':cp['availability'],'diff':None,'status':'Not listed'}
                if cp['price'] and cp['availability']=='In Stock': pfl.append((src,cp['price'],cp['url']))
            else:
                row[src]={'url':'','price':None,'availability':'','diff':None,'status':'Not listed'}
        if pfl:
            ch=min(pfl,key=lambda x:x[1])
            row['lowest_price']=ch[1]; row['cheapest_brand']=ch[0]; row['cheapest_link']=ch[2]
        else:
            row['lowest_price']=row['cheapest_brand']=row['cheapest_link']=None
        row['our_diff_vs_cheapest']=None
        rows.append(row)
    return rows

# ── Google Sheets ─────────────────────────────────────────────────────────────
GH=(['Timestamp','Product Name','Our Site (ksa.amt.tv)','',''] +
    sum([[s,'','','',''] for s in COMPETITORS],[]) + ['Summary','','',''])
CH=(['Timestamp','Product Name','Our Price (SAR)','Our Availability','Our Product URL'] +
    ['Product URL','Price (SAR)','Availability','Price Diff (SAR)','Status']*len(COMPETITORS) +
    ['Lowest Price (SAR)','Cheapest Brand','Cheapest Link','Our Price Diff vs Cheapest'])
SH=['Source','Total Products','Cheaper Than Us','More Expensive','Same Price','Not Listed','Updated']
SC={'Cheaper than competitor':{'red':0.20,'green':0.73,'blue':0.40},
    'More expensive':{'red':0.91,'green':0.27,'blue':0.27},
    'Same price':{'red':1.0,'green':0.90,'blue':0.20},
    'Not listed':{'red':0.85,'green':0.85,'blue':0.85}}

def get_client():
    info=json.loads(SA_JSON)
    scopes=['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive']
    return gspread.authorize(Credentials.from_service_account_info(info,scopes=scopes))

def make_url(url):
    """Wrap URL in HYPERLINK formula for Google Sheets."""
    if url and url.startswith('http'):
        # Escape double quotes in URL
        safe = url.replace('"', '%22')
        return f'=HYPERLINK("{safe}","{safe}")'
    return url or ''

def row2list(row):
    out=[row['timestamp'],row['name'],
         row.get('our_price',''),row.get('our_availability',''),make_url(row.get('our_url',''))]
    for s in COMPETITORS:
        d=row.get(s,{})
        out+=[make_url(d.get('url','')),d.get('price',''),d.get('availability',''),d.get('diff',''),d.get('status','')]
    out+=[row.get('lowest_price',''),row.get('cheapest_brand',''),
          make_url(row.get('cheapest_link','')),row.get('our_diff_vs_cheapest','')]
    return out

def make_summary(rows,ts):
    out=[]
    for src in SOURCES:
        if src==OUR_SITE:
            t=sum(1 for r in rows if r.get('our_price')); c=me=sa=nl=0
        else:
            t=sum(1 for r in rows if r.get(src,{}).get('price'))
            c=sum(1 for r in rows if r.get(src,{}).get('status')=='Cheaper than competitor')
            me=sum(1 for r in rows if r.get(src,{}).get('status')=='More expensive')
            sa=sum(1 for r in rows if r.get(src,{}).get('status')=='Same price')
            nl=sum(1 for r in rows if r.get(src,{}).get('status')=='Not listed')
        out.append([src,t,c,me,sa,nl,ts])
    return out

def color_cells(ws,rows,sh):
    reqs=[]
    for ri,row in enumerate(rows):
        sr=ri+2
        for ci,src in enumerate(COMPETITORS):
            st=row.get(src,{}).get('status',''); col=SC.get(st)
            if not col: continue
            colidx=5+ci*5+4
            reqs.append({'repeatCell':{'range':{'sheetId':ws.id,'startRowIndex':sr,'endRowIndex':sr+1,
                'startColumnIndex':colidx,'endColumnIndex':colidx+1},
                'cell':{'userEnteredFormat':{'backgroundColor':col}},
                'fields':'userEnteredFormat.backgroundColor'}})
    for i in range(0,len(reqs),1000): sh.batch_update({'requests':reqs[i:i+1000]})

def write_sheet(client,pt,rows):
    tn='Lenses' if pt=='lenses' else 'Cameras'
    sn='Lenses Summary' if pt=='lenses' else 'Cameras Summary'
    ts=datetime.now().strftime('%Y-%m-%d %H:%M')
    sh=client.open_by_key(GSHEET_ID)
    try: ws=sh.worksheet(tn)
    except gspread.WorksheetNotFound: ws=sh.add_worksheet(title=tn,rows=500,cols=70)
    sh.batch_update({'requests':[{'updateSheetProperties':{'properties':{'sheetId':ws.id,
        'gridProperties':{'columnCount':70}},'fields':'gridProperties.columnCount'}}]})
    ws.clear()
    data=[GH,CH]+[row2list(r) for r in rows]
    ws.update(values=data,range_name='A1',value_input_option='USER_ENTERED')
    color_cells(ws,rows,sh)
    log.info(f'Written {len(rows)} rows to [{tn}]')
    try: ws2=sh.worksheet(sn)
    except gspread.WorksheetNotFound: ws2=sh.add_worksheet(title=sn,rows=20,cols=10)
    ws2.clear()
    ws2.update(values=[SH]+make_summary(rows,ts),range_name='A1',value_input_option='RAW')
    log.info(f'Written summary to [{sn}]')

# ── Main ──────────────────────────────────────────────────────────────────────
COMP_KEYS={
    'Qomra':'qomra','Me Stores':'mestores','Abdulwahed':'abdulwahed',
    'Amazon SA':'amazon','Noon':'noon','CameraMix':'cameramix',
    'PClub':'pclub','CamTime':'camtime','AlamCam':'alamcam','CameraBox':'camerabox',
}
PARSERS={
    'our_site':parse_our_site,'qomra':parse_qomra,'mestores':parse_mestores,
    'abdulwahed':parse_abdulwahed,'amazon':parse_amazon,'noon':parse_noon,
    'cameramix':parse_cameramix,'pclub':parse_pclub,'camtime':parse_camtime,
    'alamcam':parse_alamcam,'camerabox':parse_camerabox,
}

def safe(key,label,pt):
    try: return PARSERS[key](pt)
    except Exception as e: log.error(f'[{label}] FAILED: {e}'); return []

def main():
    import time as _time
    START_TIME = _time.time()
    MAX_SECONDS = 18000  # 5 hours max total scraping time

    def elapsed(): return _time.time() - START_TIME
    def time_ok(): return elapsed() < MAX_SECONDS

    log.info('=== Sony Price Comparison Scraper Started ===')
    client=get_client()
    for pt in ['lenses','cameras']:
        log.info(f'\n─── Scraping {pt.upper()} ───')
        our=safe('our_site','Our Site',pt)
        cd={}
        for label,key in COMP_KEYS.items():
            if not time_ok():
                log.warning(f'Time limit reached, skipping {label}')
                cd[label]=[]
                continue
            cd[label]=safe(key,label,pt)
        rows=build_rows(our,cd,pt)
        write_sheet(client,pt,rows)
        log.info(f'[{pt}] Done — {len(rows)} rows | elapsed {elapsed():.0f}s')
    log.info('=== Scraper Finished ===')

if __name__=='__main__':
    main()
