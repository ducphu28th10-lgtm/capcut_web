import re
import os
import json
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote
from flask import Flask, request, jsonify, send_file, render_template, send_from_directory
from capcut_tts_api import CapCutClient
import requests

app = Flask(__name__)
app.config['SECRET_KEY'] = 'capcut-web-key'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB

# Global client
_client = None
_voices_cache = None
_voices_lock = threading.Lock()

def get_client():
    global _client
    if _client is None:
        _client = CapCutClient()
    return _client

def get_voices():
    global _voices_cache
    with _voices_lock:
        if _voices_cache is None:
            try:
                _voices_cache = get_client().list_voices()
            except Exception:
                _voices_cache = []
        return _voices_cache

def format_srt_time(ms):
    """
    Chuyển milliseconds sang định dạng SRT: HH:MM:SS,mmm
    """
    ms = int(ms)
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

def generate_filename(ext="mp3"):
    """
    Tạo tên file theo định dạng DD-MM-STT.extension
    Tự động tăng STT theo số file đã tồn tại trong ngày
    """
    now = datetime.now()
    day = f"{now.day:02d}"
    month = f"{now.month:02d}"
    
    # Tìm số thứ tự cao nhất hiện có trong ngày
    pattern = f"^{day}-{month}-(\\d+)\\."
    existing_files = [f for f in os.listdir('.') if re.match(pattern, f)]
    
    max_num = 0
    for f in existing_files:
        match = re.search(pattern, f)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num
    
    video_num = max_num + 1
    filename = f"{day}-{month}-{video_num}.{ext}"
    
    # Phòng trường hợp trùng
    counter = 0
    while os.path.exists(filename):
        counter += 1
        filename = f"{day}-{month}-{video_num}_{counter}.{ext}"
    
    return filename

def generate_srt(utterances):
    """
    Tạo nội dung SRT từ danh sách utterances.
    Mỗi TỪ ĐƠN là một dòng phụ đề riêng (one word at a time).
    Tự tách cụm từ thành từng từ đơn và chia đều thời gian.
    """
    srt_lines = []
    index = 1
    
    for u in utterances:
        # Nếu có words với timestamp, tách từng từ đơn trong mỗi word
        if u.words and len(u.words) > 0:
            for w in u.words:
                start_ms = int(w.start_time)
                end_ms = int(w.end_time)
                text = w.text.strip()
                
                if not text:
                    continue
                
                # Tách text thành các từ đơn (split theo khoảng trắng)
                single_words = text.split()
                num_words = len(single_words)
                
                if num_words == 0:
                    continue
                
                if num_words == 1:
                    # Chỉ có 1 từ, giữ nguyên thời gian
                    srt_lines.append(f"{index}")
                    srt_lines.append(f"{format_srt_time(start_ms)} --> {format_srt_time(end_ms)}")
                    srt_lines.append(single_words[0])
                    srt_lines.append("")
                    index += 1
                else:
                    # Nhiều từ, chia đều thời gian theo tỷ lệ độ dài từ
                    total_duration = end_ms - start_ms
                    
                    # Tính độ dài mỗi từ (dùng để chia thời gian theo tỷ lệ)
                    word_lengths = [len(word) for word in single_words]
                    total_length = sum(word_lengths)
                    
                    current_time = start_ms
                    
                    for i, word in enumerate(single_words):
                        # Chia thời gian theo tỷ lệ độ dài từ
                        word_duration = int(total_duration * word_lengths[i] / total_length)
                        word_start = current_time
                        word_end = current_time + word_duration
                        
                        # Đảm bảo từ cuối cùng kết thúc đúng end_ms
                        if i == num_words - 1:
                            word_end = end_ms
                        
                        srt_lines.append(f"{index}")
                        srt_lines.append(f"{format_srt_time(word_start)} --> {format_srt_time(word_end)}")
                        srt_lines.append(word)
                        srt_lines.append("")
                        index += 1
                        
                        current_time = word_end
        else:
            # Nếu không có words, dùng cả câu và tách từng từ
            start_ms = int(u.start_time)
            end_ms = int(u.end_time)
            text = u.text.strip()
            
            if not text:
                continue
            
            single_words = text.split()
            num_words = len(single_words)
            
            if num_words == 0:
                continue
            
            if num_words == 1:
                srt_lines.append(f"{index}")
                srt_lines.append(f"{format_srt_time(start_ms)} --> {format_srt_time(end_ms)}")
                srt_lines.append(single_words[0])
                srt_lines.append("")
                index += 1
            else:
                total_duration = end_ms - start_ms
                
                # Tính độ dài mỗi từ
                word_lengths = [len(word) for word in single_words]
                total_length = sum(word_lengths)
                
                current_time = start_ms
                
                for i, word in enumerate(single_words):
                    word_duration = int(total_duration * word_lengths[i] / total_length)
                    word_start = current_time
                    word_end = current_time + word_duration
                    
                    if i == num_words - 1:
                        word_end = end_ms
                    
                    srt_lines.append(f"{index}")
                    srt_lines.append(f"{format_srt_time(word_start)} --> {format_srt_time(word_end)}")
                    srt_lines.append(word)
                    srt_lines.append("")
                    index += 1
                    
                    current_time = word_end
    
    return "\n".join(srt_lines)

def generate_title(full_text):
    """
    Tạo tiêu đề tự động từ nội dung văn bản.
    Lấy câu đầu tiên hoặc 6-8 từ đầu tiên làm tiêu đề.
    """
    if not full_text:
        return "Không có tiêu đề"
    
    text = full_text.strip()
    
    # Tách câu
    sentences = re.split(r'[.!?]+', text)
    first_sentence = sentences[0].strip() if sentences else text
    
    # Lấy 6-8 từ đầu tiên
    words = first_sentence.split()
    
    if len(words) <= 8:
        title = first_sentence
    else:
        title = ' '.join(words[:8]) + '...'
    
    # Viết hoa chữ cái đầu
    if title:
        title = title[0].upper() + title[1:]
    
    return title


# ============================================
# HÀM HỖ TRỢ TẢI VIDEO
# ============================================
def extract_clean_url(messy_string):
    """
    Trích xuất URL sạch từ chuỗi bất kỳ.
    """
    decoded = unquote(messy_string)
    raw_urls = re.findall(r'https?://[^\s<>"\'()\[\]{}]+', decoded)
    if not raw_urls:
        raw_urls = re.findall(r'https?://[^\s<>"\'()\[\]{}]+', messy_string)
        if not raw_urls:
            return None
    url = raw_urls[0].strip('.,;:!?')
    return url

def get_video_info(clean_url):
    """
    Gọi API GenDownload.
    """
    api_url = "https://gendownload.com/api/extract"
    headers = {"Content-Type": "application/json"}
    payload = {"url": clean_url}
    
    response = requests.post(api_url, json=payload, headers=headers, timeout=30)
    if response.status_code != 200:
        return {"error": f"API lỗi: {response.status_code}"}
    return response.json()

def download_video_from_format(format_url, filename):
    """
    Tải video.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://gendownload.com/'
    }
    
    response = requests.get(format_url, headers=headers, stream=True, timeout=60)
    if response.status_code != 200:
        return {"error": f"Tải thất bại: {response.status_code}"}
    
    total_size = int(response.headers.get('content-length', 0))
    downloaded = 0
    
    with open(filename, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
    
    return {"status": "success", "filename": filename, "size": downloaded}

@app.route('/')
def index():
    return render_template('index.html')

# ============================================
# TAB TẢI VIDEO
# ============================================
@app.route('/download-video', methods=['GET', 'POST'])
def download_video():
    if request.method == 'POST':
        video_url = request.form.get('video_url', '').strip()
        quality = request.form.get('quality', 'best').strip()
        
        if not video_url:
            return render_template('download_video.html', error="Vui lòng nhập link video")
        
        # Trích xuất URL sạch
        clean_url = extract_clean_url(video_url)
        if not clean_url:
            return render_template('download_video.html', error="Không tìm thấy URL hợp lệ")
        
        # Gọi API
        video_data = get_video_info(clean_url)
        if "error" in video_data:
            return render_template('download_video.html', error=video_data['error'])
        
        # Chọn format
        formats = video_data.get('formats', [])
        if not formats:
            return render_template('download_video.html', error="Không có format")
        
        selected_format = None
        if quality == "audio":
            for fmt in formats:
                if fmt.get('type') == 'audio':
                    selected_format = fmt
                    break
        elif quality.isdigit():
            target = int(quality)
            for fmt in formats:
                if fmt.get('type') == 'video':
                    label = fmt.get('label', '')
                    if 'p' in label:
                        try:
                            h = int(label.replace('p', ''))
                            if h <= target:
                                selected_format = fmt
                                break
                        except:
                            pass
            if not selected_format:
                selected_format = formats[0]
        else:
            for fmt in formats:
                if fmt.get('type') == 'video':
                    selected_format = fmt
                    break
            if not selected_format:
                selected_format = formats[0]
        
        if not selected_format:
            return render_template('download_video.html', error="Không tìm thấy format")
        
        # Lấy URL tải
        format_url = selected_format.get('url')
        if not format_url:
            return render_template('download_video.html', error="Không có URL tải")
        
        # Tạo thư mục downloads nếu chưa có
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
        
        # Tạo tên file
        ext = selected_format.get('ext', 'mp4')
        filename = generate_filename(ext)
        filepath = os.path.join('downloads', filename)
        
        # Tải video
        result = download_video_from_format(format_url, filepath)
        
        if "error" in result:
            return render_template('download_video.html', error=result['error'])
        
        # Trả về file tải xuống
        return send_from_directory('downloads', filename, as_attachment=True)
    
    return render_template('download_video.html')

@app.route('/api/voices')
def api_voices():
    lang = request.args.get('lang', '')
    voices = get_voices()
    if lang:
        voices = [v for v in voices if v.lang.lower() == lang.lower()]
    result = [{
        'voice_type': v.voice_type,
        'display_name': v.display_name,
        'resource_id': v.resource_id,
        'lang': v.lang,
        'lan': v.lan,
        'is_neural': 'Neural' in v.voice_type
    } for v in voices]
    return jsonify(result)

@app.route('/api/tts', methods=['POST'])
def api_tts():
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', 'BV074_streaming')
    rate = data.get('rate', '1.0')
    if not text:
        return jsonify({'error': 'No text'}), 400

    try:
        # Neural voices via edge-tts
        if 'Neural' in voice:
            import asyncio
            import edge_tts
            tmp = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            tmp.close()
            try:
                r = float(rate)
            except:
                r = 1.0
            pct = int((r - 1.0) * 100)
            rate_str = f"+{pct}%" if pct >= 0 else f"{pct}%"
            async def gen():
                comm = edge_tts.Communicate(text=text, voice=voice, rate=rate_str)
                await comm.save(tmp.name)
            asyncio.run(gen())
            # Tạo tên file theo định dạng DD-MM-STT.mp3
            filename = generate_filename("mp3")
            # Đổi tên file tạm thành tên đúng định dạng
            os.rename(tmp.name, filename)
            return send_file(filename, as_attachment=True, download_name=filename)
        else:
            # CapCut API
            client = get_client()
            create_res = client.create_tts_task(texts=text, voice=voice, rate=rate)
            tasks = (create_res.get('data') or {}).get('tasks') or []
            if not tasks:
                return jsonify({'error': 'No task created'}), 500
            task_id = tasks[0]['id']
            token = tasks[0]['token']
            # Poll until success
            for _ in range(60):
                time.sleep(2)
                q = client.query_tts_task(task_id, token)
                qtasks = (q.get('data') or {}).get('tasks') or []
                if not qtasks:
                    continue
                status = qtasks[0].get('status', '')
                if status in ('succeed', 'success', 'completed', 'done'):
                    payload_raw = qtasks[0].get('payload', '{}')
                    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                    subs = payload.get('audio_subtitles') or []
                    for sub in subs:
                        url = sub.get('speech_url')
                        if url:
                            # Tạo tên file theo định dạng DD-MM-STT.mp3
                            filename = generate_filename("mp3")
                            urllib.request.urlretrieve(url, filename)
                            return send_file(filename, as_attachment=True, download_name=filename)
                    return jsonify({'error': 'No audio URL'}), 500
                elif status in ('failed', 'error', 'fail'):
                    return jsonify({'error': f'Task failed: {status}'}), 500
            return jsonify({'error': 'Timeout'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stt/upload', methods=['POST'])
def api_stt_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
    tmp = tempfile.NamedTemporaryFile(suffix='.' + file.filename.split('.')[-1], delete=False)
    tmp.close()
    file.save(tmp.name)
    lang = request.form.get('lang', 'vi-VN')
    trans_lang = request.form.get('trans_lang', 'vi-VN')
    use_trans = request.form.get('use_trans', 'false').lower() == 'true'

    try:
        client = get_client()
        upload = client.upload_audio(tmp.name)
        stt_res = client.create_stt_task(
            audio_vid=upload.vid,
            audio_md5=upload.md5,
            duration_ms=upload.duration_ms or 10000,
            language=lang,
            translation_language=trans_lang,
            use_translation=use_trans
        )
        tasks = (stt_res.get('data') or {}).get('tasks') or []
        if not tasks:
            return jsonify({'error': 'No STT task'}), 500
        task_id = tasks[0]['id']
        token = tasks[0]['token']
        for _ in range(90):
            time.sleep(2)
            q = client.query_stt_task(task_id, token)
            qtasks = (q.get('data') or {}).get('tasks') or []
            if not qtasks:
                continue
            status = qtasks[0].get('status', '')
            if status in ('succeed', 'success', 'completed', 'done'):
                subs = client.extract_subtitles(q)
                result = {
                    'full_text': subs.full_text,
                    'utterances': [{
                        'text': u.text,
                        'start': u.start_time,
                        'end': u.end_time,
                        'words': [{'text': w.text, 'start': w.start_time, 'end': w.end_time} for w in u.words]
                    } for u in subs.utterances]
                }
                # THÊM SRT CONTENT VÀO KẾT QUẢ
                result['srt_content'] = generate_srt(subs.utterances)
                os.unlink(tmp.name)
                return jsonify(result)
            elif status in ('failed', 'error', 'fail'):
                os.unlink(tmp.name)
                return jsonify({'error': f'STT failed: {status}'}), 500
        os.unlink(tmp.name)
        return jsonify({'error': 'STT timeout'}), 500
    except Exception as e:
        try:
            os.unlink(tmp.name)
        except:
            pass
        return jsonify({'error': str(e)}), 500

@app.route('/api/stt/download-srt', methods=['POST'])
def api_stt_download_srt():
    """
    Nhận dữ liệu SRT từ client và cho tải về file .srt
    """
    data = request.get_json()
    srt_content = data.get('srt_content', '')
    filename = data.get('filename', 'subtitles.srt')
    
    if not srt_content:
        return jsonify({'error': 'No SRT content'}), 400
    
    # Tạo file tạm
    tmp = tempfile.NamedTemporaryFile(suffix='.srt', delete=False, mode='w', encoding='utf-8')
    tmp.write(srt_content)
    tmp.close()
    
    return send_file(tmp.name, as_attachment=True, download_name=filename)

@app.route('/api/languages')
def api_languages():
    return jsonify(['vi-VN', 'zh-CN', 'en-US', 'ja-JP', 'ko-KR', 'fr-FR', 'de-DE', 'es-ES', 'th-TH', 'id-ID'])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
