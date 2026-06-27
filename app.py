from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import concurrent.futures
import urllib.parse
import re
import time

app = Flask(__name__)

# Tích hợp Cloudscraper (đã có sẵn trong requirements.txt) để vượt qua tường lửa Cloudflare Anti-Bot
try:
    import cloudscraper
    session = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
except ImportError:
    session = requests.Session()

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Cookie': 'nw=1; over18=1; age_verified=1; is_adult=1'
}
session.headers.update(headers)

def fetch_url(url, retries=2):
    """Hàm tải trang chung có thử lại (retry) khi gặp rate-limit hoặc chặn Cloudflare."""
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                return resp
            elif resp.status_code in [403, 429, 503]:
                time.sleep(0.5) # Nghỉ nhẹ nếu gặp tường lửa rate-limit
        except Exception:
            time.sleep(0.3)
    return None

def get_all_pages(start_url):
    """Kiểm tra trang web, tìm nút 'show all' nếu có, hoặc tìm các trang phân trang 1 2 3 ... n."""
    urls = [start_url]
    response = fetch_url(start_url)
    if not response:
        return urls
        
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 1. Kiểm tra ưu tiên: nút hoặc link "Show all" / "Xem tất cả"
    show_all_keywords = ['show all', 'view all', 'xem tất cả', 'load all', 'toàn bộ', 'read all']
    show_all_hrefs = ['show_all', 'view_all', 'all=1', 'paging=all', '?hc=1']
    
    for a in soup.find_all('a', href=True):
        text = a.get_text().strip().lower()
        href = a['href'].lower()
        if any(kw in text for kw in show_all_keywords) or any(kw in href for kw in show_all_hrefs):
            full_url = urllib.parse.urljoin(start_url, a['href'])
            resp_all = fetch_url(full_url)
            if resp_all:
                return [full_url]
                
    parsed_start = urllib.parse.urlparse(start_url)
    base_netloc = parsed_start.netloc
    base_path = parsed_start.path.rstrip('/')

    # 2. Kiểm tra phân trang theo parameter phổ biến (?p=1, ?page=2)
    param_values = {}
    for a in soup.find_all('a', href=True):
        full_url = urllib.parse.urljoin(start_url, a['href'])
        parsed = urllib.parse.urlparse(full_url)
        if parsed.netloc != base_netloc or parsed.path.rstrip('/') != base_path:
            continue
        params = urllib.parse.parse_qs(parsed.query)
        for key in ['p', 'page', 'offset', 'pg']:
            if key in params:
                try:
                    val = int(params[key][0])
                    param_values[val] = (full_url, key, parsed)
                except ValueError:
                    pass
                    
    if param_values:
        max_v = max(param_values.keys())
        min_v = min(param_values.keys())
        if max_v >= min_v:
            sample_url, param_key, sample_parsed = param_values[max_v]
            generated = [start_url]
            start_v = 0 if min_v == 1 and not urllib.parse.parse_qs(parsed_start.query).get(param_key) else min_v
            for v in range(start_v, max_v + 1):
                qs_dict = urllib.parse.parse_qs(sample_parsed.query)
                qs_dict[param_key] = [str(v)]
                new_query = urllib.parse.urlencode(qs_dict, doseq=True)
                new_url = urllib.parse.urlunparse((sample_parsed.scheme, sample_parsed.netloc, sample_parsed.path, sample_parsed.params, new_query, ''))
                if new_url not in generated:
                    generated.append(new_url)
            return list(dict.fromkeys(generated))

    # 3. Tìm phân trang dạng số trang (1, 2, 3...)
    page_numbers = {}
    for a in soup.find_all('a', href=True):
        full_url = urllib.parse.urljoin(start_url, a['href'])
        parsed_url = urllib.parse.urlparse(full_url)
        
        if parsed_url.netloc != base_netloc:
            continue
            
        text = a.get_text().strip()
        num = None
        if text.isdigit():
            num = int(text)
        else:
            m = re.match(r'^(?:trang|page|p\.?)\s*(\d+)$', text, re.IGNORECASE)
            if m:
                num = int(m.group(1))
        
        if num is not None and 0 <= num <= 2000:
            page_numbers[num] = full_url
            
    if page_numbers:
        max_p = max(page_numbers.keys())
        min_p = min(page_numbers.keys())
        
        if max_p - min_p + 1 > len(page_numbers):
            sample_url = page_numbers[max_p]
            pattern = r'(?<!\d)' + str(max_p) + r'(?!\d)'
            if re.search(pattern, sample_url):
                generated = []
                for p in range(min_p, max_p + 1):
                    if p in page_numbers:
                        generated.append(page_numbers[p])
                    else:
                        gen_url = re.sub(pattern, str(p), sample_url)
                        generated.append(gen_url)
                return list(dict.fromkeys(generated))
        
        sorted_urls = [page_numbers[k] for k in sorted(page_numbers.keys())]
        for u in sorted_urls:
            if u not in urls:
                urls.append(u)
                
    return list(dict.fromkeys(urls))

