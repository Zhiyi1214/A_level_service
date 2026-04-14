// Global state
let currentConversationId = null;
let uploadedFiles = [];
let oauthConfigured = false;
let emailAuthConfigured = false;
/** 任一登录方式（Google 或邮箱验证码）已启用 */
let authConfigured = false;
let authUser = null;
let loginBrandRotateTimer = null;
const LOGIN_BRAND_ROTATE_MS = 2400;

function startLoginBrandRotate() {
    const wrap = document.querySelector('.login-screen-brand-rotate');
    if (!wrap || loginBrandRotateTimer) {
        return;
    }
    const items = wrap.querySelectorAll('.login-screen-brand-line');
    if (!items.length) {
        return;
    }
    let idx = 0;
    function applyIndex() {
        items.forEach((el, j) => {
            el.classList.toggle('login-screen-brand-line--visible', j === idx);
        });
    }
    applyIndex();
    loginBrandRotateTimer = window.setInterval(() => {
        idx = (idx + 1) % items.length;
        applyIndex();
    }, LOGIN_BRAND_ROTATE_MS);
}

function stopLoginBrandRotate() {
    if (loginBrandRotateTimer) {
        window.clearInterval(loginBrandRotateTimer);
        loginBrandRotateTimer = null;
    }
    const wrap = document.querySelector('.login-screen-brand-rotate');
    if (wrap) {
        const items = wrap.querySelectorAll('.login-screen-brand-line');
        items.forEach((el, j) => {
            el.classList.toggle('login-screen-brand-line--visible', j === 0);
        });
    }
}
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

/** 同源 API：统一附加 X-Requested-With（见 auth/csrf_guard），禁止对受保护路径裸用 fetch */
function apiFetch(url, options = {}) {
    return fetch(url, withAjaxHeaders({ credentials: 'same-origin', ...options }));
}

const THEME_STORAGE_KEY = 'a_level_theme_v46';
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'a_level_sidebar_collapsed_v46';
const MAX_UPLOAD_IMAGES = 3;
/** 会话详情首次加载与「加载更早」时每页条数 */
const CONVERSATION_MESSAGE_PAGE_SIZE = 8;
/** 会话详情 localStorage 缓存：TTL 内再次点开同一会话可跳过 GET，减轻服务端压力 */
const CONVERSATION_DETAIL_CACHE_STORAGE_KEY = 'a_level_conv_detail_cache_v1';
const CONVERSATION_DETAIL_CACHE_TTL_MS = 5 * 60 * 1000;
const CONVERSATION_DETAIL_CACHE_MAX_ENTRIES = 40;
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

/** 管理台只读查看：与 adminBrowsingUserId 配合，非空时拉取 /api/admin/conversations 并隐藏输入区 */
let adminInspectConversationId = null;
let adminBrowsingUserId = null;
let adminBrowsingUserEmail = '';

/** 单条会话 API（支持 message_limit / before_message_id 分页）；匿名身份由服务端 Cookie session 绑定，勿传 user_id */
function conversationDetailUrl(conversationId, query = {}) {
    const useAdmin =
        adminInspectConversationId && conversationId === adminInspectConversationId;
    const base = useAdmin
        ? `/api/admin/conversations/${encodeURIComponent(conversationId)}`
        : `/api/conversations/${encodeURIComponent(conversationId)}`;
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
        if (authStatus === 'ok' && authConfigured && !authUser) {
            await new Promise(resolve => window.setTimeout(resolve, 250));
            await refreshAuthState();
        }
    } catch (e) {
        console.warn('initAuth failed:', e);
        oauthConfigured = false;
        emailAuthConfigured = false;
        authConfigured = false;
        authUser = null;
    }
    applyAuthView(authStatus);
    applyLoginProvidersVisibility();
    initLoginEmailBlock();
    renderAuthPanel();
    consumeOAuthReturnStatus(authStatus);
}

function initLoginEmailBlock() {
    const sendBtn = document.getElementById('loginEmailSendBtn');
    const verifyBtn = document.getElementById('loginEmailVerifyBtn');
    if (sendBtn && !sendBtn.dataset.wired) {
        sendBtn.dataset.wired = '1';
        sendBtn.addEventListener('click', requestEmailLoginCode);
    }
    if (verifyBtn && !verifyBtn.dataset.wired) {
        verifyBtn.dataset.wired = '1';
        verifyBtn.addEventListener('click', verifyEmailLogin);
    }
}

async function requestEmailLoginCode() {
    const emailInput = document.getElementById('loginEmailInput');
    const msg = document.getElementById('loginEmailMessage');
    const email = emailInput ? (emailInput.value || '').trim() : '';
    if (!email) {
        if (msg) {
            msg.textContent = '请输入邮箱';
            msg.classList.remove('login-screen-message--hidden');
        }
        return;
    }
    if (msg) {
        msg.classList.add('login-screen-message--hidden');
    }
    try {
        const response = await apiFetch('/api/auth/email/request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (msg) {
                msg.textContent = data.error === 'invalid email' ? '邮箱格式不正确' : '发送失败，请稍后重试';
                msg.classList.remove('login-screen-message--hidden');
            }
            return;
        }
        if (msg) {
            msg.textContent = '验证码已发送至邮箱，请查收';
            msg.classList.remove('login-screen-message--hidden');
        }
    } catch (e) {
        if (msg) {
            msg.textContent = '发送失败，请检查网络';
            msg.classList.remove('login-screen-message--hidden');
        }
    }
}

async function verifyEmailLogin() {
    const emailInput = document.getElementById('loginEmailInput');
    const codeInput = document.getElementById('loginEmailCodeInput');
    const msg = document.getElementById('loginEmailMessage');
    const email = emailInput ? (emailInput.value || '').trim() : '';
    const code = codeInput ? (codeInput.value || '').trim() : '';
    if (!email || !code) {
        if (msg) {
            msg.textContent = '请输入邮箱与 6 位验证码';
            msg.classList.remove('login-screen-message--hidden');
        }
        return;
    }
    try {
        const response = await apiFetch('/api/auth/email/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, code })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (msg) {
                msg.textContent = response.status === 401 ? '验证码错误或已过期' : '登录失败，请重试';
                msg.classList.remove('login-screen-message--hidden');
            }
            return;
        }
        if (msg) {
            msg.classList.add('login-screen-message--hidden');
            msg.textContent = '';
        }
        await refreshAuthState();
        applyAuthView('');
        renderAuthPanel();
        if (data.redirect && typeof data.redirect === 'string') {
            window.location.href = data.redirect;
            return;
        }
        loadConversations();
    } catch (e) {
        if (msg) {
            msg.textContent = '登录失败，请检查网络';
            msg.classList.remove('login-screen-message--hidden');
        }
    }
}

function enforceNonAdminAdminUiClosed(me) {
    if (me && me.is_admin) {
        return;
    }
    const dock = document.getElementById('adminSidebarDock');
    if (dock) {
        dock.hidden = true;
    }
    const app = document.querySelector('.app-container');
    if (app) {
        app.classList.remove('mobile-admin-open', 'admin-right-collapsed');
    }
    const needReset = !!(adminInspectConversationId || adminBrowsingUserId);
    exitAdminBrowseMode();
    if (needReset) {
        startNewChat();
    }
}

async function refreshAuthState() {
    const response = await apiFetch('/api/me', {
        cache: 'no-store',
        headers: {
            'Cache-Control': 'no-cache'
        }
    });
    let data = {};
    try {
        data = await response.json();
    } catch (e) {
        data = {};
    }
    oauthConfigured = !!data.oauth_configured;
    emailAuthConfigured = !!data.email_auth_configured;
    authConfigured = !!data.auth_configured;
    if (data.auth_configured === undefined) {
        authConfigured = oauthConfigured || emailAuthConfigured;
    }
    authUser = data.authenticated && data.user ? data.user : null;
    updateFooterAdminLink(data);
    enforceNonAdminAdminUiClosed(data);
}

