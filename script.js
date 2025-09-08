document.addEventListener('DOMContentLoaded', () => {
    // サイドバーの表示・非表示を切り替える
    const menuToggle = document.getElementById('menu-toggle');
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('overlay');
    
    function toggleSidebar() {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('show');
    }

    if (menuToggle && sidebar && overlay) {
        menuToggle.addEventListener('click', toggleSidebar);
        overlay.addEventListener('click', toggleSidebar);
    }

    // ナビゲーションメニューのクリックイベント
    const navItems = document.querySelectorAll('.nav-item');
    const sections = document.querySelectorAll('.content-wrapper');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            
            // 全てのナビアイテムからactiveクラスを削除
            navItems.forEach(nav => nav.classList.remove('active'));
            
            // クリックされたアイテムにactiveクラスを追加
            item.classList.add('active');
            
            // 全てのセクションを非表示にする
            sections.forEach(section => section.classList.remove('active-section'));
            
            // 対応するセクションを表示する
            const target = item.dataset.target;
            const targetSection = document.getElementById(`${target}-section`);
            if (targetSection) {
                targetSection.classList.add('active-section');
            }

            // 議事録一覧タブに切り替えたときに一覧を読み込む
            if (target === 'list') {
                loadMinutesList();
            }

            // モバイルの場合はサイドバーを閉じる
            if (window.innerWidth <= 768) {
                toggleSidebar();
            }
        });
    });

    // 初期表示設定
    const initialActiveItem = document.querySelector('.nav-item.active');
    if (initialActiveItem) {
        const initialTarget = initialActiveItem.dataset.target;
        const initialSection = document.getElementById(`${initialTarget}-section`);
        if (initialSection) {
            initialSection.classList.add('active-section');
        }

        // 初期表示が一覧タブの場合は読み込む
        if (initialTarget === 'list') {
            loadMinutesList();
        }
    }

    // ログインユーザー名とアバター、ログアウトボタンの表示ロジック
    const loggedInUser = document.getElementById('logged-in-user');
    const userNameElement = document.getElementById('user-name');
    const userAvatarElement = document.getElementById('user-avatar');
    const logoutBtn = document.getElementById('logout-btn');

    // ログイン状態をチェックし、ユーザー名を取得する関数
    async function checkLoginStatus() {
        try {
            const response = await fetch('/.auth/me');
            const data = await response.json();
            const user = data.clientPrincipal;

            if (user) {
                // ログインしている場合
                loggedInUser.style.display = 'flex';
                logoutBtn.style.display = 'flex';

                // GitHubアカウント名を表示
                userNameElement.textContent = user.userDetails;

                // ユーザー名のイニシャルを取得してアバターに表示
                const userInitial = user.userDetails.charAt(0).toUpperCase();
                userAvatarElement.textContent = userInitial;
            } else {
                // ログインしていない場合はlogin.htmlにリダイレクト
                window.location.href = '/login.html';
            }
        } catch (error) {
            console.error('Failed to fetch user info:', error);
            // エラー時もログインしていないとみなし、login.htmlにリダイレクト
            window.location.href = '/login.html';
        }
    }

    // ページの読み込み時にログイン状態をチェック
    checkLoginStatus();

    // ログアウトボタンのクリックイベント
    logoutBtn.addEventListener('click', (e) => {
        e.preventDefault();
        window.location.href = '/.auth/logout';
    });
});

// ドラッグ＆ドロップでのファイルアップロード
const dropzone = document.getElementById('upload-dropzone');
const fileInput = document.getElementById('file-input');
const fileSelectBtn = document.getElementById('file-select-btn');
const progressCard = document.getElementById('progress-card');

if (dropzone) {
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
    });

    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('dragover');
    });

    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileUpload(files[0]);
        }
    });

    fileSelectBtn.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            handleFileUpload(fileInput.files[0]);
        }
    });
}

