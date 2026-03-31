// Global state
let currentConversationId = null;
let uploadedFiles = [];
let oauthConfigured = false;
let authUser = null;
let lastEnterDownMs = 0;
let sendBtnDefaultHtml = '';
let availableSources = [];
let selectedSourceId = '';
/** 侧栏按时间排序后，最近一次活跃会话的知识库 id（用于新建聊天默认选中） */
let defaultSourceIdForNewChat = '';
/** 与后端一致：跨站简单请求难以伪造此头，作 CSRF 缓解 */
const API_AJAX_HEADERS = Object.freeze({ 'X-Requested-With': 'XMLHttpRequest' });

function withAjaxHeaders(options = {}) {
    const o = { ...options };
    const h = options.headers && typeof options.headers === 'object' && !(options.headers instanceof Headers)
        ? { ...options.headers }
        : {};
    o.headers = { ...API_AJAX_HEADERS, ...h };
    return o;
}

const THEME_STORAGE_KEY = 'a_level_theme_v46';
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'a_level_sidebar_collapsed_v46';
const MAX_UPLOAD_IMAGES = 3;
/** 会话详情首次加载与「加载更早」时每页条数 */
const CONVERSATION_MESSAGE_PAGE_SIZE = 8;
const conversationStates = new Map();

function getConversationState(conversationId) {
    if (!conversationId) {
        return null;
    }
    if (!conversationStates.has(conversationId)) {
        conversationStates.set(conversationId, {
            serverConversation: null,
            localMessages: [],
            pendingCount: 0,
            abortController: null,
            loadingOlderMessages: false
        });
    }
    return conversationStates.get(conversationId);
}

function getCurrentConversationState() {
    return currentConversationId ? getConversationState(currentConversationId) : null;
}

function setCurrentConversation(conversationId) {
    currentConversationId = conversationId || null;
    updateActiveConversation();
    syncSendBtn();
}

function getCurrentPendingCount() {
    const state = getCurrentConversationState();
    return state ? state.pendingCount : 0;
}

/** 单条会话 API（支持 message_limit / before_message_id 分页）；匿名身份由服务端 Cookie session 绑定，勿传 user_id */
function conversationDetailUrl(conversationId, query = {}) {
    const base = `/api/conversations/${encodeURIComponent(conversationId)}`;
    const params = new URLSearchParams();
    if (query.messageLimit != null) {
        params.set('message_limit', String(query.messageLimit));
    }
    if (query.beforeMessageId != null) {
        params.set('before_message_id', String(query.beforeMessageId));
    }
    const s = params.toString();
    return s ? `${base}?${s}` : base;
}

async function initAuth() {
    const authStatus = getOAuthReturnStatus();
    try {
        await refreshAuthState();
        if (authStatus === 'ok' && oauthConfigured && !authUser) {
            await new Promise(resolve => window.setTimeout(resolve, 250));
            await refreshAuthState();
        }
    } catch (e) {
        console.warn('initAuth failed:', e);
        oauthConfigured = false;
        authUser = null;
    }
    applyAuthView(authStatus);
    renderAuthPanel();
    consumeOAuthReturnStatus(authStatus);
}

async function refreshAuthState() {
    const response = await fetch('/api/me', {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
            'Cache-Control': 'no-cache'
        }
    });
    const data = await response.json();
    oauthConfigured = !!data.oauth_configured;
    authUser = data.authenticated && data.user ? data.user : null;
}

function getOAuthReturnStatus() {
    const params = new URLSearchParams(window.location.search);
    return params.get('auth');
}

function isAuthGateActive() {
    return oauthConfigured && !authUser;
}

function applyAuthView(authStatus = '') {
    const loginScreen = document.getElementById('loginScreen');
    const loginMessage = document.getElementById('loginScreenMessage');
    const appContainer = document.querySelector('.app-container');
    const gateActive = isAuthGateActive();

    if (document.body) {
        document.body.classList.toggle('auth-gated', gateActive);
    }
    if (loginScreen) {
        loginScreen.classList.toggle('login-screen--hidden', !gateActive);
    }
    if (appContainer) {
        appContainer.classList.toggle('app-container--hidden', gateActive);
    }
    if (loginMessage) {
        let message = '';
        if (authStatus === 'ok' && gateActive) {
            message = '登录回调已完成，但当前会话未生效。';
        } else if (authStatus === 'error') {
            message = 'Google 登录失败，请重试。';
        }
        loginMessage.textContent = message;
        loginMessage.classList.toggle('login-screen-message--hidden', !message);
    }
}

function consumeOAuthReturnStatus(authStatus = '') {
    try {
        const params = new URLSearchParams(window.location.search);
        if (!authStatus) {
            return;
        }
        params.delete('auth');
        const q = params.toString();
        const path = window.location.pathname + (q ? '?' + q : '') + window.location.hash;
        window.history.replaceState({}, '', path);
    } catch (e) {
        /* ignore */
    }
}

function renderAuthPanel() {
    const panel = document.getElementById('authPanel');
    if (!panel) {
        return;
    }
    panel.classList.remove('auth-panel--login-cta', 'auth-panel--user-state');
    if (!oauthConfigured || !authUser) {
        panel.innerHTML = '';
        panel.classList.add('auth-panel--hidden');
        return;
    }
    panel.classList.remove('auth-panel--hidden');
    panel.classList.add('auth-panel--user-state');
    const label = authUser.display_name || authUser.email || '已登录';
    const title = authUser.email || '';
    panel.innerHTML = `
        <div class="sidebar-capsule sidebar-capsule--auth">
            <div class="auth-panel-inner">
                <span class="auth-panel-user" title="${escapeHtml(title)}">${escapeHtml(label)}</span>
                <button type="button" class="auth-panel-logout" id="authLogoutBtn">退出</button>
            </div>
        </div>`;
    const btn = document.getElementById('authLogoutBtn');
    if (btn) {
        btn.addEventListener('click', () => { logoutAuth(); });
    }
}

function renderSidebarStatus(message = '') {
    const status = document.getElementById('sidebarStatus');
    const conversationsList = document.querySelector('.conversations-list');
    if (!status) {
        return;
    }

    const nextMessage = message.trim();
    status.textContent = nextMessage;
    status.classList.toggle('sidebar-status--hidden', !nextMessage);
    if (conversationsList) {
        conversationsList.classList.toggle('conversations-list--status-visible', !!nextMessage);
    }
}

async function logoutAuth() {
    try {
        await fetch('/auth/logout', withAjaxHeaders({ method: 'POST', credentials: 'same-origin' }));
    } catch (e) {
        console.warn('logout failed:', e);
    }
    conversationStates.clear();
    authUser = null;
    currentConversationId = null;
    selectedSourceId = '';
    defaultSourceIdForNewChat = '';
    availableSources = [];
    renderSidebarStatus('');
    renderAuthPanel();
    applyAuthView('');
    startNewChat();
}

function setConversationServerData(conversationId, conv) {
    const state = getConversationState(conversationId);
    if (!state) return;
    state.serverConversation = {
        id: conversationId,
        created_at: conv && conv.created_at ? conv.created_at : '',
        messages: Array.isArray(conv && conv.messages) ? conv.messages.slice() : [],
        source_id: conv && conv.source_id ? conv.source_id : '',
        source_name: conv && conv.source_name ? conv.source_name : '',
        dify_title: conv && typeof conv.dify_title === 'string' ? conv.dify_title.trim() : '',
        has_more_older: !!(conv && conv.has_more_older),
        message_count_total:
            conv && typeof conv.message_count_total === 'number'
                ? conv.message_count_total
                : null
    };
}