function updateFooterAdminLink(data) {
    const el = document.getElementById('footerAdminLink');
    if (!el) {
        return;
    }
    const on = !!(data && data.show_admin_link);
    el.hidden = !on;
}

function consumeAdminDeepLink() {
    try {
        const params = new URLSearchParams(window.location.search);
        if (params.get('admin') !== '1') {
            return;
        }
        params.delete('admin');
        const q = params.toString();
        window.history.replaceState(
            {},
            '',
            window.location.pathname + (q ? `?${q}` : '') + window.location.hash
        );
    } catch (e) {
        /* ignore */
    }
}

function bindFooterAdminLink() {
    const el = document.getElementById('footerAdminLink');
    if (!el || el.dataset.adminWired === '1') {
        return;
    }
    el.dataset.adminWired = '1';
    el.addEventListener('click', (ev) => {
        ev.preventDefault();
        toggleAdminWorkspace();
    });
}

function toggleAdminWorkspace() {
    const dock = document.getElementById('adminSidebarDock');
    if (!dock) {
        return;
    }
    if (!dock.hidden) {
        closeAdminWorkspace();
        return;
    }
    void (async () => {
        try {
            if (!authConfigured) {
                await refreshAuthState();
                let meR = await apiFetch('/api/me', { cache: 'no-store' });
                let me = await meR.json();
                if (!me.is_admin) {
                    const input = document.getElementById('messageInput');
                    const secret = input ? (input.value || '').trim() : '';
                    if (!secret) {
                        return;
                    }
                    const lr = await apiFetch('/api/admin/login', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ secret })
                    });
                    if (!lr.ok) {
                        return;
                    }
                    if (input) {
                        input.value = '';
                        input.style.height = '44px';
                    }
                    syncSendBtn();
                    await refreshAuthState();
                    meR = await apiFetch('/api/me', { cache: 'no-store' });
                    me = await meR.json();
                    if (!me.is_admin) {
                        return;
                    }
                }
                dock.hidden = false;
                const app = document.querySelector('.app-container');
                if (app) {
                    if (isMobileViewport()) {
                        app.classList.add('mobile-admin-open');
                    } else {
                        app.classList.remove('mobile-admin-open');
                    }
                }
                switchAdminTab('overview');
                loadAdminOverviewMetrics();
                return;
            }
            const r = await apiFetch('/api/me', { cache: 'no-store' });
            const me = await r.json();
            if (!me.is_admin) {
                window.location.href = '/admin';
                return;
            }
            dock.hidden = false;
            const app = document.querySelector('.app-container');
            if (app) {
                if (isMobileViewport()) {
                    app.classList.add('mobile-admin-open');
                } else {
                    app.classList.remove('mobile-admin-open');
                }
            }
            switchAdminTab('overview');
            loadAdminOverviewMetrics();
        } catch (e) {
            if (authConfigured) {
                window.location.href = '/admin';
            }
        }
    })();
}

function closeAdminWorkspace() {
    const dock = document.getElementById('adminSidebarDock');
    if (dock) {
        dock.hidden = true;
    }
    const app = document.querySelector('.app-container');
    if (app) {
        app.classList.remove('mobile-admin-open', 'admin-right-collapsed');
    }
    const needReset = !!(adminInspectConversationId || adminBrowsingUserId);
    exitAdminBrowseMode();
    if (needReset) {
        startNewChat();
    }
}

function toggleAdminRightPanelCollapsed() {
    if (isMobileViewport()) {
        return;
    }
    const app = document.querySelector('.app-container');
    if (!app) {
        return;
    }
    app.classList.toggle('admin-right-collapsed');
}

function switchAdminTab(tab) {
    const overview = document.getElementById('adminPanelOverview');
    const users = document.getElementById('adminPanelUsers');
    const bOverview = document.getElementById('adminTabBtnOverview');
    const bUsers = document.getElementById('adminTabBtnUsers');
    const onOverview = tab === 'overview';
    if (overview) overview.hidden = !onOverview;
    if (users) users.hidden = onOverview;
    if (bOverview) bOverview.classList.toggle('admin-sidebar-tab--active', onOverview);
    if (bUsers) bUsers.classList.toggle('admin-sidebar-tab--active', !onOverview);
    if (!onOverview) {
        loadAdminUsersList();
    }
}

async function loadAdminOverviewMetrics() {
    const grid = document.getElementById('adminMetricsGrid');
    const pre = document.getElementById('adminMetricsApp');
    const secretBtn = document.getElementById('adminSecretLogoutBtn');
    if (grid) {
        grid.innerHTML = '<span class="admin-loading-text">加载中…</span>';
    }
    if (pre) {
        pre.textContent = '';
    }
    try {
        const meR = await apiFetch('/api/me', { cache: 'no-store' });
        const me = await meR.json();
        if (secretBtn) {
            secretBtn.hidden = !me.secret_login_available;
        }
        const r = await apiFetch('/api/admin/metrics', { cache: 'no-store' });
        if (!r.ok) {
            if (grid) grid.innerHTML = '<span class="admin-loading-text">无权限或加载失败</span>';
            return;
        }
        const payload = await r.json();
        const db = payload.database || {};
        const items = [
            ['用户数', db.total_users],
            ['会话总数', db.total_conversations],
            ['消息总数', db.total_messages],
            ['会话涉及用户数', db.distinct_conversation_user_ids],
            ['未过期邮箱验证码', db.pending_email_login_challenges]
        ];
        if (grid) {
            grid.innerHTML = items.map(([k, v]) =>
                `<div class="admin-metric"><div class="admin-metric-k">${k}</div><div class="admin-metric-v">${v}</div></div>`
            ).join('');
        }
        if (pre) {
            const app = payload.app || {};
            const prov = db.user_identities_by_provider || {};
            pre.textContent = `${JSON.stringify(app, null, 2)}\n\n身份来源计数：\n${JSON.stringify(prov, null, 2)}`;
        }
    } catch (e) {
        console.warn('loadAdminOverviewMetrics', e);
        if (grid) grid.innerHTML = '<span class="admin-loading-text">加载失败</span>';
    }
}

function getAdminUserSortValue() {
    const sel = document.getElementById('adminUserSortSelect');
    const v = sel && sel.value ? String(sel.value).trim() : '';
    const allowed = new Set([
        'recent_activity',
        'message_volume',
        'conversation_count',
        'signup',
        'email'
    ]);
    return allowed.has(v) ? v : 'recent_activity';
}