def analyze_page_content(page_url):
    """Phân tích trang để lấy danh sách ảnh trực tiếp hoặc link subpages."""
    subpage_links = []
    direct_images = []
    
    response = fetch_url(page_url)
    if not response:
        return {"type": "images", "data": []}
        
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 1. Tìm ảnh trực tiếp
    for img in soup.find_all('img'):
        src = (img.get('data-original') or img.get('data-src') or img.get('data-lazy-src') or img.get('data-url') or img.get('src'))
        if src:
            src = src.strip()
            if src.startswith('data:') or any(k in src.lower() for k in ['loading.', 'spinner.', 'spacer.', 'icon', 'avatar', 'logo']):
                continue
            full_img_url = urllib.parse.urljoin(page_url, src)
            if full_img_url.startswith('http') and full_img_url not in direct_images:
                direct_images.append(full_img_url)

    # 2. Tìm link subpages
    parsed_base = urllib.parse.urlparse(page_url)
    base_netloc = parsed_base.netloc
    base_path = parsed_base.path.rstrip('/')
    
    for a in soup.find_all('a', href=True):
        href = a['href']
        full_url = urllib.parse.urljoin(page_url, href)
        parsed_url = urllib.parse.urlparse(full_url)
        
        if parsed_url.netloc != base_netloc or full_url.split('#')[0] == page_url.split('#')[0]:
            continue
            
        path_lower = parsed_url.path.lower()
        if any(k in path_lower for k in ['login', 'register', 'forum', 'faq', 'search', 'home', 'user', 'comment']):
            continue
            
        text = a.get_text().strip()
        if text.isdigit() or re.match(r'^(?:trang|page|p\.?)\s*\d+$', text, re.IGNORECASE):
            continue
            
        has_thumb = a.find('img') is not None
        is_viewer_path = any(k in path_lower for k in ['/s/', '/view/', '/photo/', '/image/', '/p/', '/slide/', '/reader/'])
        is_numbered_subpage = bool(re.search(r'/\d+/?$', path_lower)) and path_lower != base_path.lower()
        
        if has_thumb or is_viewer_path or is_numbered_subpage:
            if full_url not in subpage_links:
                subpage_links.append(full_url)
                
    if len(subpage_links) >= 3:
        return {"type": "subpages", "data": subpage_links}
    else:
        return {"type": "images", "data": direct_images}

def fetch_single_image_from_subpage(subpage_url):
    """Lấy link ảnh gốc từ một trang xem ảnh con."""
    response = fetch_url(subpage_url)
    if not response:
        return None
        
    soup = BeautifulSoup(response.text, 'html.parser')
    
    for target_id in ['img', 'image', 'comic', 'main_img', 'photo', 'image-container']:
        main_img = soup.find('img', id=target_id)
        if main_img:
            src = main_img.get('src') or main_img.get('data-src') or main_img.get('data-original')
            if src and src.startswith('http'):
                return urllib.parse.urljoin(subpage_url, src)
                
    for img in soup.find_all('img'):
        src = (img.get('data-original') or img.get('data-src') or img.get('src'))
        if src:
            src = src.strip()
            if src.startswith('data:') or any(k in src.lower() for k in ['loading.', 'spinner.', 'spacer.', 'icon', 'avatar', 'logo', 'thumb', 'cover']):
                continue
            full_img_url = urllib.parse.urljoin(subpage_url, src)
            if full_img_url.startswith('http'):
                return full_img_url
    return None

