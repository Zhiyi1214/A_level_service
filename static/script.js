// Global state
let currentConversationId = null;
let uploadedFiles = [];
const userId = 'user_' + Math.random().toString(36).slice(2, 11);
let pendingChatRequests = 0;
let lastEnterDownMs = 0;
let chatAbortController = null;
let sendBtnDefaultHtml = '';
let availableSources = [];
let selectedSourceId = '';
const THEME_STORAGE_KEY = 'a_level_theme';
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'a_level_sidebar_collapsed';

// Initialize app
document.addEventListener('DOMContentLoaded', function() {
    console.log('🚀 AI Assistant initialized');
    console.log('👤 User ID:', userId);
    try {
        if (typeof marked !== 'undefined' && typeof marked.setOptions === 'function') {
            marked.setOptions({ gfm: true, breaks: true });
        }
    } catch (e) {
        console.warn('marked init skipped:', e);
    }
    const sb = document.getElementById('sendBtn');
    if (sb) {
        sendBtnDefaultHtml = sb.innerHTML;
    }
    initTheme();
    initSidebarState();
    syncSendBtn();
    loadSources();
    loadConversations();
    setupEventListeners();
});

function renderWelcomeState() {
    return `
        <div class="welcome-section">
            <div class="welcome-orb">
                <span class="welcome-orb-core"></span>
                <span class="welcome-orb-ring welcome-orb-ring-one"></span>
                <span class="welcome-orb-ring welcome-orb-ring-two"></span>
            </div>
        </div>
    `;
}

function applyTheme(theme) {
    const body = document.body;
    if (!body) return;
    const nextTheme = theme === 'light' ? 'light' : 'dark';
    body.setAttribute('data-theme', nextTheme);
    const toggleIcon = document.querySelector('.theme-toggle-icon');
    if (toggleIcon) {
        toggleIcon.textContent = nextTheme === 'dark' ? '☀' : '☾';
    }
    try {
        localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    } catch (e) {
        console.warn('Theme persistence skipped:', e);
    }
}

function initTheme() {
    let preferredTheme = 'dark';
    try {
        preferredTheme = localStorage.getItem(THEME_STORAGE_KEY) || preferredTheme;
    } catch (e) {
        console.warn('Theme read skipped:', e);
    }
    applyTheme(preferredTheme);
}

function toggleTheme() {
    const currentTheme = document.body.getAttribute('data-theme') || 'dark';
    applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
}

function getPurify() {
    return typeof DOMPurify !== 'undefined' ? DOMPurify : (typeof window !== 'undefined' && window.DOMPurify ? window.DOMPurify : null);
}

/** 从 /api/chat 返回体中取出可展示的回复文本 */
function pickAssistantReply(data) {
    if (!data || typeof data !== 'object') return '';
    if (typeof data.response === 'string') return data.response;
    if (data.response != null && typeof data.response !== 'object') return String(data.response);
    if (typeof data.answer === 'string') return data.answer;
    if (data.response && typeof data.response === 'object' && typeof data.response.answer === 'string') {
        return data.response.answer;
    }
    return '';
}

function katexAvailable() {
    return typeof katex !== 'undefined' && typeof katex.renderToString === 'function';
}

/** 将 LaTeX 交给 KaTeX；失败时退回转义文本，避免整段崩掉 */
function katexRender(tex, displayMode) {
    if (!katexAvailable()) {
        const s = document.createElement('span');
        s.className = 'math-fallback';
        s.textContent = '$' + tex + '$';
        return s.outerHTML;
    }
    try {
        return katex.renderToString(tex, {
            displayMode: !!displayMode,
            throwOnError: false,
            strict: 'ignore'
        });
    } catch (e) {
        const d = document.createElement('span');
        d.className = 'math-error';
        d.textContent = tex;
        return d.outerHTML;
    }
}

/**
 * 在一段「纯 Markdown」里处理行内公式：\( ... \) 与 $ ... $（单美元，不含换行）
 * 先于 marked 执行，避免 _ 在公式里被当成斜体。
 */