async function loadAdminUsersList() {
    const input = document.getElementById('adminUserSearchInput');
    const box = document.getElementById('adminUserResults');
    const q = input ? (input.value || '').trim() : '';
    const sort = getAdminUserSortValue();
    if (!box) {
        return;
    }
    box.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
        const r = await apiFetch(
            `/api/admin/users?${new URLSearchParams({ q, sort, limit: '80' })}`,
            { cache: 'no-store' }
        );
        if (!r.ok) {
            box.innerHTML = '<div class="empty-state">请求失败</div>';
            return;
        }
        const data = await r.json();
        const users = data.users || [];
        if (!users.length) {
            box.innerHTML = '<div class="empty-state">无用户</div>';
            return;
        }
        box.innerHTML = '';
        users.forEach((u) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'admin-user-pick-row';
            const em = (u.email || '').trim() || '(无邮箱)';
            const nm = (u.display_name || '').trim();
            const title = nm ? `${em} — ${nm}` : em;
            const sub = [];
            if (u.last_message_at) {
                sub.push(`最近消息 ${u.last_message_at.slice(0, 10)}`);
            }
            sub.push(`${u.message_count != null ? u.message_count : 0} 条消息`);
            sub.push(`${u.conversation_count != null ? u.conversation_count : 0} 个会话`);
            row.innerHTML = `<span class="admin-user-pick-title"></span><span class="admin-user-pick-meta"></span>`;
            row.querySelector('.admin-user-pick-title').textContent = title;
            row.querySelector('.admin-user-pick-meta').textContent = sub.join(' · ');
            row.addEventListener('click', () => adminSelectUser(u));
            box.appendChild(row);
        });
    } catch (e) {
        console.warn('loadAdminUsersList', e);
        box.innerHTML = '<div class="empty-state">加载失败</div>';
    }
}

function syncAdminBrowseChrome() {
    const bar = document.getElementById('adminUserBrowseBar');
    const barLabel = document.getElementById('adminUserBrowseBarLabel');
    if (bar) {
        bar.hidden = !adminBrowsingUserId;
    }
    if (barLabel && adminBrowsingUserId) {
        const em = (adminBrowsingUserEmail || '').trim();
        const text = em
            ? `正在代管：${em}（${adminBrowsingUserId}）`
            : `正在代管用户：${adminBrowsingUserId}`;
        barLabel.textContent = text;
        barLabel.title = text;
    } else if (barLabel) {
        barLabel.title = '';
    }
}

async function adminSelectUser(u) {
    adminBrowsingUserId = u.id;
    adminBrowsingUserEmail = (u.email || '').trim();
    adminInspectConversationId = null;
    const chat = document.getElementById('chatContainer');
    if (chat) {
        chat.classList.remove('admin-inspect-mode');
        chat.classList.add('admin-browsing-user');
    }
    const dock = document.getElementById('composerDock');
    if (dock) {
        dock.hidden = false;
    }
    const app = document.querySelector('.app-container');
    if (app) {
        app.classList.add('admin-browsing-user');
    }
    syncAdminBrowseChrome();
    setCurrentConversation(null);
    const messagesArea = document.getElementById('messagesArea');
    if (messagesArea) {
        messagesArea.innerHTML =
            '<div class="messages-area-loading" role="status">请从左侧选择该用户的会话（只读）</div>';
    }
    updateCurrentSourceTitle();
    applyComposerDockLayout();
    await loadConversations();
    updateActiveConversation();
}

function initAdminWorkspaceUi() {
    const closeBtn = document.getElementById('adminWorkspaceClose');
    if (closeBtn && !closeBtn.dataset.wired) {
        closeBtn.dataset.wired = '1';
        closeBtn.addEventListener('click', (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            closeAdminWorkspace();
        });
    }
    const railCollapse = document.getElementById('adminSidebarCollapseBtn');
    if (railCollapse && !railCollapse.dataset.wired) {
        railCollapse.dataset.wired = '1';
        railCollapse.addEventListener('click', (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            toggleAdminRightPanelCollapsed();
        });
    }
    const tOverview = document.getElementById('adminTabBtnOverview');
    const tUsers = document.getElementById('adminTabBtnUsers');
    if (tOverview && !tOverview.dataset.wired) {
        tOverview.dataset.wired = '1';
        tOverview.addEventListener('click', () => {
            switchAdminTab('overview');
            loadAdminOverviewMetrics();
        });
    }
    if (tUsers && !tUsers.dataset.wired) {
        tUsers.dataset.wired = '1';
        tUsers.addEventListener('click', () => {
            switchAdminTab('users');
        });
    }
    const sortSel = document.getElementById('adminUserSortSelect');
    if (sortSel && !sortSel.dataset.wired) {
        sortSel.dataset.wired = '1';
        sortSel.addEventListener('change', () => loadAdminUsersList());
    }
    const searchBtn = document.getElementById('adminUserSearchBtn');
    if (searchBtn && !searchBtn.dataset.wired) {
        searchBtn.dataset.wired = '1';
        searchBtn.addEventListener('click', () => loadAdminUsersList());
    }
    const secretBtn = document.getElementById('adminSecretLogoutBtn');
    if (secretBtn && !secretBtn.dataset.wired) {
        secretBtn.dataset.wired = '1';
        secretBtn.addEventListener('click', async () => {
            try {
                await apiFetch('/api/admin/logout', { method: 'POST' });
            } catch (e) {
                /* ignore */
            }
            await refreshAuthState();
            await loadAdminOverviewMetrics();
        });
    }
    const exitBrowse = document.getElementById('adminUserBrowseExit');
    if (exitBrowse && !exitBrowse.dataset.wired) {
        exitBrowse.dataset.wired = '1';
        exitBrowse.addEventListener('click', async () => {
            exitAdminBrowseMode();
            setCurrentConversation(null);
            const messagesArea = document.getElementById('messagesArea');
            if (messagesArea) {
                messagesArea.innerHTML = '';
            }
            await loadConversations();
            updateCurrentSourceTitle();
            applyComposerDockLayout();
        });
    }
}

function getOAuthReturnStatus() {
    const params = new URLSearchParams(window.location.search);
    return params.get('auth');
}

function isAuthGateActive() {
    return authConfigured && !authUser;
}

