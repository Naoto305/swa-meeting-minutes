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
let progressTimer = null;
let currentMinutesTotal = 0;
const toastContainer = document.getElementById('toast-container');
// 一覧の状態
let minutesListCache = [];
const listState = { sort: 'last_modified', order: 'desc', page: 1, size: 20, q: '', from: '', to: '' };

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

function showToast(message, type = 'info', timeoutMs = 4000) {
    if (!toastContainer) return;
    const div = document.createElement('div');
    div.className = `toast ${type}`;
    div.textContent = message;
    toastContainer.appendChild(div);
    setTimeout(() => { div.remove(); }, timeoutMs);
}

function startProgress(titleText = '処理中...', initial = 10) {
    if (!progressCard) return;
    const title = progressCard.querySelector('.progress-title');
    const fill = progressCard.querySelector('.progress-fill');
    if (title) title.textContent = titleText;
    if (fill) fill.style.width = `${initial}%`;
    progressCard.style.display = 'block';
}

function setProgress(percent, titleText) {
    if (!progressCard) return;
    const title = progressCard.querySelector('.progress-title');
    const fill = progressCard.querySelector('.progress-fill');
    if (title && titleText) title.textContent = titleText;
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function finishProgress(success = true, hideDelay = 1200) {
    if (!progressCard) return;
    setProgress(100, success ? '完了しました' : '処理を終了しました');
    if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
    setTimeout(() => { progressCard.style.display = 'none'; }, hideDelay);
}

async function handleFileUpload(file) {
    console.log('Uploaded file:', file);
    progressCard.style.display = 'block';

    const formData = new FormData();
    formData.append('file', file);

    try {
        // Azure Functionsのエンドポイント '/api/upload' にファイルをPOST
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData,
            // Easy Auth使用時は認証情報が自動で付与されるため、手動でのヘッダー設定は不要
        });

        if (response.ok) {
            const result = await response.json();
            console.log('Upload successful:', result);
            // 現在の件数を記録
            try {
                const r0 = await fetch('/api/list-minutes');
                const j0 = await r0.json();
                currentMinutesTotal = (j0 && typeof j0.total === 'number') ? j0.total : 0;
            } catch (e) { currentMinutesTotal = 0; }

            if (result.container === 'video') {
                showToast('動画を受け付けました。音声抽出→文字起こしを実行します。', 'info', 6000);
                startProgress('音声抽出中...');
                let pct = 10;
                progressTimer = setInterval(() => {
                    pct = Math.min(95, pct + 5);
                    setProgress(pct);
                }, 5000);
                // しばらくの間、一覧を自動更新し、完了検知で進捗終了
                let left = 12; // 約6分
                const watcher = setInterval(async () => {
                    try {
                        const r = await fetch('/api/list-minutes');
                        const j = await r.json();
                        const total = (j && typeof j.total === 'number') ? j.total : (Array.isArray(j.minutes)? j.minutes.length : 0);
                        if (total > currentMinutesTotal) {
                            await loadMinutesList();
                            finishProgress(true);
                            clearInterval(watcher);
                            showToast('議事録が生成されました', 'success');
                            return;
                        }
                    } catch (e) { /* noop */ }
                    if (--left <= 0) { clearInterval(watcher); finishProgress(false); }
                }, 30000);
            } else {
                showToast('アップロード完了。文字起こしを開始します。', 'success');
                startProgress('文字起こし中...', 30);
                let pct = 30;
                progressTimer = setInterval(() => {
                    pct = Math.min(95, pct + 5);
                    setProgress(pct);
                }, 5000);
                // 軽めにポーリング
                setTimeout(async () => {
                    finishProgress();
                    try { await loadMinutesList(); } catch (e) {}
                }, 120000);
            }
            await loadMinutesList();
        } else {
            const errorText = await response.text();
            showToast(`エラーが発生しました: ${errorText}`, 'error', 6000);
            console.error('Upload failed:', errorText);
        }
    } catch (error) {
        showToast(`エラーが発生しました: ${error.message}`, 'error', 6000);
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
        const params = new URLSearchParams({
            sort: listState.sort,
            order: listState.order,
            page: String(listState.page),
            size: String(listState.size),
        });
        if (listState.q) params.set('q', listState.q);
        if (listState.from) params.set('from', listState.from);
        if (listState.to) params.set('to', listState.to);
        const r = await fetch(`/api/list-minutes?${params.toString()}`);
        const j = await r.json();
        minutesListCache = Array.isArray(j.minutes) ? j.minutes : [];
        renderMinutesTable(minutesListCache);
        bindSearchIfNeeded();
        bindListControlsIfNeeded(j.total || 0);
    } catch (e) { console.error(e); }
}