async function handleFileUpload(file) {
    console.log('Uploaded file:', file);
    progressCard.style.display = 'block';
    let toUpload = file;
    try {
        if (file && file.type && file.type.startsWith('video/')) {
            // フロントでffmpeg.wasmを用いて音声抽出
            if (progressCard) {
                const title = progressCard.querySelector('.progress-title');
                if (title) title.textContent = '動画から音声を抽出中...';
            }
            toUpload = await extractAudioOnClient(file);
        }
    } catch (e) {
        alert('動画から音声抽出に失敗しました。');
        console.error(e);
        progressCard.style.display = 'none';
        return;
    }

    const formData = new FormData();
    formData.append('file', toUpload);

    try {
        // Azure Functionsのエンドポイント '/api/upload' にファイルをPOST
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData,
            // Easy Auth使用時は認証情報が自動で付与されるため、手動でのヘッダー設定は不要
        });

        if (response.ok) {
            const result = await response.json();
            alert('ファイルのアップロードが完了しました。文字起こしを開始します。');
            console.log('Upload successful:', result);
        } else {
            const errorText = await response.text();
            alert(`エラーが発生しました: ${errorText}`);
            console.error('Upload failed:', errorText);
        }
    } catch (error) {
        alert(`エラーが発生しました: ${error.message}`);
        console.error('Upload error:', error);
    } finally {
        progressCard.style.display = 'none';
        // ファイル選択をリセットして、同じファイルを再度アップロードできるようにする
        fileInput.value = '';
    }
}

// ---- Added: realtime-ish status polling & list loader ----
const minutesCard = document.getElementById('minutes-card');
const minutesBody = document.getElementById('minutes-body');
let pollTimer = null;

async function pollStatus(jobId) {
    try {
        const r = await fetch(`/api/status?job_id=${encodeURIComponent(jobId)}`);
        const j = await r.json();
        if (r.status === 404) return;
        if (j.status === 'completed') {
            if (pollTimer) clearInterval(pollTimer);
            if (minutesBody && minutesCard) {
                minutesBody.textContent = j.minutes || '';
                minutesCard.style.display = 'block';
            }
        }
    } catch (e) { console.error(e); }
}

