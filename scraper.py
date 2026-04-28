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
        'qomra':      'https://qomra.pro/en/category/sony-lenses?filters[brand_id]=174800383',
        'mestores':   'https://mestores.com/en_sa/cameras-accessories/lenses?page={page}&brand%5Bfilter%5D=SONY%2C1722',
        'abdulwahed': 'https://www.abdulwahed.com/en/photography-c-868/lenses-c-879',
        'amazon':     'https://www.amazon.sa/s?rh=p_89%3ASony&i=electronics&language=en_AE&k=sony+lens',
        'noon':       'https://www.noon.com/saudi-en/electronics-and-mobiles/camera-and-photo-16165/lenses-16166/?q=sony',
        'cameramix':  'https://www.cameramix.com/Sony',
        'pclub':      'https://pclub.com.sa/sony-1-10?limit=100',
        'camtime':    'https://camtime.sa/%D8%A7%D9%84%D8%B9%D8%AF%D8%B3%D8%A7%D8%AA-%D9%88%D9%85%D9%84%D8%AD%D9%82%D8%A7%D8%AA%D9%87%D8%A71772710825?fm=10',
        'alamcam':    'https://alamcam.sa/%D8%A7%D9%84%D8%B9%D8%AF%D8%B3%D8%A7%D8%AA-%D9%88%D9%85%D9%84%D8%AD%D9%82%D8%A7%D8%AA%D9%87%D8%A7',
        'camerabox':  'https://camerabox.com.sa/en/sony/brand-1380282655',
    },
    'cameras': {
        'our_site':   'https://ksa.amt.tv/camcorders-digital-cameras/photography/digital-camera.html?product_brand=1',
        'qomra':      'https://qomra.pro/en/category/jKQvBD?filters[category_id]=1061595081&filters[brand_id]=174800383',
        'mestores':   'https://mestores.com/en_sa/cameras-accessories/cameras?page={page}&brand%5Bfilter%5D=SONY%2C1722',
        'abdulwahed': 'https://www.abdulwahed.com/en/photography-c-868/cameras-c-869/digital-cameras-c-870',
        'amazon':     'https://www.amazon.sa/s?rh=p_89%3ASony&i=electronics&language=en_AE&k=sony+camera',
        'noon':       'https://www.noon.com/saudi-en/electronics-and-mobiles/camera-and-photo-16165/digital-cameras-16168/?q=sony',
        'cameramix':  'https://www.cameramix.com/Sony',
        'pclub':      'https://pclub.com.sa/sony-1-10?limit=100',
        'camtime':    'https://camtime.sa/%D9%83%D8%A7%D9%85%D9%8A%D8%B1%D8%A7%D8%AA-%D8%A7%D9%84%D8%AA%D8%B5%D9%88%D9%8A%D8%B11772717544?fm=10',
        'alamcam':    'https://alamcam.sa/%D8%A7%D9%84%D9%83%D8%A7%D9%85%D9%8A%D8%B1%D8%A7%D8%AA',
        'camerabox':  'https://camerabox.com.sa/en/sony/brand-1380282655',
    },
}

NON_LENS = ['lens cap','lens hood','uv filter','cpl filter','nd filter','cleaning kit','tripod','flash','battery','charger','bag','strap','memory card','sd card','cable','adapter ring','camera body','bundle','cine lens','cinema lens','body only','camcorder','action cam','vlogging camera','usb dock','dock','remote','grip','screen protector']
LENS_KW  = ['mm','f/','f1.','f2.','f4','f5.','f6.','g master','g lens','zeiss','fe ','e-mount','e mount','sel','macro','fisheye','zoom lens','prime lens','gm lens','telephoto','wide angle']
NON_CAM  = ['tripod','bag','strap','battery','charger','memory card','sd card','flash','filter','cleaning','lens cap','hood','g master','gm lens','zoom lens','prime lens','macro lens','fisheye','usb dock','dock','remote','grip','screen protector','carrying case']
CAM_KW   = ['alpha','a7','a9','a6','a1 ','zv-','fx3','fx6','fx30','ilce','ilc-','dsc-','cyber-shot','mirrorless','digital camera','full frame','aps-c','a7r','a7s','a7c','a5100','a6000','a6100','a6400','a6600','a6700','camera body','interchangeable']
LENS_SIG = ['mm f/','mm f1','mm f2','mm f4','mm f5','mm f6','g master','zeiss','sel1','sel2','sel3','sel4','sel5','zoom lens','prime lens','macro lens','fisheye lens']

def norm(s): return s.lower().strip()
def tr_east(s):
    for i,c in enumerate('٠١٢٣٤٥٦٧٨٩'): s=s.replace(c,str(i))
    return s.replace('٬',',')