function getOldestServerMessageId(conversationId) {
    const state = getConversationState(conversationId);
    const msgs = state && state.serverConversation && Array.isArray(state.serverConversation.messages)
        ? state.serverConversation.messages
        : [];
    let minId = null;
    for (const m of msgs) {
        const id = m && m.id != null ? Number(m.id) : null;
        if (id != null && !Number.isNaN(id)) {
            if (minId == null || id < minId) {
                minId = id;
            }
        }
    }
    return minId;
}

function mergeOlderServerMessages(existing, olderBatch) {
    const byId = new Map();
    for (const m of olderBatch || []) {
        if (m && m.id != null) {
            byId.set(Number(m.id), m);
        }
    }
    for (const m of existing || []) {
        if (m && m.id != null) {
            const k = Number(m.id);
            if (!byId.has(k)) {
                byId.set(k, m);
            }
        }
    }
    return Array.from(byId.values()).sort((a, b) => Number(a.id) - Number(b.id));
}

function addLocalMessages(conversationId, messages) {
    const state = getConversationState(conversationId);
    if (!state) return;
    state.localMessages.push(...messages);
}

function removeLocalMessagesByRequest(conversationId, requestId, options = {}) {
    const state = getConversationState(conversationId);
    if (!state) return;
    const revokeUserBlobUrls = options.revokeUserBlobUrls !== false;
    if (revokeUserBlobUrls) {
        for (const msg of state.localMessages) {
            if (msg.requestId === requestId && msg.role === 'user') {
                revokeBlobUrlsInUserMessageContent(msg.content);
            }
        }
    }
    state.localMessages = state.localMessages.filter(msg => msg.requestId !== requestId);
}

/** 若服务端拉取与流式终稿不一致，用终稿覆盖内存中最后一条助手消息（优先 done.response） */
function patchLastAssistantMessageContent(conversationId, streamedText) {
    if (!conversationId || streamedText == null || streamedText === '') {
        return;
    }
    const state = getConversationState(conversationId);
    if (!state || !state.serverConversation || !Array.isArray(state.serverConversation.messages)) {
        return;
    }
    const msgs = state.serverConversation.messages;
    for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].role !== 'assistant') {
            continue;
        }
        if (typeof msgs[i].content !== 'string') {
            return;
        }
        if (msgs[i].content === streamedText) {
            return;
        }
        msgs[i] = { ...msgs[i], content: streamedText };
        return;
    }
}

function replacePendingAssistantMessage(conversationId, requestId, content) {
    const state = getConversationState(conversationId);
    if (!state) return;
    state.localMessages = state.localMessages.map(msg => {
        if (msg.requestId === requestId && msg.role === 'assistant' && msg.pending) {
            return { ...msg, content, pending: false };
        }
        return msg;
    });
}

/** 同步本地 pending 状态与当前 DOM 气泡（避免全量 renderConversationView 闪烁） */
function applyStreamingAssistantUpdate(conversationId, requestId, fullText) {
    const state = getConversationState(conversationId);
    if (state) {
        state.localMessages = state.localMessages.map(msg => {
            if (msg.requestId === requestId && msg.role === 'assistant' && msg.pending) {
                return { ...msg, content: fullText, pending: true };
            }
            return msg;
        });
    }
    if (currentConversationId !== conversationId || !requestId) {
        return;
    }
    const messagesArea = document.getElementById('messagesArea');
    if (!messagesArea) {
        return;
    }
    let row = null;
    messagesArea.querySelectorAll('.message.assistant.message-pending').forEach((el) => {
        if (el.dataset.requestId === requestId) {
            row = el;
        }
    });
    if (!row) {
        return;
    }
    const bubble = row.querySelector('.message-bubble');
    if (!bubble) {
        return;
    }
    bubble.className = 'message-bubble message-bubble-md message-bubble-streaming';
    bubble.innerHTML = renderAssistantMarkdown(fullText);
    scrollMessagesToBottom();
}

function getConversationMessagesForView(conversationId) {
    const state = getConversationState(conversationId);
    if (!state) return [];
    const serverMessages = Array.isArray(state.serverConversation && state.serverConversation.messages)
        ? state.serverConversation.messages
        : [];
    return serverMessages.concat(state.localMessages);
}

function conversationServerMessagesEqual(a, b) {
    const aa = Array.isArray(a) ? a : [];
    const bb = Array.isArray(b) ? b : [];
    if (aa.length !== bb.length) {
        return false;
    }
    for (let i = 0; i < aa.length; i++) {
        if (aa[i].role !== bb[i].role) {
            return false;
        }
        if (String(aa[i].content || '') !== String(bb[i].content || '')) {
            return false;
        }
    }
    return true;
}

function renderConversationView(conversationId, options = {}) {
    const preserveScroll = !!(options && options.preserveScroll);
    const messagesArea = document.getElementById('messagesArea');
    if (!messagesArea) return;
    const messages = getConversationMessagesForView(conversationId);
    let prevScrollHeight = 0;
    let prevScrollTop = 0;
    if (preserveScroll) {
        prevScrollHeight = messagesArea.scrollHeight;
        prevScrollTop = messagesArea.scrollTop;
    }
    messagesArea.innerHTML = '';
    if (!messages.length) {
        messagesArea.innerHTML = renderWelcomeState();
        return;
    }
    const state = getConversationState(conversationId);
    const showOlderHint = !!(
        state
        && state.serverConversation
        && state.serverConversation.has_more_older
        && state.loadingOlderMessages
    );
    if (showOlderHint) {
        const hint = document.createElement('div');
        hint.className = 'messages-area-older-loading';
        hint.setAttribute('role', 'status');
        hint.textContent = '加载更早消息…';
        messagesArea.appendChild(hint);
    }
    messages.forEach(msg => {
        addMessageToUI(msg.role, msg.content, {
            pending: !!msg.pending,
            requestId: msg.requestId || undefined,
            skipScroll: true
        });
    });
    if (preserveScroll) {
        const delta = messagesArea.scrollHeight - prevScrollHeight;
        messagesArea.scrollTop = Math.max(0, prevScrollTop + delta);
    } else {
        scrollMessagesToBottom();
    }
}