async function loadMinutesList() {
    try {
        const r = await fetch('/api/list-minutes');
        const j = await r.json();
        const tbody = document.querySelector('.data-table tbody');
        if (!tbody) return;
        tbody.innerHTML = (j.minutes || []).map(m => {
            const dt = m.last_modified ? new Date(m.last_modified).toLocaleString() : '';
            return `<tr>
                <td>
                    <div style="display:flex;align-items:center;gap:8px;">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="stroke: var(--text-tertiary);">
                            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        ${m.title || m.name}
                    </div>
                </td>
                <td>${dt}</td>
                <td>-</td>
                <td><span class="status-badge completed"><span class="status-dot"></span>完了</span></td>
                <td>
                  <div class="table-actions">
                    <button class="btn-small" data-name="${encodeURIComponent(m.name)}">表示</button>
                    <button class="btn-small" data-download="${encodeURIComponent(m.name)}">ダウンロード</button>
                    <button class="btn-small" data-translate="${encodeURIComponent(m.name)}">翻訳</button>
                    <button class="btn-small" data-regenerate="${encodeURIComponent(m.name)}">再生成</button>
                  </div>
                </td>
            </tr>`;
        }).join('');
        // クリックした行の直下に詳細（議事録本文）をアコーディオン表示
        tbody.querySelectorAll('button[data-name]').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.preventDefault();
                const enc = btn.getAttribute('data-name');
                const name = decodeURIComponent(enc);

                // すでに開いている詳細行を閉じる（複数開かない仕様）
                tbody.querySelectorAll('tr.detail-row').forEach(r => r.remove());

                // 本ボタンの行の次にプレースホルダーを挿入
                const tr = btn.closest('tr');
                const detail = document.createElement('tr');
                detail.className = 'detail-row';
                detail.innerHTML = `
                    <td colspan="5">
                        <div class="card" style="margin-top:8px;">
                            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;">
                                <h3 style="margin:0;font-size:16px;">本文プレビュー</h3>
                                <div style="display:flex;gap:8px;">
                                  <button class="btn-small" data-close>閉じる</button>
                                </div>
                            </div>
                            <pre class="minutes-inline" style="white-space:pre-wrap;word-break:break-word;min-height:60px;opacity:.8;">読み込み中...</pre>
                        </div>
                    </td>`;
                tr.after(detail);

                const closeBtn = detail.querySelector('button[data-close]');
                if (closeBtn) closeBtn.addEventListener('click', () => detail.remove());

                const pre = detail.querySelector('pre.minutes-inline');
                try {
                    const r = await fetch(`/api/status?name=${encodeURIComponent(name)}`);
                    if (!r.ok) {
                        const t = await r.text();
                        alert(`本文取得に失敗しました: ${r.status} ${t}`);
                        detail.remove();
                        return;
                    }
                    const j = await r.json();
                    if (j.status === 'completed') {
                        pre.textContent = j.minutes || '';
                        pre.style.opacity = '1';
                    } else if (j.status === 'forbidden') {
                        alert('この議事録へのアクセス権がありません');
                        detail.remove();
                    } else {
                        pre.textContent = `現在のステータス: ${j.status || 'unknown'}`;
                        pre.style.opacity = '1';
                    }
                } catch (e) {
                    console.error(e);
                    alert('本文取得中にエラーが発生しました');
                    detail.remove();
                }
            });
        });
        tbody.querySelectorAll('button[data-download]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const enc = btn.getAttribute('data-download');
                const name = decodeURIComponent(enc);
                try {
                    const r = await fetch(`/api/get-minutes?name=${encodeURIComponent(name)}`);
                    if (!r.ok) {
                        const t = await r.text();
                        alert(`ダウンロードに失敗しました: ${r.status} ${t}`);
                        return;
                    }
                    // サーバーのタイプに関わらず .txt として保存させる
                    let blob = await r.blob();
                    // 明示的に text/plain を指定
                    blob = new Blob([blob], { type: 'text/plain; charset=utf-8' });
                    const a = document.createElement('a');
                    const url = URL.createObjectURL(blob);
                    a.href = url;
                    let fileName = (name.split('/').pop() || 'minutes.txt');
                    if (!fileName.toLowerCase().endsWith('.txt')) fileName += '.txt';
                    a.download = fileName;
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    URL.revokeObjectURL(url);
                } catch (e) {
                    console.error(e);
                    alert('ダウンロード中にエラーが発生しました');
                }
            });
        });
        tbody.querySelectorAll('button[data-regenerate]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = decodeURIComponent(btn.getAttribute('data-regenerate'));
                const prompt = window.prompt('新しい指示を入力してください（要約観点や形式など）');
                if (!prompt) return;
                try {
                    const r = await fetch('/api/regenerate-minutes', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name, prompt })
                    });
                    if (!r.ok) {
                        const t = await r.text();
                        alert(`再生成に失敗しました: ${t}`);
                        return;
                    }
                    const j = await r.json();
                    if (minutesCard && minutesBody) {
                        minutesBody.textContent = j.minutes || '';
                        minutesCard.style.display = 'block';
                    }
                    // 更新後の一覧を再読み込み
                    await loadMinutesList();
                } catch (e) { console.error(e); }
            });
        });

        // 翻訳: 言語コードを聞いてサーバーで翻訳し、行下に表示
        tbody.querySelectorAll('button[data-translate]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const enc = btn.getAttribute('data-translate');
                const name = decodeURIComponent(enc);
                const to = window.prompt('翻訳先の言語コードを入力してください (例: en, ja, zh-Hans)');
                if (!to) return;

                // 既存の詳細行を閉じる
                const tbodyEl = btn.closest('tbody');
                tbodyEl && tbodyEl.querySelectorAll('tr.detail-row').forEach(r => r.remove());

                // 詳細行テンプレートを挿入
                const tr = btn.closest('tr');
                const detail = document.createElement('tr');
                detail.className = 'detail-row';
                detail.innerHTML = `
                    <td colspan="5">
                        <div class="card" style="margin-top:8px;">
                            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;">
                                <h3 style="margin:0;font-size:16px;">翻訳プレビュー (${to})</h3>
                                <div style="display:flex;gap:8px;">
                                  <button class="btn-small" data-save>保存</button>
                                  <button class="btn-small" data-close>閉じる</button>
                                </div>
                            </div>
                            <pre class="minutes-inline" style="white-space:pre-wrap;word-break:break-word;min-height:60px;opacity:.8;">翻訳中...</pre>
                        </div>
                    </td>`;
                tr.after(detail);
                const closeBtn = detail.querySelector('button[data-close]');
                if (closeBtn) closeBtn.addEventListener('click', () => detail.remove());

                const pre = detail.querySelector('pre.minutes-inline');
                try {
                    const r = await fetch('/api/translate-minutes', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name, to })
                    });
                    if (!r.ok) {
                        const t = await r.text();
                        alert(`翻訳に失敗しました: ${r.status} ${t}`);
                        detail.remove();
                        return;
                    }
                    const j = await r.json();
                    pre.textContent = j.translated || '';
                    pre.style.opacity = '1';
                    // 保存ボタン
                    const saveBtn = detail.querySelector('button[data-save]');
                    if (saveBtn) {
                        saveBtn.addEventListener('click', async () => {
                            try {
                                const r2 = await fetch('/api/translate-minutes', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ name, to, save: true, translated: pre.textContent })
                                });
                                if (!r2.ok) {
                                    const t2 = await r2.text();
                                    alert(`保存に失敗しました: ${r2.status} ${t2}`);
                                    return;
                                }
                                const j2 = await r2.json();
                                alert('翻訳を新しい議事録として保存しました');
                                // 一覧を更新
                                await loadMinutesList();
                            } catch (e) { console.error(e); alert('保存中にエラーが発生しました'); }
                        });
                    }
                } catch (e) {
                    console.error(e);
                    alert('翻訳中にエラーが発生しました');
                    detail.remove();
                }
            });
        });
    } catch (e) { console.error(e); }
}