def try_predict_all_images(subpages, resolved_first_few):
    """THUẬT TOÁN ĐOÁN QUY LUẬT SIÊU TỐC."""
    if len(resolved_first_few) < 2 or not all(resolved_first_few[:2]):
        return None
        
    url1, url2 = resolved_first_few[0], resolved_first_few[1]
    m1 = re.search(r'/(\d+)\.([a-zA-Z0-9]+)(?:\?.*)?$', url1)
    m2 = re.search(r'/(\d+)\.([a-zA-Z0-9]+)(?:\?.*)?$', url2)
    
    if m1 and m2:
        num1, ext1 = int(m1.group(1)), m1.group(2)
        num2, ext2 = int(m2.group(1)), m2.group(2)
        
        if ext1 == ext2 and (num2 - num1 == 1):
            base_prefix = url1[:m1.start(1)]
            pad_len = len(m1.group(1)) if m1.group(1).startswith('0') else 0
            
            predicted = []
            for i in range(len(subpages)):
                curr_num = num1 + i
                num_str = str(curr_num).zfill(pad_len) if pad_len > 0 else str(curr_num)
                pred_url = f"{base_prefix}{num_str}.{ext1}"
                predicted.append(pred_url)
                
            if len(resolved_first_few) >= 3 and resolved_first_few[2]:
                if resolved_first_few[2].split('?')[0] != predicted[2].split('?')[0]:
                    return None
                    
            return predicted
    return None

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/read', methods=['POST'])
def read_comic():
    url = request.form.get('url')
    if not url:
        return "Vui lòng nhập link!", 400

    pages_data = []

    try:
        pages = get_all_pages(url)
        all_subpages = []
        all_direct_images = []
        
        # Dùng luồng nhỏ (max_workers=10) tránh kích hoạt cơ chế chống DDoS của Cloudflare
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(analyze_page_content, pages))
            for res in results:
                if res["type"] == "subpages":
                    for sub_url in res["data"]:
                        if sub_url not in all_subpages:
                            all_subpages.append(sub_url)
                else:
                    for img_url in res["data"]:
                        if img_url not in all_direct_images:
                            all_direct_images.append(img_url)
                            
        if all_subpages:
            def sort_key(u):
                nums = re.findall(r'\d+', u)
                return int(nums[-1]) if nums else 0
            all_subpages.sort(key=sort_key)
            
            sample_subpages = all_subpages[:min(3, len(all_subpages))]
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                resolved_samples = list(executor.map(fetch_single_image_from_subpage, sample_subpages))
                
            predicted_images = try_predict_all_images(all_subpages, resolved_samples)
            
            if predicted_images:
                pages_data = [{"img_url": img, "subpage_url": None} for img in predicted_images]
            else:
                initial_subs = all_subpages[:20]
                remaining_subs = all_subpages[20:]
                
                # Hạn chế luồng đồng thời ở mức 20 để không bị Cloudflare quét cào dồn dập (Rate Limiting)
                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                    resolved_20 = list(executor.map(fetch_single_image_from_subpage, initial_subs))
                    
                for i, sub_u in enumerate(initial_subs):
                    pages_data.append({"img_url": resolved_20[i], "subpage_url": sub_u if not resolved_20[i] else None})
                for sub_u in remaining_subs:
                    pages_data.append({"img_url": None, "subpage_url": sub_u})
        else:
            pages_data = [{"img_url": img, "subpage_url": None} for img in all_direct_images]
        
        if not pages_data:
            return "Không tìm thấy ảnh nào từ đường dẫn này. Vui lòng kiểm tra lại link!", 400

    except Exception as e:
        return f"Có lỗi xảy ra: {e}"

    return render_template('reader.html', pages_data=pages_data)

@app.route('/api/resolve_image', methods=['GET'])
def api_resolve_image():
    subpage_url = request.args.get('url')
    if not subpage_url:
        return jsonify({"error": "Missing url parameter"}), 400
        
    img_url = fetch_single_image_from_subpage(subpage_url)
    if img_url:
        return jsonify({"image_url": img_url})
    else:
        return jsonify({"error": "Cannot find image"}), 404

if __name__ == '__main__':
    app.run(debug=True)