def is_lens(name):
    n=norm(name)
    if any(k in n for k in NON_LENS): return False
    return bool(re.search(r'\d+\s*mm',n)) or bool(re.search(r'\d{2,3}[-/]\d',n)) or any(k in n for k in LENS_KW)
def is_camera(name):
    n=norm(name)
    if any(s in n for s in LENS_SIG): return False
    if any(k in n for k in NON_CAM): return False
    return any(k in n for k in CAM_KW)
def fix_arabic(name,url,val):
    if any('\u0600'<=c<='\u06FF' for c in name):
        slug=url.rstrip('/').split('/')[-1].split('?')[0]
        sn=re.sub(r'[_-]',' ',slug).title()
        sn=re.sub(r'\.html?$','',sn,flags=re.I).strip()
        if val(sn): return sn
    return name
def pparse(text):
    t=tr_east(str(text))
    # Find all number patterns (handles SAR 4,849.00 and ر.س 4٬849)
    nums=re.findall(r'[\d]+(?:[,،][\d]+)*(?:\.[\d]+)?', t)
    for n in nums:
        clean=n.replace(',','').replace('،','').split('.')[0]
        try:
            v=float(clean)
            if v>100: return v  # skip small numbers like ratings/percentages
        except: continue
    return None

def zenrows(url,wait=8000,scroll=False,retries=2):
    p={'apikey':ZENROWS_KEY,'url':url,'antibot':'true','premium_proxy':'true','js_render':'true','proxy_country':'sa','wait':str(wait)}
    if scroll:
        p['js_instructions']=json.dumps([{'scroll_y':1000},{'wait':2000},{'scroll_y':2000},{'wait':2000},{'scroll_y':3000},{'wait':2000},{'scroll_y':4000},{'wait':2000},{'scroll_y':5000},{'wait':2000}])
    for a in range(retries+1):
        try:
            r=requests.get('https://api.zenrows.com/v1/',params=p,timeout=120); r.raise_for_status(); return r.text
        except Exception as e:
            log.warning(f'ZenRows attempt {a+1}: {e}')
            if a<retries: time.sleep(5)
    return None

def plain(url,ssl=True,retries=2):
    h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36','Accept-Language':'en-US,en;q=0.9'}
    for a in range(retries+1):
        try:
            r=requests.get(url,headers=h,timeout=30,verify=ssl); r.raise_for_status(); return r.text
        except Exception as e:
            log.warning(f'plain_get attempt {a+1}: {e}')
            if a<retries: time.sleep(3)
    return None

def opencart_parse(html, base_url, label, validator):
    soup=BeautifulSoup(html,'lxml')
    items=soup.select('.product-thumb,.product-layout,[class*="product-item"]')
    log.info(f'[{label}] opencart_parse found {len(items)} raw items')
    if not items:
        body=soup.find('body')
        snippet=str(body)[:500] if body else html[:500]
        log.warning(f'[{label}] HTML snippet: {snippet[:300]}')
    results=[]
    seen=set()
    logged=0; no_name=0
    for item in items:
        try:
            # Try multiple name selectors in order of specificity
            ne = (item.select_one('.caption h4 a') or
                  item.select_one('.product-name a') or
                  item.select_one('h4 a') or
                  item.select_one('h3 a') or
                  item.select_one('h2 a') or
                  item.select_one('[class*="product-title"] a') or
                  item.select_one('[class*="name"] a') or
                  item.select_one('a[href]'))  # last resort: any link
            if not ne:
                no_name+=1; continue
            name=ne.get_text(strip=True); link=ne.get('href','').strip()
            if not name or len(name)<3: continue
            if not link.startswith('http'): link=base_url+link
            if link in seen: continue
            seen.add(link)
            if logged<5: log.info(f'[{label}] candidate: {name[:80]}'); logged+=1
            name=fix_arabic(name,link,validator)
            if not validator(name): continue
            pe=item.select_one('.price,[class*="price"]')
            price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
            avail='Out of Stock' if 'out of stock' in item.get_text().lower() else 'In Stock'
            results.append({'name':name,'price':price,'availability':avail,'url':link})
        except Exception as e: log.debug(f'[{label}] {e}')
    if no_name>0: log.info(f'[{label}] {no_name} items had no name element')
    return results, seen