function renderMdWithInlineMath(mdChunk, purify) {
    if (!mdChunk) return '';
    const INLINE_PAREN = /\\\(([\s\S]*?)\\\)/g;
    const parts = [];
    let last = 0;
    let m;
    while ((m = INLINE_PAREN.exec(mdChunk)) !== null) {
        if (m.index > last) {
            parts.push(...splitDollarInlineThenMd(mdChunk.slice(last, m.index), purify));
        }
        parts.push({ html: katexRender(m[1].trim(), false) });
        last = m.index + m[0].length;
    }
    if (last < mdChunk.length) {
        parts.push(...splitDollarInlineThenMd(mdChunk.slice(last), purify));
    }
    return parts.map(p => p.html).join('');
}

function splitDollarInlineThenMd(text, purify) {
    const out = [];
    // 允许 $ 内出现反斜杠命令（如 $\Delta H$），避免过早截断
    const re = /\$((?:[^$\\]|\\.)+?)\$/g;
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
        if (m.index > last) {
            const raw = text.slice(last, m.index);
            // 与行内公式拼接时不能用 marked.parse（会包 <p>，整块变换行）
            out.push({ html: mdToSafeHtml(raw, purify, { inlineOnly: true }) });
        }
        out.push({ html: katexRender(m[1].trim(), false) });
        last = m.index + m[0].length;
    }
    if (last < text.length) {
        out.push({ html: mdToSafeHtml(text.slice(last), purify, { inlineOnly: true }) });
    }
    if (out.length === 0) {
        out.push({ html: mdToSafeHtml(text, purify) });
    }
    return out;
}

/**
 * @param {{ inlineOnly?: boolean }} [opts] inlineOnly：夹在 $…$ 之间的片段，须行内解析，避免 <p> 把公式顶到单独一行
 */
function mdToSafeHtml(raw, purify, opts) {
    if (!raw) return '';
    const inlineOnly = !!(opts && opts.inlineOnly);

    if (inlineOnly) {
        if (typeof marked.parseInline === 'function') {
            let html = marked.parseInline(raw);
            if (html && typeof html.then === 'function') {
                const d = document.createElement('span');
                d.textContent = raw;
                return d.innerHTML;
            }
            return purify.sanitize(html);
        }
        let html = marked.parse(raw);
        if (html && typeof html.then === 'function') {
            const d = document.createElement('span');
            d.textContent = raw;
            return d.innerHTML;
        }
        const trimmed = String(html).trim();
        const singleP = /^<p[^>]*>([\s\S]*)<\/p>\s*$/i.exec(trimmed);
        if (singleP) {
            return purify.sanitize(singleP[1]);
        }
        return purify.sanitize(trimmed);
    }

    let html = marked.parse(raw);
    if (html && typeof html.then === 'function') {
        const d = document.createElement('div');
        d.textContent = raw;
        return d.innerHTML;
    }
    return purify.sanitize(html);
}

/**
 * 先抽出 $$ 块级公式，再对其余部分做 Markdown + 行内公式。
 * 模型输出的 $$...$$ 是合法的；原先仅用 marked 不会渲染数学，且会破坏下划线。
 */
function renderMarkdownWithMath(text, purify) {
    const DISPLAY = /\$\$([\s\S]*?)\$\$/g;
    const segments = [];
    let last = 0;
    let m;
    while ((m = DISPLAY.exec(text)) !== null) {
        if (m.index > last) {
            segments.push({ t: 'md', s: text.slice(last, m.index) });
        }
        segments.push({ t: 'math', s: m[1].trim(), display: true });
        last = m.index + m[0].length;
    }
    if (last < text.length) {
        segments.push({ t: 'md', s: text.slice(last) });
    }
    if (segments.length === 0) {
        segments.push({ t: 'md', s: text });
    }

    let html = '';
    for (const seg of segments) {
        if (seg.t === 'math') {
            html += katexRender(seg.s, true);
        } else {
            html += renderMdWithInlineMath(seg.s, purify);
        }
    }
    return html;
}