function applyLoginProvidersVisibility() {
    const googleBtn = document.getElementById('loginScreenGoogleBtn');
    const emailBlock = document.getElementById('loginScreenEmailBlock');
    if (googleBtn) {
        googleBtn.classList.toggle('login-auth-hidden', !oauthConfigured);
    }
    if (emailBlock) {
        emailBlock.classList.toggle('login-auth-hidden', !emailAuthConfigured);
    }
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
    if (gateActive) {
        window.requestAnimationFrame(() => { startLoginBrandRotate(); });
    } else {
        stopLoginBrandRotate();
    }
    if (appContainer) {
        appContainer.classList.toggle('app-container--hidden', gateActive);
    }
    if (loginMessage) {
        let message = '';
        if (authStatus === 'ok' && gateActive) {
            message = '登录回调已完成，但当前会话未生效。';
        } else if (authStatus === 'error') {
            message = oauthConfigured ? 'Google 登录失败，请重试。' : '登录失败，请重试。';
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
    if (!authConfigured || !authUser) {
        panel.innerHTML = '';
        panel.classList.add('auth-panel--hidden');
        applyComposerDockLayout();
        return;
    }
    panel.classList.remove('auth-panel--hidden');
    panel.classList.add('auth-panel--user-state');
    const label = authUser.display_name || authUser.email || '已登录';
    const title = authUser.email || '';
    panel.innerHTML = `
        <div class="sidebar-capsule sidebar-capsule--auth">
            <div class="auth-panel-inner">
                <span class="auth-panel-user auth-panel-user--wrap" title="${escapeHtml(title)}">${escapeHtml(label)}</span>
                <button type="button" class="auth-panel-logout" id="authLogoutBtn">退出</button>
            </div>
        </div>`;
    const btn = document.getElementById('authLogoutBtn');
    if (btn) {
        btn.addEventListener('click', () => { logoutAuth(); });
    }
    applyComposerDockLayout();
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
        await apiFetch('/auth/logout', { method: 'POST' });
    } catch (e) {
        console.warn('logout failed:', e);
    }
    conversationStates.clear();
    clearAllConversationDetailCaches();
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

function readConversationDetailCacheRoot() {
    try {
        const raw = localStorage.getItem(CONVERSATION_DETAIL_CACHE_STORAGE_KEY);
        if (!raw) {
            return { entries: {}, order: [] };
        }
        const o = JSON.parse(raw);
        if (!o || typeof o !== 'object') {
            return { entries: {}, order: [] };
        }
        return {
            entries: o.entries && typeof o.entries === 'object' ? o.entries : {},
            order: Array.isArray(o.order) ? o.order : []
        };
    } catch {
        return { entries: {}, order: [] };
    }
}

function writeConversationDetailCacheRoot(root) {
    try {
        localStorage.setItem(CONVERSATION_DETAIL_CACHE_STORAGE_KEY, JSON.stringify(root));
    } catch (e) {
        console.warn('conversation cache write failed:', e);
        if (root && root.order && root.order.length > 1) {
            const half = Math.floor(root.order.length / 2);
            for (let i = 0; i < half; i++) {
                const id = root.order[i];
                if (id && root.entries[id]) {
                    delete root.entries[id];
                }
            }
            root.order = root.order.slice(half);
            try {
                localStorage.setItem(CONVERSATION_DETAIL_CACHE_STORAGE_KEY, JSON.stringify(root));
            } catch (e2) {
                console.warn('conversation cache shrink failed:', e2);
            }
        }
    }
}

function getConversationDetailCache(conversationId) {
    if (!conversationId) {
        return null;
    }
    const root = readConversationDetailCacheRoot();
    const e = root.entries[conversationId];
    if (!e || typeof e.savedAt !== 'number' || !e.data || typeof e.data !== 'object') {
        return null;
    }
    return { savedAt: e.savedAt, data: e.data };
}

function setConversationDetailCache(conversationId, apiData) {
    if (!conversationId || !apiData || typeof apiData !== 'object') {
        return;
    }
    const root = readConversationDetailCacheRoot();
    root.entries[conversationId] = { savedAt: Date.now(), data: apiData };
    root.order = root.order.filter(id => id !== conversationId);
    root.order.push(conversationId);
    while (root.order.length > CONVERSATION_DETAIL_CACHE_MAX_ENTRIES) {
        const evict = root.order.shift();
        if (evict && root.entries[evict]) {
            delete root.entries[evict];
        }
    }
    writeConversationDetailCacheRoot(root);
}

function removeConversationDetailCache(conversationId) {
    if (!conversationId) {
        return;
    }
    const root = readConversationDetailCacheRoot();
    if (!root.entries[conversationId]) {
        return;
    }
    delete root.entries[conversationId];
    root.order = root.order.filter(id => id !== conversationId);
    writeConversationDetailCacheRoot(root);
}

function clearAllConversationDetailCaches() {
    try {
        localStorage.removeItem(CONVERSATION_DETAIL_CACHE_STORAGE_KEY);
    } catch (e) {
        console.warn('conversation cache clear failed:', e);
    }
}

/** 将当前内存中的会话详情写回 localStorage（加载更早消息后等） */
function persistConversationDetailCacheFromState(conversationId) {
    if (adminInspectConversationId || adminBrowsingUserId) {
        return;
    }
    const state = getConversationState(conversationId);
    if (!state || !state.serverConversation) {
        return;
    }
    const sc = state.serverConversation;
    setConversationDetailCache(conversationId, {
        success: true,
        id: conversationId,
        created_at: sc.created_at || '',
        messages: Array.isArray(sc.messages) ? sc.messages : [],
        source_id: sc.source_id || '',
        source_name: sc.source_name || '',
        dify_title: sc.dify_title || '',
        messages_truncated: false,
        message_count_total:
            sc.message_count_total != null
                ? sc.message_count_total
                : (Array.isArray(sc.messages) ? sc.messages.length : 0),
        has_more_older: !!sc.has_more_older
    });
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

function maxNumericMessageId(messages) {
    let max = 0;
    for (const m of messages || []) {
        const n = Number(m && m.id);
        if (Number.isFinite(n) && n > max) {
            max = n;
        }
    }
    return max;
}

/** 为本地回合分配排序键：与服务器消息的 id 混排时，失败回合仍排在当时「最后一条已持久化消息」之后、后续成功消息之前。 */
function addLocalMessages(conversationId, messages) {
    const state = getConversationState(conversationId);
    if (!state) return;
    const serverMsgs = Array.isArray(state.serverConversation && state.serverConversation.messages)
        ? state.serverConversation.messages
        : [];
    const maxId = maxNumericMessageId(serverMsgs);
    state.localBlockSortCounter = (state.localBlockSortCounter || 0) + 1;
    const c = state.localBlockSortCounter;
    const keyed = (messages || []).map((msg) => {
        const roleOff = msg.role === 'assistant' ? 2e-6 : 1e-6;
        const _sortKey = maxId + c * 3e-6 + roleOff;
        return { ...msg, _sortKey };
    });
    state.localMessages.push(...keyed);
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
    scheduleAssistantMermaid(bubble, 320);
    scrollMessagesToBottom();
}

function messageViewSortKey(m) {
    if (m && m._sortKey != null) {
        return Number(m._sortKey);
    }
    const n = Number(m && m.id);
    return Number.isFinite(n) ? n : 0;
}

function getConversationMessagesForView(conversationId) {
    const state = getConversationState(conversationId);
    if (!state) return [];
    const serverMessages = Array.isArray(state.serverConversation && state.serverConversation.messages)
        ? state.serverConversation.messages
        : [];
    const merged = serverMessages.concat(state.localMessages);
    merged.sort((a, b) => messageViewSortKey(a) - messageViewSortKey(b));
    return merged;
}

function shouldCenterComposer() {
    const messagesArea = document.getElementById('messagesArea');
    if (!messagesArea) {
        return false;
    }
    if (messagesArea.querySelector('.messages-area-loading')) {
        return false;
    }
    return !messagesArea.querySelector('.message');
}

function getGreetingDisplayName() {
    if (authUser) {
        const n = (authUser.display_name || authUser.email || '').trim();
        if (n) {
            return n;
        }
    }
    return '同学';
}

/** 用户气泡头像：展示名或邮箱的首字符（支持多字节首字） */
function getUserAvatarInitial() {
    const raw = (authUser && (authUser.display_name || authUser.email) || '').trim();
    if (!raw) {
        return '?';
    }
    const first = Array.from(raw)[0];
    return first || '?';
}

function prefersComposerMotionReduced() {
    try {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) {
        return false;
    }
}

/**
 * 空会话时输入区垂直居中并显示问候；有消息后固定在底部。
 * @param {{ preferAnimate?: boolean, firstRect?: DOMRect | null }} options
 *        preferAnimate + firstRect：从居中首次出现消息时的 FLIP 平移（发送首条消息）。
 */
function applyComposerDockLayout(options = {}) {
    const preferAnimate = !!(options && options.preferAnimate);
    const firstRect = options && options.firstRect ? options.firstRect : null;
    const container = document.getElementById('chatContainer');
    const dock = document.getElementById('composerDock');
    const line1 = document.getElementById('emptyComposerGreetingLine1');
    const greet = document.getElementById('emptyComposerGreeting');
    if (!container || !dock) {
        return;
    }
    if (adminBrowsingUserId || adminInspectConversationId) {
        container.classList.remove('chat-container--composer-centered');
        dock.style.transition = '';
        dock.style.transform = '';
        return;
    }

    const center = shouldCenterComposer();
    const name = getGreetingDisplayName();
    if (line1) {
        line1.textContent = `${name}，你好`;
    }
    if (greet) {
        greet.setAttribute('aria-hidden', center ? 'false' : 'true');
    }

    container.classList.toggle('chat-container--composer-centered', center);

    const doAnimate = preferAnimate && firstRect && !center && !prefersComposerMotionReduced();
    if (doAnimate) {
        requestAnimationFrame(() => {
            const last = dock.getBoundingClientRect();
            const dy = firstRect.top - last.top;
            if (Math.abs(dy) < 1.5) {
                dock.style.transition = '';
                dock.style.transform = '';
                return;
            }
            dock.style.transition = 'none';
            dock.style.transform = `translateY(${dy}px)`;
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    dock.style.transition = 'transform 0.45s cubic-bezier(0.22, 1, 0.36, 1)';
                    dock.style.transform = 'translateY(0)';
                    const cleanup = (e) => {
                        if (e && e.propertyName && e.propertyName !== 'transform') {
                            return;
                        }
                        dock.removeEventListener('transitionend', cleanup);
                        dock.style.transition = '';
                        dock.style.transform = '';
                    };
                    dock.addEventListener('transitionend', cleanup);
                    window.setTimeout(cleanup, 520);
                });
            });
        });
    } else {
        dock.style.transition = '';
        dock.style.transform = '';
    }
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
    const wasEmptyView = shouldCenterComposer();
    const dock = document.getElementById('composerDock');
    const firstRect = wasEmptyView && dock ? dock.getBoundingClientRect() : null;
    const messages = getConversationMessagesForView(conversationId);
    let prevScrollHeight = 0;
    let prevScrollTop = 0;
    if (preserveScroll) {
        prevScrollHeight = messagesArea.scrollHeight;
        prevScrollTop = messagesArea.scrollTop;
    }
    messagesArea.innerHTML = '';
    if (!messages.length) {
        applyComposerDockLayout();
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
            skipScroll: true,
            skipDockLayout: true
        });
    });
    applyComposerDockLayout({
        preferAnimate: !!(wasEmptyView && firstRect),
        firstRect
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
    initTheme();
    await initAuth();
    consumeAdminDeepLink();
    bindFooterAdminLink();
    initAdminWorkspaceUi();
    initSidebarState();
    syncSendBtn();
    setupEventListeners();
    if (!isAuthGateActive()) {
        loadSources();
        loadConversations();
    }
    applyComposerDockLayout();
});