// 本文コピー
document.addEventListener('click', (e) => {
    const btn = e.target && e.target.id === 'copy-minutes-btn' ? e.target : null;
    if (!btn) return;
    const pre = document.getElementById('minutes-body');
    if (!pre) return;
    try {
        const text = pre.textContent || '';
        navigator.clipboard.writeText(text).then(() => {
            showToast('本文をコピーしました', 'success');
        }).catch(() => {
            showToast('コピーに失敗しました', 'error');
        });
    } catch (err) { showToast('コピーに失敗しました', 'error'); }
});

function bindSearchIfNeeded() {
    const input = document.querySelector('#list-section .search-input');
    if (!input || input.dataset.bound === '1') return;
    input.dataset.bound = '1';
    input.addEventListener('input', () => {
        listState.q = (input.value || '').trim();
        listState.page = 1;
        loadMinutesList();
    });
}

function bindListControlsIfNeeded(total) {
    const sortSel = document.getElementById('sort-select');
    const sizeSel = document.getElementById('size-select');
    const fromInput = document.getElementById('from-date');
    const toInput = document.getElementById('to-date');
    const prev = document.getElementById('page-prev');
    const next = document.getElementById('page-next');
    const info = document.getElementById('page-info');
    if (sortSel && sortSel.dataset.bound !== '1') {
        sortSel.dataset.bound = '1';
        sortSel.addEventListener('change', () => {
            const val = sortSel.value || 'last_modified:desc';
            const [s, o] = val.split(':');
            listState.sort = s; listState.order = o || 'desc';
            listState.page = 1;
            loadMinutesList();
        });
    }
    if (sizeSel && sizeSel.dataset.bound !== '1') {
        sizeSel.dataset.bound = '1';
        sizeSel.addEventListener('change', () => {
            listState.size = parseInt(sizeSel.value || '20', 10) || 20;
            listState.page = 1;
            loadMinutesList();
        });
    }
    if (fromInput && fromInput.dataset.bound !== '1') {
        fromInput.dataset.bound = '1';
        fromInput.addEventListener('change', () => {
            listState.from = fromInput.value || '';
            listState.page = 1;
            loadMinutesList();
        });
    }
    if (toInput && toInput.dataset.bound !== '1') {
        toInput.dataset.bound = '1';
        toInput.addEventListener('change', () => {
            listState.to = toInput.value || '';
            listState.page = 1;
            loadMinutesList();
        });
    }
    if (prev && prev.dataset.bound !== '1') {
        prev.dataset.bound = '1';
        prev.addEventListener('click', () => {
            if (listState.page > 1) { listState.page -= 1; loadMinutesList(); }
        });
    }
    if (next && next.dataset.bound !== '1') {
        next.dataset.bound = '1';
        next.addEventListener('click', () => {
            const totalPages = Math.max(1, Math.ceil(total / listState.size));
            if (listState.page < totalPages) { listState.page += 1; loadMinutesList(); }
        });
    }
    if (info) {
        const totalPages = Math.max(1, Math.ceil(total / listState.size));
        info.textContent = `${listState.page} / ${totalPages}`;
    }
}