// Initialize app
document.addEventListener('DOMContentLoaded', async function() {
    console.log('🚀 AI Assistant initialized');
    try {
        if (typeof marked !== 'undefined' && typeof marked.setOptions === 'function') {
            marked.setOptions({ gfm: true, breaks: true, async: false });
        }
        if (
            typeof marked !== 'undefined' &&
            typeof marked.use === 'function' &&
            typeof markedKatex === 'function' &&
            typeof katex !== 'undefined' &&
            typeof katex.renderToString === 'function'
        ) {
            marked.use(
                markedKatex({
                    throwOnError: false,
                    strict: 'ignore',
                    nonStandard: true
                })
            );
        }
    } catch (e) {
        console.warn('marked init skipped:', e);
    }
    const sb = document.getElementById('sendBtn');
    if (sb) {
        sendBtnDefaultHtml = sb.innerHTML;
    }
    await initAuth();
    initTheme();
    initSidebarState();
    syncSendBtn();
    setupEventListeners();
    if (!isAuthGateActive()) {
        loadSources();
        loadConversations();
    }
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

function renderMessagesLoadingPlaceholder() {
    return '<div class="messages-area-loading" role="status">加载会话…</div>';
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

/** marked v12+ 在部分配置下会返回 Promise；强制同步并打开 GFM，避免回退成纯文本导致 ## 原样显示 */
const MARKED_PARSE_OPTS = { async: false, gfm: true, breaks: true };

/**
 * 将 API 返回的 content 规范为「字符串」或「多段数组」。
 * JSONB/序列化偶发把 [{type,text},{type:image}] 落成一段 JSON 字符串，原先会整段 escape 成乱码。
 */
function coerceMessageContent(raw) {
    if (raw == null) {
        return '';
    }
    if (Array.isArray(raw)) {
        return raw;
    }
    if (typeof raw === 'object') {
        return raw;
    }
    if (typeof raw !== 'string') {
        return String(raw);
    }
    let s = raw.trim();
    for (let attempt = 0; attempt < 2; attempt++) {
        if (!s.startsWith('[{')) {
            break;
        }
        if (!/"(?:type|text|url)"\s*:/.test(s)) {
            break;
        }
        try {
            const parsed = JSON.parse(s);
            if (Array.isArray(parsed)) {
                return parsed;
            }
            if (typeof parsed === 'string') {
                s = parsed.trim();
                continue;
            }
            break;
        } catch (e) {
            break;
        }
    }
    return s;
}

/**
 * 用户气泡/预览：纯文本被存成带首尾 ASCII/弯引号的「JSON 字符串壳」时剥掉（与助手 decode 分开，避免误伤）。
 */
function normalizeUserPlainTextForDisplay(raw) {
    if (raw == null || typeof raw !== 'string') {
        return raw;
    }
    let cur = raw.trim().replace(/^\uFEFF/, '');
    if (!cur) {
        return '';
    }
    for (let i = 0; i < 4; i++) {
        if (cur.length < 2) {
            break;
        }
        const a = cur[0];
        const b = cur[cur.length - 1];
        if (a === '"' && b === '"') {
            try {
                const parsed = JSON.parse(cur);
                if (typeof parsed === 'string') {
                    cur = parsed;
                    continue;
                }
            } catch (e) {
                const inner = cur.slice(1, -1);
                if (inner.indexOf('"') === -1 && inner.indexOf('\\') === -1) {
                    cur = inner;
                    continue;
                }
            }
            break;
        }
        if (a === '\u201c' && b === '\u201d') {
            cur = cur.slice(1, -1);
            continue;
        }
        if (a === '\u2018' && b === '\u2019') {
            cur = cur.slice(1, -1);
            continue;
        }
        break;
    }
    return cur;
}

/**
 * KaTeX MathML / 结构在 DOMPurify 中的白名单（与 renderToString 输出对齐）
 */
const ASSISTANT_KATEX_PURIFY_TAGS = [
    'math', 'semantics', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'mfrac', 'msqrt', 'mroot',
    'mstyle', 'mspace', 'mtext', 'menclose', 'mpadded', 'mtable', 'mtr', 'mtd', 'mlabeledtr',
    'munder', 'mover', 'munderover', 'msubsup', 'annotation', 'none', 'line', 'ms', 'mglyph',
    'maligngroup', 'malignmark', 'mprescripts', 'maction',
    'svg', 'path', 'g', 'defs', 'use', 'polyline', 'polygon', 'clipPath', 'foreignObject'
];

const ASSISTANT_KATEX_PURIFY_ATTR = [
    'class', 'style', 'id', 'title', 'xmlns', 'encoding', 'mathvariant', 'mathsize', 'mathcolor',
    'mathbackground', 'dir', 'scriptlevel', 'displaystyle', 'stretchy', 'symmetric', 'largeop',
    'movablelimits', 'accent', 'accentunder', 'lspace', 'rspace', 'minsize', 'maxsize',
    'width', 'height', 'depth', 'rowspacing', 'columnspacing', 'columnalign', 'rowalign',
    'columnlines', 'rowlines', 'frame', 'framespacing', 'equalrows', 'equalcolumns',
    'bevelled', 'linethickness', 'numalign', 'denomalign', 'scriptminsize', 'side',
    'alignmentscope', 'groupalign', 'href', 'xlink:href', 'viewBox', 'preserveAspectRatio',
    'fill', 'stroke', 'stroke-width', 'd', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'transform',
    'fill-rule', 'clip-path', 'marker-end', 'aria-hidden', 'focusable', 'xmlns:xlink', 'version'
];

function sanitizeAssistantHtml(purify, html) {
    if (!purify || html == null) {
        return html || '';
    }
    return purify.sanitize(html, {
        ADD_TAGS: ASSISTANT_KATEX_PURIFY_TAGS,
        ADD_ATTR: ASSISTANT_KATEX_PURIFY_ATTR,
        ALLOW_DATA_ATTR: true
    });
}

/**
 * 仅剥 JSON 字符串壳（首尾引号 + JSON.parse），不做 \\n/\\t 替换，以免破坏 LaTeX（如 \\text、\\right）。
 */
function decodeAssistantJsonShell(raw) {
    if (raw == null || typeof raw !== 'string') {
        return raw;
    }
    let s = raw.trim().replace(/^\uFEFF/, '');
    if (!s) {
        return raw;
    }
    let cur = s;
    for (let i = 0; i < 4; i++) {
        if (cur.length < 2 || cur[0] !== '"' || cur[cur.length - 1] !== '"') {
            break;
        }
        try {
            const parsed = JSON.parse(cur);
            if (typeof parsed !== 'string') {
                break;
            }
            cur = parsed;
        } catch (e) {
            break;
        }
    }
    return cur;
}

/** 常见「\\n 实为换行」且非 LaTeX 命令前缀（避免误伤 \\neq、\\nabla 等） */
const ASSISTANT_SAFE_N_PREFIX = /^(abla|eq|i\b|u\b|ot|otin|parallel|subseteq|supseteq|subset|supset|rightarrow|leftarrow|Rightarrow|Leftarrow|Leftrightarrow|warrow|earrow|exists|uplus|atural)/;

/**
 * Dify 等返回的字面量 \\r\\n、\\n、\\"；不处理 \\t/\\r，避免 \\text、\\right 被破坏。
 * \\n 在疑似 LaTeX 命令前缀处保留原样。
 */
function assistantDecodeLiteralEscapes(s) {
    if (s == null || typeof s !== 'string' || !/\\[nr"\\]/.test(s)) {
        return s;
    }
    return s
        .replace(/\\r\\n/g, '\n')
        .replace(/\\n/g, (m, offset, str) => {
            const after = str.slice(offset + 2);
            if (ASSISTANT_SAFE_N_PREFIX.test(after)) {
                return m;
            }
            return '\n';
        })
        .replace(/\\"/g, '"');
}

/**
 * 修复整段被二次 JSON 编码、或含字面量 \\n 与首尾引号的助手正文（Dify/部分上游会如此返回）。
 */
function decodeAssistantEscapedContent(raw) {
    const s = decodeAssistantJsonShell(raw);
    return assistantDecodeLiteralEscapes(s);
}

function countBackticksAt(text, pos) {
    let c = 0;
    const n = text.length;
    while (pos + c < n && text[pos + c] === '`') {
        c++;
    }
    return c;
}

function isAssistantLineStart(text, pos) {
    if (pos === 0) {
        return true;
    }
    const ch = text[pos - 1];
    return ch === '\n' || ch === '\r';
}

/** marked-katex-extension 不识别 \\[ \\] \\( \\) ；在 parse 前提取，避免 \\t、\\[ 被 Markdown 当转义吃掉 */
function makeBracketMathPlaceholder(id) {
    return '\u2060\uFFFA' + String(id) + '\uFFFA\u2060';
}

function katexRenderBracket(tex, displayMode) {
    if (typeof katex === 'undefined' || typeof katex.renderToString !== 'function') {
        const s = document.createElement('span');
        s.className = 'math-fallback' + (displayMode ? ' math-fallback--display' : '');
        s.textContent = (displayMode ? '\\[' : '\\(') + tex + (displayMode ? '\\]' : '\\)');
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
 * 在 fenced / 行内代码外提取 \\[…\\]、\\(…\\)；支持流式未闭合。$ / $$ 仍由 marked-katex-extension 处理。
 * @returns {{ md: string, mathEntries: { tex: string, display: boolean }[] }}
 */
function assistantPreExtractBracketMath(text) {
    const mathEntries = [];
    let out = '';
    let i = 0;
    const n = text.length;
    let inFence = false;

    function appendMath(tex, display) {
        mathEntries.push({ tex, display });
        out += makeBracketMathPlaceholder(mathEntries.length - 1);
    }

    while (i < n) {
        if (inFence) {
            if (isAssistantLineStart(text, i)) {
                const bc = countBackticksAt(text, i);
                if (bc >= 3) {
                    out += text.slice(i, i + bc);
                    i += bc;
                    while (i < n && text[i] !== '\n') {
                        out += text[i];
                        i++;
                    }
                    if (i < n) {
                        out += text[i];
                        i++;
                    }
                    inFence = false;
                    continue;
                }
            }
            out += text[i];
            i++;
            continue;
        }

        if (isAssistantLineStart(text, i)) {
            const bc = countBackticksAt(text, i);
            if (bc >= 3) {
                out += text.slice(i, i + bc);
                i += bc;
                while (i < n && text[i] !== '\n') {
                    out += text[i];
                    i++;
                }
                if (i < n) {
                    out += text[i];
                    i++;
                }
                inFence = true;
                continue;
            }
        }

        if (text[i] === '`') {
            const bc = countBackticksAt(text, i);
            if (bc === 1) {
                out += '`';
                i++;
                while (i < n && text[i] !== '`' && text[i] !== '\n') {
                    out += text[i];
                    i++;
                }
                if (i < n && text[i] === '`') {
                    out += '`';
                    i++;
                }
                continue;
            }
            if (bc >= 3) {
                out += text.slice(i, i + bc);
                i += bc;
                while (i < n && text[i] !== '\n') {
                    out += text[i];
                    i++;
                }
                if (i < n) {
                    out += text[i];
                    i++;
                }
                inFence = true;
                continue;
            }
            if (bc === 2) {
                out += '``';
                i += 2;
                continue;
            }
        }

        if (text[i] === '\\' && i + 1 < n && text[i + 1] === '[') {
            const innerStart = i + 2;
            const close = text.indexOf('\\]', innerStart);
            if (close === -1) {
                appendMath(text.slice(innerStart), true);
                i = n;
            } else {
                appendMath(text.slice(innerStart, close), true);
                i = close + 2;
            }
            continue;
        }

        if (text[i] === '\\' && i + 1 < n && text[i + 1] === '(') {
            const innerStart = i + 2;
            const close = text.indexOf('\\)', innerStart);
            if (close === -1) {
                appendMath(text.slice(innerStart), false);
                i = n;
            } else {
                appendMath(text.slice(innerStart, close), false);
                i = close + 2;
            }
            continue;
        }

        out += text[i];
        i++;
    }

    return { md: out, mathEntries };
}

function assistantRestoreBracketMathPlaceholders(html, mathEntries) {
    let result = String(html);
    for (let idx = 0; idx < mathEntries.length; idx++) {
        const ph = makeBracketMathPlaceholder(idx);
        const { tex, display } = mathEntries[idx];
        const piece = katexRenderBracket(tex.trim(), display);
        const parts = result.split(ph);
        if (parts.length > 1) {
            result = parts.join(piece);
        }
    }
    return result;
}

/**
 * 助手消息：JSON 解壳 → 提取 \\[\\]\\(\\) 公式 → 字面量 \\n 解码 → marked（含 katex 扩展）→ 回填公式 → DOMPurify。
 */
function renderAssistantMarkdown(text) {
    if (text == null || text === '') return '';
    if (typeof text !== 'string') {
        const d = document.createElement('div');
        d.textContent = String(text);
        return d.innerHTML;
    }
    let raw = decodeAssistantJsonShell(text);
    const bracket = assistantPreExtractBracketMath(raw);
    raw = assistantDecodeLiteralEscapes(bracket.md);
    const purify = getPurify();
    if (typeof marked === 'undefined' || typeof marked.parse !== 'function' || !purify) {
        const d = document.createElement('div');
        d.textContent = raw;
        return d.innerHTML;
    }
    try {
        let html = marked.parse(raw, MARKED_PARSE_OPTS);
        if (html && typeof html.then === 'function') {
            console.warn('marked returned Promise; use plain text fallback');
            const d = document.createElement('div');
            d.textContent = raw;
            return d.innerHTML;
        }
        html = assistantRestoreBracketMathPlaceholders(String(html), bracket.mathEntries);
        return sanitizeAssistantHtml(purify, html);
    } catch (e) {
        console.error('Markdown render failed:', e);
        const d = document.createElement('div');
        d.textContent = raw;
        return d.innerHTML;
    }
}

function syncSendBtn() {
    const btn = document.getElementById('sendBtn');
    if (!btn) return;
    if (getCurrentPendingCount() > 0) {
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
    const state = getCurrentConversationState();
    if (state && state.abortController) {
        state.abortController.abort();
    }
}

// Setup event listeners
function setupEventListeners() {
    const messageInput = document.getElementById('messageInput');
    const sidebar = document.querySelector('.sidebar');
    const shellRail = document.querySelector('.shell-rail');
    const appContainer = document.querySelector('.app-container');
    const sourceLockHit = document.getElementById('sourceSelectLockHitbox');
    if (sourceLockHit) {
        sourceLockHit.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            showSourceLockHint();
        });
    }

    messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.max(44, Math.min(this.scrollHeight, 140)) + 'px';
    });

    messageInput.addEventListener('paste', function(event) {
        const items = Array.from((event.clipboardData && event.clipboardData.items) || []);
        const imageFiles = items
            .filter(item => item && item.kind === 'file' && item.type && item.type.startsWith('image/'))
            .map(item => item.getAsFile())
            .filter(Boolean);

        if (!imageFiles.length) {
            return;
        }

        event.preventDefault();
        queueUploadedFiles(imageFiles, { fromClipboard: true });
    });

    document.addEventListener('click', function(event) {
        if (!isMobileViewport() || !isSidebarOpen() || !sidebar) {
            return;
        }
        if (
            sidebar.contains(event.target)
            || (shellRail && shellRail.contains(event.target))
        ) {
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

    const messagesArea = document.getElementById('messagesArea');
    if (messagesArea) {
        messagesArea.addEventListener(
            'scroll',
            () => {
                if (!currentConversationId) {
                    return;
                }
                if (messagesArea.scrollTop > 120) {
                    return;
                }
                const st = getConversationState(currentConversationId);
                if (!st || !st.serverConversation || st.loadingOlderMessages) {
                    return;
                }
                if (!st.serverConversation.has_more_older) {
                    return;
                }
                loadOlderConversationMessages(currentConversationId);
            },
            { passive: true }
        );
    }
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
    updateCurrentSourceTitle();
}

function setSourceLocked(locked) {
    const select = document.getElementById('sourceSelect');
    const hit = document.getElementById('sourceSelectLockHitbox');
    if (select) {
        select.disabled = !!locked;
        select.title = locked ? '左上角新建聊天即可切换知识库' : '请选择知识库';
    }
    if (hit) {
        hit.hidden = !(locked && availableSources.length > 0);
    }
}

function showSourceLockHint() {
    let el = document.getElementById('sourceLockToast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'sourceLockToast';
        el.className = 'source-lock-toast';
        el.setAttribute('role', 'status');
        document.body.appendChild(el);
    }
    el.textContent = '左上角新建聊天即可切换知识库';
    const wrap = document.getElementById('sourceSelectWrap');
    const gap = 25;
    if (wrap) {
        const r = wrap.getBoundingClientRect();
        el.style.left = `${Math.round(r.left + r.width / 2)}px`;
        el.style.top = 'auto';
        el.style.bottom = `${Math.round(window.innerHeight - r.top + gap)}px`;
        el.style.transform = 'translateX(-50%)';
    } else {
        el.style.left = '50%';
        el.style.top = 'auto';
        el.style.bottom = '160px';
        el.style.transform = 'translateX(-50%)';
    }
    el.hidden = false;
    clearTimeout(showSourceLockHint._t);
    showSourceLockHint._t = setTimeout(() => {
        el.hidden = true;
    }, 2800);
}

function renderSourceOptions() {
    const select = document.getElementById('sourceSelect');
    if (!select) return;
    if (!availableSources.length) {
        select.innerHTML = '<option value="">无知识库</option>';
        select.disabled = true;
        const hitEmpty = document.getElementById('sourceSelectLockHitbox');
        if (hitEmpty) hitEmpty.hidden = true;
        return;
    }

    const placeholder = '<option value="">请选择知识库</option>';
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
    updateCurrentSourceTitle();
}

async function loadSources() {
    try {
        const response = await fetch('/api/sources', { credentials: 'same-origin' });
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
    syncNewChatDefaultSource({ force: false });
}

async function ensureSessionReady() {
    if (currentConversationId) return true;
    if (oauthConfigured && !authUser) {
        addMessageToUI('assistant', '❌ 请先使用侧栏的 Google 登录后再开始对话。');
        return false;
    }
    if (!selectedSourceId) {
        addMessageToUI('assistant', '❌ 请先选择知识库再开始对话。');
        return false;
    }
    try {
        const payload = { source_id: selectedSourceId };
        const response = await fetch('/api/sessions', withAjaxHeaders({
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload)
        }));
        const data = await response.json();
        if (!response.ok || !data.success) {
            addMessageToUI('assistant', '❌ 创建会话失败：' + (data.error || ('HTTP ' + response.status)));
            return false;
        }
        currentConversationId = data.session_id || data.conversation_id;
        selectedSourceId = data.source_id || selectedSourceId;
        setConversationServerData(currentConversationId, {
            created_at: new Date().toISOString(),
            messages: [],
            source_id: data.source_id || '',
            source_name: data.source_name || ''
        });
        setSourceLocked(true);
        syncSendBtn();
        loadConversations();
        updateCurrentSourceTitle();
        return true;
    } catch (error) {
        addMessageToUI('assistant', '❌ 创建会话失败：' + (error && error.message ? error.message : String(error)));
        return false;
    }
}

let loadConversationsInFlight = null;

/** 用于侧栏排序：取会话「最后活跃」时间（最后一条消息时间与创建时间的较大值） */
function conversationActivityTimeMs(conv) {
    if (!conv || typeof conv !== 'object') return 0;
    let t = 0;
    if (conv.created_at) {
        const c = Date.parse(conv.created_at);
        if (!Number.isNaN(c)) t = Math.max(t, c);
    }
    const lm = conv.last_message;
    if (lm && typeof lm === 'object' && lm.timestamp) {
        const m = Date.parse(lm.timestamp);
        if (!Number.isNaN(m)) t = Math.max(t, m);
    }
    return t;
}

function pickDefaultSourceIdForNewChat() {
    const sid = (defaultSourceIdForNewChat || '').trim();
    if (sid && availableSources.some(s => s && s.id === sid)) return sid;
    return '';
}

/**
 * 无当前会话时同步知识库下拉框。
 * @param {{ force?: boolean }} opts force 为 true 时（新建聊天）始终采用最近一次会话的知识库；否则仅在尚未选择时填充。
 */
function syncNewChatDefaultSource(opts = {}) {
    if (currentConversationId) return;
    if (opts.force || !selectedSourceId) {
        selectedSourceId = pickDefaultSourceIdForNewChat();
    }
    renderSourceOptions();
    updateCurrentSourceTitle();
}

// Load conversations from backend（合并并发请求，避免短时重复打满限流）
async function loadConversations() {
    if (loadConversationsInFlight) {
        return loadConversationsInFlight;
    }
    loadConversationsInFlight = (async () => {
        try {
            if (oauthConfigured && !authUser) {
                const container = document.getElementById('conversationsList');
                if (container) {
                    container.innerHTML = '';
                }
                renderSidebarStatus('登录后查看会话');
                defaultSourceIdForNewChat = '';
                syncNewChatDefaultSource({ force: false });
                return;
            }
            renderSidebarStatus('');
            const response = await fetch('/api/conversations', {
                credentials: 'same-origin'
            });
            if (response.status === 429) {
                renderSidebarStatus('请求过于频繁，请稍后再试');
                return;
            }
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            const container = document.getElementById('conversationsList');

            if (data.conversations && Object.keys(data.conversations).length > 0) {
                container.innerHTML = '';
                const entries = Object.entries(data.conversations);
                entries.sort(
                    (a, b) => conversationActivityTimeMs(b[1]) - conversationActivityTimeMs(a[1])
                );
                const topConv = entries[0] && entries[0][1];
                defaultSourceIdForNewChat = topConv && topConv.source_id
                    ? String(topConv.source_id).trim()
                    : '';
                entries.forEach(([id, conv]) => {
                    const state = getConversationState(id);
                    if (state && state.serverConversation) {
                        state.serverConversation = {
                            ...state.serverConversation,
                            created_at: conv.created_at,
                            source_id: conv.source_id || '',
                            source_name: conv.source_name || '',
                            dify_title: typeof conv.dify_title === 'string'
                                ? conv.dify_title.trim()
                                : ''
                        };
                    }
                    container.appendChild(createConversationItem(id, conv));
                });
            } else {
                container.innerHTML = '<div class="empty-state">还没有会话</div>';
                defaultSourceIdForNewChat = '';
            }
            syncNewChatDefaultSource({ force: false });
        } catch (error) {
            console.error('Error loading conversations:', error);
        } finally {
            loadConversationsInFlight = null;
        }
    })();
    return loadConversationsInFlight;
}

function createConversationItem(id, conv) {
    const div = document.createElement('div');
    div.className = 'conversation-item';
    div.dataset.convId = id;
    if (id === currentConversationId) div.classList.add('active');

    const difyTitle = conv && typeof conv.dify_title === 'string'
        ? conv.dify_title.trim()
        : '';

    const label = document.createElement('span');
    label.className = 'conversation-item-label';
    label.textContent = difyTitle || '新对话';

    const delBtn = document.createElement('button');
    delBtn.className = 'delete-btn';
    delBtn.textContent = '×';
    delBtn.addEventListener('click', (e) => deleteConversation(id, e));

    div.addEventListener('click', () => switchConversation(id));
    div.appendChild(label);
    div.appendChild(delBtn);
    return div;
}

// Start new chat
function startNewChat() {
    setCurrentConversation(null);
    uploadedFiles = [];
    resetComposer();
    syncNewChatDefaultSource({ force: true });
    setSourceLocked(false);

    const messagesArea = document.getElementById('messagesArea');
    messagesArea.innerHTML = renderWelcomeState();
    closeSidebar();
    loadConversations();
}

async function loadOlderConversationMessages(conversationId) {
    const state = getConversationState(conversationId);
    if (!state || !state.serverConversation) {
        return;
    }
    if (!state.serverConversation.has_more_older || state.loadingOlderMessages) {
        return;
    }
    const beforeId = getOldestServerMessageId(conversationId);
    if (beforeId == null) {
        return;
    }

    state.loadingOlderMessages = true;
    if (currentConversationId === conversationId) {
        renderConversationView(conversationId, { preserveScroll: true });
    }
    try {
        const response = await fetch(
            conversationDetailUrl(conversationId, {
                messageLimit: CONVERSATION_MESSAGE_PAGE_SIZE,
                beforeMessageId: beforeId
            }),
            { credentials: 'same-origin' }
        );
        if (!response.ok) {
            return;
        }
        const data = await response.json();
        if (!data.success) {
            return;
        }
        const batch = Array.isArray(data.messages) ? data.messages : [];
        state.serverConversation.messages = mergeOlderServerMessages(
            state.serverConversation.messages,
            batch
        );
        state.serverConversation.has_more_older = !!data.has_more_older;
        if (typeof data.message_count_total === 'number') {
            state.serverConversation.message_count_total = data.message_count_total;
        }
    } catch (e) {
        console.error('loadOlderConversationMessages:', e);
    } finally {
        state.loadingOlderMessages = false;
        if (currentConversationId === conversationId) {
            renderConversationView(conversationId, { preserveScroll: true });
        }
    }
}

// Switch conversation
async function switchConversation(conversationId) {
    setCurrentConversation(conversationId);
    selectedSourceId = getConversationSourceId(conversationId) || selectedSourceId;
    renderSourceOptions();
    setSourceLocked(true);
    closeSidebar();

    const state = getConversationState(conversationId);
    if (state) {
        state.loadingOlderMessages = false;
        if (state.serverConversation) {
            state.serverConversation = {
                ...state.serverConversation,
                messages: [],
                has_more_older: false,
                message_count_total: null
            };
        }
    }

    const hadRenderableMessages = getConversationMessagesForView(conversationId).length > 0;
    const messagesArea = document.getElementById('messagesArea');
    if (hadRenderableMessages) {
        renderConversationView(conversationId);
    } else if (messagesArea) {
        messagesArea.innerHTML = renderMessagesLoadingPlaceholder();
    }

    try {
        const response = await fetch(
            conversationDetailUrl(conversationId, { messageLimit: CONVERSATION_MESSAGE_PAGE_SIZE }),
            { credentials: 'same-origin' }
        );
        if (!response.ok) {
            console.error('Error loading conversation: HTTP', response.status);
            renderConversationView(conversationId);
            return;
        }
        const data = await response.json();
        if (!data.success) {
            console.error('Error loading conversation:', data.error);
            renderConversationView(conversationId);
            return;
        }

        const prevServerMessages = state && state.serverConversation
            && Array.isArray(state.serverConversation.messages)
            ? state.serverConversation.messages
            : null;

        setConversationServerData(conversationId, data);
        uploadedFiles = [];
        resetComposer();
        if (data.source_id) {
            selectedSourceId = data.source_id;
            renderSourceOptions();
        }
        setSourceLocked(true);

        const nextMessages = Array.isArray(data.messages) ? data.messages : [];
        const serverChanged = !conversationServerMessagesEqual(prevServerMessages, nextMessages);
        const hasLocalTail = state && Array.isArray(state.localMessages) && state.localMessages.length > 0;
        if (!hadRenderableMessages || serverChanged || hasLocalTail) {
            renderConversationView(conversationId);
        }
        updateCurrentSourceTitle();
    } catch (error) {
        console.error('Error loading conversation:', error);
        renderConversationView(conversationId);
    }
}

function getConversationSourceId(conversationId) {
    const state = getConversationState(conversationId);
    if (!state || !state.serverConversation) {
        return '';
    }
    return state.serverConversation.source_id || '';
}

function getSourceDisplayName(sourceId) {
    if (!sourceId) {
        return '';
    }
    const s = availableSources.find(x => x && x.id === sourceId);
    return s ? String(s.name || s.id) : sourceId;
}

function updateCurrentSourceTitle() {
    const el = document.getElementById('currentSourceTitle');
    if (!el) {
        return;
    }
    let label = '请选择知识库';
    if (currentConversationId) {
        const sid = getConversationSourceId(currentConversationId);
        if (sid) {
            label = getSourceDisplayName(sid);
        }
    } else if (selectedSourceId) {
        label = getSourceDisplayName(selectedSourceId);
    }
    el.textContent = label;
}

// Delete conversation
async function deleteConversation(conversationId, event) {
    event.stopPropagation();
    
    if (!confirm('确定要删除这条会话吗？')) {
        return;
    }
    
    try {
        const response = await fetch(
            conversationDetailUrl(conversationId),
            withAjaxHeaders({ method: 'DELETE', credentials: 'same-origin' })
        );
        
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
    queueUploadedFiles(files);
    if (event.target) {
        event.target.value = '';
    }
}

function queueUploadedFiles(files, options = {}) {
    const fromClipboard = !!options.fromClipboard;
    for (let file of files || []) {
        if (!file) continue;
        if (!file.type || !file.type.startsWith('image/')) {
            continue;
        }
        // Check file size (max 50MB)
        if (file.size > 52428800) {
            alert('File too large. Maximum size is 50MB');
            continue;
        }

        if (uploadedFiles.length >= MAX_UPLOAD_IMAGES) {
            alert(`最多只能上传 ${MAX_UPLOAD_IMAGES} 张图片。`);
            break;
        }

        if (fromClipboard && !file.name) {
            const ext = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
            file = new File([file], `clipboard-image-${Date.now()}.${ext}`, { type: file.type });
        }

        if (file.type && file.type.startsWith('image/')) {
            file._previewUrl = URL.createObjectURL(file);
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

    if (file._previewUrl) {
        const preview = document.createElement('img');
        preview.className = 'file-tag-preview';
        preview.src = file._previewUrl;
        preview.alt = file.name || '上传图片预览';
        tag.appendChild(preview);
    }

    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove-btn';
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', () => removeFileById(fileId));

    tag.appendChild(removeBtn);
    container.appendChild(tag);
}

function revokeQueuedFilePreview(file) {
    if (file && file._previewUrl) {
        URL.revokeObjectURL(file._previewUrl);
        delete file._previewUrl;
    }
}

function revokeBlobUrlsInUserMessageContent(content) {
    if (!content || !Array.isArray(content)) {
        return;
    }
    for (const item of content) {
        if (item && item.type === 'image' && typeof item.url === 'string' && item.url.startsWith('blob:')) {
            URL.revokeObjectURL(item.url);
        }
    }
}

function clearQueuedFiles(options = {}) {
    const revokePreviews = options.revokePreviews !== false;
    if (revokePreviews) {
        uploadedFiles.forEach(revokeQueuedFilePreview);
    }
    uploadedFiles = [];
    const uploadedFilesContainer = document.getElementById('uploadedFiles');
    if (uploadedFilesContainer) {
        uploadedFilesContainer.innerHTML = '';
    }
}

function removeFileById(fileId) {
    const nextFiles = [];
    uploadedFiles.forEach(file => {
        if (file._tagId === fileId) {
            revokeQueuedFilePreview(file);
        } else {
            nextFiles.push(file);
        }
    });
    uploadedFiles = nextFiles;
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

function buildUserMessageContent(message, files) {
    const text = (message || '').trim();
    const imageItems = (files || [])
        .filter(file => file && file.type && file.type.startsWith('image/') && file._previewUrl)
        .map(file => ({
            type: 'image',
            url: file._previewUrl
        }));

    if (!imageItems.length) {
        return text;
    }

    const content = [];
    if (text) {
        content.push({ type: 'text', text });
    }
    return content.concat(imageItems);
}

async function postChatMessage({ message, files, controller, conversationId, sourceId }) {
    const formData = new FormData();
    formData.append('message', message);
    formData.append('conversation_id', conversationId || '');
    formData.append('source_id', sourceId || '');

    (files || []).forEach(file => {
        formData.append('files', file);
    });

    return fetch('/api/chat', withAjaxHeaders({
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
        signal: controller.signal
    }));
}

function tryParseJsonResponse(rawText, status) {
    try {
        return { ok: true, data: rawText ? JSON.parse(rawText) : {} };
    } catch (e) {
        const hint = rawText && rawText.trim().startsWith('<')
            ? '（上游返回了 HTML，多为反向代理/Nginx 超时或 502）'
            : '';
        const snippet = rawText ? rawText.slice(0, 280).replace(/\s+/g, ' ') : '';
        return {
            ok: false,
            errorMessage: '❌ Error: 响应不是合法 JSON (HTTP ' + status + ') ' + hint + (snippet ? '\n' + snippet : '')
        };
    }
}

/** 合并 SSE 文本片段：兼容增量与「每帧为全文前缀」的累积式上游，避免 += 造成重复。 */
function mergeStreamChunk(current, chunk) {
    if (chunk == null || chunk === '') {
        return current == null ? '' : String(current);
    }
    const c = String(chunk);
    const cur = current == null ? '' : String(current);
    if (c === cur) {
        return cur;
    }
    if (cur && c.startsWith(cur)) {
        return c;
    }
    return cur + c;
}

async function consumeChatSse(response, conversationId, requestId) {
    const reader = response.body && response.body.getReader ? response.body.getReader() : null;
    if (!reader) {
        return { ok: false, detail: '浏览器不支持流式读取' };
    }
    const decoder = new TextDecoder();
    let buffer = '';
    let fullText = '';
    let rafScheduled = false;

    function scheduleRender() {
        if (rafScheduled) return;
        rafScheduled = true;
        requestAnimationFrame(() => {
            rafScheduled = false;
            applyStreamingAssistantUpdate(conversationId, requestId, fullText);
        });
    }

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            let sep;
            while ((sep = buffer.indexOf('\n\n')) >= 0) {
                const block = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                const lines = block.split('\n');
                const dataLine = lines.find(line => line.startsWith('data: '));
                if (!dataLine) {
                    continue;
                }
                let payload;
                try {
                    payload = JSON.parse(dataLine.slice(6).trim());
                } catch (e) {
                    continue;
                }
                if (payload.event === 'delta' && payload.text != null) {
                    fullText = mergeStreamChunk(fullText, payload.text);
                    scheduleRender();
                } else if (payload.event === 'done') {
                    return { ok: true, data: payload, fullText };
                } else if (payload.event === 'error') {
                    return {
                        ok: false,
                        detail: payload.detail || 'Unknown error'
                    };
                }
            }
        }
    } catch (e) {
        if (e && e.name === 'AbortError') {
            return { ok: false, detail: 'aborted', aborted: true };
        }
        return { ok: false, detail: (e && e.message) ? e.message : String(e) };
    }
    return { ok: false, detail: '流结束但未收到完成事件' };
}

async function refreshConversationFromServer(conversationId, options = {}) {
    if (!conversationId) return;
    const skipViewRender = !!(options && options.skipViewRender);
    try {
        const state = getConversationState(conversationId);
        const prevLen = state && state.serverConversation && Array.isArray(state.serverConversation.messages)
            ? state.serverConversation.messages.length
            : 0;
        const lim = Math.min(200, Math.max(CONVERSATION_MESSAGE_PAGE_SIZE, prevLen + 4));
        const response = await fetch(
            conversationDetailUrl(conversationId, { messageLimit: lim }),
            { credentials: 'same-origin' }
        );
        if (!response.ok) {
            return;
        }
        const data = await response.json();
        if (!data.success) {
            return;
        }
        setConversationServerData(conversationId, data);
        if (currentConversationId === conversationId) {
            if (!skipViewRender) {
                renderConversationView(conversationId);
            }
            syncSendBtn();
            updateCurrentSourceTitle();
        }
    } catch (error) {
        console.error('Error refreshing conversation:', error);
    }
}

function failPendingMessage(convId, requestId, errorText) {
    replacePendingAssistantMessage(convId, requestId, errorText);
    if (currentConversationId === convId) renderConversationView(convId);
}

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    const filesToSend = uploadedFiles.slice();
    const outboundMessage = message || (filesToSend.length ? '根据图片回答' : '');

    if (!outboundMessage && !filesToSend.length) {
        return;
    }

    const hasSession = await ensureSessionReady();
    if (!hasSession) {
        return;
    }

    const requestConversationId = currentConversationId;
    const requestSourceId = selectedSourceId || '';
    const state = getConversationState(requestConversationId);
    if (!state || state.pendingCount > 0) {
        return;
    }

    const requestId = `req_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const userMessageContent = buildUserMessageContent(message, filesToSend);
    addLocalMessages(requestConversationId, [
        { requestId, role: 'user', content: userMessageContent, pending: false },
        { requestId, role: 'assistant', content: '', pending: true }
    ]);
    if (currentConversationId === requestConversationId) {
        renderConversationView(requestConversationId);
    }

    input.value = '';
    input.style.height = 'auto';
    input.style.height = '44px';
    clearQueuedFiles({ revokePreviews: false });

    const requestController = new AbortController();
    state.pendingCount += 1;
    state.abortController = requestController;
    syncSendBtn();

    try {
        let activeConvId = requestConversationId;
        let response = await postChatMessage({
            message: outboundMessage,
            files: filesToSend,
            controller: requestController,
            conversationId: activeConvId,
            sourceId: requestSourceId
        });

        async function handleErrorResponse(res, data, parsedOk) {
            if (
                res.status === 404
                && data
                && data.detail === 'Session expired or invalid conversation_id.'
            ) {
                return { renew: true };
            }
            if (!parsedOk) {
                failPendingMessage(activeConvId, requestId, data.errorMessage || 'Invalid response');
                return { done: true };
            }
            const detail = data.detail || data.error || '';
            const errorMsg = (res.status === 409 && data.error === 'source_locked')
                ? '❌ 当前会话已锁定知识库，不能中途切换。请新建对话后再切换。'
                : '❌ Error: HTTP ' + res.status + (detail ? ' — ' + detail : '');
            failPendingMessage(activeConvId, requestId, errorMsg);
            return { done: true };
        }

        async function handleOkResponse(res) {
            const ct = (res.headers.get('Content-Type') || '').toLowerCase();
            if (ct.includes('text/event-stream')) {
                const sse = await consumeChatSse(res, activeConvId, requestId);
                if (sse.aborted) {
                    failPendingMessage(activeConvId, requestId, '已停止生成。');
                    return;
                }
                if (!sse.ok) {
                    failPendingMessage(activeConvId, requestId, '❌ Error: ' + (sse.detail || ''));
                    return;
                }
                const data = sse.data || {};
                if (data.success !== false) {
                    removeLocalMessagesByRequest(activeConvId, requestId);
                    await refreshConversationFromServer(activeConvId, { skipViewRender: true });
                    const streamedBody =
                        (typeof data.response === 'string' && data.response) ||
                        (typeof sse.fullText === 'string' && sse.fullText) ||
                        '';
                    patchLastAssistantMessageContent(activeConvId, streamedBody);
                    if (currentConversationId === activeConvId) {
                        renderConversationView(activeConvId);
                    }
                    if (currentConversationId === activeConvId && data.source_id) {
                        selectedSourceId = data.source_id;
                        renderSourceOptions();
                        setSourceLocked(true);
                    }
                    loadConversations();
                    updateActiveConversation();
                    updateCurrentSourceTitle();
                } else {
                    const detail = data.detail ? ` (${data.detail})` : '';
                    failPendingMessage(
                        activeConvId,
                        requestId,
                        '❌ Error: ' + (data.error || 'Failed to get response') + detail
                    );
                }
                return;
            }
            const rawText = await res.text();
            const parsed = tryParseJsonResponse(rawText, res.status);
            if (!parsed.ok) {
                failPendingMessage(activeConvId, requestId, parsed.errorMessage);
                return;
            }
            const data = parsed.data;
            if (data.success !== false) {
                removeLocalMessagesByRequest(activeConvId, requestId);
                await refreshConversationFromServer(activeConvId);
                if (currentConversationId === activeConvId && data.source_id) {
                    selectedSourceId = data.source_id;
                    renderSourceOptions();
                    setSourceLocked(true);
                }
                loadConversations();
                updateActiveConversation();
                updateCurrentSourceTitle();
            } else {
                const detail = data.detail ? ` (${data.detail})` : '';
                failPendingMessage(
                    activeConvId,
                    requestId,
                    '❌ Error: ' + (data.error || 'Failed to get response') + detail
                );
            }
        }

        if (!response.ok) {
            const rawText = await response.text();
            const parsed = tryParseJsonResponse(rawText, response.status);
            const data = parsed.ok ? parsed.data : parsed;
            const err = await handleErrorResponse(response, data, parsed.ok);
            if (err && err.renew) {
                if (currentConversationId !== requestConversationId) {
                    failPendingMessage(requestConversationId, requestId, '❌ 当前会话已失效，请回到该会话后重试。');
                    return;
                }
                currentConversationId = null;
                setSourceLocked(false);
                const renewedSession = await ensureSessionReady();
                if (!renewedSession) {
                    failPendingMessage(requestConversationId, requestId, '❌ 无法续会话，请重试。');
                    return;
                }
                const renewedConversationId = currentConversationId;
                if (renewedConversationId && renewedConversationId !== requestConversationId) {
                    removeLocalMessagesByRequest(requestConversationId, requestId, {
                        revokeUserBlobUrls: false
                    });
                    activeConvId = renewedConversationId;
                    addLocalMessages(renewedConversationId, [
                        { requestId, role: 'user', content: userMessageContent, pending: false },
                        { requestId, role: 'assistant', content: '', pending: true }
                    ]);
                    if (currentConversationId === renewedConversationId) {
                        renderConversationView(renewedConversationId);
                    }
                }
                response = await postChatMessage({
                    message: outboundMessage,
                    files: filesToSend,
                    controller: requestController,
                    conversationId: activeConvId,
                    sourceId: requestSourceId
                });
                if (!response.ok) {
                    const raw2 = await response.text();
                    const parsed2 = tryParseJsonResponse(raw2, response.status);
                    const data2 = parsed2.ok ? parsed2.data : parsed2;
                    await handleErrorResponse(response, data2, parsed2.ok);
                    return;
                }
                await handleOkResponse(response);
                return;
            }
            return;
        }

        await handleOkResponse(response);
    } catch (error) {
        console.error('Error sending message:', error);
        const errorMsg = (error && error.name === 'AbortError')
            ? '已停止生成。'
            : '❌ Connection error: ' + (error && error.message ? error.message : String(error));
        failPendingMessage(requestConversationId, requestId, errorMsg);
    } finally {
        const activeState = getConversationState(requestConversationId);
        if (activeState) {
            activeState.pendingCount = Math.max(0, activeState.pendingCount - 1);
            if (activeState.abortController === requestController) {
                activeState.abortController = null;
            }
        }
        if (currentConversationId === requestConversationId) {
            syncSendBtn();
        }
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
    const messageInput = document.getElementById('messageInput');
    clearQueuedFiles();
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

    const coerced = coerceMessageContent(content);

    if (role === 'assistant' && !options.pending) {
        bubble.classList.add('message-bubble-md');
    }
    if (options.pending) {
        if (role === 'assistant' && typeof coerced === 'string' && coerced.length > 0) {
            bubble.classList.add('message-bubble-md', 'message-bubble-streaming');
            bubble.innerHTML = renderAssistantMarkdown(coerced);
            return;
        }
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
        if (typeof coerced === 'string') {
            bubble.innerHTML = escapeHtml(normalizeUserPlainTextForDisplay(coerced));
        } else if (Array.isArray(coerced)) {
            const imageItems = coerced.filter(item => item && item.type === 'image');
            coerced.forEach(item => {
                if (item && item.type === 'text' && item.text != null) {
                    const textBlock = document.createElement('div');
                    textBlock.className = 'md-block';
                    textBlock.innerHTML = escapeHtml(
                        normalizeUserPlainTextForDisplay(String(item.text))
                    );
                    bubble.appendChild(textBlock);
                }
            });
            if (imageItems.length) {
                const imageGrid = document.createElement('div');
                imageGrid.className = 'message-image-grid';
                imageItems.forEach(item => {
                    const img = document.createElement('img');
                    img.className = 'message-image message-image--thumb';
                    img.src = item.url || '';
                    img.alt = '';
                    img.referrerPolicy = 'no-referrer';
                    imageGrid.appendChild(img);
                });
                bubble.appendChild(imageGrid);
            }
        }
    } else {
        let assistantText = coerced;
        if (coerced !== null && typeof coerced === 'object' && !Array.isArray(coerced)) {
            assistantText = coerced.answer != null ? coerced.answer : (coerced.text != null ? coerced.text : JSON.stringify(coerced));
        }
        if (typeof assistantText === 'string') {
            bubble.innerHTML = renderAssistantMarkdown(assistantText);
        } else if (Array.isArray(coerced)) {
            coerced.forEach(item => {
                if (!item) {
                    return;
                }
                if (item.type === 'text') {
                    const block = document.createElement('div');
                    block.className = 'md-block';
                    block.innerHTML = renderAssistantMarkdown(item.text || '');
                    bubble.appendChild(block);
                } else if (item.type === 'image') {
                    const img = document.createElement('img');
                    img.className = 'message-image';
                    img.src = item.url || '';
                    img.alt = '';
                    img.referrerPolicy = 'no-referrer';
                    bubble.appendChild(img);
                }
            });
        }
    }
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
    if (options.requestId) {
        message.dataset.requestId = options.requestId;
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
    if (!options.skipScroll) {
        scrollMessagesToBottom();
    }
    return message;
}

// Escape HTML to prevent XSS (safe for text nodes and attribute values; & first avoids double-escaping)
function escapeHtml(text) {
    return String(text ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
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
    const collapseBtn = document.getElementById('sidebarCollapseBtn');
    const mobileOpen = isSidebarOpen();
    const desktopCollapsed = isDesktopSidebarCollapsed();

    if (collapseBtn) {
        if (isMobileViewport()) {
            collapseBtn.title = mobileOpen ? '收起会话列表' : '打开会话列表';
            collapseBtn.setAttribute('aria-label', collapseBtn.title);
        } else {
            collapseBtn.title = desktopCollapsed ? '展开侧边栏' : '收起侧边栏';
            collapseBtn.setAttribute('aria-label', collapseBtn.title);
        }
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
