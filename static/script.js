// Global state
let currentConversationId = null;
let uploadedFiles = [];
const userId = 'user_' + Math.random().toString(36).slice(2, 11);
let pendingChatRequests = 0;
let lastEnterDownMs = 0;
let chatAbortController = null;
let sendBtnDefaultHtml = '';

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
    syncSendBtn();
    loadConversations();
    setupEventListeners();
});

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
    if (parts.length === 0) {
        parts.push(...splitDollarInlineThenMd(mdChunk, purify));
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
    const bar = document.getElementById('chatStatusBar');
    if (bar) {
        bar.hidden = pendingChatRequests === 0;
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
    
    // Auto-expand textarea
    messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
}

// Load conversations from backend
async function loadConversations() {
    try {
        const response = await fetch(`/api/conversations?user_id=${userId}`);
        const data = await response.json();
        
        const container = document.getElementById('conversationsList');
        
        if (data.conversations && Object.keys(data.conversations).length > 0) {
            container.innerHTML = '';
            
            Object.entries(data.conversations).forEach(([id, conv]) => {
                const item = createConversationItem(id, conv);
                container.appendChild(item);
            });
        } else {
            container.innerHTML = '<div class="empty-state">No conversations yet</div>';
        }
    } catch (error) {
        console.error('Error loading conversations:', error);
    }
}

// Create conversation item element
function createConversationItem(id, conv) {
    const div = document.createElement('div');
    div.className = 'conversation-item';
    if (id === currentConversationId) {
        div.classList.add('active');
    }
    
    const lastMessage = conv.last_message;
    let preview = 'New conversation';
    
    if (lastMessage) {
        let content = lastMessage.content;
        if (typeof content === 'object') {
            content = content[0]?.text || 'Image message';
        }
        preview = content.substring(0, 40) + (content.length > 40 ? '...' : '');
    }
    
    div.innerHTML = `
        <span style="flex: 1; cursor: pointer;" onclick="switchConversation('${id}')">${preview}</span>
        <button class="delete-btn" onclick="deleteConversation('${id}', event)">×</button>
    `;
    
    return div;
}

// Start new chat
function startNewChat() {
    currentConversationId = null;
    uploadedFiles = [];
    document.getElementById('uploadedFiles').innerHTML = '';
    document.getElementById('messageInput').value = '';
    document.getElementById('chatTitle').textContent = 'Start a new conversation';
    document.getElementById('chatSubtitle').textContent = 'Ask me anything';
    
    // Reset messages area to welcome screen
    const messagesArea = document.getElementById('messagesArea');
    messagesArea.innerHTML = `
        <div class="welcome-section">
            <div class="welcome-icon">👋</div>
            <h2>Welcome to AI Assistant</h2>
            <p>Powered by Dify - Your intelligent conversation partner</p>
            <div class="quick-starts">
                <button class="quick-start-btn" onclick="sendMessage('What can you help me with?')">
                    What can you help me with?
                </button>
                <button class="quick-start-btn" onclick="sendMessage('Tell me about your capabilities')">
                    Tell me about your capabilities
                </button>
                <button class="quick-start-btn" onclick="sendMessage('How can I use images with my queries?')">
                    Image support info
                </button>
            </div>
        </div>
    `;
    
    loadConversations();
}

// Switch conversation
async function switchConversation(conversationId) {
    currentConversationId = conversationId;
    uploadedFiles = [];
    document.getElementById('uploadedFiles').innerHTML = '';
    document.getElementById('messageInput').value = '';
    
    try {
        const response = await fetch(`/api/conversations/${conversationId}`);
        const data = await response.json();
        
        if (data.success) {
            displayConversation(data);
        }
    } catch (error) {
        console.error('Error loading conversation:', error);
    }
    
    loadConversations();
}

// Display conversation
function displayConversation(conv) {
    document.getElementById('chatTitle').textContent = 'Conversation';
    document.getElementById('chatSubtitle').textContent = new Date(conv.created_at).toLocaleDateString();
    
    const messagesArea = document.getElementById('messagesArea');
    messagesArea.innerHTML = '';
    
    if (conv.messages && conv.messages.length > 0) {
        conv.messages.forEach(msg => {
            addMessageToUI(msg.role, msg.content);
        });
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }
}

// Delete conversation
async function deleteConversation(conversationId, event) {
    event.stopPropagation();
    
    if (!confirm('Are you sure you want to delete this conversation?')) {
        return;
    }
    
    try {
        const response = await fetch(`/api/conversations/${conversationId}`, {
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

// Add file tag to UI
function addFileTag(file) {
    const container = document.getElementById('uploadedFiles');
    
    const tag = document.createElement('div');
    tag.className = 'file-tag';
    
    const icon = getFileIcon(file.type);
    const fileName = file.name.length > 20 ? file.name.substring(0, 17) + '...' : file.name;
    
    tag.innerHTML = `
        <span>${icon} ${fileName}</span>
        <button class="remove-btn" onclick="removeFile('${file.name}')">×</button>
    `;
    
    container.appendChild(tag);
}

// Get file icon based on type
function getFileIcon(fileType) {
    if (fileType.startsWith('image/')) return '🖼️';
    if (fileType.includes('pdf')) return '📄';
    if (fileType.includes('word')) return '📝';
    return '📎';
}

// Remove file
function removeFile(fileName) {
    uploadedFiles = uploadedFiles.filter(f => f.name !== fileName);
    
    const tags = document.querySelectorAll('.file-tag');
    tags.forEach(tag => {
        if (tag.textContent.includes(fileName)) {
            tag.remove();
        }
    });
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
async function sendMessage(quickMessage = null) {
    const input = document.getElementById('messageInput');
    const message = quickMessage || input.value.trim();

    if (!message) {
        return;
    }
    if (pendingChatRequests > 0) {
        return;
    }

    // Add user message to UI
    addMessageToUI('user', message);

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
            addMessageToUI('assistant', '❌ Error: 响应不是合法 JSON (HTTP ' + response.status + ') ' + hint + (snippet ? '\n' + snippet : ''));
            return;
        }

        if (!response.ok) {
            const detail = data.detail || data.error || '';
            addMessageToUI('assistant', '❌ Error: HTTP ' + response.status + (detail ? ' — ' + detail : ''));
            return;
        }

        const replyText = pickAssistantReply(data);
        const hasSuccessFlag = data.success === true;
        const hasReply = replyText.length > 0;

        if (hasSuccessFlag || (!hasSuccessFlag && data.success !== false && hasReply)) {
            const apiConv = data.conversation_id;
            // 始终以服务端返回的 conversation_id 为准，保证与 Dify 多轮一致
            if (apiConv) {
                currentConversationId = apiConv;
            }
            addMessageToUI('assistant', replyText || '(未收到模型正文，请检查 Dify 应用与 API 返回结构)');

            loadConversations();
            updateActiveConversation();

            const messagesArea = document.getElementById('messagesArea');
            messagesArea.scrollTop = messagesArea.scrollHeight;
        } else {
            const detail = data.detail ? ` (${data.detail})` : '';
            addMessageToUI('assistant', '❌ Error: ' + (data.error || 'Failed to get response') + detail);
        }
    } catch (error) {
        console.error('Error sending message:', error);
        if (error && error.name === 'AbortError') {
            addMessageToUI('assistant', '已停止生成。');
        } else {
            addMessageToUI('assistant', '❌ Connection error: ' + (error && error.message ? error.message : String(error)));
        }
    } finally {
        chatAbortController = null;
        setChatBusy(false);
    }
}

// Add message to UI
function addMessageToUI(role, content) {
    const messagesArea = document.getElementById('messagesArea');
    
    // Remove welcome section if it exists
    const welcomeSection = messagesArea.querySelector('.welcome-section');
    if (welcomeSection) {
        welcomeSection.remove();
    }
    
    const message = document.createElement('div');
    message.className = `message ${role}`;
    
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';
    
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    
    if (role === 'assistant') {
        bubble.classList.add('message-bubble-md');
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
    
    if (role === 'user') {
        message.appendChild(bubble);
        message.appendChild(avatar);
    } else {
        message.appendChild(avatar);
        message.appendChild(bubble);
    }
    
    messagesArea.appendChild(message);
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Update active conversation
function updateActiveConversation() {
    const items = document.querySelectorAll('.conversation-item');
    items.forEach(item => {
        item.classList.remove('active');
        if (item.textContent.includes(currentConversationId)) {
            item.classList.add('active');
        }
    });
}

// Toggle sidebar on mobile
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    sidebar.classList.toggle('show');
}

// Update chat title when conversation loads
function updateChatTitle() {
    if (currentConversationId) {
        const item = document.querySelector(`.conversation-item.active`);
        if (item) {
            const preview = item.textContent.trim();
            document.getElementById('chatTitle').textContent = preview.substring(0, 50);
        }
    } else {
        document.getElementById('chatTitle').textContent = 'New Conversation';
    }
}

// Utility function to format timestamps
function formatTime(isoString) {
    const date = new Date(isoString);
    const now = new Date();
    const diffInMs = now - date;
    const diffInMinutes = Math.floor(diffInMs / 60000);
    const diffInHours = Math.floor(diffInMs / 3600000);
    const diffInDays = Math.floor(diffInMs / 86400000);
    
    if (diffInMinutes < 1) return 'just now';
    if (diffInMinutes < 60) return `${diffInMinutes}m ago`;
    if (diffInHours < 24) return `${diffInHours}h ago`;
    if (diffInDays < 7) return `${diffInDays}d ago`;
    
    return date.toLocaleDateString();
}