def salla_parse(html, base_url, label, validator):
    soup=BeautifulSoup(html,'lxml')
    items=soup.select('custom-salla-product-card,s-product-card-entry,[class~="s-product-card-entry"]')
    log.info(f'[{label}] salla_parse found {len(items)} raw items')
    if not items:
        # Log first 500 chars of body to help debug
        body=soup.find('body')
        snippet=str(body)[:500] if body else html[:500]
        log.warning(f'[{label}] HTML snippet: {snippet[:300]}')
    results=[]
    seen=set()
    for item in items:
        try:
            te=item.select_one('h1.s-product-card-content-title a,h2.s-product-card-content-title a,.s-product-card-content-title a')
            if not te: te=item.select_one(f'a[href*="{base_url.replace("https://","").split("/")[0]}"]')
            if not te: continue
            name=te.get_text(strip=True); link=te.get('href','').strip()
            if not link.startswith('http'): link=base_url+link
            if link in seen: continue
            seen.add(link)
            if not name:
                slug=link.rstrip('/').split('/')[-1].split('?')[0]; name=re.sub(r'[_-]',' ',slug).title()
            name=fix_arabic(name,link,validator)
            if not validator(name): continue
            pe=item.select_one('.s-product-card-sale-price h4,.s-product-card-sale-price span')
            price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
            if not price:
                for el in item.select('h4,[class*="price"]'):
                    p=pparse(tr_east(el.get_text(strip=True)))
                    if p and p>100: price=p; break
            card=item.get_text()
            avail='Out of Stock' if 'out of stock' in card.lower() or 'نفد' in card else 'In Stock'
            results.append({'name':name,'price':price,'availability':avail,'url':link})
        except Exception as e: log.debug(f'[{label}] item error: {e}')
    log.info(f'[{label}] salla_parse returning {len(results)} valid items')
    return results, seen

# ── Our Site ──────────────────────────────────────────────────────────────────
def parse_our_site(pt):
    base=URLS[pt]['our_site']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=20:
        url=f"{base}&p={page}" if page>1 else base
        log.info(f'[Our Site] page {page}')
        html=zenrows(url,wait=8000)
        if not html: break
        soup=BeautifulSoup(html,'lxml'); items=soup.select('li.product-item,.product-item-info'); nf=0
        for item in items:
            try:
                ne=item.select_one('.product-item-name a,.product-item-link')
                le=item.select_one('a.product-item-link,.product-item-name a')
                if not ne: continue
                name=ne.get_text(strip=True)
                link=le['href'] if le and le.get('href') else ''
                if link in seen: continue
                seen.add(link); name=fix_arabic(name,link,val)
                if not val(name): continue
                # Try multiple price selectors (from working Sigma scraper)
                price=None
                for sel in ['[data-price-type="finalPrice"] .price',
                             '.special-price .price','.price-box .price','.price']:
                    pe=item.select_one(sel)
                    if pe:
                        price=pparse(pe.get_text(strip=True))
                        if price: break
                # Log first product price attempt for debugging
                if len(products)==0:
                    pe_raw=item.select_one('.price')
                    log.info(f'[Our Site] first price raw: {pe_raw.get_text(strip=True) if pe_raw else "NOT FOUND"}')
                ae=item.select_one('.stock,.availability'); avail='Out of Stock' if ae and 'out' in ae.get_text().lower() else 'In Stock'
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[Our Site] {e}')
        if nf==0: break
        page+=1
    log.info(f'[Our Site] {pt}: {len(products)}'); return products

# ── Qomra ─────────────────────────────────────────────────────────────────────
def parse_qomra(pt):
    base=URLS[pt]['qomra']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=20:
        url=f"{base}&page={page}" if page>1 else base
        log.info(f'[Qomra] page {page}')
        html=zenrows(url,wait=15000)
        # If no items found on first try, retry with longer wait
        if html:
            from bs4 import BeautifulSoup as BS
            test=BS(html,'lxml')
            if not test.select('custom-salla-product-card,s-product-card-entry'):
                log.info(f'[Qomra] No items on first try, retrying with wait=20000')
                html=zenrows(url,wait=20000)
        if not html: break
        r,s=salla_parse(html,'https://qomra.pro','Qomra',val)
        new=[p for p in r if p['url'] not in seen]
        for p in new: seen.add(p['url'])
        products.extend(new)
        if not new: break
        page+=1
    log.info(f'[Qomra] {pt}: {len(products)}'); return products

# ── Me Stores ─────────────────────────────────────────────────────────────────
def parse_mestores(pt):
    base=URLS[pt]['mestores']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=20:
        url=base.format(page=page)
        log.info(f'[Me Stores] page {page}')
        html=zenrows(url,wait=12000)
        if not html: break
        soup=BeautifulSoup(html,'lxml')
        anchors=soup.select('a[href*="/en_sa/sony"]')
        if not anchors:
            g=soup.select_one('[class*="gallery-root"],[class*="infinite-scroll"]')
            if g: anchors=[a for a in g.select('a[href]') if '/en_sa/' in a.get('href','')]
        log.info(f'[Me Stores] found {len(anchors)} anchors on page {page}')
        if not anchors:
            body=soup.find('body'); snippet=str(body)[:400] if body else html[:400]
            log.warning(f'[Me Stores] HTML snippet: {snippet[:300]}')
            break
        nf=0
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
                    tip=a.select_one('[class*="tooltipText"]')
                    if tip: name=tip.get_text(strip=True)
                if not name:
                    slug=link.rstrip('/').split('/')[-1].split('?')[0]; name=re.sub(r'[_-]',' ',slug).title()
                name=fix_arabic(name,link,val)
                # Log first few to debug
                if len(products)<3: log.info(f'[Me Stores] candidate: {name[:80]}')
                if not val(name): continue
                pe=a.select_one('[class*="priceAmount"],[class*="priceValue"]')
                price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
                if not price:
                    for el in a.select('span'):
                        p=pparse(tr_east(el.get_text(strip=True)))
                        if p and p>100: price=p; break
                ct=a.get_text().lower(); avail='Out of Stock' if 'out of stock' in ct or 'notify me' in ct else 'In Stock'
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[Me Stores] {e}')
        if nf==0: break
        page+=1
    log.info(f'[Me Stores] {pt}: {len(products)}'); return products

# ── Abdulwahed ────────────────────────────────────────────────────────────────
def parse_abdulwahed(pt):
    base=URLS[pt]['abdulwahed']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen_urls=set(); seen_page_hashes=set(); page=1
    while page<=20:
        url=f"{base}?page={page}" if page>1 else base
        log.info(f'[Abdulwahed] page {page}')
        html=zenrows(url,wait=10000)
        if not html: break
        # Detect repeated pages (infinite scroll returns same content)
        page_hash=hash(html[:2000])
        if page_hash in seen_page_hashes:
            log.info(f'[Abdulwahed] Repeated page detected at page {page}, stopping')
            break
        seen_page_hashes.add(page_hash)
        soup=BeautifulSoup(html,'lxml')
        cards=soup.select('div[class*="grid-cols-2"] > div,div[class*="grid-cols-3"] > div,div[class*="grid-cols-4"] > div,div[class*="sm:grid-cols"] > div')
        if not cards:
            cards=[d for d in soup.select('div') if d.select_one('img[alt]') and re.search(r'\d{3,}',d.get_text()) and d.select_one('a[href]')]
        log.info(f'[Abdulwahed] found {len(cards)} cards page {page}')
        if not cards: break
        nf=0
        for card in cards:
            try:
                ie=card.select_one('img[alt]')
                if not ie: continue
                name=ie.get('alt','').strip()
                if not name or 'sony' not in norm(name): continue
                le=card.select_one('a[href]')
                if not le: continue
                link=le.get('href','').strip()
                if not link.startswith('http'): link='https://www.abdulwahed.com'+link
                if link in seen_urls: continue
                seen_urls.add(link); name=fix_arabic(name,link,val)
                if not val(name): continue
                price=None
                for el in card.select('span,div,p'):
                    p=pparse(tr_east(el.get_text(strip=True)))
                    if p and 100<p<200000: price=p; break
                ct=card.get_text().lower(); avail='Out of Stock' if 'out of stock' in ct or 'notify' in ct else 'In Stock'
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[Abdulwahed] {e}')
        log.info(f'[Abdulwahed] page {page} added {nf} Sony products')
        page+=1
    log.info(f'[Abdulwahed] {pt}: {len(products)}'); return products

