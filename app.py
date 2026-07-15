import hmac
import hashlib
import logging
import mimetypes
import os
import re
import time
from urllib.parse import quote, urlparse

import urllib3
import cloudscraper
import requests
from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


app = Flask(__name__)
CORS(app)

# ==========================================
# CONFIGURATION
# ==========================================
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
REQUEST_TIMEOUT = 30
HMAC_SECRET = os.environ.get('SECRET_KEY', 'super-secret-key').encode()

# ==========================================
# SCRAPER LOGIC
# ==========================================
def validate_url(url):
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith('www.'):
            host = host[4:]
        
        if 'tera' in host or 'baidu' in host:
            return True
            
        allowed = [
            'terabox.com', 'terabox.app', 'terabox.fun', 'teraboxapp.com',
            'mirrobox.com', 'nephobox.com', 'freeterabox.com', '1024tera.com',
            '4funbox.com', 'momerybox.com', 'tibibox.com'
        ]
        return host in allowed
    except Exception:
        return False

def get_surl(url):
    match = re.search(r'surl=([A-Za-z0-9_-]+)', url)
    if match:
        surl = match.group(1)
        return surl if surl.startswith("1") else "1" + surl
    
    match = re.search(r'/s/([A-Za-z0-9_-]+)', url)
    if match:
        surl = match.group(1)
        return surl if surl.startswith("1") else "1" + surl
        
    return None