/** Render assistant plain text / markdown to safe HTML */
function renderAssistantMarkdown(text) {
    if (text == null || text === '') return '';
    if (typeof text !== 'string') {
        const d = document.createElement('div');
        d.textContent = String(text);
        return d.innerHTML;
    }
    const purify = getPurify();
    if (typeof marked === 'undefined' || typeof marked.parse !== 'function' || !purify) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }
    try {
        if (katexAvailable()) {
            return renderMarkdownWithMath(text, purify);
        }
        let html = marked.parse(text);
        if (html && typeof html.then === 'function') {
            console.warn('marked returned Promise; use plain text fallback');
            const d = document.createElement('div');
            d.textContent = text;
            return d.innerHTML;
        }
        return purify.sanitize(html);
    } catch (e) {
        console.error('Markdown render failed:', e);
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }
}

function setChatBusy(busy) {
    if (busy) {
        pendingChatRequests++;
    } else {
        pendingChatRequests = Math.max(0, pendingChatRequests - 1);
    }
    syncSendBtn();
}

function syncSendBtn() {
    const btn = document.getElementById('sendBtn');
    if (!btn) return;
    if (pendingChatRequests > 0) {
        btn.innerHTML = '<span class="send-btn-stop-inner">停止</span>';
        btn.classList.add('send-btn--stop');
        btn.onclick = function (e) {
            if (e) e.preventDefault();
            stopChatRequest();
        };
        btn.title = '停止当前请求';
        btn.setAttribute('aria-label', '停止生成');
    } else {
        btn.innerHTML = sendBtnDefaultHtml || btn.innerHTML;
        btn.classList.remove('send-btn--stop');
        btn.onclick = function () {
            sendMessage();
        };
        btn.title = '发送（连续按两次 Enter 也可发送）';
        btn.setAttribute('aria-label', '发送');
    }
}

function stopChatRequest() {
    if (chatAbortController) {
        chatAbortController.abort();
    }
}

// Setup event listeners
function setupEventListeners() {
    const messageInput = document.getElementById('messageInput');
    const sidebar = document.querySelector('.sidebar');
    const mobileMenuBtn = document.querySelector('.mobile-menu-btn');
    const appContainer = document.querySelector('.app-container');
    
    messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.max(44, Math.min(this.scrollHeight, 140)) + 'px';
    });

    document.addEventListener('click', function(event) {
        if (!isMobileViewport() || !isSidebarOpen() || !sidebar || !mobileMenuBtn) {
            return;
        }
        if (sidebar.contains(event.target) || mobileMenuBtn.contains(event.target)) {
            return;
        }
        closeSidebar();
    });

    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && isSidebarOpen()) {
            closeSidebar();
        }
    });

    window.addEventListener('resize', function() {
        if (isMobileViewport()) {
            if (appContainer) {
                appContainer.classList.remove('sidebar-collapsed');
            }
            syncSidebarButtons();
        } else {
            closeSidebar();
            syncSidebarButtons();
        }
    });
}

function onSourceChange() {
    if (currentConversationId) {
        // 会话中被锁定，理论上不会触发（select disabled），这里做兜底
        const select = document.getElementById('sourceSelect');
        if (select) select.value = selectedSourceId || '';
        return;
    }
    const select = document.getElementById('sourceSelect');
    selectedSourceId = select ? select.value : '';
}

function setSourceLocked(locked) {
    const select = document.getElementById('sourceSelect');
    if (select) {
        select.disabled = !!locked;
    }
}

function renderSourceOptions() {
    const select = document.getElementById('sourceSelect');
    if (!select) return;
    if (!availableSources.length) {
        select.innerHTML = '<option value="">无知识库</option>';
        select.disabled = true;
        return;
    }

    const placeholder = '<option value="">选择知识库</option>';
    const options = availableSources
        .map(s => `<option value="${escapeHtml(String(s.id))}">${escapeHtml(String(s.name || s.id))}</option>`)
        .join('');
    select.innerHTML = placeholder + options;
    if (selectedSourceId && availableSources.find(s => s.id === selectedSourceId)) {
        select.value = selectedSourceId;
    } else {
        selectedSourceId = '';
        select.value = '';
    }
    setSourceLocked(!!currentConversationId);
}

async function loadSources() {
    try {
        const response = await fetch('/api/sources');
        const data = await response.json();
        if (response.ok && data.success && Array.isArray(data.sources)) {
            availableSources = data.sources.filter(s => s && s.enabled !== false);
        } else {
            availableSources = [];
        }
    } catch (error) {
        console.error('Error loading sources:', error);
        availableSources = [];
    }
    renderSourceOptions();
}