# ── Amazon SA ─────────────────────────────────────────────────────────────────
def parse_amazon(pt):
    base=URLS[pt]['amazon']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=15:
        url=f"{base}&page={page}" if page>1 else base
        log.info(f'[Amazon SA] page {page}')
        # Standard ZenRows WITHOUT js_render (same as working Sigma scraper)
        params={'apikey':ZENROWS_KEY,'url':url,'antibot':'true','premium_proxy':'true','proxy_country':'sa'}
        try:
            resp=requests.get('https://api.zenrows.com/v1/',params=params,timeout=90)
            resp.raise_for_status(); html=resp.text
        except Exception as e:
            log.warning(f'[Amazon SA] failed: {e}'); break
        soup=BeautifulSoup(html,'lxml')
        items=[i for i in soup.select('[data-component-type="s-search-result"]') if i.get('data-asin')]
        log.info(f'[Amazon SA] found {len(items)} items page {page}')
        if not items:
            body=soup.find('body'); log.warning(f'[Amazon SA] snippet: {str(body)[:300] if body else html[:300]}')
            break
        nf=0
        for item in items:
            try:
                ne=(item.select_one('h2 a span') or
                    item.select_one('.a-size-medium.a-color-base.a-text-normal') or
                    item.select_one('h2 span'))
                if not ne: continue
                name=ne.get_text(strip=True)
                # Price from price-whole + fraction (working Sigma approach)
                pw=item.select_one('.a-price-whole'); pf=item.select_one('.a-price-fraction')
                price=None
                if pw:
                    ps=pw.get_text(strip=True).replace(',','').replace('.','')
                    ps+='.'+pf.get_text(strip=True) if pf else '.00'
                    try: price=float(ps)
                    except: pass
                avail='In Stock' if price else 'Out of Stock'
                le=item.select_one('h2 a'); href=le.get('href','') if le else ''
                if '/dp/' in href:
                    asin=href.split('/dp/')[1].split('/')[0]; link=f'https://www.amazon.sa/dp/{asin}'
                else:
                    asin=item.get('data-asin',''); link=f'https://www.amazon.sa/dp/{asin}' if asin else ''
                if link in seen: continue
                seen.add(link)
                if page==1 and nf<5: log.info(f'[Amazon SA] candidate: {name[:80]}')
                if 'sony' not in norm(name): continue
                name=fix_arabic(name,link,val)
                if not val(name): continue
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[Amazon SA] {e}')
        nxt=soup.select_one('.s-pagination-next:not(.s-pagination-disabled)')
        if not nxt: break
        page+=1; time.sleep(2)
    log.info(f'[Amazon SA] {pt}: {len(products)}'); return products

# ── Noon ──────────────────────────────────────────────────────────────────────
def parse_noon(pt):
    base=URLS[pt]['noon']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=10:
        url=f"{base}?page={page}" if page>1 else base
        log.info(f'[Noon] page {page}')
        html=zenrows(url,wait=10000)  # js_render=True, wait=10000 (working Sigma approach)
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
        log.info(f'[Noon] found {len(items)} items page {page}')
        if not items:
            body=soup.find('body'); log.warning(f'[Noon] snippet: {str(body)[:300] if body else html[:300]}')
            break
        nf=0
        for item in items:
            try:
                ne=(item.select_one('[data-qa="product-name"]') or item.select_one('[class*="productTitle"]') or
                    item.select_one('[class*="product-title"]') or item.select_one('[class*="name"]') or
                    item.select_one('h3') or item.select_one('h2'))
                pe=(item.select_one('[data-qa="price-amount"]') or item.select_one('[class*="priceNow"]') or
                    item.select_one('[class*="selling-price"]') or item.select_one('[class*="Price"]') or
                    item.select_one('[class*="price"]'))
                le=item.select_one('a[href*="/p/"]') or item.select_one('a[href]')
                if not ne or not le: continue
                name=ne.get_text(strip=True)
                price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
                link=le.get('href','')
                if not link.startswith('http'): link='https://www.noon.com'+link
                if link in seen: continue
                seen.add(link)
                if nf<3: log.info(f'[Noon] candidate: {name[:80]}')
                if 'sony' not in norm(name): continue
                name=fix_arabic(name,link,val)
                if not val(name): continue
                ct=item.get_text().lower(); avail='Out of Stock' if 'out of stock' in ct or 'sold out' in ct else 'In Stock'
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[Noon] {e}')
        nxt=soup.select_one('[aria-label="Next"],[class*="nextPage"],[class*="next-page"]')
        if not nxt or nf==0: break
        page+=1; time.sleep(2)
    log.info(f'[Noon] {pt}: {len(products)}'); return products

# ── CameraMix ─────────────────────────────────────────────────────────────────
def parse_cameramix(pt):
    base=URLS[pt]['cameramix']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=20:
        url=f"{base}?page={page}" if page>1 else base
        log.info(f'[CameraMix] page {page}')
        html=plain(url) or zenrows(url,wait=8000)
        if not html: break
        r,s=opencart_parse(html,'https://www.cameramix.com','CameraMix',val)
        new=[p for p in r if p['url'] not in seen]
        for p in new: seen.add(p['url'])
        products.extend(new)
        if not new: break
        page+=1
    log.info(f'[CameraMix] {pt}: {len(products)}'); return products

# ── PClub ─────────────────────────────────────────────────────────────────────
def parse_pclub(pt):
    base=URLS[pt]['pclub']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=10:
        url=f"{base}&page={page}" if page>1 else base
        log.info(f'[PClub] page {page}')
        html=zenrows(url,wait=8000)
        if not html: break
        r,s=opencart_parse(html,'https://pclub.com.sa','PClub',val)
        new=[p for p in r if p['url'] not in seen]
        for p in new: seen.add(p['url'])
        products.extend(new)
        if not new: break
        page+=1
    log.info(f'[PClub] {pt}: {len(products)}'); return products

