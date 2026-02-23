/**
 * Ask — 클라이언트 앱 v3 (서버 SQLite 저장소)
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
  let currentProvider = 'claude';
  let providers = [];
  let pendingFiles = [];
  let webSearchEnabled = true;   // 기본 ON
  let deepResearchEnabled = false; // 기본 OFF
  let streamStartTime = 0;
  let firstChunkReceived = false;
  let thinkingTimer = null;

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
    userAvatar.innerHTML = '<img src="/static/user-avatar.jpg" alt="User" style="width:100%;height:100%;object-fit:cover;border-radius:8px;">';
    userName.textContent = devMode ? '🔧 DEV' : `@${username}`;
    loadVersion();
    loadProviders();
    loadConversations();
    connectWS();
  }

  async function loadVersion() {
    try {
      const r = await fetch('/api/health');
      const d = await r.json();
      const vi = $('version-info');
      if (vi && d.version) vi.textContent = `v${d.version}`;
    } catch {}
  }

  async function loadProviders() {
    try {
      const r = await fetch('/api/providers');
      const d = await r.json();
      providers = d.providers || [];
      rebuildModelSelect();
    } catch { /* fallback: use hardcoded Claude options */ }
  }

  function rebuildModelSelect() {
    if (!providers.length) return;
    // Header model select
    modelSelect.innerHTML = '';
    // Settings model select
    const settingModel = $('setting-model');
    if (settingModel) settingModel.innerHTML = '';

    providers.forEach(p => {
      if (!p.enabled) return;
      const group = document.createElement('optgroup');
      group.label = p.name;
      const settingGroup = document.createElement('optgroup');
      settingGroup.label = p.name;

      p.models.forEach(m => {
        const val = `${p.id}/${m.id}`;
        const opt = document.createElement('option');
        opt.value = val;
        opt.textContent = m.name;
        group.appendChild(opt);

        if (settingModel) {
          const opt2 = document.createElement('option');
          opt2.value = val;
          opt2.textContent = `${m.name} — ${m.desc}`;
          settingGroup.appendChild(opt2);
        }
      });
      modelSelect.appendChild(group);
      if (settingModel) settingModel.appendChild(settingGroup);
    });

    // Set default
    const defaultProvider = providers.find(p => p.enabled) || providers[0];
    if (defaultProvider) {
      const defaultVal = `${defaultProvider.id}/${defaultProvider.default_model}`;
      modelSelect.value = defaultVal;
      if (settingModel) settingModel.value = defaultVal;
      applyModelSelection(defaultVal);
    }
  }

  function getProviderIcon() {
    const p = providers.find(p => p.id === currentProvider);
    return p ? p.icon : '/static/claude-icon.svg';
  }

  function getProviderName() {
    const p = providers.find(p => p.id === currentProvider);
    return p ? p.name : 'Claude';
  }

  function applyModelSelection(val) {
    const [prov, model] = val.split('/');
    currentProvider = prov;
    currentModel = model;
    // Update provider icon
    const icon = $('provider-icon');
    const provData = providers.find(p => p.id === prov);
    if (icon && provData) icon.src = provData.icon;
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
        <div class="msg-avatar ${isUser ? 'user-a' : 'bot-a'}">${isUser ? '<img src="/static/user-avatar.jpg" alt="User">' : `<img src="${getProviderIcon()}" alt="AI">`}</div>
        <span class="msg-name">${isUser ? 'You' : getProviderName()}</span>
        <span class="msg-time">${timeStr}</span>
      </div>
      ${filesHtml}
      <div class="msg-content">${isUser ? escapeHtml(content) : renderMarkdown(content)}</div>
      <div class="msg-actions">
        <button class="msg-action-btn msg-copy-btn" title="복사">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
        </button>
      </div>
      ${elapsed ? `<div class="msg-footer">⏱ ${elapsed}초</div>` : ''}
    `;

    // 메시지 전체 복사
    el.querySelector('.msg-copy-btn')?.addEventListener('click', () => {
      const raw = el.querySelector('.msg-content')?.innerText || content;
      navigator.clipboard.writeText(raw).then(() => {
        const btn = el.querySelector('.msg-copy-btn');
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>';
        setTimeout(() => {
          btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
        }, 2000);
      });
    });

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
    ws.onclose = (e) => {
      connIndicator.classList.remove('connected');
      if (wsPingInterval) { clearInterval(wsPingInterval); wsPingInterval = null; }
      if (e.code === 4001) {
        // 인증 실패 — 재연결하지 않고 로그인으로
        connText.textContent = '인증 만료';
        showLogin();
        return;
      }
      connText.textContent = '재연결 중...';
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
        isStreaming = true; streamBuffer = ''; streamStartTime = Date.now(); firstChunkReceived = false;
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
        // Hide msg-content until actual response arrives; show thinking at bottom
        const msgContent = currentMsgEl.querySelector('.msg-content');
        if (msgContent) msgContent.style.display = 'none';
        const ti = document.createElement('div');
        ti.className = 'thinking-status';
        ti.innerHTML = '<div class="thinking-animation"><div class="thinking-icon">🔆</div><span class="thinking-text">Thinking</span><span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span></div><div class="thinking-timer">0s</div>';
        currentMsgEl.appendChild(ti);
        // 경과 시간 타이머
        if (thinkingTimer) clearInterval(thinkingTimer);
        thinkingTimer = setInterval(() => {
          const el = currentMsgEl?.querySelector('.thinking-timer');
          if (el) {
            const sec = Math.floor((Date.now() - streamStartTime) / 1000);
            el.textContent = sec < 60 ? `${sec}s` : `${Math.floor(sec/60)}m ${sec%60}s`;
          }
        }, 1000);
        statusText.textContent = '🔆 Claude 생각 중...';
        sendBtn.disabled = true;
        break;
      
      case 'system_init':
        // Claude CLI 초기화 정보
        const sessionId = data.session_id;
        const model = data.model;
        const tools = data.tools || [];
        statusText.textContent = `🚀 Claude ${model} 시작 (세션: ${sessionId?.slice(0, 8)}...)`;
        break;
        
      case 'tool_use':
        // 도구 사용 요청
        if (!firstChunkReceived) {
          firstChunkReceived = true;
          if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
          const elapsed = ((Date.now() - streamStartTime) / 1000).toFixed(1);
          statusText.textContent = `🔧 도구 실행 중... (thinking ${elapsed}s)`;
        }
        const ind = currentMsgEl?.querySelector('.thinking-status');
        if (ind) ind.remove();
        
        addToolUseBlock(currentMsgEl, data);
        scrollToBottom();
        break;
        
      case 'tool_result':
        // 도구 실행 결과
        updateToolResult(data.tool_use_id, data.content, data.is_error);
        statusText.textContent = data.is_error ? '⚠️ 도구 실행 오류' : '✅ 도구 실행 완료';
        scrollToBottom();
        break;
        
      case 'permission_request':
        // 권한 요청
        showPermissionRequest(data.tool_use_id, data.content);
        statusText.textContent = '🔐 권한 요청 대기 중...';
        break;
        
      case 'chunk':
        streamBuffer += data.content;
        if (!firstChunkReceived) {
          firstChunkReceived = true;
          if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
          const elapsed = ((Date.now() - streamStartTime) / 1000).toFixed(1);
          statusText.textContent = `✍️ 응답 작성 중... (thinking ${elapsed}s)`;
          // Remove thinking, move msg-content to end, show it
          currentMsgEl?.querySelectorAll('.thinking-status').forEach(e => e.remove());
          const mc = currentMsgEl?.querySelector('.msg-content');
          if (mc) {
            currentMsgEl.appendChild(mc); // move to end (after tool blocks)
            mc.style.display = '';
          }
        }
        updateStreamContent(currentMsgEl, streamBuffer);
        scrollToBottom();
        break;
        
      case 'final_result':
        // 최종 결과 및 비용 정보
        if (data.content && data.content !== streamBuffer) {
          streamBuffer += data.content;
          updateStreamContent(currentMsgEl, streamBuffer);
        }
        if (data.total_cost) {
          const costInfo = document.createElement('div');
          costInfo.className = 'msg-cost';
          costInfo.textContent = `💰 $${data.total_cost.toFixed(4)}`;
          currentMsgEl?.appendChild(costInfo);
        }
        break;
        
      case 'done':
        isStreaming = false;
        if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
        // Ensure thinking removed and content at bottom, visible
        currentMsgEl?.querySelectorAll('.thinking-status').forEach(e => e.remove());
        const mcDone = currentMsgEl?.querySelector('.msg-content');
        if (mcDone) {
          currentMsgEl.appendChild(mcDone);
          mcDone.style.display = '';
        }
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
        // Update thinking box text if visible
        const thinkText = currentMsgEl?.querySelector('.thinking-text');
        if (thinkText) thinkText.textContent = data.content;
        break;
        
      case 'error':
        isStreaming = false;
        if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
        if (data.content === 'Not authenticated') return; // 4001 close에서 처리
        showToast(data.content);
        statusText.textContent = '❌ 오류';
        updateSendState();
        break;
    }
  }
  
  // ── 도구 사용 UI ──────────────────────────────────
  function addToolUseBlock(msgEl, toolData) {
    if (!msgEl) return;
    
    const toolBlock = document.createElement('div');
    toolBlock.className = 'tool-use-block';
    toolBlock.dataset.toolUseId = toolData.tool_use_id;
    
    const toolName = toolData.tool_name;
    const toolInput = toolData.tool_input || {};
    const description = toolData.description || '';
    
    let toolDetails = '';
    if (toolName === 'Bash') {
      toolDetails = `<div class="tool-command">$ ${escapeHtml(toolInput.command || '')}</div>`;
    } else if (toolName === 'Write' || toolName === 'Edit') {
      const filePath = toolInput.file_path || toolInput.path || '';
      toolDetails = `<div class="tool-file">📝 ${escapeHtml(filePath)}</div>`;
    } else if (toolName === 'Read') {
      const filePath = toolInput.file_path || toolInput.path || '';
      toolDetails = `<div class="tool-file">👁 ${escapeHtml(filePath)}</div>`;
    } else {
      toolDetails = `<div class="tool-generic">${escapeHtml(JSON.stringify(toolInput))}</div>`;
    }
    
    toolBlock.innerHTML = `
      <div class="tool-header">
        <span class="tool-icon">🔧</span>
        <span class="tool-name">${escapeHtml(toolName)}</span>
        <span class="tool-status">실행 중...</span>
      </div>
      ${toolDetails}
      ${description ? `<div class="tool-description">${escapeHtml(description)}</div>` : ''}
      <div class="tool-result-area"></div>
    `;
    
    msgEl.appendChild(toolBlock);
  }
  
  function updateToolResult(toolUseId, content, isError) {
    const toolBlock = document.querySelector(`[data-tool-use-id="${toolUseId}"]`);
    if (!toolBlock) return;
    
    const status = toolBlock.querySelector('.tool-status');
    const resultArea = toolBlock.querySelector('.tool-result-area');
    
    if (status) {
      status.textContent = isError ? '실행 실패' : '실행 완료';
      status.className = `tool-status ${isError ? 'error' : 'success'}`;
    }
    
    if (resultArea && content) {
      const resultDiv = document.createElement('div');
      resultDiv.className = `tool-result ${isError ? 'error' : 'success'}`;
      resultDiv.innerHTML = `<pre><code>${escapeHtml(content)}</code></pre>`;
      resultArea.appendChild(resultDiv);
    }
  }
  
  // ── 권한 요청 UI ──────────────────────────────────
  function showPermissionRequest(toolUseId, content) {
    // 기존 권한 요청 제거
    const existing = document.querySelector('.permission-request');
    if (existing) existing.remove();
    
    const permReq = document.createElement('div');
    permReq.className = 'permission-request';
    permReq.innerHTML = `
      <div class="perm-header">
        <span class="perm-icon">🔐</span>
        <span class="perm-title">권한 요청</span>
      </div>
      <div class="perm-content">${escapeHtml(content)}</div>
      <div class="perm-actions">
        <button class="perm-btn allow" data-tool-use-id="${toolUseId}">허용</button>
        <button class="perm-btn deny" data-tool-use-id="${toolUseId}">거부</button>
        <button class="perm-btn always" data-tool-use-id="${toolUseId}">이 세션에서 항상 허용</button>
      </div>
      <div class="perm-timer">30초 후 자동 거부</div>
    `;
    
    document.body.appendChild(permReq);
    
    // 30초 타이머
    let countdown = 30;
    const timer = permReq.querySelector('.perm-timer');
    const timerInterval = setInterval(() => {
      countdown--;
      if (countdown <= 0) {
        clearInterval(timerInterval);
        sendPermissionResponse(toolUseId, false);
        permReq.remove();
      } else {
        timer.textContent = `${countdown}초 후 자동 거부`;
      }
    }, 1000);
    
    // 버튼 이벤트
    permReq.addEventListener('click', (e) => {
      const btn = e.target.closest('.perm-btn');
      if (!btn) return;
      
      clearInterval(timerInterval);
      const allowed = btn.classList.contains('allow') || btn.classList.contains('always');
      sendPermissionResponse(toolUseId, allowed);
      permReq.remove();
    });
  }
  
  function sendPermissionResponse(toolUseId, allowed) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'permission_response',
        tool_use_id: toolUseId,
        allowed: allowed
      }));
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

    // 슬래시 명령어 처리
    if (text.startsWith('/')) {
      addMessageEl('user', text, Date.now(), null, fileInfos);
      ws.send(JSON.stringify({
        type: 'slash_command',
        command: text
      }));
      input.value = ''; input.style.height = 'auto';
      charCount.textContent = '0';
      pendingFiles = [];
      filePreviewArea.innerHTML = '';
      return;
    }

    if (!activeConvId) activeConvId = newConvId();

    addMessageEl('user', text, Date.now(), null, fileInfos);
    ws.send(JSON.stringify({
      message: text, provider: currentProvider, model: currentModel,
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

  modelSelect.addEventListener('change', () => {
    applyModelSelection(modelSelect.value);
    const sm = $('setting-model');
    if (sm) sm.value = modelSelect.value;
  });

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
  const chkWebSearch = $('chk-web-search');
  const chkDeepResearch = $('chk-deep-research');

  if (chkWebSearch) chkWebSearch.addEventListener('change', () => {
    webSearchEnabled = chkWebSearch.checked;
  });
  if (chkDeepResearch) chkDeepResearch.addEventListener('change', () => {
    deepResearchEnabled = chkDeepResearch.checked;
    if (deepResearchEnabled && !webSearchEnabled) {
      webSearchEnabled = true;
      if (chkWebSearch) chkWebSearch.checked = true;
    }
  });

  $('sidebar-toggle').addEventListener('click', () => sidebar.classList.toggle('open'));
  document.addEventListener('click', e => {
    if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && e.target !== $('sidebar-toggle')) {
      sidebar.classList.remove('open');
    }
  });

  $('setting-model')?.addEventListener('change', e => {
    applyModelSelection(e.target.value);
    modelSelect.value = e.target.value;
  });

  checkAuth();
})();