async function ensureSessionReady() {
    if (currentConversationId) return true;
    if (!selectedSourceId) {
        addMessageToUI('assistant', '❌ 请先选择知识库再开始对话。');
        return false;
    }
    try {
        const response = await fetch('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_id: selectedSourceId,
                user_id: userId
            })
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            addMessageToUI('assistant', '❌ 创建会话失败：' + (data.error || ('HTTP ' + response.status)));
            return false;
        }
        currentConversationId = data.session_id || data.conversation_id;
        selectedSourceId = data.source_id || selectedSourceId;
        setSourceLocked(true);
        loadConversations();
        return true;
    } catch (error) {
        addMessageToUI('assistant', '❌ 创建会话失败：' + (error && error.message ? error.message : String(error)));
        return false;
    }
}

// Load conversations from backend
async function loadConversations() {
    try {
        const response = await fetch(`/api/conversations?user_id=${userId}`);
        if (!response.ok) return;
        const data = await response.json();
        const container = document.getElementById('conversationsList');

        if (data.conversations && Object.keys(data.conversations).length > 0) {
            container.innerHTML = '';
            Object.entries(data.conversations).forEach(([id, conv]) => {
                container.appendChild(createConversationItem(id, conv));
            });
        } else {
            container.innerHTML = '<div class="empty-state">还没有会话</div>';
        }
    } catch (error) {
        console.error('Error loading conversations:', error);
    }
}

// Create conversation item element
function createConversationItem(id, conv) {
    const div = document.createElement('div');
    div.className = 'conversation-item';
    div.dataset.convId = id;
    if (id === currentConversationId) {
        div.classList.add('active');
    }

    const lastMessage = conv.last_message;
    let preview = '新对话';
    if (lastMessage) {
        let content = lastMessage.content;
        if (typeof content === 'object') {
            content = content[0]?.text || '图片消息';
        }
        preview = content.substring(0, 40) + (content.length > 40 ? '...' : '');
    }
    const sourcePrefix = conv && conv.source_name ? `[${conv.source_name}] ` : '';

    const trigger = document.createElement('span');
    trigger.className = 'conversation-trigger';
    trigger.textContent = sourcePrefix + preview;
    trigger.addEventListener('click', () => switchConversation(id));

    const delBtn = document.createElement('button');
    delBtn.className = 'delete-btn';
    delBtn.textContent = '×';
    delBtn.addEventListener('click', (e) => deleteConversation(id, e));

    div.appendChild(trigger);
    div.appendChild(delBtn);
    return div;
}

// Start new chat
function startNewChat() {
    currentConversationId = null;
    selectedSourceId = '';
    uploadedFiles = [];
    resetComposer();
    renderSourceOptions();
    setSourceLocked(false);
    
    const messagesArea = document.getElementById('messagesArea');
    messagesArea.innerHTML = renderWelcomeState();
    closeSidebar();
    
    loadConversations();
}