# ── CamTime ───────────────────────────────────────────────────────────────────
def parse_camtime(pt):
    base=URLS[pt]['camtime']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=10:
        url=f"{base}&page={page}" if page>1 else base
        log.info(f'[CamTime] page {page}')
        html=plain(url,ssl=False) or zenrows(url,wait=8000)
        if not html: break
        r,s=opencart_parse(html,'https://camtime.sa','CamTime',val)
        new=[p for p in r if p['url'] not in seen]
        for p in new: seen.add(p['url'])
        products.extend(new)
        if not new: break
        page+=1
    log.info(f'[CamTime] {pt}: {len(products)}'); return products

# ── AlamCam ───────────────────────────────────────────────────────────────────
def parse_alamcam(pt):
    base=URLS[pt]['alamcam']; val=is_lens if pt=='lenses' else is_camera
    products=[]; seen=set(); page=1
    while page<=10:
        sep='&' if '?' in base else '?'
        url=base if page==1 else f"{base}{sep}page={page}"
        log.info(f'[AlamCam] page {page}')
        html=plain(url) or zenrows(url,wait=8000)
        if not html: break
        soup=BeautifulSoup(html,'lxml')
        items=(soup.select('.product-layout,.product-thumb') or
               soup.select('.product-item') or
               soup.select('[class*="product-card"]'))
        items=[i for i in items if i.select_one('a[href]')]
        log.info(f'[AlamCam] found {len(items)} items page {page}')
        if not items:
            body=soup.find('body'); log.warning(f'[AlamCam] snippet: {str(body)[:300] if body else ""}')
            break
        # Deduplicate by URL
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
                link=ne.get('href','') if ne.name=='a' else (item.select_one('a[href]') or {}).get('href','')
                if not link: continue
                if not link.startswith('http'): link='https://alamcam.sa'+link
                if link in seen: continue
                seen.add(link)
                if logged<3: log.info(f'[AlamCam] candidate: {name[:80]}'); logged+=1
                if 'sony' not in norm(name): continue
                name=fix_arabic(name,link,val)
                if not val(name): continue
                pe=item.select_one('.price-new,.price-normal,.price')
                price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
                avail='Out of Stock' if any(x in item.get_text().lower() for x in ['out of stock','unavailable','sold out']) else 'In Stock'
                products.append({'name':name,'price':price,'availability':avail,'url':link}); nf+=1
            except Exception as e: log.debug(f'[AlamCam] {e}')
        log.info(f'[AlamCam] page {page}: {nf} Sony products')
        nxt=soup.select_one('ul.pagination li.active + li a,[aria-label="Next"],.next a')
        if not nxt: break
        page+=1; time.sleep(1.5)
    log.info(f'[AlamCam] {pt}: {len(products)}'); return products
    log.info(f'[AlamCam] {pt}: {len(products)}'); return products

# ── CameraBox ─────────────────────────────────────────────────────────────────
def parse_camerabox(pt):
    url=URLS[pt]['camerabox']; val=is_lens if pt=='lenses' else is_camera
    log.info(f'[CameraBox] fetching with scroll')
    products=[]
    try:
        html=zenrows(url,wait=8000,scroll=True)
        if not html: return products
        soup=BeautifulSoup(html,'lxml')
        items=soup.select('custom-salla-product-card,s-product-card-entry,[class~="s-product-card-entry"]')
        log.info(f'[CameraBox] found {len(items)} raw items')
        seen=set()
        for item in items:
            try:
                # Try title link first
                te=item.select_one('h1.s-product-card-content-title a,h2.s-product-card-content-title a,.s-product-card-content-title a')
                # Fallback: aria-label on the image anchor (contains full product name)
                if not te: te=item.select_one('a[aria-label][href]')
                if not te: te=item.select_one('a[href*="camerabox"]')
                if not te: continue
                # Prefer aria-label over text content (text may be a product ID)
                name=te.get('aria-label','').strip() or te.get_text(strip=True)
                link=te.get('href','').strip()
                if not link.startswith('http'): link='https://camerabox.com.sa'+link
                if link in seen: continue
                seen.add(link)
                if not name or re.match(r'^P\d+$', name):
                    # Name is a product ID — try img alt text
                    img=item.select_one('img[alt]')
                    if img: name=img.get('alt','').strip()
                if not name or re.match(r'^P\d+$', name):
                    slug=link.rstrip('/').split('/')[-1].split('?')[0]; name=re.sub(r'[_-]',' ',slug).title()
                name=fix_arabic(name,link,val)
                if len(products)<5: log.info(f'[CameraBox] candidate: {name[:80]}')
                if not val(name): continue
                pe=item.select_one('.s-product-card-sale-price h4,.s-product-card-sale-price span')
                price=pparse(tr_east(pe.get_text(strip=True))) if pe else None
                if not price:
                    for el in item.select('h4,[class*="price"]'):
                        p=pparse(tr_east(el.get_text(strip=True)))
                        if p and p>100: price=p; break
                card=item.get_text()
                avail='Out of Stock' if 'out of stock' in card.lower() or 'نفد' in card else 'In Stock'
                products.append({'name':name,'price':price,'availability':avail,'url':link})
            except Exception as e: log.debug(f'[CameraBox] {e}')
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
def cam_score(a,b):
    na,nb=norm(a),norm(b)
    ma=set(re.findall(r'a\d[a-z0-9]*|zv-\w+|fx\d+|ilce-[\w-]+|dsc-[\w-]+',na))
    mb=set(re.findall(r'a\d[a-z0-9]*|zv-\w+|fx\d+|ilce-[\w-]+|dsc-[\w-]+',nb))
    if ma and mb: return 100 if ma&mb else 0
    return min(70,len(set(na.split())&set(nb.split()))*15)
