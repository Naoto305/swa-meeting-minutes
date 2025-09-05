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
                        ${m.name}
                    </div>
                </td>
                <td>${dt}</td>
                <td>-</td>
                <td><span class="status-badge completed"><span class="status-dot"></span>完了</span></td>
                <td><div class="table-actions"><button class="btn-small" data-open="${m.job_id}">表示</button></div></td>
            </tr>`;
        }).join('');
        tbody.querySelectorAll('button[data-open]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.getAttribute('data-open');
                if (minutesCard) minutesCard.style.display = 'none';
                if (minutesBody) minutesBody.textContent = '';
                await pollStatus(id);
            });
        });
    } catch (e) { console.error(e); }
}
