/**
 * Claude Web Gateway — 클라이언트 앱 v3 (서버 SQLite 저장소)
 */
(() => {
  const $ = id => document.getElementById(id);
  const loginScreen = $('login-screen');
  const app = $('app');
  const messages = $('messages');
  const messagesWrap = $('messages-wrap');
  const input = $('input');
  const sendBtn = $('send-btn');
  const connIndicator = $('conn-indicator');
  const connText = $('conn-text');
  const statusText = $('status-text');
  const charCount = $('char-count');
  const userAvatar = $('user-avatar');
  const userName = $('user-name');
  const convList = $('conv-list');
  const modelSelect = $('model-select');
  const settingsModal = $('settings-modal');
  const sidebar = $('sidebar');

  const attachBtn = $('attach-btn');
  const fileInput = $('file-input');
  const filePreviewArea = $('file-preview-area');

  let ws = null;
  let isStreaming = false;
  let currentMsgEl = null;
  let streamBuffer = '';
  let conversations = []; // [{id, title, created_at, updated_at}]
  let activeConvId = null;
  let currentModel = 'opus';
  let pendingFiles = [];
  let webSearchEnabled = true;   // 기본 ON
  let deepResearchEnabled = false; // 기본 OFF

  marked.setOptions({
    highlight: (code, lang) => {
      if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
      return hljs.highlightAuto(code).value;
    },
    breaks: true, gfm: true
  });

  // ── AUTH ─────────────────────────────────────
  async function checkAuth() {
    try {
      const r = await fetch('/api/me');
      const d = await r.json();
      if (d.authenticated) showApp(d.username, d.dev_mode);
      else showLogin();
    } catch { showLogin(); }
  }

  function showLogin() { loginScreen.classList.remove('hidden'); app.classList.add('hidden'); }

  function showApp(username, devMode) {
    loginScreen.classList.add('hidden');
    app.classList.remove('hidden');
    userAvatar.textContent = username[0].toUpperCase();
    userName.textContent = devMode ? '🔧 DEV' : `@${username}`;
    loadConversations();
    connectWS();
  }

  // ── CONVERSATIONS (서버 API) ─────────────────
  function newConvId() { return 'c_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6); }

  async function loadConversations() {
    try {
      const r = await fetch('/api/conversations');
      const d = await r.json();
      conversations = d.conversations || [];
    } catch { conversations = []; }

    if (!activeConvId && conversations.length) {
      activeConvId = conversations[0].id;
    }
    renderConvList();
    if (activeConvId) await loadMessages(activeConvId);
    else renderMessages([]);
  }

  async function loadMessages(convId) {
    try {
      const r = await fetch(`/api/conversations/${convId}/messages`);
      const d = await r.json();
      renderMessages(d.messages || []);
    } catch {
      renderMessages([]);
    }
  }

  function createConversation() {
    const id = newConvId();
    activeConvId = id;
    // Will be created on server when first message is sent
    renderConvList();
    renderMessages([]);
    return id;
  }

  function renderConvList() {
    const sorted = [...conversations].sort((a, b) => b.updated_at - a.updated_at);
    const today = new Date().toDateString();
    const yesterday = new Date(Date.now() - 86400000).toDateString();

    let html = '';
    let lastSection = '';
    sorted.forEach(c => {
      const d = new Date(c.created_at * 1000);
      const section = d.toDateString() === today ? '오늘' : d.toDateString() === yesterday ? '어제' : d.toLocaleDateString('ko-KR');
      if (section !== lastSection) { html += `<div class="conv-section">${section}</div>`; lastSection = section; }
      const time = d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
      const active = c.id === activeConvId ? 'active' : '';
      html += `<div class="conv-item ${active}" data-id="${c.id}">
        <span class="conv-icon">💬</span>
        <span class="conv-title">${escapeHtml(c.title)}</span>
        <span class="conv-time">${time}</span>
        <button class="conv-delete" data-del="${c.id}" title="삭제">✕</button>
      </div>`;
    });
    if (!sorted.length) html = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px">대화가 없습니다</div>';
    convList.innerHTML = html;
  }

  async function switchConversation(id) {
    activeConvId = id;
    renderConvList();
    await loadMessages(id);
  }

  async function deleteConversationById(id) {
    try { await fetch(`/api/conversations/${id}`, { method: 'DELETE' }); } catch {}
    conversations = conversations.filter(c => c.id !== id);
    if (activeConvId === id) {
      activeConvId = conversations.length ? conversations[0].id : null;
    }
    renderConvList();
    if (activeConvId) await loadMessages(activeConvId);
    else renderMessages([]);
  }

  function renderMessages(msgs) {
    messages.innerHTML = '';
    if (!msgs || !msgs.length) {
      messages.innerHTML = getWelcomeHTML();
      return;
    }
    msgs.forEach(m => {
      const files = (m.files || []).map(f => ({
        file_id: f.id || f.filename,
        filename: f.original_name || f.filename,
        is_image: isImageFile(f.filename || f.original_name || ''),
      }));
      addMessageEl(m.role, m.content, m.created_at ? m.created_at * 1000 : Date.now(), m.elapsed, files.length ? files : null);
    });
    scrollToBottom();
  }

  function isImageFile(name) {
    return /\.(png|jpg|jpeg|gif|webp)$/i.test(name);
  }

  function getWelcomeHTML() {
    return `<div class="welcome">
      <div class="welcome-icon"><div class="logo-mark-lg">C</div></div>
      <h2>무엇을 도와드릴까요?</h2>
      <p class="welcome-sub">Claude Opus가 Mac mini에서 실행 중입니다</p>
      <div class="quick-grid">
        <button class="quick-card" data-prompt="Python으로 FastAPI 웹 서버를 만들어줘"><span class="quick-icon">🐍</span><span class="quick-label">Python 웹 서버</span><span class="quick-desc">FastAPI로 REST API 만들기</span></button>
        <button class="quick-card" data-prompt="이 코드를 리뷰하고 개선점을 알려줘"><span class="quick-icon">🔍</span><span class="quick-label">코드 리뷰</span><span class="quick-desc">품질 개선 제안 받기</span></button>
        <button class="quick-card" data-prompt="Docker와 Kubernetes의 차이를 설명해줘"><span class="quick-icon">🐳</span><span class="quick-label">기술 설명</span><span class="quick-desc">복잡한 개념 쉽게 이해</span></button>
        <button class="quick-card" data-prompt="효율적인 알고리즘을 설계해줘"><span class="quick-icon">⚡</span><span class="quick-label">알고리즘</span><span class="quick-desc">최적화된 솔루션 설계</span></button>
      </div>
    </div>`;
  }

  // ── MESSAGE UI ──────────────────────────────
  function addMessageEl(role, content, time, elapsed, files) {
    const w = messages.querySelector('.welcome');
    if (w) w.remove();

    const el = document.createElement('div');
    el.className = `message ${role}`;
    const now = time ? new Date(time) : new Date();
    const timeStr = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
    const isUser = role === 'user';

    let filesHtml = '';
    if (files && files.length) {
      const badges = files.map(f => {
        if (f.is_image) return `<img class="msg-file-img" src="/api/uploads/${f.file_id}" alt="${escapeHtml(f.filename)}" title="${escapeHtml(f.filename)}">`;
        return `<span class="msg-file-badge">📄 ${escapeHtml(f.filename)}</span>`;
      }).join('');
      filesHtml = `<div class="msg-files">${badges}</div>`;
    }

    el.innerHTML = `
      <div class="msg-header">
        <div class="msg-avatar ${isUser ? 'user-a' : 'bot-a'}">${isUser ? '👤' : 'C'}</div>
        <span class="msg-name">${isUser ? 'You' : 'Claude'}</span>
        <span class="msg-time">${timeStr}</span>
      </div>
      ${filesHtml}
      <div class="msg-content">${isUser ? escapeHtml(content) : renderMarkdown(content)}</div>
      ${elapsed ? `<div class="msg-footer">⏱ ${elapsed}초</div>` : ''}
    `;
    messages.appendChild(el);
    if (!isUser) addCopyButtons(el);
    scrollToBottom();
    return el;
  }

  function updateStreamContent(el, content) {
    if (!el) return;
    const body = el.querySelector('.msg-content');
    body.innerHTML = renderMarkdown(content);
    body.querySelectorAll('pre code').forEach(b => hljs.highlightElement(b));
    addCopyButtons(el);
  }

  function addCopyButtons(el) {
    el.querySelectorAll('pre').forEach(pre => {
      if (pre.querySelector('.copy-btn')) return;
      const btn = document.createElement('button');
      btn.className = 'copy-btn';
      btn.textContent = '복사';
      btn.onclick = () => {
        const code = pre.querySelector('code')?.textContent || pre.textContent;
        navigator.clipboard.writeText(code).then(() => {
          btn.textContent = '✓ 복사됨';
          setTimeout(() => btn.textContent = '복사', 2000);
        });
      };
      pre.style.position = 'relative';
      pre.appendChild(btn);
    });
  }

  function renderMarkdown(text) { try { return marked.parse(text); } catch { return escapeHtml(text); } }
  function escapeHtml(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
  function scrollToBottom() { requestAnimationFrame(() => { messagesWrap.scrollTop = messagesWrap.scrollHeight; }); }
  function showToast(msg) {
    const t = document.createElement('div'); t.className = 'error-toast'; t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 3500);
  }

  // ── WEBSOCKET ───────────────────────────────
  let wsRetryDelay = 1000;
  let wsPingInterval = null;
  let wsReconnectTimer = null;

  function connectWS() {
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    try { ws = new WebSocket(`${proto}//${location.host}/ws`); } catch(e) { scheduleReconnect(); return; }

    ws.onopen = () => {
      connIndicator.classList.add('connected'); connText.textContent = '연결됨';
      wsRetryDelay = 1000;
      if (wsPingInterval) clearInterval(wsPingInterval);
      wsPingInterval = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          try { ws.send(JSON.stringify({type: 'ping'})); } catch(e) {}
        }
      }, 30000);
    };
    ws.onmessage = e => { try { handleWS(JSON.parse(e.data)); } catch(ex) {} };
    ws.onclose = () => {
      connIndicator.classList.remove('connected'); connText.textContent = '재연결 중...';
      if (wsPingInterval) { clearInterval(wsPingInterval); wsPingInterval = null; }
      scheduleReconnect();
    };
    ws.onerror = () => {};
  }

  function scheduleReconnect() {
    if (wsReconnectTimer) return;
    wsReconnectTimer = setTimeout(() => { wsReconnectTimer = null; connectWS(); }, wsRetryDelay);
    wsRetryDelay = Math.min(wsRetryDelay * 1.5, 30000);
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && (!ws || ws.readyState !== WebSocket.OPEN)) {
      wsRetryDelay = 1000; connectWS();
    }
  });

  function handleWS(data) {
    switch (data.type) {
      case 'connected': break;
      case 'start':
        isStreaming = true; streamBuffer = '';
        // Update activeConvId from server
        if (data.conversation_id) {
          activeConvId = data.conversation_id;
          // Add to local list if not present
          if (!conversations.find(c => c.id === activeConvId)) {
            conversations.unshift({ id: activeConvId, title: '새 대화', created_at: Date.now()/1000, updated_at: Date.now()/1000 });
            renderConvList();
          }
        }
        currentMsgEl = addMessageEl('assistant', '', Date.now());
        const ti = document.createElement('div');
        ti.className = 'typing-indicator';
        ti.innerHTML = '<span></span><span></span><span></span>';
        currentMsgEl.appendChild(ti);
        statusText.textContent = '⏳ Claude 응답 중...';
        sendBtn.disabled = true;
        break;
      case 'chunk':
        streamBuffer += data.content;
        const ind = currentMsgEl?.querySelector('.typing-indicator');
        if (ind) ind.remove();
        updateStreamContent(currentMsgEl, streamBuffer);
        scrollToBottom();
        break;
      case 'done':
        isStreaming = false;
        const ti2 = currentMsgEl?.querySelector('.typing-indicator');
        if (ti2) ti2.remove();
        updateStreamContent(currentMsgEl, streamBuffer);
        const footer = document.createElement('div');
        footer.className = 'msg-footer';
        footer.textContent = `⏱ ${data.elapsed}초`;
        currentMsgEl?.appendChild(footer);
        // Refresh conversation list from server
        loadConversationList();
        currentMsgEl = null; streamBuffer = '';
        statusText.textContent = `✅ 완료 (${data.elapsed}초)`;
        updateSendState();
        scrollToBottom();
        break;
      case 'status':
        statusText.textContent = data.content;
        break;
      case 'error':
        isStreaming = false;
        showToast(data.content);
        statusText.textContent = '❌ 오류';
        updateSendState();
        break;
    }
  }

  async function loadConversationList() {
    try {
      const r = await fetch('/api/conversations');
      const d = await r.json();
      conversations = d.conversations || [];
      renderConvList();
    } catch {}
  }

  // ── FILE UPLOAD ──────────────────────────────
  async function uploadFile(file) {
    const maxSize = 10 * 1024 * 1024;
    if (file.size > maxSize) { showToast(`파일 크기 초과: ${file.name} (최대 10MB)`); return null; }
    const formData = new FormData();
    formData.append('file', file);
    try {
      const r = await fetch('/api/upload', { method: 'POST', body: formData });
      if (!r.ok) { const d = await r.json(); showToast(d.detail || '업로드 실패'); return null; }
      return await r.json();
    } catch (e) { showToast('업로드 오류: ' + e.message); return null; }
  }

  async function handleFiles(files) {
    for (const file of files) {
      const result = await uploadFile(file);
      if (result) { pendingFiles.push(result); renderFilePreview(); }
    }
    updateSendState();
  }

  function renderFilePreview() {
    filePreviewArea.innerHTML = pendingFiles.map((f, i) => {
      const sizeStr = f.size < 1024 ? f.size + 'B' : f.size < 1048576 ? (f.size/1024).toFixed(1) + 'KB' : (f.size/1048576).toFixed(1) + 'MB';
      const thumb = f.is_image ? `<img class="file-thumb" src="/api/uploads/${f.file_id}" alt="">` : `<span class="file-icon">📄</span>`;
      return `<div class="file-preview">
        ${thumb}
        <div class="file-info"><span class="file-name">${escapeHtml(f.filename)}</span><span class="file-size">${sizeStr}</span></div>
        <button class="file-remove" data-idx="${i}">✕</button>
      </div>`;
    }).join('');
  }

  function updateSendState() {
    sendBtn.disabled = (!input.value.trim() && !pendingFiles.length) || isStreaming;
  }

  // ── SEND ────────────────────────────────────
  function sendMessage(text) {
    text = text || input.value.trim();
    const fileIds = pendingFiles.map(f => f.file_id);
    const fileInfos = [...pendingFiles];

    if ((!text && !fileIds.length) || isStreaming || !ws || ws.readyState !== WebSocket.OPEN) return;

    if (!activeConvId) activeConvId = newConvId();

    addMessageEl('user', text, Date.now(), null, fileInfos);
    ws.send(JSON.stringify({
      message: text, model: currentModel,
      file_ids: fileIds.length ? fileIds : undefined,
      conversation_id: activeConvId,
      web_search: webSearchEnabled,
      deep_research: deepResearchEnabled,
    }));
    input.value = ''; input.style.height = 'auto';
    charCount.textContent = '0';
    pendingFiles = [];
    filePreviewArea.innerHTML = '';
    sendBtn.disabled = true;
  }

  // ── SEARCH ──────────────────────────────────
  let searchTimeout = null;
  async function handleSearch(q) {
    if (!q.trim()) {
      await loadConversationList();
      return;
    }
    try {
      const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      const d = await r.json();
      const results = d.results || [];
      // Show search results in conv list
      let html = results.map(r => {
        const active = r.id === activeConvId ? 'active' : '';
        return `<div class="conv-item ${active}" data-id="${r.id}">
          <span class="conv-icon">🔍</span>
          <span class="conv-title">${escapeHtml(r.title)}</span>
          <div style="font-size:11px;color:var(--text-muted);padding:2px 0 0 28px">${r.snippet || ''}</div>
        </div>`;
      }).join('');
      if (!results.length) html = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px">검색 결과 없음</div>';
      convList.innerHTML = html;
    } catch {}
  }

  // ── EVENT HANDLERS ──────────────────────────
  attachBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => { if (fileInput.files.length) { handleFiles(fileInput.files); fileInput.value = ''; } });
  filePreviewArea.addEventListener('click', e => {
    const btn = e.target.closest('.file-remove');
    if (btn) { pendingFiles.splice(parseInt(btn.dataset.idx), 1); renderFilePreview(); updateSendState(); }
  });

  let dragCounter = 0;
  document.addEventListener('dragenter', e => { e.preventDefault(); dragCounter++; if (dragCounter === 1) showDragOverlay(); });
  document.addEventListener('dragleave', e => { e.preventDefault(); dragCounter--; if (dragCounter <= 0) { dragCounter = 0; hideDragOverlay(); } });
  document.addEventListener('dragover', e => e.preventDefault());
  document.addEventListener('drop', e => { e.preventDefault(); dragCounter = 0; hideDragOverlay(); if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files); });

  function showDragOverlay() {
    if (document.getElementById('drag-overlay')) return;
    const o = document.createElement('div'); o.id = 'drag-overlay'; o.className = 'drag-overlay';
    o.innerHTML = '<span>📎 파일을 드롭하여 첨부</span>';
    document.body.appendChild(o);
  }
  function hideDragOverlay() { document.getElementById('drag-overlay')?.remove(); }

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 150) + 'px';
    updateSendState();
    charCount.textContent = input.value.length.toLocaleString();
  });
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
  sendBtn.addEventListener('click', () => sendMessage());

  $('new-chat-btn').addEventListener('click', () => { createConversation(); });

  convList.addEventListener('click', e => {
    const del = e.target.closest('[data-del]');
    if (del) { e.stopPropagation(); deleteConversationById(del.dataset.del); return; }
    const item = e.target.closest('.conv-item');
    if (item) switchConversation(item.dataset.id);
  });

  document.addEventListener('click', e => {
    const card = e.target.closest('.quick-card');
    if (card) sendMessage(card.dataset.prompt);
  });

  modelSelect.addEventListener('change', () => { currentModel = modelSelect.value; });

  $('settings-btn').addEventListener('click', () => { settingsModal.classList.remove('hidden'); });
  $('settings-close').addEventListener('click', () => { settingsModal.classList.add('hidden'); });
  settingsModal.addEventListener('click', e => { if (e.target === settingsModal) settingsModal.classList.add('hidden'); });

  $('clear-history-btn')?.addEventListener('click', async () => {
    if (!confirm('모든 대화 기록을 삭제할까요?')) return;
    try { await fetch('/api/history', { method: 'DELETE' }); } catch {}
    conversations = []; activeConvId = null;
    renderConvList(); renderMessages([]);
    settingsModal.classList.add('hidden');
  });

  $('search-input').addEventListener('input', e => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => handleSearch(e.target.value), 300);
  });

  // ── TOGGLE HANDLERS ──────────────────────
  function handleToggle(id) {
    if (id === 'toggle-web-search') {
      webSearchEnabled = !webSearchEnabled;
      document.getElementById(id).classList.toggle('active', webSearchEnabled);
    } else if (id === 'toggle-deep-research') {
      deepResearchEnabled = !deepResearchEnabled;
      document.getElementById(id).classList.toggle('active', deepResearchEnabled);
      if (deepResearchEnabled && !webSearchEnabled) {
        webSearchEnabled = true;
        document.getElementById('toggle-web-search').classList.add('active');
      }
    }
  }
  ['toggle-web-search', 'toggle-deep-research'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    // click for desktop
    el.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); handleToggle(id); });
    // touchend for mobile Safari
    el.addEventListener('touchend', e => { e.preventDefault(); e.stopPropagation(); handleToggle(id); }, {passive: false});
  });

  $('sidebar-toggle').addEventListener('click', () => sidebar.classList.toggle('open'));
  document.addEventListener('click', e => {
    if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && e.target !== $('sidebar-toggle')) {
      sidebar.classList.remove('open');
    }
  });

  $('setting-model')?.addEventListener('change', e => {
    currentModel = e.target.value;
    modelSelect.value = currentModel;
  });

  checkAuth();
})();