def find_match(our,comps,pt):
    sc=lens_score if pt=='lenses' else cam_score
    bs=0; bm=None
    for cp in comps:
        s=sc(our['name'],cp['name'])
        if s>bs: bs=s; bm=cp
    return bm if bs>=80 else None

# ── Build Rows ────────────────────────────────────────────────────────────────
def build_rows(our,comp_data,pt):
    rows=[]; ts=datetime.now().strftime('%Y-%m-%d %H:%M'); used={s:set() for s in COMPETITORS}
    for o in our:
        row={'timestamp':ts,'name':o['name'],'our_price':o['price'],'our_availability':o['availability'],'our_url':o['url']}
        pfl=[(OUR_SITE,o['price'],o['url'])] if o['price'] and o['availability']=='In Stock' else []
        for src in COMPETITORS:
            m=find_match(o,comp_data.get(src,[]),pt)
            if m:
                used[src].add(m['url'])
                diff=round(m['price']-o['price'],2) if m['price'] and o['price'] else None
                st=('Cheaper than competitor' if diff and diff>0 else 'More expensive' if diff and diff<0 else 'Same price' if diff==0 else 'Not listed')
                row[src]={'url':m['url'],'price':m['price'],'availability':m['availability'],'diff':diff,'status':st}
                if m['price'] and m['availability']=='In Stock': pfl.append((src,m['price'],m['url']))
            else: row[src]={'url':'','price':None,'availability':'','diff':None,'status':'Not listed'}
        if pfl:
            ch=min(pfl,key=lambda x:x[1]); row['lowest_price']=ch[1]; row['cheapest_brand']=ch[0]; row['cheapest_link']=ch[2]; row['our_diff_vs_cheapest']=round((o['price'] or 0)-ch[1],2)
        else: row['lowest_price']=row['cheapest_brand']=row['cheapest_link']=None; row['our_diff_vs_cheapest']=None
        rows.append(row)
    for src in COMPETITORS:
        for cp in comp_data.get(src,[]):
            if cp['url'] in used[src]: continue
            row={'timestamp':ts,'name':cp['name'],'our_price':None,'our_availability':'Not listed','our_url':''}
            for ot in COMPETITORS: row[ot]={'url':cp['url'],'price':cp['price'],'availability':cp['availability'],'diff':None,'status':'Not listed'} if ot==src else {'url':'','price':None,'availability':'','diff':None,'status':'Not listed'}
            row['lowest_price']=cp['price']; row['cheapest_brand']=src; row['cheapest_link']=cp['url']; row['our_diff_vs_cheapest']=None
            rows.append(row)
    return rows

# ── Google Sheets ─────────────────────────────────────────────────────────────
# Row 1: group headers — cols A-B blank, C-E = Our Site, then 5 cols per competitor, then 4 summary
GH=['Timestamp','Product Name','Our Site (ksa.amt.tv)','',''] + sum([[s,'','','',''] for s in COMPETITORS],[]) + ['Summary','','','']
# Row 2: column headers
CH=['Timestamp','Product Name','Our Price (SAR)','Our Availability','Our Product URL'] + ['Product URL','Price (SAR)','Availability','Price Diff (SAR)','Status']*len(COMPETITORS) + ['Lowest Price (SAR)','Cheapest Brand','Cheapest Link','Our Price Diff vs Cheapest']
SH=['Source','Total Products','Cheaper Than Us','More Expensive','Same Price','Not Listed','Updated']
SC={'Cheaper than competitor':{'red':0.20,'green':0.73,'blue':0.40},'More expensive':{'red':0.91,'green':0.27,'blue':0.27},'Same price':{'red':1.0,'green':0.90,'blue':0.20},'Not listed':{'red':0.85,'green':0.85,'blue':0.85}}