function renderMessagesLoadingPlaceholder() {
    return '<div class="messages-area-loading" role="status">加载会话…</div>';
}

/** 与 Unicode ☀/☾ 不同：iOS 会把后者画成彩色 emoji；SVG 用 currentColor 全平台一致 */
const THEME_ICON_SUN_SVG =
    '<svg class="theme-toggle-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32l1.41-1.41"/></svg>';
const THEME_ICON_MOON_SVG =
    '<svg class="theme-toggle-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

function applyTheme(theme) {
    const body = document.body;
    if (!body) return;
    const nextTheme = theme === 'light' ? 'light' : 'dark';
    body.setAttribute('data-theme', nextTheme);
    document.querySelectorAll('.theme-toggle-icon').forEach((toggleIcon) => {
        toggleIcon.innerHTML = nextTheme === 'dark' ? THEME_ICON_SUN_SVG : THEME_ICON_MOON_SVG;
    });
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

/** 根级 table 外包一层，便于窄屏横向滚动且与格子样式一致 */
function wrapAssistantTables(html) {
    if (!html || typeof html !== 'string' || !/<table[\s>]/i.test(html)) {
        return html;
    }
    try {
        const tpl = document.createElement('template');
        tpl.innerHTML = html.trim();
        const frag = tpl.content;
        const top = Array.from(frag.childNodes);
        for (const node of top) {
            if (node.nodeType === 1 && node.tagName === 'TABLE') {
                const wrap = document.createElement('div');
                wrap.className = 'table-scroll';
                frag.insertBefore(wrap, node);
                wrap.appendChild(node);
            }
        }
        const out = document.createElement('div');
        out.appendChild(frag);
        return out.innerHTML;
    } catch (e) {
        return html;
    }
}

/** 与 script.js 同源的查询串，便于部署更新后一并刷新 vendor 缓存 */
function getVendorMermaidScriptUrl() {
    const el = document.querySelector('script[src*="script.js"]');
    if (el && el.src) {
        try {
            const u = new URL(el.src, window.location.href);
            const p = u.pathname.replace(/\/script\.js$/i, '/vendor/mermaid.min.js');
            return p + (u.search || '');
        } catch (e) {
            /* ignore */
        }
    }
    return '/static/vendor/mermaid.min.js';
}

let mermaidScriptLoadPromise = null;
function loadMermaidLibrary() {
    if (typeof mermaid !== 'undefined') {
        return Promise.resolve(window.mermaid);
    }
    if (mermaidScriptLoadPromise) {
        return mermaidScriptLoadPromise;
    }
    mermaidScriptLoadPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = getVendorMermaidScriptUrl();
        s.async = true;
        s.onload = () => {
            if (typeof mermaid !== 'undefined') {
                resolve(window.mermaid);
            } else {
                mermaidScriptLoadPromise = null;
                reject(new Error('mermaid global missing'));
            }
        };
        s.onerror = () => {
            mermaidScriptLoadPromise = null;
            console.warn('Mermaid 脚本加载失败（请确认已部署 static/vendor/mermaid.min.js）:', s.src);
            reject(new Error('mermaid script failed to load'));
        };
        document.head.appendChild(s);
    });
    return mermaidScriptLoadPromise;
}

let mermaidConfigApplied = false;
function ensureMermaidInitialized() {
    if (typeof mermaid === 'undefined' || typeof mermaid.initialize !== 'function') {
        return null;
    }
    if (!mermaidConfigApplied) {
        // 始终用浅色 Mermaid 主题：dark 主题下常出现「浅色节点填充 + 浅色 label」对比度崩溃（如过渡态粉底灰字）。
        // 图表在 .assistant-mermaid-wrap 内相当于浅色卡片，与暗色聊天背景分层清晰、字可读。
        mermaid.initialize({
            startOnLoad: false,
            theme: 'default',
            securityLevel: 'strict'
        });
        mermaidConfigApplied = true;
    }
    return mermaid;
}

/** 渲染前把源码挂在元素上，解析失败时可展示给用户（问题多在模型输出的语法，而非前端） */
const assistantMermaidSourceByEl = new WeakMap();

function assistantCreateMermaidWrapElement(source) {
    const normalized = String(source || '').replace(/\r\n/g, '\n').trim();
    if (!normalized) {
        return null;
    }
    const wrap = document.createElement('div');
    wrap.className = 'assistant-mermaid-wrap';
    const graph = document.createElement('div');
    graph.className = 'mermaid';
    graph.textContent = normalized;
    assistantMermaidSourceByEl.set(graph, normalized);
    wrap.appendChild(graph);
    return wrap;
}

/**
 * 识别模型直接输出的「无 ```mermaid 围栏」的源码（整段落在单个 p / pre 里）。
 */
function assistantTextLooksLikeMermaidSource(raw) {
    const s = String(raw || '')
        .replace(/\r\n/g, '\n')
        .trim();
    if (!s || s.length > 120000) {
        return false;
    }
    const head = s.slice(0, 1200).trimStart();
    if (
        /^\s*(?:sequenceDiagram\b|classDiagram\b|stateDiagram-v2\b|stateDiagram\b|erDiagram\b|gantt\b|pie\b|gitgraph\b|journey\b|mindmap\b|timeline\b|sankey-beta\b|block-beta\b)/i.test(
            head
        )
    ) {
        return true;
    }
    if (/^\s*(?:graph|flowchart)\s+[A-Za-z]{1,2}\b/i.test(head)) {
        return true;
    }
    const lines = s
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
    if (
        lines.length >= 2 &&
        /^(?:graph|flowchart)$/i.test(lines[0]) &&
        /^[A-Za-z]{1,2}$/.test(lines[1])
    ) {
        return true;
    }
    return false;
}

