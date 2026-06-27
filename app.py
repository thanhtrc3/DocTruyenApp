from flask import Flask, render_template, request
import requests
from bs4 import BeautifulSoup
import concurrent.futures
import urllib.parse
import re

app = Flask(__name__)

# Header và Cookie chuẩn để giả lập trình duyệt, vượt qua các trang cảnh báo nội dung/độ tuổi (18+)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Cookie': 'nw=1; over18=1; age_verified=1; is_adult=1'
}

def get_all_pages(start_url):
    """Kiểm tra trang web, tìm nút 'show all' nếu có, hoặc tìm các trang phân trang 1 2 3 ... n."""
    urls = [start_url]
    try:
        response = requests.get(start_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Kiểm tra ưu tiên: nút hoặc link "Show all" / "Xem tất cả" để mở rộng giới hạn trang
        show_all_keywords = ['show all', 'view all', 'xem tất cả', 'load all', 'toàn bộ', 'read all']
        show_all_hrefs = ['show_all', 'view_all', 'all=1', 'paging=all']
        
        for a in soup.find_all('a', href=True):
            text = a.get_text().strip().lower()
            href = a['href'].lower()
            if any(kw in text for kw in show_all_keywords) or any(kw in href for kw in show_all_hrefs):
                full_url = urllib.parse.urljoin(start_url, a['href'])
                try:
                    resp_all = requests.get(full_url, headers=headers, timeout=10)
                    if resp_all.status_code == 200:
                        return [full_url]
                except Exception:
                    pass
                    
        # 2. Nếu không có "Show all", tìm các trang phân trang 1, 2, 3... n
        parsed_start = urllib.parse.urlparse(start_url)
        base_netloc = parsed_start.netloc
        
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
            
            # Thử sinh tự động các trang nếu có quy luật rõ ràng
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
                    
    except Exception as e:
        print(f"Lỗi khi kiểm tra phân trang: {e}")
        
    return list(dict.fromkeys(urls))

def analyze_page_content(page_url):
    """Phân tích trang để xem trang chứa ảnh trực tiếp hay chứa link dẫn đến các trang xem ảnh con."""
    subpage_links = []
    direct_images = []
    try:
        response = requests.get(page_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Tìm các ảnh trực tiếp
        for img in soup.find_all('img'):
            src = (img.get('data-original') or img.get('data-src') or img.get('data-lazy-src') or img.get('data-url') or img.get('src'))
            if src:
                src = src.strip()
                if src.startswith('data:') or any(k in src.lower() for k in ['loading.', 'spinner.', 'spacer.', 'icon', 'avatar', 'logo']):
                    continue
                full_img_url = urllib.parse.urljoin(page_url, src)
                if full_img_url.startswith('http') and full_img_url not in direct_images:
                    direct_images.append(full_img_url)

        # 2. Tìm các liên kết đến trang con xem ảnh (thường chứa thumbnail bên trong hoặc đường dẫn mang ký hiệu /s/, /view/...)
        parsed_base = urllib.parse.urlparse(page_url)
        base_netloc = parsed_base.netloc
        
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
            
            if has_thumb or is_viewer_path:
                if full_url not in subpage_links:
                    subpage_links.append(full_url)
                    
    except Exception as e:
        print(f"Lỗi phân tích trang {page_url}: {e}")
        
    # Nếu tìm thấy từ 3 trang con xem ảnh trở lên, đây là gallery phân cấp
    if len(subpage_links) >= 3:
        return {"type": "subpages", "data": subpage_links}
    else:
        return {"type": "images", "data": direct_images}

def fetch_single_image_from_subpage(subpage_url):
    """Lấy link ảnh gốc từ một trang xem ảnh con."""
    try:
        response = requests.get(subpage_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Thử tìm thẻ ảnh chính qua các id/class chuẩn phổ biến của các web truyện
        for target_id in ['img', 'image', 'comic', 'main_img', 'photo']:
            main_img = soup.find('img', id=target_id)
            if main_img:
                src = main_img.get('src') or main_img.get('data-src') or main_img.get('data-original')
                if src and src.startswith('http'):
                    return urllib.parse.urljoin(subpage_url, src)
                    
        # Nếu không thấy id chuẩn, duyệt tìm ảnh hợp lệ lớn nhất
        for img in soup.find_all('img'):
            src = (img.get('data-original') or img.get('data-src') or img.get('src'))
            if src:
                src = src.strip()
                if src.startswith('data:') or any(k in src.lower() for k in ['loading.', 'spinner.', 'spacer.', 'icon', 'avatar', 'logo', 'thumb']):
                    continue
                full_img_url = urllib.parse.urljoin(subpage_url, src)
                if full_img_url.startswith('http'):
                    return full_img_url
    except Exception:
        pass
    return None

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/read', methods=['POST'])
def read_comic():
    url = request.form.get('url')
    if not url:
        return "Vui lòng nhập link!", 400

    final_images = []

    try:
        pages = get_all_pages(url)
        
        all_subpages = []
        all_direct_images = []
        
        # Phân tích từng trang phân trang
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
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
                            
        # Nếu phát hiện đây là dạng gallery phân cấp (như các trang archive truyện/ảnh)
        if all_subpages:
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                sub_images = list(executor.map(fetch_single_image_from_subpage, all_subpages))
                for img_url in sub_images:
                    if img_url and img_url not in final_images:
                        final_images.append(img_url)
        else:
            final_images = all_direct_images
        
        if not final_images:
            return "Không tìm thấy ảnh nào từ đường dẫn này. Vui lòng kiểm tra lại link!", 400

    except Exception as e:
        return f"Có lỗi xảy ra: {e}"

    return render_template('reader.html', image_links=final_images)

if __name__ == '__main__':
    app.run(debug=True)