def get_client():
    info=json.loads(SA_JSON); scopes=['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive']
    return gspread.authorize(Credentials.from_service_account_info(info,scopes=scopes))

def row2list(row):
    out=[row['timestamp'],row['name'],row.get('our_price',''),row.get('our_availability',''),row.get('our_url','')]
    for s in COMPETITORS:
        d=row.get(s,{}); out+=[d.get('url',''),d.get('price',''),d.get('availability',''),d.get('diff',''),d.get('status','')]
    out+=[row.get('lowest_price',''),row.get('cheapest_brand',''),row.get('cheapest_link',''),row.get('our_diff_vs_cheapest','')]
    return out

def summary(rows,ts):
    out=[]
    for src in SOURCES:
        if src==OUR_SITE: t=sum(1 for r in rows if r.get('our_price')); c=me=sa=nl=0
        else: t=sum(1 for r in rows if r.get(src,{}).get('price')); c=sum(1 for r in rows if r.get(src,{}).get('status')=='Cheaper than competitor'); me=sum(1 for r in rows if r.get(src,{}).get('status')=='More expensive'); sa=sum(1 for r in rows if r.get(src,{}).get('status')=='Same price'); nl=sum(1 for r in rows if r.get(src,{}).get('status')=='Not listed')
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
            reqs.append({'repeatCell':{'range':{'sheetId':ws.id,'startRowIndex':sr,'endRowIndex':sr+1,'startColumnIndex':colidx,'endColumnIndex':colidx+1},'cell':{'userEnteredFormat':{'backgroundColor':col}},'fields':'userEnteredFormat.backgroundColor'}})
    for i in range(0,len(reqs),1000): sh.batch_update({'requests':reqs[i:i+1000]})

def write_sheet(client,pt,rows):
    tn='Lenses' if pt=='lenses' else 'Cameras'; sn='Lenses Summary' if pt=='lenses' else 'Cameras Summary'
    ts=datetime.now().strftime('%Y-%m-%d %H:%M'); sh=client.open_by_key(GSHEET_ID)
    try: ws=sh.worksheet(tn)
    except gspread.WorksheetNotFound: ws=sh.add_worksheet(title=tn,rows=500,cols=70)
    # Resize to clear all old columns before writing
    sh.batch_update({'requests':[{'updateSheetProperties':{'properties':{'sheetId':ws.id,'gridProperties':{'columnCount':70}},'fields':'gridProperties.columnCount'}}]})
    ws.clear()
    data=[GH,CH]+[row2list(r) for r in rows]
    ws.update(values=data,range_name='A1',value_input_option='USER_ENTERED')
    color_cells(ws,rows,sh); log.info(f'Written {len(rows)} rows to [{tn}]')
    try: ws2=sh.worksheet(sn)
    except gspread.WorksheetNotFound: ws2=sh.add_worksheet(title=sn,rows=20,cols=10)
    ws2.clear(); ws2.update(values=[SH]+summary(rows,ts),range_name='A1',value_input_option='USER_ENTERED')
    log.info(f'Written summary to [{sn}]')

# ── Main ──────────────────────────────────────────────────────────────────────
PARSERS={'our_site':parse_our_site,'qomra':parse_qomra,'mestores':parse_mestores,'abdulwahed':parse_abdulwahed,'amazon':parse_amazon,'noon':parse_noon,'cameramix':parse_cameramix,'pclub':parse_pclub,'camtime':parse_camtime,'alamcam':parse_alamcam,'camerabox':parse_camerabox}
COMP_KEYS={'Qomra':'qomra','Me Stores':'mestores','Abdulwahed':'abdulwahed','Amazon SA':'amazon','Noon':'noon','CameraMix':'cameramix','PClub':'pclub','CamTime':'camtime','AlamCam':'alamcam','CameraBox':'camerabox'}

def safe(key,label,pt):
    try: return PARSERS[key](pt)
    except Exception as e: log.error(f'[{label}] FAILED: {e}'); return []

def main():
    log.info('=== Sony Price Comparison Scraper Started ===')
    client=get_client()
    for pt in ['lenses','cameras']:
        log.info(f'\n─── Scraping {pt.upper()} ───')
        our=safe('our_site','Our Site',pt)
        cd={label:safe(key,label,pt) for label,key in COMP_KEYS.items()}
        rows=build_rows(our,cd,pt)
        write_sheet(client,pt,rows)
        log.info(f'[{pt}] Done — {len(rows)} rows')
    log.info('=== Scraper Finished ===')

if __name__=='__main__':
    main()