def find_key(obj, keys):
    """Recursively search JSON for a specific key."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str) and v.startswith('http'):
                return v
            if k in keys and isinstance(v, str) and v != "":
                return v
            res = find_key(v, keys)
            if res: return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_key(item, keys)
            if res: return res
    return None

def extract_terabox(url):
    if not validate_url(url):
        return None, "Invalid URL. Please provide a valid Terabox link."

    surl = get_surl(url)
    if not surl:
        return None, "Could not find Terabox share ID in the link."

    # ==========================================
    # METHOD 1: Proxy Bypass (Bypasses your IP's CAPTCHA block)
    # ==========================================
    try:
        logging.info("Trying Method 1: AllOrigins Proxy")
        terabox_api_url = f"https://www.terabox.com/share/list?app_id=250528&web=1&channel=0&jsToken=&shorturl={surl}&root=1"
        proxy_url = f"https://api.allorigins.win/raw?url={quote(terabox_api_url, safe='')}"
        
        resp = requests.get(proxy_url, timeout=20, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        data = resp.json()

        if data.get("errno") == 0 and data.get("list"):
            file_data = data["list"][0]
            dlink = file_data.get("dlink")
            if dlink:
                logging.info("Success via Proxy!")
                return {
                    'direct_url': dlink,
                    'filename': file_data.get('server_filename', 'terabox_file'),
                    'size': file_data.get('size'),
                    'thumbnail': file_data.get('thumbs', {}).get('url') if file_data.get('thumbs') else None
                }, None
    except Exception as e:
        logging.error(f"Proxy failed: {e}")

    # ==========================================
    # METHOD 2: Multiple Fallback APIs with Recursive JSON Parser
    # This searches the entire JSON response for the link, no matter the format
    # ==========================================
    apis = [
        "https://widipe.com/api/terabox?url={}",
        "https://api.akuari.my.id/api/dl/terabox?link={}",
        "https://ytshorts.savetube.me/api/terabox/?url={}",
        "https://teradl-api.dapuntaratya.com/api?mode=fast&url={}"
    ]

    for api in apis:
        try:
            logging.info(f"Trying API: {api}")
            api_url = api.format(quote(url, safe=''))
            
            # verify=False prevents SSL errors
            resp = requests.get(api_url, timeout=15, verify=False, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()

            # Use recursive parser to find the download link and filename
            dlink = find_key(data, ['dlink', 'download_link', 'direct_link', 'link', 'url'])
            filename = find_key(data, ['filename', 'server_filename', 'name'])
            size = find_key(data, ['size', 'file_size'])
            thumb = find_key(data, ['thumb', 'thumbnail', 'thumbs'])

            if dlink and dlink.startswith('http'):
                logging.info(f"Success via {api}!")
                return {
                    'direct_url': dlink,
                    'filename': filename or 'terabox_file',
                    'size': size,
                    'thumbnail': thumb
                }, None

        except Exception as e:
            logging.error(f"API {api} failed: {e}")

    return None, "Failed. Terabox is actively blocking all requests from your network. Please try again later."
# ==========================================
# SECURITY & STREAMING
# ==========================================
def sign_url(url: str, ttl: int = 3600) -> str:
    exp = int(time.time()) + ttl
    msg = f"{exp}|{url}".encode()
    sig = hmac.new(HMAC_SECRET, msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"

def verify_url(exp_str: str, sig: str, url: str) -> bool:
    try:
        if int(exp_str) < time.time():
            return False
        expected = hmac.new(HMAC_SECRET, f"{exp_str}|{url}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False

# ==========================================
# HTML FRONTEND
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>TeraDownloader - Download Terabox Files</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" />
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', sans-serif; }
        body { background: #f0f4f8; color: #1a1a2e; line-height: 1.6; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        .navbar { display: flex; justify-content: space-between; align-items: center; background: #fff; padding: 15px 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); margin-bottom: 30px; }
        .logo { font-size: 26px; font-weight: 800; color: #e74c3c; text-decoration: none; }
        .logo span { color: #2d3436; }
        .hero { background: #fff; border-radius: 20px; padding: 40px 35px; box-shadow: 0 4px 20px rgba(0,0,0,0.06); text-align: center; margin-bottom: 30px; }
        .hero h1 { font-size: 32px; margin-bottom: 10px; }
        .hero h1 i { color: #e74c3c; margin-right: 10px; }
        .hero p { color: #555; margin-bottom: 25px; font-size: 16px; }
        .download-form { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; max-width: 700px; margin: 0 auto; }
        .download-form input { flex: 1; min-width: 250px; padding: 16px 20px; border: 2px solid #dfe6e9; border-radius: 50px; font-size: 16px; outline: none; }
        .download-form input:focus { border-color: #e74c3c; }
        .download-form button { padding: 16px 40px; background: #e74c3c; color: #fff; border: none; border-radius: 50px; font-size: 18px; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 10px; }
        .download-form button:hover { background: #c0392b; }
        #result { margin-top: 30px; padding: 20px; border-radius: 12px; background: #f8f9fa; display: none; }
        #result.show { display: block; }
        #result .thumbnail { max-width: 100%; border-radius: 10px; margin: 10px 0; max-height: 300px; }
        #result .direct-download-btn { display: inline-block; padding: 14px 35px; background: #27ae60; color: #fff; border-radius: 50px; text-decoration: none; font-weight: 700; margin-top: 15px; }
        .loader { border: 4px solid #f3f3f3; border-top: 4px solid #e74c3c; border-radius: 50%; width: 40px; height: 40px; animation: spin 0.8s linear infinite; margin: 20px auto; display: none; }
        .loader.show { display: block; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .error-message { color: #e74c3c; padding: 15px; background: #fde8e8; border-radius: 8px; margin-top: 15px; display: none; }
        .error-message.show { display: block; }
        .footer { margin-top: 40px; padding: 30px 0; text-align: center; font-size: 14px; color: #777; }
    </style>
</head>
<body>
    <div class="container">
        <nav class="navbar">
            <a href="/" class="logo">Tera<span>Downloader</span></a>
        </nav>
        
        <div class="hero">
            <h1><i class="fas fa-download"></i>Download Terabox Files</h1>
            <p>Paste your Terabox link below to generate a direct download link for any file type.</p>
            
            <form class="download-form" id="downloadForm">
                <input type="url" id="urlInput" placeholder="Enter terabox link here" required />
                <button type="submit"><i class="fas fa-download"></i> Download</button>
            </form>
            
            <div class="loader" id="loader"></div>
            <div class="error-message" id="errorMessage"></div>
            
            <div id="result">
                <h3><i class="fas fa-check-circle" style="color:#27ae60;"></i> Ready to Download!</h3>
                <img id="thumbnail" class="thumbnail" style="display:none;" />
                <div id="fileInfo"></div>
                <a id="downloadBtn" class="direct-download-btn"><i class="fas fa-download"></i> Download File</a>
            </div>
        </div>
        
        <footer class="footer">
            <p>&copy; 2024 TeraDownloader. All rights reserved.</p>
        </footer>
    </div>
    
    <script>
        const form = document.getElementById('downloadForm');
        const urlInput = document.getElementById('urlInput');
        const loader = document.getElementById('loader');
        const result = document.getElementById('result');
        const errorMessage = document.getElementById('errorMessage');
        const thumbnail = document.getElementById('thumbnail');
        const fileInfo = document.getElementById('fileInfo');
        const downloadBtn = document.getElementById('downloadBtn');
        
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            result.classList.remove('show');
            errorMessage.classList.remove('show');
            errorMessage.textContent = '';
            
            const url = urlInput.value.trim();
            if (!url) { showError('Please enter a URL'); return; }
            
            loader.classList.add('show');
            
            try {
                const response = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url })
                });
                
                const data = await response.json();
                
                if (!response.ok || !data.success) {
                    showError(data.error || 'Unknown error occurred.');
                    return;
                }
                
                if (data.thumbnail) {
                    thumbnail.src = data.thumbnail;
                    thumbnail.style.display = 'block';
                } else {
                    thumbnail.style.display = 'none';
                }
                
                fileInfo.textContent = 'File: ' + (data.filename || 'terabox_file');
                downloadBtn.href = data.download_url;
                downloadBtn.style.display = 'inline-block';
                
                result.classList.add('show');
                
            } catch (error) {
                showError('Network error. Is the server running?');
            } finally {
                loader.classList.remove('show');
            }
        });
        
        function showError(message) {
            errorMessage.textContent = message;
            errorMessage.classList.add('show');
        }
    </script>
</body>
</html>
"""