function assistantParagraphIsMermaidPlainHost(p) {
    if (!p || p.tagName !== 'P') {
        return false;
    }
    for (const n of p.childNodes) {
        if (n.nodeType === 3) {
            continue;
        }
        if (n.nodeType === 1 && n.tagName === 'BR') {
            continue;
        }
        return false;
    }
    return true;
}

/**
 * 将裸露的 Mermaid 段落/pre 转为 .assistant-mermaid-wrap（在 rewriteMermaidCodeBlocksToDivs 之后执行）。
 */
function rewriteBareMermaidBlocksToDivs(html) {
    if (!html || typeof html !== 'string') {
        return html;
    }
    try {
        const tpl = document.createElement('template');
        tpl.innerHTML = html.trim();
        const root = tpl.content;

        root.querySelectorAll('p').forEach((p) => {
            if (p.closest('.assistant-mermaid-wrap')) {
                return;
            }
            if (!assistantParagraphIsMermaidPlainHost(p)) {
                return;
            }
            const source = (p.textContent || '').replace(/\r\n/g, '\n').trim();
            if (!assistantTextLooksLikeMermaidSource(source)) {
                return;
            }
            const wrap = assistantCreateMermaidWrapElement(source);
            if (wrap) {
                p.replaceWith(wrap);
            }
        });

        root.querySelectorAll('pre').forEach((pre) => {
            if (pre.closest('.assistant-mermaid-wrap')) {
                return;
            }
            const code = pre.querySelector(':scope > code');
            let source = '';
            if (code) {
                const cls = (code.getAttribute('class') || '').toLowerCase();
                if (/\blanguage-mermaid\b/.test(cls) || /\blang-mermaid\b/.test(cls)) {
                    return;
                }
                source = (code.textContent || '').replace(/\r\n/g, '\n').trim();
            } else {
                source = (pre.textContent || '').replace(/\r\n/g, '\n').trim();
            }
            if (!assistantTextLooksLikeMermaidSource(source)) {
                return;
            }
            const wrap = assistantCreateMermaidWrapElement(source);
            if (wrap) {
                pre.replaceWith(wrap);
            }
        });

        const out = document.createElement('div');
        out.appendChild(root);
        return out.innerHTML;
    } catch (e) {
        console.warn('Bare Mermaid rewrite failed:', e);
        return html;
    }
}

/**
 * 将 GFM ```mermaid 代码块转为 Mermaid 所需的 div（在 DOMPurify 之后执行，源为纯文本）。
 */
function rewriteMermaidCodeBlocksToDivs(html) {
    if (!html || typeof html !== 'string') {
        return html;
    }
    if (!html.includes('language-mermaid') && !html.includes('lang-mermaid')) {
        return html;
    }
    try {
        const tpl = document.createElement('template');
        tpl.innerHTML = html.trim();
        const root = tpl.content;
        root.querySelectorAll('pre > code').forEach((code) => {
            const cls = (code.getAttribute('class') || '').toLowerCase();
            if (!/\blanguage-mermaid\b/.test(cls) && !/\blang-mermaid\b/.test(cls)) {
                return;
            }
            const pre = code.parentElement;
            if (!pre || pre.tagName !== 'PRE') {
                return;
            }
            const source = (code.textContent || '').replace(/\r\n/g, '\n').trim();
            const wrap = assistantCreateMermaidWrapElement(source);
            if (wrap) {
                pre.replaceWith(wrap);
            }
        });
        const out = document.createElement('div');
        out.appendChild(root);
        return out.innerHTML;
    } catch (e) {
        console.warn('Mermaid block rewrite failed:', e);
        return html;
    }
}

const assistantMermaidTimers = new WeakMap();

function scheduleAssistantMermaid(root, debounceMs) {
    if (!root || !root.querySelector || !root.querySelector('.assistant-mermaid-wrap .mermaid')) {
        return;
    }
    const run = () => {
        assistantMermaidTimers.delete(root);
        void runAssistantMermaidHydrate(root);
    };
    if (debounceMs <= 0) {
        queueMicrotask(run);
        return;
    }
    const prev = assistantMermaidTimers.get(root);
    if (prev) {
        clearTimeout(prev);
    }
    assistantMermaidTimers.set(root, setTimeout(run, debounceMs));
}

/** 判断 Mermaid 是否已产出可展示输出（内联 SVG，或 sandbox 模式下的 iframe） */
function assistantMermaidDiagramPresent(graphEl) {
    if (!graphEl) {
        return false;
    }
    if (graphEl.querySelector('svg')) {
        return true;
    }
    const fe = graphEl.firstElementChild;
    return !!(fe && fe.tagName === 'IFRAME');
}

/**
 * 仅当 Mermaid 在容器内画出了「语法错误」炸弹页时才视为失败。
 * 不能用「没有 svg」当失败：部分主题/时机下成功图的检测若误判，会错误挂上保底 <details>，盖住正常图或造成重复说明。
 */
function assistantMermaidShowsSyntaxErrorUi(graphEl) {
    if (!graphEl) {
        return false;
    }
    const svg = graphEl.querySelector('svg');
    if (!svg) {
        return false;
    }
    const blob = (svg.textContent || '').toLowerCase();
    if (blob.includes('syntax error')) {
        return true;
    }
    return !!(svg.querySelector('path.error-icon') || svg.querySelector('.error-icon'));
}

function attachAssistantMermaidSyntaxFallback(wrap, source) {
    if (!wrap || !source || wrap.querySelector(':scope > .assistant-mermaid-fallback')) {
        return;
    }
    const det = document.createElement('details');
    det.className = 'assistant-mermaid-fallback';
    const sum = document.createElement('summary');
    sum.textContent = '无法解析该图（多为模型输出的 Mermaid 语法错误）。展开查看原始代码';
    const pre = document.createElement('pre');
    pre.className = 'assistant-mermaid-source';
    pre.textContent = source;
    det.appendChild(sum);
    det.appendChild(pre);
    wrap.appendChild(det);
}

function finalizeAssistantMermaidNodes(nodes, options = {}) {
    const forceIfNoDiagram = !!options.forceIfNoDiagram;
    for (const el of nodes) {
        if (!el.isConnected) {
            continue;
        }
        const wrap = el.parentElement;
        const src = assistantMermaidSourceByEl.get(el);
        if (!wrap || !src) {
            continue;
        }
        if (wrap.querySelector(':scope > .assistant-mermaid-fallback')) {
            continue;
        }
        const hasDiagram = assistantMermaidDiagramPresent(el);
        const syntaxErr = assistantMermaidShowsSyntaxErrorUi(el);
        const stuckRaw =
            !hasDiagram &&
            el.childElementCount === 0 &&
            (el.textContent || '').trim().length > 0;
        const needsFallback =
            syntaxErr || (forceIfNoDiagram && !hasDiagram) || (!syntaxErr && stuckRaw);
        if (needsFallback) {
            attachAssistantMermaidSyntaxFallback(wrap, src);
            if (stuckRaw) {
                el.textContent = '';
            }
        }
    }
}

