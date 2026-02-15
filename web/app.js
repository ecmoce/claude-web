/**
 * Claude Web Gateway — 클라이언트 앱
 * WebSocket 스트리밍, 마크다운 렌더링, 코드 하이라이팅
 */

(() => {
    // DOM 요소
    const loginScreen = document.getElementById('login-screen');
    const chatScreen = document.getElementById('chat-screen');
    const messages = document.getElementById('messages');
    const input = document.getElementById('input');
    const sendBtn = document.getElementById('send-btn');
    const clearBtn = document.getElementById('clear-btn');
    const userBadge = document.getElementById('user-badge');
    const connectionBadge = document.getElementById('connection-badge');
    const statusText = document.getElementById('status-text');
    const charCount = document.getElementById('char-count');

    let ws = null;
    let isStreaming = false;
    let currentAssistantEl = null;
    let streamBuffer = '';

    // marked 설정 — 코드 하이라이팅
    marked.setOptions({
        highlight: (code, lang) => {
            if (lang && hljs.getLanguage(lang)) {
                return hljs.highlight(code, { language: lang }).value;
            }
            return hljs.highlightAuto(code).value;
        },
        breaks: true,
        gfm: true,
    });

    // ── 인증 확인 ──────────────────────────────

    async function checkAuth() {
        try {
            const resp = await fetch('/api/me');
            const data = await resp.json();
            if (data.authenticated) {
                showChat(data.username, data.dev_mode);
            } else {
                showLogin();
            }
        } catch {
            showLogin();
        }
    }

    function showLogin() {
        loginScreen.classList.remove('hidden');
        chatScreen.classList.add('hidden');
    }

    function showChat(username, devMode) {
        loginScreen.classList.add('hidden');
        chatScreen.classList.remove('hidden');
        userBadge.textContent = devMode ? '🔧 DEV' : `@${username}`;
        connectWS();
    }

    // ── WebSocket ──────────────────────────────

    function connectWS() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${proto}//${location.host}/ws`);

        ws.onopen = () => {
            connectionBadge.textContent = '연결됨';
            connectionBadge.classList.add('connected');
            statusText.textContent = '준비됨';
        };

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            handleMessage(data);
        };

        ws.onclose = () => {
            connectionBadge.textContent = '연결 끊김';
            connectionBadge.classList.remove('connected');
            statusText.textContent = '재연결 중...';
            // 3초 후 재연결
            setTimeout(connectWS, 3000);
        };

        ws.onerror = () => {
            showToast('WebSocket 연결 오류');
        };
    }

    function handleMessage(data) {
        switch (data.type) {
            case 'connected':
                break;

            case 'start':
                // 스트리밍 시작 — assistant 메시지 생성
                isStreaming = true;
                streamBuffer = '';
                currentAssistantEl = addMessage('assistant', '');
                showTyping(currentAssistantEl);
                statusText.textContent = '⏳ Claude 응답 중...';
                sendBtn.disabled = true;
                break;

            case 'chunk':
                // 스트리밍 청크 — 버퍼에 추가 후 렌더링
                streamBuffer += data.content;
                updateMessageContent(currentAssistantEl, streamBuffer);
                scrollToBottom();
                break;

            case 'done':
                // 스트리밍 완료
                isStreaming = false;
                hideTyping(currentAssistantEl);
                updateMessageContent(currentAssistantEl, streamBuffer);
                addFooter(currentAssistantEl, data.elapsed);
                currentAssistantEl = null;
                streamBuffer = '';
                statusText.textContent = `✅ 완료 (${data.elapsed}초)`;
                sendBtn.disabled = !input.value.trim();
                scrollToBottom();
                break;

            case 'error':
                isStreaming = false;
                showToast(data.content);
                statusText.textContent = '❌ 오류 발생';
                sendBtn.disabled = !input.value.trim();
                break;
        }
    }

    // ── 메시지 UI ──────────────────────────────

    function clearWelcome() {
        const welcome = messages.querySelector('.welcome-message');
        if (welcome) welcome.remove();
    }

    function addMessage(role, content) {
        clearWelcome();

        const el = document.createElement('div');
        el.className = `message ${role}`;

        const header = document.createElement('div');
        header.className = 'msg-header';
        header.textContent = role === 'user' ? '👤 You' : '🤖 Claude';

        const body = document.createElement('div');
        body.className = 'msg-content';
        if (content) {
            body.innerHTML = role === 'user' ? escapeHtml(content) : renderMarkdown(content);
        }

        el.appendChild(header);
        el.appendChild(body);
        messages.appendChild(el);
        scrollToBottom();
        return el;
    }

    function updateMessageContent(el, content) {
        if (!el) return;
        const body = el.querySelector('.msg-content');
        body.innerHTML = renderMarkdown(content);
        // 코드 블록 하이라이팅 재적용
        body.querySelectorAll('pre code').forEach((block) => {
            hljs.highlightElement(block);
        });
    }

    function addFooter(el, elapsed) {
        if (!el) return;
        const footer = document.createElement('div');
        footer.className = 'msg-footer';
        footer.textContent = `⏱ ${elapsed}초`;
        el.appendChild(footer);
    }

    function showTyping(el) {
        if (!el) return;
        const indicator = document.createElement('div');
        indicator.className = 'typing-indicator';
        indicator.innerHTML = '<span></span><span></span><span></span>';
        el.appendChild(indicator);
    }

    function hideTyping(el) {
        if (!el) return;
        const indicator = el.querySelector('.typing-indicator');
        if (indicator) indicator.remove();
    }

    function renderMarkdown(text) {
        try {
            return marked.parse(text);
        } catch {
            return escapeHtml(text);
        }
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function scrollToBottom() {
        requestAnimationFrame(() => {
            messages.scrollTop = messages.scrollHeight;
        });
    }

    function showToast(msg) {
        const toast = document.createElement('div');
        toast.className = 'error-toast';
        toast.textContent = msg;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3500);
    }

    // ── 전송 ───────────────────────────────────

    function sendMessage() {
        const text = input.value.trim();
        if (!text || isStreaming || !ws || ws.readyState !== WebSocket.OPEN) return;

        addMessage('user', text);
        ws.send(JSON.stringify({ message: text }));
        input.value = '';
        input.style.height = 'auto';
        updateCharCount();
        sendBtn.disabled = true;
    }

    // ── 히스토리 삭제 ──────────────────────────

    async function clearHistory() {
        if (!confirm('대화 기록을 모두 삭제할까요?')) return;
        try {
            await fetch('/api/history', { method: 'DELETE' });
            messages.innerHTML = '';
            // 웰컴 메시지 복원
            messages.innerHTML = `
                <div class="welcome-message">
                    <div class="welcome-icon">🤖</div>
                    <h2>안녕하세요!</h2>
                    <p>Claude에게 무엇이든 물어보세요.</p>
                </div>`;
        } catch {
            showToast('히스토리 삭제 실패');
        }
    }

    // ── 입력 핸들링 ────────────────────────────

    function updateCharCount() {
        const len = input.value.length;
        charCount.textContent = `${len.toLocaleString()} / 10,000`;
        charCount.style.color = len > 9000 ? 'var(--red)' : 'var(--text-muted)';
    }

    // textarea 자동 높이 조절
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 150) + 'px';
        sendBtn.disabled = !input.value.trim() || isStreaming;
        updateCharCount();
    });

    // Enter 전송, Shift+Enter 줄바꿈
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    sendBtn.addEventListener('click', sendMessage);
    clearBtn.addEventListener('click', clearHistory);

    // 퀵 프롬프트 클릭
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('quick-prompt')) {
            input.value = e.target.dataset.prompt;
            input.dispatchEvent(new Event('input'));
            sendMessage();
        }
    });

    // ── 시작 ───────────────────────────────────
    checkAuth();
})();