// Switch conversation
async function switchConversation(conversationId) {
    try {
        const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}?user_id=${encodeURIComponent(userId)}`);
        if (!response.ok) {
            console.error('Error loading conversation: HTTP', response.status);
            return;
        }
        const data = await response.json();
        if (!data.success) {
            console.error('Error loading conversation:', data.error);
            return;
        }

        currentConversationId = conversationId;
        uploadedFiles = [];
        resetComposer();
        if (data.source_id) {
            selectedSourceId = data.source_id;
            renderSourceOptions();
        }
        setSourceLocked(true);
        displayConversation(data);
        closeSidebar();
        loadConversations();
    } catch (error) {
        console.error('Error loading conversation:', error);
    }
}

// Display conversation
function displayConversation(conv) {
    const messagesArea = document.getElementById('messagesArea');
    messagesArea.innerHTML = '';
    
    if (conv.messages && conv.messages.length > 0) {
        conv.messages.forEach(msg => {
            addMessageToUI(msg.role, msg.content);
        });
        scrollMessagesToBottom();
    }
}

// Delete conversation
async function deleteConversation(conversationId, event) {
    event.stopPropagation();
    
    if (!confirm('确定要删除这条会话吗？')) {
        return;
    }
    
    try {
        const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}?user_id=${encodeURIComponent(userId)}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            if (currentConversationId === conversationId) {
                startNewChat();
            } else {
                loadConversations();
            }
        }
    } catch (error) {
        console.error('Error deleting conversation:', error);
    }
}

// Handle file selection
function handleFileSelect(event) {
    const files = event.target.files;
    
    for (let file of files) {
        // Check file size (max 50MB)
        if (file.size > 52428800) {
            alert('File too large. Maximum size is 50MB');
            continue;
        }
        
        uploadedFiles.push(file);
        addFileTag(file);
    }
}

let fileIdCounter = 0;

function addFileTag(file) {
    const container = document.getElementById('uploadedFiles');
    const fileId = ++fileIdCounter;
    file._tagId = fileId;

    const tag = document.createElement('div');
    tag.className = 'file-tag';
    tag.dataset.fileId = fileId;

    const label = document.createElement('span');
    const displayName = file.name.length > 20 ? file.name.substring(0, 17) + '...' : file.name;
    label.textContent = '🖼️ ' + displayName;

    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove-btn';
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', () => removeFileById(fileId));

    tag.appendChild(label);
    tag.appendChild(removeBtn);
    container.appendChild(tag);
}

function removeFileById(fileId) {
    uploadedFiles = uploadedFiles.filter(f => f._tagId !== fileId);
    const tag = document.querySelector(`.file-tag[data-file-id="${fileId}"]`);
    if (tag) tag.remove();
}

// Handle input keydown：连续两次 Enter（约 0.52s 内）发送；Shift+Enter 换行
function handleInputKeydown(event) {
    if (event.key !== 'Enter' || event.shiftKey) {
        return;
    }
    const now = Date.now();
    if (now - lastEnterDownMs < 520) {
        event.preventDefault();
        lastEnterDownMs = 0;
        sendMessage();
        return;
    }
    lastEnterDownMs = now;
}

// Send message
async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();

    if (!message) {
        return;
    }
    if (pendingChatRequests > 0) {
        return;
    }

    const hasSession = await ensureSessionReady();
    if (!hasSession) {
        return;
    }

    // Add user message to UI
    addMessageToUI('user', message);
    const pendingAssistantMessage = addMessageToUI('assistant', '正在生成回复...', { pending: true });

    // Clear input and files
    input.value = '';
    input.style.height = 'auto';
    input.style.height = '44px';
    document.getElementById('uploadedFiles').innerHTML = '';

    setChatBusy(true);
    chatAbortController = new AbortController();

    try {
        // Prepare form data for file upload
        const formData = new FormData();
        formData.append('message', message);
        formData.append('conversation_id', currentConversationId || '');
        formData.append('user_id', userId);
        formData.append('source_id', selectedSourceId || '');

        // Add uploaded files
        uploadedFiles.forEach(file => {
            formData.append('files', file);
        });

        uploadedFiles = [];

        // Send to backend
        const response = await fetch('/api/chat', {
            method: 'POST',
            body: formData,
            signal: chatAbortController.signal
        });

        const rawText = await response.text();
        let data = {};
        try {
            data = rawText ? JSON.parse(rawText) : {};
        } catch (e) {
            const hint = rawText && rawText.trim().startsWith('<')
                ? '（上游返回了 HTML，多为反向代理/Nginx 超时或 502）'
                : '';
            const snippet = rawText ? rawText.slice(0, 280).replace(/\s+/g, ' ') : '';
            updateMessageInUI(pendingAssistantMessage, 'assistant', '❌ Error: 响应不是合法 JSON (HTTP ' + response.status + ') ' + hint + (snippet ? '\n' + snippet : ''));
            return;
        }

        if (!response.ok) {
            const detail = data.detail || data.error || '';
            if (response.status === 409 && data.error === 'source_locked') {
                updateMessageInUI(pendingAssistantMessage, 'assistant', '❌ 当前会话已锁定知识库，不能中途切换。请新建对话后再切换。');
            } else {
                updateMessageInUI(pendingAssistantMessage, 'assistant', '❌ Error: HTTP ' + response.status + (detail ? ' — ' + detail : ''));
            }
            return;
        }

        const replyText = pickAssistantReply(data);

        if (data.success !== false) {
            const apiConv = data.conversation_id;
            // 始终以服务端返回的 conversation_id 为准，保证与 Dify 多轮一致
            if (apiConv) {
                currentConversationId = apiConv;
            }
            if (data.source_id) {
                selectedSourceId = data.source_id;
                renderSourceOptions();
            }
            setSourceLocked(true);
            updateMessageInUI(pendingAssistantMessage, 'assistant', replyText || '(未收到模型正文，请检查 Dify 应用与 API 返回结构)');

            loadConversations();
            updateActiveConversation();

            scrollMessagesToBottom();
        } else {
            const detail = data.detail ? ` (${data.detail})` : '';
            updateMessageInUI(pendingAssistantMessage, 'assistant', '❌ Error: ' + (data.error || 'Failed to get response') + detail);
        }
    } catch (error) {
        console.error('Error sending message:', error);
        if (error && error.name === 'AbortError') {
            updateMessageInUI(pendingAssistantMessage, 'assistant', '已停止生成。');
        } else {
            updateMessageInUI(pendingAssistantMessage, 'assistant', '❌ Connection error: ' + (error && error.message ? error.message : String(error)));
        }
    } finally {
        chatAbortController = null;
        setChatBusy(false);
    }
}

function removeWelcomeSection() {
    const messagesArea = document.getElementById('messagesArea');
    const welcomeSection = messagesArea.querySelector('.welcome-section');
    if (welcomeSection) {
        welcomeSection.remove();
    }
}

function resetComposer() {
    const uploadedFilesContainer = document.getElementById('uploadedFiles');
    const messageInput = document.getElementById('messageInput');
    if (uploadedFilesContainer) {
        uploadedFilesContainer.innerHTML = '';
    }
    if (messageInput) {
        messageInput.value = '';
        messageInput.style.height = '44px';
    }
}

function closeSidebar() {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.classList.remove('show');
    }
    syncSidebarButtons();
}

function scrollMessagesToBottom() {
    const messagesArea = document.getElementById('messagesArea');
    if (!messagesArea) return;
    messagesArea.scrollTop = messagesArea.scrollHeight;
}

function renderMessageBubble(bubble, role, content, options = {}) {
    bubble.innerHTML = '';
    bubble.className = 'message-bubble';

    if (role === 'assistant' && !options.pending) {
        bubble.classList.add('message-bubble-md');
    }
    if (options.pending) {
        bubble.classList.add('message-bubble-pending');
        bubble.innerHTML = `
            <span class="pending-label">正在生成回复</span>
            <span class="pending-dots" aria-hidden="true">
                <span></span><span></span><span></span>
            </span>
        `;
        return;
    }

    // Handle content (could be string or array)
    if (role === 'user') {
        if (typeof content === 'string') {
            bubble.innerHTML = escapeHtml(content);
        } else if (Array.isArray(content)) {
            content.forEach(item => {
                if (item.type === 'text') {
                    bubble.innerHTML += escapeHtml(item.text);
                } else if (item.type === 'image') {
                    const img = document.createElement('img');
                    img.className = 'message-image';
                    img.src = item.url;
                    bubble.appendChild(img);
                }
            });
        }
    } else {
        let assistantText = content;
        if (content !== null && typeof content === 'object' && !Array.isArray(content)) {
            assistantText = content.answer != null ? content.answer : (content.text != null ? content.text : JSON.stringify(content));
        }
        if (typeof assistantText === 'string') {
            bubble.innerHTML = renderAssistantMarkdown(assistantText);
        } else if (Array.isArray(content)) {
            content.forEach(item => {
                if (item.type === 'text') {
                    const block = document.createElement('div');
                    block.className = 'md-block';
                    block.innerHTML = renderAssistantMarkdown(item.text || '');
                    bubble.appendChild(block);
                } else if (item.type === 'image') {
                    const img = document.createElement('img');
                    img.className = 'message-image';
                    img.src = item.url;
                    bubble.appendChild(img);
                }
            });
        }
    }
}

function updateMessageInUI(message, role, content, options = {}) {
    if (!message) return null;
    message.className = `message ${role}`;
    if (options.pending) {
        message.classList.add('message-pending');
    }

    const avatar = message.querySelector('.message-avatar');
    if (avatar) {
        avatar.textContent = role === 'user' ? '👤' : '🤖';
    }

    const bubble = message.querySelector('.message-bubble');
    if (bubble) {
        renderMessageBubble(bubble, role, content, options);
    }

    scrollMessagesToBottom();
    return message;
}

// Add message to UI
function addMessageToUI(role, content, options = {}) {
    const messagesArea = document.getElementById('messagesArea');
    removeWelcomeSection();

    const message = document.createElement('div');
    message.className = `message ${role}`;
    if (options.pending) {
        message.classList.add('message-pending');
    }
    
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';
    
    const bubble = document.createElement('div');
    renderMessageBubble(bubble, role, content, options);
    
    if (role === 'user') {
        message.appendChild(bubble);
        message.appendChild(avatar);
    } else {
        message.appendChild(avatar);
        message.appendChild(bubble);
    }
    
    messagesArea.appendChild(message);
    scrollMessagesToBottom();
    return message;
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function updateActiveConversation() {
    document.querySelectorAll('.conversation-item').forEach(item => {
        item.classList.toggle('active', item.dataset.convId === currentConversationId);
    });
}

function isMobileViewport() {
    return window.innerWidth <= 768;
}

function isSidebarOpen() {
    const sidebar = document.querySelector('.sidebar');
    return !!(sidebar && sidebar.classList.contains('show'));
}

function isDesktopSidebarCollapsed() {
    const appContainer = document.querySelector('.app-container');
    return !!(appContainer && appContainer.classList.contains('sidebar-collapsed'));
}

function syncSidebarButtons() {
    const mobileMenuBtn = document.querySelector('.mobile-menu-btn');
    const collapseBtn = document.getElementById('sidebarCollapseBtn');
    const mobileOpen = isSidebarOpen();
    const desktopCollapsed = isDesktopSidebarCollapsed();

    if (mobileMenuBtn) {
        const expanded = isMobileViewport() ? mobileOpen : !desktopCollapsed;
        mobileMenuBtn.setAttribute('aria-expanded', String(expanded));
        mobileMenuBtn.title = desktopCollapsed ? '展开侧边栏' : '切换侧边栏';
        mobileMenuBtn.setAttribute('aria-label', mobileMenuBtn.title);
    }

    if (collapseBtn) {
        const collapsed = desktopCollapsed;
        collapseBtn.title = collapsed ? '展开侧边栏' : '收起侧边栏';
        collapseBtn.setAttribute('aria-label', collapseBtn.title);
    }
}

function setDesktopSidebarCollapsed(collapsed, options = {}) {
    const appContainer = document.querySelector('.app-container');
    if (!appContainer || isMobileViewport()) {
        return;
    }
    appContainer.classList.toggle('sidebar-collapsed', !!collapsed);
    syncSidebarButtons();

    if (options.persist === false) {
        return;
    }
    try {
        localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, collapsed ? '1' : '0');
    } catch (e) {
        console.warn('Sidebar state persistence skipped:', e);
    }
}

function initSidebarState() {
    if (isMobileViewport()) {
        syncSidebarButtons();
        return;
    }

    let collapsed = false;
    try {
        collapsed = localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1';
    } catch (e) {
        console.warn('Sidebar state read skipped:', e);
    }
    setDesktopSidebarCollapsed(collapsed, { persist: false });
}

function toggleDesktopSidebar() {
    if (isMobileViewport()) {
        toggleSidebar();
        return;
    }
    setDesktopSidebarCollapsed(!isDesktopSidebarCollapsed());
}

function toggleSidebar() {
    if (isMobileViewport()) {
        const sidebar = document.querySelector('.sidebar');
        const nextOpen = !(sidebar && sidebar.classList.contains('show'));
        if (sidebar) {
            sidebar.classList.toggle('show', nextOpen);
        }
        syncSidebarButtons();
        return;
    }
    setDesktopSidebarCollapsed(!isDesktopSidebarCollapsed());
}