async function runAssistantMermaidHydrate(root) {
    if (!root || !root.isConnected) {
        return;
    }
    let nodes = [...root.querySelectorAll('.assistant-mermaid-wrap > .mermaid')].filter(
        (el) => el.isConnected && !assistantMermaidDiagramPresent(el)
    );
    if (!nodes.length) {
        return;
    }
    try {
        await loadMermaidLibrary();
    } catch (e) {
        console.warn('Mermaid load failed:', e);
        finalizeAssistantMermaidNodes(nodes, { forceIfNoDiagram: true });
        return;
    }
    const m = ensureMermaidInitialized();
    if (!m || typeof m.run !== 'function') {
        finalizeAssistantMermaidNodes(nodes, { forceIfNoDiagram: true });
        return;
    }
    nodes = nodes.filter((el) => el.isConnected);
    for (const el of nodes) {
        el.removeAttribute('data-processed');
    }
    if (!nodes.length) {
        return;
    }
    try {
        await m.run({ nodes });
    } catch (e) {
        console.warn('Mermaid render failed:', e);
        finalizeAssistantMermaidNodes(nodes, { forceIfNoDiagram: true });
        return;
    }
    finalizeAssistantMermaidNodes(nodes, { forceIfNoDiagram: false });
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

/**
 * 仅处理明确的 CRLF 与字面量 \\"；不把 \\n 强行换成换行，以免误伤 LaTeX（\\nabla 等）。
 * 若上游在 JSON 层已正确解码，正文换行应由 json.parse / Markdown 渲染自然呈现。
 */
function assistantDecodeLiteralEscapes(s) {
    if (s == null || typeof s !== 'string' || !/\\r\\n|\\"/.test(s)) {
        return s;
    }
    return s.replace(/\\r\\n/g, '\n').replace(/\\"/g, '"');
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
        html = sanitizeAssistantHtml(purify, html);
        html = wrapAssistantTables(html);
        html = rewriteMermaidCodeBlocksToDivs(html);
        return rewriteBareMermaidBlocksToDivs(html);
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
        btn.title = '发送';
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

    const mainChrome = document.querySelector('.main-chrome');
    document.addEventListener('click', function(event) {
        if (!isMobileViewport() || !isSidebarOpen() || !sidebar) {
            return;
        }
        if (
            sidebar.contains(event.target)
            || (shellRail && shellRail.contains(event.target))
            || (mainChrome && mainChrome.contains(event.target))
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
        const response = await apiFetch('/api/sources');
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
    if (authConfigured && !authUser) {
        addMessageToUI('assistant', '❌ 请先登录后再开始对话。');
        return false;
    }
    if (!selectedSourceId) {
        addMessageToUI('assistant', '❌ 请先选择知识库再开始对话。');
        return false;
    }
    try {
        const payload = { source_id: selectedSourceId };
        const response = await apiFetch('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
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
            if (adminBrowsingUserId) {
                renderSidebarStatus('管理员查看 · 以下为该用户会话');
                const response = await apiFetch(
                    `/api/admin/users/${encodeURIComponent(adminBrowsingUserId)}/conversations`,
                    { cache: 'no-store' }
                );
                const container = document.getElementById('conversationsList');
                if (!container) {
                    return;
                }
                if (response.status === 429) {
                    renderSidebarStatus('请求过于频繁，请稍后再试');
                    return;
                }
                if (!response.ok) {
                    container.innerHTML = '<div class="empty-state">加载失败</div>';
                    return;
                }
                const data = await response.json();
                const convs = data.conversations || {};
                const entries = Object.entries(convs);
                entries.sort(
                    (a, b) => conversationActivityTimeMs(b[1]) - conversationActivityTimeMs(a[1])
                );
                if (entries.length) {
                    container.innerHTML = '';
                    const topConv = entries[0] && entries[0][1];
                    defaultSourceIdForNewChat = topConv && topConv.source_id
                        ? String(topConv.source_id).trim()
                        : '';
                    entries.forEach(([id, conv]) => {
                        container.appendChild(
                            createConversationItem(id, conv, { adminBrowse: true })
                        );
                    });
                } else {
                    container.innerHTML = '<div class="empty-state">该用户暂无会话</div>';
                    defaultSourceIdForNewChat = '';
                }
                syncNewChatDefaultSource({ force: false });
                updateActiveConversation();
                return;
            }
            if (authConfigured && !authUser) {
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
            const response = await apiFetch('/api/conversations');
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

function createConversationItem(id, conv, options = {}) {
    const adminBrowse = !!(options && options.adminBrowse);
    const div = document.createElement('div');
    div.className = adminBrowse
        ? 'conversation-item conversation-item--admin-browse'
        : 'conversation-item';
    div.dataset.convId = id;
    if (id === currentConversationId) div.classList.add('active');

    const difyTitle = conv && typeof conv.dify_title === 'string'
        ? conv.dify_title.trim()
        : '';

    const label = document.createElement('span');
    label.className = 'conversation-item-label';
    label.textContent = difyTitle || '新对话';

    div.addEventListener('click', () => switchConversation(id));
    div.appendChild(label);
    if (!adminBrowse) {
        const delBtn = document.createElement('button');
        delBtn.className = 'delete-btn';
        delBtn.textContent = '×';
        delBtn.addEventListener('click', (e) => deleteConversation(id, e));
        div.appendChild(delBtn);
    }
    return div;
}

function exitAdminBrowseMode() {
    adminInspectConversationId = null;
    adminBrowsingUserId = null;
    adminBrowsingUserEmail = '';
    const chat = document.getElementById('chatContainer');
    if (chat) {
        chat.classList.remove('admin-inspect-mode', 'admin-browsing-user');
    }
    const dock = document.getElementById('composerDock');
    if (dock) {
        dock.hidden = false;
    }
    const app = document.querySelector('.app-container');
    if (app) {
        app.classList.remove('admin-browsing-user');
    }
    const bar = document.getElementById('adminUserBrowseBar');
    if (bar) {
        bar.hidden = true;
    }
    const barLabel = document.getElementById('adminUserBrowseBarLabel');
    if (barLabel) {
        barLabel.textContent = '';
    }
}

async function adminLoadReadonlyConversation(conversationId) {
    adminInspectConversationId = conversationId;
    const chat = document.getElementById('chatContainer');
    if (chat) {
        chat.classList.add('admin-inspect-mode');
    }
    setCurrentConversation(conversationId);
    const messagesArea = document.getElementById('messagesArea');
    if (messagesArea) {
        messagesArea.innerHTML = renderMessagesLoadingPlaceholder();
    }
    const state = getConversationState(conversationId);
    if (state) {
        state.loadingOlderMessages = false;
        state.serverConversation = {
            id: conversationId,
            created_at: '',
            messages: [],
            source_id: '',
            source_name: '',
            dify_title: '',
            has_more_older: false,
            message_count_total: null
        };
    }
    try {
        const response = await apiFetch(
            conversationDetailUrl(conversationId, { messageLimit: CONVERSATION_MESSAGE_PAGE_SIZE })
        );
        if (!response.ok) {
            if (messagesArea) {
                messagesArea.innerHTML = '<div class="messages-area-loading" role="status">无法加载会话</div>';
            }
            return;
        }
        const data = await response.json();
        if (!data.success) {
            if (messagesArea) {
                messagesArea.innerHTML = '<div class="messages-area-loading" role="status">无法加载会话</div>';
            }
            return;
        }
        setConversationServerData(conversationId, data);
        if (data.source_id) {
            selectedSourceId = data.source_id;
            renderSourceOptions();
        }
        setSourceLocked(true);
        renderConversationView(conversationId);
        updateCurrentSourceTitle();
        updateActiveConversation();
    } catch (e) {
        console.error('adminLoadReadonlyConversation:', e);
        if (messagesArea) {
            messagesArea.innerHTML = '<div class="messages-area-loading" role="status">加载失败</div>';
        }
    }
}

// Start new chat
function startNewChat() {
    exitAdminBrowseMode();
    setCurrentConversation(null);
    uploadedFiles = [];
    resetComposer();
    syncNewChatDefaultSource({ force: true });
    setSourceLocked(false);

    const messagesArea = document.getElementById('messagesArea');
    if (messagesArea) {
        messagesArea.innerHTML = '';
    }
    applyComposerDockLayout();
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
        const response = await apiFetch(
            conversationDetailUrl(conversationId, {
                messageLimit: CONVERSATION_MESSAGE_PAGE_SIZE,
                beforeMessageId: beforeId
            })
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
        persistConversationDetailCacheFromState(conversationId);
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
    if (adminBrowsingUserId) {
        await adminLoadReadonlyConversation(conversationId);
        closeSidebar();
        return;
    }
    exitAdminBrowseMode();
    setCurrentConversation(conversationId);
    selectedSourceId = getConversationSourceId(conversationId) || selectedSourceId;
    renderSourceOptions();
    setSourceLocked(true);
    closeSidebar();

    const cached = getConversationDetailCache(conversationId);
    const now = Date.now();
    const cacheFresh = !!(cached && now - cached.savedAt < CONVERSATION_DETAIL_CACHE_TTL_MS);

    if (cacheFresh && cached) {
        const st = getConversationState(conversationId);
        if (st) {
            st.loadingOlderMessages = false;
        }
        setConversationServerData(conversationId, cached.data);
        uploadedFiles = [];
        resetComposer();
        if (cached.data.source_id) {
            selectedSourceId = cached.data.source_id;
            renderSourceOptions();
        }
        setSourceLocked(true);
        renderConversationView(conversationId);
        updateCurrentSourceTitle();
        return;
    }

    const state = getConversationState(conversationId);
    if (state) {
        state.loadingOlderMessages = false;
        if (cached) {
            setConversationServerData(conversationId, cached.data);
        } else if (state.serverConversation) {
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
        const response = await apiFetch(
            conversationDetailUrl(conversationId, { messageLimit: CONVERSATION_MESSAGE_PAGE_SIZE })
        );
        if (!response.ok) {
            if (response.status === 404) {
                removeConversationDetailCache(conversationId);
            }
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
        setConversationDetailCache(conversationId, data);
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
    if (adminBrowsingUserId) {
        const em = (adminBrowsingUserEmail || '').trim();
        const parts = ['管理员查看'];
        if (em) {
            parts.push(em);
        } else {
            parts.push(adminBrowsingUserId);
        }
        const sourceId = currentConversationId
            ? getConversationSourceId(currentConversationId)
            : (selectedSourceId || pickDefaultSourceIdForNewChat() || '');
        const kbName = sourceId ? getSourceDisplayName(sourceId) : '';
        parts.push(kbName ? `知识库 ${kbName}` : '知识库 —');
        if (em) {
            parts.push(adminBrowsingUserId);
        }
        if (currentConversationId) {
            parts.push(`会话 ${currentConversationId}`);
        }
        const text = parts.join(' · ');
        el.textContent = text;
        el.title = text;
        el.classList.add('main-chrome-title--wrap');
        return;
    }
    el.title = '';
    el.classList.remove('main-chrome-title--wrap');
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
    if (label && label !== '请选择知识库') {
        el.title = label;
    } else {
        el.title = '';
    }
}

// Delete conversation
async function deleteConversation(conversationId, event) {
    event.stopPropagation();
    if (adminBrowsingUserId) {
        return;
    }

    if (!confirm('确定要删除这条会话吗？')) {
        return;
    }
    
    try {
        const response = await apiFetch(conversationDetailUrl(conversationId), {
            method: 'DELETE'
        });
        
        if (response.ok) {
            removeConversationDetailCache(conversationId);
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

// Enter 发送；Shift+Enter 换行（不拦截，交给浏览器默认插入换行）
// 组字/选词阶段勿拦截 Enter，否则拼音选词上屏会被当成发送（见 event.isComposing）
function handleInputKeydown(event) {
    if (adminInspectConversationId || adminBrowsingUserId) {
        return;
    }
    if (event.isComposing || event.keyCode === 229) {
        return;
    }
    if (event.key !== 'Enter' || event.shiftKey) {
        return;
    }
    event.preventDefault();
    sendMessage();
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

    return apiFetch('/api/chat', {
        method: 'POST',
        body: formData,
        signal: controller.signal
    });
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
        const response = await apiFetch(
            conversationDetailUrl(conversationId, { messageLimit: lim })
        );
        if (!response.ok) {
            return;
        }
        const data = await response.json();
        if (!data.success) {
            return;
        }
        setConversationServerData(conversationId, data);
        setConversationDetailCache(conversationId, data);
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
    if (adminInspectConversationId || adminBrowsingUserId) {
        return;
    }
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
    const appContainer = document.querySelector('.app-container');
    if (appContainer) {
        appContainer.classList.remove('mobile-sidebar-open');
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
            scheduleAssistantMermaid(bubble, 300);
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

    if (role === 'assistant' && bubble.classList.contains('message-bubble-md')) {
        scheduleAssistantMermaid(bubble, options.pending ? 300 : 0);
    }
}

/** 助手气泡头像：与顶栏相同的 A*（静态 HTML，无用户输入） */
const ASSISTANT_AVATAR_MARK_HTML = '<span class="app-brand-mark app-brand-mark--avatar" aria-hidden="true"><span class="app-brand-mark-inner"><span class="app-brand-mark-letter">A</span><span class="app-brand-mark-star">*</span></span></span>';

// Add message to UI
function addMessageToUI(role, content, options = {}) {
    const messagesArea = document.getElementById('messagesArea');
    const skipDockLayout = !!(options && options.skipDockLayout);
    const wasEmptyView = shouldCenterComposer();
    const dock = document.getElementById('composerDock');
    const firstRect = !skipDockLayout && wasEmptyView && dock ? dock.getBoundingClientRect() : null;
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
    if (role === 'user') {
        avatar.classList.add('message-avatar--initial');
        const who = (authUser && (authUser.display_name || authUser.email || '').trim()) || '';
        avatar.textContent = getUserAvatarInitial();
        avatar.setAttribute('aria-label', who ? `我：${who}` : '我');
    } else {
        avatar.classList.add('message-avatar--brand');
        avatar.innerHTML = ASSISTANT_AVATAR_MARK_HTML;
        avatar.setAttribute('aria-label', '助手');
    }
    
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
    if (!skipDockLayout) {
        applyComposerDockLayout({
            preferAnimate: !!(wasEmptyView && firstRect),
            firstRect
        });
    }
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
    document.querySelectorAll('#conversationsList .conversation-item').forEach((item) => {
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
    const collapseBtnMobile = document.getElementById('sidebarCollapseBtnMobile');
    const mobileOpen = isSidebarOpen();
    const desktopCollapsed = isDesktopSidebarCollapsed();

    const applyCollapseLabels = (btn) => {
        if (!btn) return;
        if (isMobileViewport()) {
            btn.title = mobileOpen ? '收起会话列表' : '打开会话列表';
        } else {
            btn.title = desktopCollapsed ? '展开侧边栏' : '收起侧边栏';
        }
        btn.setAttribute('aria-label', btn.title);
    };

    applyCollapseLabels(collapseBtn);
    applyCollapseLabels(collapseBtnMobile);
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
    const sidebar = document.querySelector('.sidebar');
    const appContainer = document.querySelector('.app-container');
    const nextOpen = !(sidebar && sidebar.classList.contains('show'));
    if (sidebar) {
        sidebar.classList.toggle('show', nextOpen);
    }
    if (appContainer && isMobileViewport()) {
        appContainer.classList.toggle('mobile-sidebar-open', nextOpen);
    }
    syncSidebarButtons();
}