// --- ffmpeg.wasm による動画→音声抽出 ---
let ffmpegInstance = null;
async function ensureFfmpeg() {
    if (window.FFmpeg && window.FFmpeg.createFFmpeg) {
        // ok
    } else {
        await loadScript('https://unpkg.com/@ffmpeg/ffmpeg@0.12.6/dist/ffmpeg.min.js');
    }
    if (!ffmpegInstance) {
        ffmpegInstance = window.FFmpeg.createFFmpeg({
            log: false,
            corePath: 'https://unpkg.com/@ffmpeg/core@0.12.6/dist/ffmpeg-core.js'
        });
        await ffmpegInstance.load();
    }
}

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = src;
        s.onload = () => resolve();
        s.onerror = reject;
        document.head.appendChild(s);
    });
}

async function extractAudioOnClient(file) {
    await ensureFfmpeg();
    const { fetchFile } = window.FFmpeg;
    const data = await fetchFile(file);
    const inputName = 'input' + (file.name && file.name.includes('.') ? file.name.slice(file.name.lastIndexOf('.')) : '.mp4');
    const outputName = 'output.wav';
    ffmpegInstance.FS('writeFile', inputName, data);
    // 16kHz mono WAV に変換
    await ffmpegInstance.run('-i', inputName, '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', outputName);
    const out = ffmpegInstance.FS('readFile', outputName);
    const base = (file.name || 'audio').replace(/\.[^.]+$/, '');
    const wavFile = new File([out.buffer], `${base}_audio.wav`, { type: 'audio/wav' });
    // クリーンアップ（失敗しても致命的ではない）
    try { ffmpegInstance.FS('unlink', inputName); } catch {}
    try { ffmpegInstance.FS('unlink', outputName); } catch {}
    return wavFile;
}

async function fetchAndShowByName(name) {
    try {
        const r = await fetch(`/api/status?name=${encodeURIComponent(name)}`);
        if (!r.ok) {
            const t = await r.text();
            alert(`本文取得に失敗しました: ${r.status} ${t}`);
            return;
        }
        const j = await r.json();
        if (j.status === 'completed' && minutesBody && minutesCard) {
            minutesBody.textContent = j.minutes || '';
            minutesCard.style.display = 'block';
        } else if (j.status === 'forbidden') {
            alert('この議事録へのアクセス権がありません');
        }
    } catch (e) { console.error(e); }
}
