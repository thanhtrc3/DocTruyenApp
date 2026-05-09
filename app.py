from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import concurrent.futures

app = Flask(__name__)

# Thêm User-Agent và Cookie để giả lập trình duyệt, tránh bị web chặn (đặc biệt E-Hentai)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Cookie': 'nw=1' # Bỏ qua cảnh báo nội dung của E-Hentai
}

def get_image_url(page_url):
    """Truy cập trang con và lấy link ảnh gốc."""
    try:
        page_resp = requests.get(page_url, headers=headers, timeout=10)
        page_soup = BeautifulSoup(page_resp.text, 'html.parser')
        
        # Tìm thẻ img có id là 'img' (chuẩn của E-Hentai)
        img_tag = page_soup.find('img', id='img')
        if img_tag and img_tag.get('src'):
            return img_tag.get('src')
    except Exception:
        pass
    return None

def get_all_gallery_pages(start_url):
    """Lấy danh sách các trang phân trang của gallery."""
    try:
        response = requests.get(start_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Tìm phân trang của E-Hentai (nằm trong table class ptt)
        ptb = soup.find('table', class_='ptt')
        urls = [start_url]
        if ptb:
            for a in ptb.find_all('a'):
                href = a.get('href')
                if href and 'e-hentai.org/g/' in href and href not in urls:
                    urls.append(href)
        
        # Xóa trùng lặp nhưng giữ nguyên thứ tự
        return list(dict.fromkeys(urls))
    except Exception:
        return [start_url]

@app.route('/')
def home():
    # Hiển thị trang nhập link
    return render_template('index.html')

@app.route('/read', methods=['POST'])
def read_comic():
    url = request.form.get('url')
    if not url:
        return "Vui lòng nhập link!", 400

    page_links = []

    try:
        # Lấy danh sách các trang của gallery
        gallery_pages = get_all_gallery_pages(url) if 'e-hentai.org' in url else [url]
        
        for g_url in gallery_pages:
            response = requests.get(g_url, headers=headers)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Lấy tất cả link trỏ đến trang chứa ảnh
            for a in soup.find_all('a'):
                href = a.get('href')
                if href and ('e-hentai.org/s/' in href or 'exhentai.org/s/' in href):
                    if href not in page_links:
                        page_links.append(href)
        
        if not page_links:
            return "Không tìm thấy link ảnh nào trong trang web này. Bạn có chắc đây là link Gallery hợp lệ?", 400

    except Exception as e:
        return f"Có lỗi xảy ra: {e}"

    # Trả danh sách page_links về giao diện đọc để lazy load
    return render_template('reader.html', page_links=page_links)

@app.route('/api/get_image', methods=['GET'])
def api_get_image():
    page_url = request.args.get('url')
    if not page_url:
        return jsonify({"error": "Missing url parameter"}), 400
        
    img_url = get_image_url(page_url)
    if img_url:
        return jsonify({"image_url": img_url})
    else:
        return jsonify({"error": "Cannot find image"}), 404

if __name__ == '__main__':
    # Chạy server ở port 5000
    app.run(debug=True)