# ==========================================
# ROUTES
# ==========================================
@app.route('/')
def index():
    return HTML_PAGE

@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({'success': False, 'error': 'No URL provided.'}), 400
    
    url = data['url'].strip()
    if not url.startswith('http'):
        return jsonify({'success': False, 'error': 'Invalid URL format.'}), 400
    
    file_info, error = extract_terabox(url)
    
    if error:
        return jsonify({'success': False, 'error': error}), 404
    
    direct_url = file_info.get('direct_url')
    filename = file_info.get('filename', 'terabox_file')
    
    signed = sign_url(direct_url)
    exp, sig = signed.split('.')
    
    proxy_url = f"/api/download-file?url={quote(direct_url, safe='')}&exp={exp}&sig={sig}&fn={quote(filename, safe='')}"
    
    return jsonify({
        'success': True,
        'download_url': proxy_url,
        'filename': filename,
        'thumbnail': file_info.get('thumbnail'),
        'size': file_info.get('size')
    })

@app.route('/api/download-file', methods=['GET'])
def download_file():
    url = request.args.get('url')
    exp = request.args.get('exp')
    sig = request.args.get('sig')
    filename = request.args.get('fn', 'terabox_file')
    
    if not (url and exp and sig) or not verify_url(exp, sig, url):
        return jsonify({'error': 'Invalid or expired link'}), 403
    
    host = urlparse(url).netloc.lower()
    if not any(h in host for h in ["terabox", "baidu", "teraboxcdn", "d-jp", "d-hk", "d-us", "teradl"]):
        return jsonify({'error': 'Host not allowed'}), 403

    def generate():
        try:
            scraper = cloudscraper.create_scraper()
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": "https://1024tera.com/"
            }
            # verify=False allows downloading from APIs with broken SSL certificates
            with scraper.get(url, stream=True, timeout=REQUEST_TIMEOUT, headers=headers, verify=False) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
        except Exception as e:
            logging.error("Stream failed: %s", e)
            yield b""

    # Detect file mimetype (video, zip, pdf, etc)
    mimetype, _ = mimetypes.guess_type(filename)
    if mimetype is None:
        mimetype = 'application/octet-stream' # Default binary stream if unknown

    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Content-Type': mimetype,
        'Cache-Control': 'no-store'
    }
    
    return Response(stream_with_context(generate()), headers=headers)

if __name__ == "__main__":
    # Disable the auto-reloader to avoid triggering reloads when
    # packages in the virtualenv are modified (install/uninstall).
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
