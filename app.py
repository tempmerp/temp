import os
import re
import time
import hmac
import hashlib
import logging
import mimetypes
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from urllib.parse import quote, urlparse

app = Flask(__name__)
CORS(app)

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HMAC_SECRET = os.environ.get('SECRET_KEY', 'super-secret-key-123').encode()

def validate_url(url):
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith('www.'): host = host[4:]
        if 'tera' in host or 'baidu' in host: return True
        return False
    except: return False

def get_surl(url):
    match = re.search(r'surl=([A-Za-z0-9_-]+)', url)
    if match: return match.group(1) if match.group(1).startswith("1") else "1" + match.group(1)
    match = re.search(r'/s/([A-Za-z0-9_-]+)', url)
    if match: return match.group(1) if match.group(1).startswith("1") else "1" + match.group(1)
    return None

# Recursive JSON searcher (Finds the link no matter how deep it is hidden)
def find_key(obj, keys):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                if isinstance(v, str) and v.startswith('http'): return v
                if isinstance(v, str) and v != "": return v
            res = find_key(v, keys)
            if res: return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_key(item, keys)
            if res: return res
    return None

def extract_terabox(url):
    if not validate_url(url): return None, "Invalid Terabox URL."
    surl = get_surl(url)
    if not surl: return None, "Could not find share ID."
    
    errors = []

    # List of all endpoints (Proxy Bypasses + Public APIs)
    terabox_api = f"https://www.terabox.com/share/list?app_id=250528&web=1&channel=0&jsToken=&shorturl={surl}&root=1"
    
    endpoints = [
        f"https://api.allorigins.win/raw?url={quote(terabox_api, safe='')}",
        f"https://corsproxy.io/?url={quote(terabox_api, safe='')}",
        f"https://api.codetabs.com/v1/proxy/?quest={quote(terabox_api, safe='')}",
        f"https://api.teradownloader.com/api/v1/fetch?url={quote(url, safe='')}",
        f"https://teraboxdown.com/api?link={quote(url, safe='')}",
        f"https://teradl-api.dapuntaratya.com/api?mode=fast&url={quote(url, safe='')}"
    ]

    for ep in endpoints:
        try:
            logging.info(f"Trying: {ep[:50]}")
            resp = requests.get(ep, timeout=15, verify=False, headers={"User-Agent": USER_AGENT})
            
            # Check if response is JSON
            try:
                data = resp.json()
            except:
                errors.append(f"{ep[:30]}: HTML/Empty")
                continue
                
            # Use recursive parser to find link anywhere in JSON
            dlink = find_key(data, ['dlink', 'download_link', 'direct_link', 'link'])
            
            if dlink and dlink.startswith('http'):
                filename = find_key(data, ['filename', 'server_filename', 'name']) or 'terabox_file'
                size = find_key(data, ['size', 'file_size'])
                thumb = find_key(data, ['thumb', 'thumbnail'])
                
                logging.info(f"Success via {ep[:30]}!")
                return {'direct_url': dlink, 'filename': filename, 'size': size, 'thumbnail': thumb}, None
            else:
                # Extract error message if present
                err_msg = find_key(data, ['errmsg', 'error', 'message'])
                errors.append(f"{ep[:30]}: {err_msg or 'No link found'}")
                
        except Exception as e:
            errors.append(f"{ep[:30]}: {str(e)[:30]}")

    return None, f"All methods failed: {' | '.join(errors)}"

def sign_url(url: str, ttl: int = 3600) -> str:
    exp = int(time.time()) + ttl
    msg = f"{exp}|{url}".encode()
    sig = hmac.new(HMAC_SECRET, msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"

def verify_url(exp_str: str, sig: str, url: str) -> bool:
    try:
        if int(exp_str) < time.time(): return False
        expected = hmac.new(HMAC_SECRET, f"{exp_str}|{url}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except: return False

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
        .error-message { color: #e74c3c; padding: 15px; background: #fde8e8; border-radius: 8px; margin-top: 15px; display: none; font-size: 14px; }
        .error-message.show { display: block; }
        .footer { margin-top: 40px; padding: 30px 0; text-align:
