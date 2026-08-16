const API = {
    voices: '/api/voices',
    tts: '/api/tts',
    stt: '/api/stt/upload',
    langs: '/api/languages'
};

let voices = [];
let currentTab = 'tts';

// --- Tabs ---
document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', function() {
        document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        this.classList.add('active');
        const tab = this.dataset.tab;
        document.getElementById(`tab-${tab}`).classList.add('active');
        currentTab = tab;
        if (tab === 'voices') loadVoices();
    });
});

// --- Load Voices ---
async function loadVoices(filterLang = '') {
    const resp = await fetch(`${API.voices}?lang=${filterLang}`);
    voices = await resp.json();
    const tbody = document.getElementById('voice-body');
    tbody.innerHTML = '';
    const search = document.getElementById('voice-search').value.toLowerCase();
    const filtered = voices.filter(v => {
        const matchName = v.display_name.toLowerCase().includes(search) || v.voice_type.toLowerCase().includes(search);
        return matchName;
    });
    filtered.forEach((v, i) => {
        const tr = document.createElement('tr');
        if (v.is_neural) tr.className = 'neural';
        const nameDisplay = v.is_neural ? `⚡ ${v.display_name} [edge-tts]` : v.display_name;
        tr.innerHTML = `<td>${nameDisplay}</td><td>${v.voice_type}</td><td>${v.lang}</td>`;
        tr.addEventListener('click', () => {
            // Заполнить TTS комбобокс
            const sel = document.getElementById('tts-voice');
            for (let opt of sel.options) {
                if (opt.value === v.voice_type) { sel.value = v.voice_type; break; }
            }
        });
        tbody.appendChild(tr);
    });
    document.getElementById('voice-count').textContent = `Всего: ${filtered.length} голосов`;
    // Обновить фильтр языков
    const langSet = new Set(voices.map(v => v.lang));
    const filterSel = document.getElementById('voice-lang-filter');
    const currentVal = filterSel.value;
    filterSel.innerHTML = '<option value="">Все</option>';
    [...langSet].sort().forEach(l => {
        const opt = document.createElement('option');
        opt.value = l;
        opt.textContent = l;
        filterSel.appendChild(opt);
    });
    filterSel.value = currentVal || '';
}

document.getElementById('voice-lang-filter').addEventListener('change', function() {
    loadVoices(this.value);
});
document.getElementById('voice-search').addEventListener('input', function() {
    loadVoices(document.getElementById('voice-lang-filter').value);
});

// --- TTS ---
async function loadTTSVoices() {
    const resp = await fetch(API.voices);
    const all = await resp.json();
    const sel = document.getElementById('tts-voice');
    sel.innerHTML = '';
    // Уникальные голоса
    const seen = new Set();
    all.forEach(v => {
        if (!seen.has(v.voice_type)) {
            seen.add(v.voice_type);
            const opt = document.createElement('option');
            opt.value = v.voice_type;
            opt.textContent = `${v.display_name} (${v.lang})`;
            sel.appendChild(opt);
        }
    });
    if (sel.options.length) sel.value = sel.options[0].value;
}
loadTTSVoices();

document.getElementById('tts-generate').addEventListener('click', async function() {
    const text = document.getElementById('tts-text').value.trim();
    if (!text) { alert('Введите текст!'); return; }
    const voice = document.getElementById('tts-voice').value;
    const rate = document.getElementById('tts-rate').value;
    this.disabled = true;
    this.textContent = '⏳ Генерация...';
    document.getElementById('tts-result').textContent = '⏳ Ожидание...';
    document.getElementById('tts-audio').style.display = 'none';
    try {
        const resp = await fetch(API.tts, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text, voice, rate})
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.error || 'Ошибка сервера');
        }
        // Получаем blob и создаём ссылку
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const audio = document.getElementById('tts-audio');
        audio.src = url;
        audio.style.display = 'block';
        audio.play();
        document.getElementById('tts-result').textContent = '✅ Генерация завершена!';
    } catch (e) {
        document.getElementById('tts-result').textContent = `❌ Ошибка: ${e.message}`;
    } finally {
        this.disabled = false;
        this.textContent = '▶ Сгенерировать';
    }
});

// --- STT ---
document.getElementById('stt-transcribe').addEventListener('click', async function() {
    const fileInput = document.getElementById('stt-file');
    if (!fileInput.files || fileInput.files.length === 0) {
        alert('Выберите файл!');
        return;
    }
    const file = fileInput.files[0];
    const lang = document.getElementById('stt-lang').value;
    const transLang = document.getElementById('stt-trans-lang').value;
    const useTrans = document.getElementById('stt-use-trans').checked;

    this.disabled = true;
    this.textContent = '⏳ Загрузка...';
    document.getElementById('stt-result').textContent = '⏳ Ожидание...';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('lang', lang);
    formData.append('trans_lang', transLang);
    formData.append('use_trans', String(useTrans));

    try {
        const resp = await fetch(API.stt, {
            method: 'POST',
            body: formData
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.error || 'Ошибка сервера');
        }
        const data = await resp.json();
        let out = `📄 Полный текст:\n${data.full_text || ''}\n\n`;
        out += `📌 ${data.utterances?.length || 0} фраз:\n`;
        if (data.utterances) {
            data.utterances.forEach(u => {
                out += `[${(u.start/1000).toFixed(2)}s → ${(u.end/1000).toFixed(2)}s]  ${u.text}\n`;
            });
        }
        document.getElementById('stt-result').textContent = out;
    } catch (e) {
        document.getElementById('stt-result').textContent = `❌ Ошибка: ${e.message}`;
    } finally {
        this.disabled = false;
        this.textContent = '▶ Распознать';
    }
});

// --- Статус ---
function setStatus(text, color = '#57f287') {
    const el = document.getElementById('status');
    el.textContent = text;
    el.style.color = color;
}
setStatus('● Готов');