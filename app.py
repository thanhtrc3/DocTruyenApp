from flask import Flask, render_template, request
import requests
from bs4 import BeautifulSoup
import concurrent.futures
import urllib.parse
import re

app = Flask(__name__)

# User-Agent chuẩn để truy cập các trang web đọc truyện/ảnh
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
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
        
        page_numbers = {} # mapping num -> full_url
        
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
            
            # Thử sinh tự động các trang từ min_p đến max_p nếu url có quy luật rõ ràng
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

def fetch_images_from_page(page_url):
    """Lấy tất cả link ảnh từ một trang."""
    images = []
    try:
        response = requests.get(page_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for img in soup.find_all('img'):
            src = (img.get('data-original') or 
                   img.get('data-src') or 
                   img.get('data-lazy-src') or 
                   img.get('data-url') or 
                   img.get('src'))
            if src:
                src = src.strip()
                if src.startswith('data:') or 'loading.' in src.lower() or 'spinner.' in src.lower() or 'spacer.' in src.lower():
                    continue
                
                full_img_url = urllib.parse.urljoin(page_url, src)
                if full_img_url.startswith('http') and full_img_url not in images:
                    images.append(full_img_url)
    except Exception as e:
        print(f"Lỗi khi lấy ảnh từ {page_url}: {e}")
    return images

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/read', methods=['POST'])
def read_comic():
    url = request.form.get('url')
    if not url:
        return "Vui lòng nhập link!", 400

    image_links = []

    try:
        pages = get_all_pages(url)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for links in executor.map(fetch_images_from_page, pages):
                for img_url in links:
                    if img_url not in image_links:
                        image_links.append(img_url)
        
        if not image_links:
            return "Không tìm thấy ảnh nào từ đường dẫn này. Vui lòng kiểm tra lại link!", 400

    except Exception as e:
        return f"Có lỗi xảy ra: {e}"

    return render_template('reader.html', image_links=image_links)

if __name__ == '__main__':
    app.run(debug=True)