function renderMinutesTable(items) {
    const tbody = document.querySelector('.data-table tbody');
    if (!tbody) return;
    if (!items || items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="padding:24px;color:var(--text-tertiary);text-align:center;">該当する議事録がありません</td></tr>`;
        return;
    }
    tbody.innerHTML = (items || []).map(m => {
            const dt = m.last_modified ? new Date(m.last_modified).toLocaleString() : '';
            return `<tr>
                <td>
                    <div class="title-cell">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="stroke: var(--text-tertiary);">
                            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        <span class="title-text" data-open="${encodeURIComponent(m.name)}" title="クリックして本文プレビュー">${m.title || m.name}</span>
                    </div>
                </td>
                <td>${dt}</td>
                <td><span class="status-badge completed"><span class="status-dot"></span>完了</span></td>
                <td>
                  <div class="table-actions">
                    <button class="btn-small" data-name="${encodeURIComponent(m.name)}">表示</button>
                    <button class="btn-small" data-download="${encodeURIComponent(m.name)}">ダウンロード</button>
                    <button class="btn-small" data-translate="${encodeURIComponent(m.name)}">翻訳</button>
                    <button class="btn-small" data-regenerate="${encodeURIComponent(m.name)}">再生成</button>
                    <button class="btn-small" data-edit-title="${encodeURIComponent(m.name)}">タイトル編集</button>
                    <button class="btn-small" data-delete="${encodeURIComponent(m.name)}">削除</button>
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
                    <td colspan="4">
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
        // タイトルクリックでも開く
        tbody.querySelectorAll('[data-open]').forEach(el => {
            el.addEventListener('click', async (e) => {
                e.preventDefault();
                const enc = el.getAttribute('data-open');
                const name = decodeURIComponent(enc);
                // すでに開いている詳細行を閉じる
                tbody.querySelectorAll('tr.detail-row').forEach(r => r.remove());
                // 本ボタンの行の次にプレースホルダーを挿入
                const tr = el.closest('tr');
                const detail = document.createElement('tr');
                detail.className = 'detail-row';
                detail.innerHTML = `
                    <td colspan="4">
                        <div class="card" style="margin-top:8px;">
                            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;">
                                <h3 style="margin:0;font-size:16px;">本文プレビュー</h3>
                                <div style="display:flex;gap:8px;">
                                  <button class="btn-small" data-copy>コピー</button>
                                  <button class="btn-small" data-close>閉じる</button>
                                </div>
                            </div>
                            <pre class="minutes-inline" style="white-space:pre-wrap;word-break:break-word;min-height:60px;opacity:.8;">読み込み中...</pre>
                        </div>
                    </td>`;
                tr.after(detail);

                const closeBtn = detail.querySelector('button[data-close]');
                if (closeBtn) closeBtn.addEventListener('click', () => detail.remove());
                const copyBtn = detail.querySelector('button[data-copy]');
                if (copyBtn) copyBtn.addEventListener('click', () => {
                    const pre = detail.querySelector('pre.minutes-inline');
                    const txt = (pre && pre.textContent) ? pre.textContent : '';
                    navigator.clipboard.writeText(txt).then(() => showToast('本文をコピーしました','success')).catch(()=>showToast('コピーに失敗しました','error'));
                });

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
                    <td colspan="4">
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

        // タイトル編集
        tbody.querySelectorAll('button[data-edit-title]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = decodeURIComponent(btn.getAttribute('data-edit-title'));
                const current = btn.closest('tr')?.querySelector('td:first-child')?.innerText.trim();
                const title = window.prompt('新しいタイトルを入力してください', current || '');
                if (title == null) return;
                try {
                    const r = await fetch('/api/update-minutes', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name, title })
                    });
                    if (!r.ok) { alert(await r.text()); return; }
                    await loadMinutesList();
                } catch (e) { console.error(e); alert('更新に失敗しました'); }
            });
        });

        // 削除
        tbody.querySelectorAll('button[data-delete]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const name = decodeURIComponent(btn.getAttribute('data-delete'));
                if (!confirm('この議事録を削除しますか？')) return;
                try {
                    const r = await fetch('/api/delete-minutes', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name })
                    });
                    if (!r.ok) { alert(await r.text()); return; }
                    // ページ末尾で0件になったら前のページに戻る
                    if (minutesListCache.length <= 1 && listState.page > 1) listState.page -= 1;
                    await loadMinutesList();
                } catch (e) { console.error(e); alert('削除に失敗しました'); }
            });
        });
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
