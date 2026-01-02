// static/js/community_page.js
// Dynamic community feed + session replay logic for visaion
// Renders posts from /api/community/feed and plays shot clips sequentially.

(() => {
    const FEED_ENDPOINT = '/api/community/feed';
    const DETAIL_ENDPOINT = sid => `/api/community/session/${encodeURIComponent(sid)}`;
    const ACTIONS_ENDPOINT = sid => `/api/community/actions/${encodeURIComponent(sid)}`;
    const OVERLAY_HOLD_MS = 6000;
    const PLAYBACK_RATE = 0.3;
    const ACTIONS_STORAGE_KEY = 'visaion.community.actions.v1';
    const ACTION_ICONS = {
        like: 'M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 6 4 4 6.5 4c1.74 0 3.41 1.01 4.5 2.09C12.09 5.01 13.76 4 15.5 4 18 4 20 6 20 8.5c0 3.78-3.4 6.86-8.55 11.54z',
        comment: 'M21 6h-18c-1.1 0-2 .9-2 2v13l4-4h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2z',
        share: 'M18 16.08c-.76 0-1.44.3-1.96.77l-7.05-4.14a2.96 2.96 0 000-1.39l7-4.11A2.99 2.99 0 0018 7.91a3 3 0 10-3-3c0 .23.03.45.08.66l-7 4.11a3 3 0 10.02 4.68l7.05 4.14c-.05.2-.07.41-.07.63a3 3 0 103-3z',
        alerts: 'M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6V11c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5S10.5 3.17 10.5 4v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z',
    };

    const feedEl = document.querySelector('[data-community-feed]');
    const filterEl = document.querySelector('[data-community-filter]');
    const tagFilterEl = document.querySelector('[data-community-tag-filter]');
    const emptyStateEl = document.querySelector('[data-community-empty]');
    const filterBarEl = document.querySelector('[data-filter-bar]');
    const tagToggleBtn = document.querySelector('[data-tag-toggle]');
    const startCardBtn = document.querySelector('[data-start-session]');
    const startSheetEl = document.querySelector('[data-start-sheet]');
    const mobileNavEl = document.querySelector('[data-mobile-nav]');

    const modalEl = document.querySelector('[data-community-modal]');
    const modalBackdropEl = modalEl ? modalEl.querySelector('[data-modal-backdrop]') : null;
    const modalCloseEl = modalEl ? modalEl.querySelector('[data-modal-close]') : null;
    const modalTitleEl = modalEl ? modalEl.querySelector('[data-modal-title]') : null;
    const modalMetaEl = modalEl ? modalEl.querySelector('[data-modal-meta]') : null;
    const modalSummaryEl = modalEl ? modalEl.querySelector('[data-modal-summary]') : null;
    const modalHighlightsEl = modalEl ? modalEl.querySelector('[data-modal-highlights]') : null;
    const modalStatsEl = modalEl ? modalEl.querySelector('[data-modal-stats]') : null;
    const modalVideoEl = modalEl ? modalEl.querySelector('[data-modal-video]') : null;
    const modalOverlayEl = modalEl ? modalEl.querySelector('[data-modal-overlay]') : null;
    const modalShotListEl = modalEl ? modalEl.querySelector('[data-modal-shots]') : null;
    const modalActionBarEl = modalEl ? modalEl.querySelector('[data-modal-actions]') : null;
    const startSheetCloseEls = startSheetEl ? Array.from(startSheetEl.querySelectorAll('[data-start-close]')) : [];
    const navItems = mobileNavEl ? Array.from(mobileNavEl.querySelectorAll('[data-nav]')) : [];

    if (modalVideoEl) {
        const enforcePlaybackRate = () => {
            try {
                modalVideoEl.defaultPlaybackRate = PLAYBACK_RATE;
                modalVideoEl.playbackRate = PLAYBACK_RATE;
            } catch { }
        };
        enforcePlaybackRate();
        modalVideoEl.addEventListener('loadedmetadata', enforcePlaybackRate);
        modalVideoEl.addEventListener('play', enforcePlaybackRate);
    }

    const state = {
        posts: [],
        filters: [],
        tagFilters: [],
        activeFilter: 'all',
        activeTag: 'all',
        tagsOpen: false,
        activeDetail: null,
        activeShotIndex: -1,
        overlayTimer: null,
        loadingDetail: false,
        loadingClipToken: null,
        clipLoads: new Map(),
        commentModal: null,
        pendingShareSid: null,
        authChecked: false,
        authed: false,
        authUser: null,
        startRequested: false,
    };

    let actionStore = {};
    let authPromise = null;

    const clipCache = new Map();
    const CLIP_CACHE_LIMIT = 6;

    function revokeClipUrl(path) {
        const entry = clipCache.get(path);
        if (entry?.url) {
            try { URL.revokeObjectURL(entry.url); } catch { }
        }
        clipCache.delete(path);
    }

    function trimClipCache() {
        if (clipCache.size <= CLIP_CACHE_LIMIT) return;
        const ordered = [...clipCache.entries()].sort((a, b) => {
            const aTs = a[1]?.fetchedAt ?? 0;
            const bTs = b[1]?.fetchedAt ?? 0;
            return aTs - bTs;
        });
        while (clipCache.size > CLIP_CACHE_LIMIT && ordered.length) {
            const [oldPath] = ordered.shift();
            revokeClipUrl(oldPath);
        }
    }

    function clearClipCache() {
        for (const [path, entry] of clipCache.entries()) {
            if (entry?.url) {
                try { URL.revokeObjectURL(entry.url); } catch { }
            }
            clipCache.delete(path);
        }
        for (const loader of state.clipLoads.values()) {
            try { loader.controller?.abort(); } catch { }
        }
        state.clipLoads.clear();
    }

    function scheduleClipWarm(path) {
        if (!path) return null;
        if (clipCache.has(path)) return null;
        const existing = state.clipLoads.get(path);
        if (existing?.promise) return existing.promise;
        const controller = new AbortController();
        const promise = (async () => {
            try {
                const response = await fetch(path, { cache: 'force-cache', signal: controller.signal });
                if (!response.ok) {
                    throw new Error(`clip fetch failed (${response.status})`);
                }
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                clipCache.set(path, { url, size: blob.size, fetchedAt: Date.now() });
                trimClipCache();
            } catch (err) {
                if (err?.name !== 'AbortError') {
                    console.warn('[community] warm failed', err);
                }
            } finally {
                state.clipLoads.delete(path);
            }
        })();
        state.clipLoads.set(path, { promise, controller });
        return promise;
    }

    async function resolveClipSource(path) {
        if (!path) return null;
        const cached = clipCache.get(path);
        if (cached?.url) return cached.url;
        const inFlight = state.clipLoads.get(path);
        if (inFlight?.promise) {
            try {
                await Promise.race([
                    inFlight.promise,
                    new Promise(resolve => setTimeout(resolve, 150))
                ]);
            } catch (err) {
                if (err?.name !== 'AbortError') {
                    console.warn('[community] warm wait failed', err);
                }
            }
            const warmed = clipCache.get(path);
            if (warmed?.url) return warmed.url;
        }
        return path;
    }

    function prefetchClip(path) {
        if (!path) return;
        if (clipCache.has(path)) return;
        const loader = state.clipLoads.get(path);
        if (loader?.promise) return;
        scheduleClipWarm(path);
    }

    function slugify(value) {
        if (!value) return 'unknown';
        return String(value)
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '')
            .replace(/-{2,}/g, '-') || 'unknown';
    }

    function normalizeReturnTarget(target) {
        if (!target) return '/static/community.html';
        try {
            const url = new URL(target, window.location.origin);
            return `${url.pathname}${url.search}${url.hash || ''}`;
        } catch {
            return '/static/community.html';
        }
    }

    function redirectToLogin(target) {
        const loginUrl = new URL('/static/login.html', window.location.origin);
        const returnTarget = normalizeReturnTarget(target);
        loginUrl.searchParams.set('return', returnTarget);
        window.location.href = loginUrl.toString();
    }

    function getAuthState() {
        if (authPromise) return authPromise;
        if (window.__AUTH_PROMISE) {
            authPromise = window.__AUTH_PROMISE
                .then(payload => {
                    const user = payload?.user || window.__AUTH_USER || null;
                    state.authUser = user;
                    state.authed = !!user;
                    state.authChecked = true;
                    try {
                        window.__AUTHED = !!user;
                        window.__AUTH_USER = user || null;
                    } catch { }
                    return payload || {};
                })
                .catch(() => {
                    state.authUser = null;
                    state.authed = false;
                    state.authChecked = true;
                    try {
                        window.__AUTHED = false;
                        window.__AUTH_USER = null;
                    } catch { }
                    return {};
                });
            return authPromise;
        }
        authPromise = fetch('/api/auth/me', { credentials: 'include', cache: 'no-store' })
            .then(res => (res.ok ? res.json() : null))
            .then(payload => {
                const user = payload?.user || null;
                state.authUser = user;
                state.authed = !!user;
                state.authChecked = true;
                try {
                    window.__AUTHED = !!user;
                    window.__AUTH_USER = user || null;
                } catch { }
                return payload || {};
            })
            .catch(() => {
                state.authUser = null;
                state.authed = false;
                state.authChecked = true;
                try {
                    window.__AUTHED = false;
                    window.__AUTH_USER = null;
                } catch { }
                return {};
            });
        try { window.__AUTH_PROMISE = authPromise; } catch { }
        return authPromise;
    }

    async function ensureAuthOrRedirect(target) {
        const payload = await getAuthState();
        if (payload?.user || state.authed) return true;
        redirectToLogin(target || window.location.href);
        return false;
    }

    function updateQueryParam(key, value) {
        try {
            const url = new URL(window.location.href);
            if (value) url.searchParams.set(key, value);
            else url.searchParams.delete(key);
            window.history.replaceState({}, '', url.toString());
        } catch { }
    }

    function formatRelativeTime(ts) {
        if (!ts) return '';
        const t = typeof ts === 'number' ? ts : Number(ts);
        if (!Number.isFinite(t) || t <= 0) return '';
        const diff = Date.now() - t;
        const abs = Math.abs(diff);
        const minute = 60 * 1000;
        const hour = 60 * minute;
        const day = 24 * hour;
        const week = 7 * day;
        if (abs < minute) return 'just now';
        if (abs < hour) {
            const mins = Math.round(abs / minute);
            return `${mins} min ${diff < 0 ? 'from now' : 'ago'}`;
        }
        if (abs < day) {
            const hrs = Math.round(abs / hour);
            return `${hrs} hr${hrs === 1 ? '' : 's'} ${diff < 0 ? 'from now' : 'ago'}`;
        }
        if (abs < week) {
            const days = Math.round(abs / day);
            return `${days} day${days === 1 ? '' : 's'} ${diff < 0 ? 'from now' : 'ago'}`;
        }
        const date = new Date(t);
        return date.toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            year: date.getFullYear() === new Date().getFullYear() ? undefined : 'numeric',
        });
    }

    function safeCount(value) {
        const num = Number(value);
        if (!Number.isFinite(num) || num <= 0) return 0;
        return Math.round(num);
    }

    function loadActionStore() {
        try {
            const raw = localStorage.getItem(ACTIONS_STORAGE_KEY);
            if (!raw) return {};
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed === 'object') return parsed;
        } catch { }
        return {};
    }

    function persistActionStore() {
        try {
            localStorage.setItem(ACTIONS_STORAGE_KEY, JSON.stringify(actionStore || {}));
        } catch { }
    }

    function getPostSessionId(post) {
        const sid = post?.sessionId || post?.id || post?.sid;
        return sid ? String(sid) : '';
    }

    function getActionKey(post) {
        const sid = getPostSessionId(post);
        if (sid) return sid;
        const label = post?.title || post?.projectName || post?.project || 'session';
        const created = post?.createdAt || '';
        return `${slugify(label)}-${created || 'unknown'}`;
    }

    function normalizeComment(entry) {
        if (!entry) return null;
        if (typeof entry === 'string') {
            const text = entry.trim();
            if (!text) return null;
            return { text, author: 'Community member', ts: Date.now() };
        }
        if (typeof entry === 'object') {
            const text = String(entry.text || entry.comment || entry.body || '').trim();
            if (!text) return null;
            const author = String(entry.author || entry.user || entry.name || 'Community member').trim() || 'Community member';
            const ts = Number(entry.ts || entry.createdAt || Date.now());
            return { text, author, ts: Number.isFinite(ts) ? ts : Date.now() };
        }
        return null;
    }

    function seedActionState(post) {
        const comments = [];
        if (Array.isArray(post?.comments)) {
            post.comments.forEach(entry => {
                const normalized = normalizeComment(entry);
                if (normalized) comments.push(normalized);
            });
        }
        const likes = safeCount(post?.likeCount ?? post?.likes ?? post?.favorites ?? post?.reactions ?? 0);
        const shares = safeCount(post?.shareCount ?? post?.shares ?? post?.reposts ?? 0);
        const subscribers = safeCount(post?.subscriberCount ?? post?.subscribeCount ?? post?.alertsCount ?? 0);
        const commentCount = safeCount(post?.commentCount ?? post?.commentsCount ?? comments.length);
        return {
            likes,
            shares,
            subscribers,
            liked: !!post?.liked,
            subscribed: !!post?.subscribed,
            comments,
            commentCount: Math.max(commentCount, comments.length),
        };
    }

    function getActionState(post) {
        const key = getActionKey(post);
        const stored = (actionStore && key && actionStore[key]) || {};
        const seed = seedActionState(post);
        const comments = Array.isArray(stored.comments) ? stored.comments : seed.comments;
        const commentCount = seed.commentCount;
        const likes = seed.likes;
        const shares = seed.shares;
        const subscribers = seed.subscribers;
        const liked = typeof stored.liked === 'boolean' ? stored.liked : seed.liked;
        const subscribed = typeof stored.subscribed === 'boolean' ? stored.subscribed : seed.subscribed;
        return {
            key,
            likes,
            shares,
            subscribers,
            liked,
            subscribed,
            comments,
            commentCount: Math.max(commentCount || 0, comments.length),
        };
    }

    function saveActionState(actionState) {
        if (!actionState?.key) return;
        actionStore[actionState.key] = {
            likes: actionState.likes,
            shares: actionState.shares,
            subscribers: actionState.subscribers,
            liked: actionState.liked,
            subscribed: actionState.subscribed,
            comments: actionState.comments,
            commentCount: actionState.commentCount,
        };
        persistActionStore();
    }

    function getCommentCount(actionState) {
        if (!actionState) return 0;
        const base = Number.isFinite(actionState.commentCount) ? actionState.commentCount : 0;
        const list = Array.isArray(actionState.comments) ? actionState.comments.length : 0;
        return Math.max(base, list);
    }

    function getCommentAuthor() {
        const user = state.authUser || window.__AUTH_USER || {};
        const name = user?.name || user?.displayName || user?.first_name || user?.firstName || user?.email;
        if (name) return String(name);
        const guest = window.__GUEST_NAME || sessionStorage.getItem('visaionGuestName');
        if (guest) return String(guest);
        return 'Community member';
    }

    function isPostByUser(post) {
        const user = state.authUser || window.__AUTH_USER;
        if (!user) return false;
        const authorRaw = String(post?.author || post?.user || post?.name || '').trim().toLowerCase();
        if (!authorRaw) return false;
        const email = String(user?.email || '').trim().toLowerCase();
        const name = String(user?.name || user?.handle || '').trim().toLowerCase();
        const userId = String(user?.user_id || user?.id || '').trim();
        const postUserId = String(post?.user_id || post?.userId || '').trim();
        if (userId && postUserId && userId === postUserId) return true;
        if (email && authorRaw.includes(email)) return true;
        if (name && authorRaw.includes(name)) return true;
        return false;
    }

    function showPrompt(text, duration = 2400) {
        if (typeof window.showPrompt === 'function') {
            window.showPrompt(text, duration);
        } else if (text) {
            alert(text);
        }
    }

    function getShareUrl(post) {
        const sid = getPostSessionId(post);
        if (!sid) return window.location.href;
        try {
            const url = new URL(window.location.href);
            url.searchParams.set('sid', sid);
            return url.toString();
        } catch {
            return `${window.location.origin}${window.location.pathname}?sid=${encodeURIComponent(sid)}`;
        }
    }

    async function copyToClipboard(text) {
        if (!text) return false;
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch { }
        }
        try {
            const input = document.createElement('input');
            input.value = text;
            input.setAttribute('readonly', 'readonly');
            input.style.position = 'fixed';
            input.style.left = '-9999px';
            document.body.appendChild(input);
            input.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(input);
            return !!ok;
        } catch { }
        return false;
    }

    function createIcon(pathD) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', pathD);
        svg.appendChild(path);
        return svg;
    }

    function buildActionButton(type, label, iconPath, countText) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `post-action ${type}`;
        btn.setAttribute('aria-label', label);
        btn.title = label;
        const icon = createIcon(iconPath);
        const count = document.createElement('span');
        count.className = 'count';
        count.textContent = String(countText ?? 0);
        btn.append(icon, count);
        return btn;
    }

    function setActionCount(btn, value) {
        const count = btn?.querySelector?.('.count');
        if (!count) return;
        count.textContent = String(value ?? 0);
    }

    function ensureCommentModal() {
        if (state.commentModal) return state.commentModal;
        const modal = document.createElement('div');
        modal.className = 'comment-modal';
        modal.innerHTML = `
            <div class="comment-panel">
                <div class="comment-head">
                    <div class="comment-title">Comments</div>
                    <button type="button" class="comment-close">Close</button>
                </div>
                <div class="comment-list"></div>
                <form class="comment-form">
                    <input type="text" maxlength="180" placeholder="Add a comment..." />
                    <button type="submit">Post</button>
                </form>
            </div>
        `;
        document.body.appendChild(modal);
        const listEl = modal.querySelector('.comment-list');
        const titleEl = modal.querySelector('.comment-title');
        const closeBtn = modal.querySelector('.comment-close');
        const form = modal.querySelector('.comment-form');
        const input = modal.querySelector('.comment-form input');
        const modalState = {
            el: modal,
            listEl,
            titleEl,
            form,
            input,
            context: null,
        };
        const close = () => {
            modal.classList.remove('open');
            modalState.context = null;
            if (!modalEl?.classList.contains('open')) {
                document.body.classList.remove('modal-open');
            }
        };
        if (closeBtn) closeBtn.addEventListener('click', close);
        modal.addEventListener('click', ev => {
            if (ev.target === modal) close();
        });
        document.addEventListener('keydown', ev => {
            if (ev.key === 'Escape' && modal.classList.contains('open')) {
                close();
            }
        });
        if (form) {
            form.addEventListener('submit', async ev => {
                ev.preventDefault();
                if (!modalState.context) return;
                const text = input?.value?.trim();
                if (!text) return;
                const actionState = modalState.context.actionState;
                const payload = await updateCommunityAction(modalState.context.post, {
                    action: 'comment',
                    comment: {
                        text,
                        author: getCommentAuthor(),
                        ts: Date.now(),
                    },
                });
                if (payload) {
                    applyActionPayload(actionState, payload);
                    renderCommentList(listEl, actionState);
                } else {
                    showPrompt('Unable to post comment right now.');
                    renderCommentList(listEl, actionState);
                }
                if (modalState.titleEl) {
                    const title = modalState.context?.post?.title || modalState.context?.post?.projectName || 'Session comments';
                    const count = getCommentCount(actionState);
                    modalState.titleEl.textContent = `${title} (${count})`;
                }
                if (typeof modalState.context.onUpdate === 'function') {
                    modalState.context.onUpdate(actionState);
                }
                if (input) {
                    input.value = '';
                    input.focus();
                }
            });
        }
        state.commentModal = modalState;
        return modalState;
    }

    function renderCommentList(listEl, actionState) {
        if (!listEl) return;
        listEl.textContent = '';
        const comments = Array.isArray(actionState?.comments) ? actionState.comments : [];
        if (!comments.length) {
            const empty = document.createElement('div');
            empty.className = 'comment-item';
            empty.textContent = 'No comments yet.';
            listEl.appendChild(empty);
            return;
        }
        comments.slice(-80).forEach(comment => {
            const item = document.createElement('div');
            item.className = 'comment-item';
            const meta = document.createElement('div');
            meta.className = 'comment-meta';
            const author = comment?.author || 'Community member';
            const time = formatRelativeTime(comment?.ts);
            meta.textContent = `${author}${time ? ` - ${time}` : ''}`;
            const body = document.createElement('div');
            body.textContent = comment?.text || '';
            item.append(meta, body);
            listEl.appendChild(item);
        });
    }

    function openCommentModal(post, actionState, onUpdate) {
        const modal = ensureCommentModal();
        if (modal.titleEl) {
            const title = post?.title || post?.projectName || 'Session comments';
            const count = getCommentCount(actionState);
            modal.titleEl.textContent = `${title} (${count})`;
        }
        modal.context = { post, actionState, onUpdate };
        renderCommentLoading(modal.listEl);
        modal.el.classList.add('open');
        document.body.classList.add('modal-open');
        if (modal.input) {
            modal.input.value = '';
            modal.input.focus();
        }
        fetchActionPayload(post).then(payload => {
            if (payload) {
                applyActionPayload(actionState, payload);
                renderCommentList(modal.listEl, actionState);
                if (modal.titleEl) {
                    const title = post?.title || post?.projectName || 'Session comments';
                    const count = getCommentCount(actionState);
                    modal.titleEl.textContent = `${title} (${count})`;
                }
                if (typeof onUpdate === 'function') onUpdate(actionState);
            } else {
                renderCommentList(modal.listEl, actionState);
            }
        });
    }

    function renderCommentLoading(listEl) {
        if (!listEl) return;
        listEl.textContent = '';
        const item = document.createElement('div');
        item.className = 'comment-item';
        item.textContent = 'Loading comments...';
        listEl.appendChild(item);
    }

    function normalizeActionPayload(payload) {
        if (!payload || typeof payload !== 'object') return null;
        const comments = Array.isArray(payload.comments) ? payload.comments.map(normalizeComment).filter(Boolean) : null;
        return {
            likeCount: safeCount(payload.likeCount ?? payload.likes),
            commentCount: safeCount(payload.commentCount ?? payload.commentsCount),
            shareCount: safeCount(payload.shareCount ?? payload.shares),
            subscriberCount: safeCount(payload.subscriberCount ?? payload.subscribeCount ?? payload.alertsCount),
            comments,
        };
    }

    function applyActionPayload(actionState, payload) {
        const normalized = normalizeActionPayload(payload);
        if (!normalized || !actionState) return;
        if (Number.isFinite(normalized.likeCount)) actionState.likes = normalized.likeCount;
        if (Number.isFinite(normalized.shareCount)) actionState.shares = normalized.shareCount;
        if (Number.isFinite(normalized.subscriberCount)) actionState.subscribers = normalized.subscriberCount;
        if (Array.isArray(normalized.comments)) {
            actionState.comments = normalized.comments;
        }
        if (Number.isFinite(normalized.commentCount)) {
            actionState.commentCount = normalized.commentCount;
        }
        if (Array.isArray(actionState.comments)) {
            actionState.commentCount = Math.max(actionState.commentCount || 0, actionState.comments.length);
        }
        saveActionState(actionState);
    }

    async function fetchActionPayload(post) {
        const sid = getPostSessionId(post);
        if (!sid) return null;
        try {
            const res = await fetch(ACTIONS_ENDPOINT(sid), { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            return data?.actions || data;
        } catch (err) {
            console.warn('[community] actions fetch failed', err);
            return null;
        }
    }

    async function updateCommunityAction(post, payload) {
        const sid = getPostSessionId(post);
        if (!sid) return null;
        try {
            const res = await fetch(ACTIONS_ENDPOINT(sid), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload || {}),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            return data?.actions || data;
        } catch (err) {
            console.warn('[community] action update failed', err);
            return null;
        }
    }

    async function handleShare(post, actionState, shareBtn) {
        const url = getShareUrl(post);
        const title = post?.title || 'visaion session recap';
        const shareData = { title, text: 'Check out this session recap.', url };
        const finalize = async (message) => {
            const payload = await updateCommunityAction(post, { action: 'share' });
            if (payload) {
                applyActionPayload(actionState, payload);
                setActionCount(shareBtn, actionState.shares);
            }
            if (message) {
                const notice = payload ? message : `${message} (count not updated)`;
                showPrompt(notice);
            } else if (!payload) {
                showPrompt('Shared, but count not updated.');
            }
        };
        if (navigator.share) {
            try {
                await navigator.share(shareData);
                await finalize('Shared.');
                return;
            } catch (err) {
                if (err?.name === 'AbortError') return;
            }
        }
        const copied = await copyToClipboard(url);
        if (copied) {
            await finalize('Link copied.');
        } else {
            showPrompt('Unable to share right now.');
        }
    }

    function isGolfSession(meta = {}) {
        const tags = Array.isArray(meta?.tags) ? meta.tags : [];
        if (tags.some(tag => String(tag).toLowerCase() === 'golf')) return true;
        const fields = [meta?.project, meta?.projectName, meta?.dataset, meta?.title];
        return fields.some(val => String(val || '').toLowerCase().includes('golf'));
    }

    function getAttemptMeta(meta = {}) {
        const rawAttempt = meta?.attemptLabel || meta?.workflow?.attemptLabel;
        let attemptLabel = typeof rawAttempt === 'string' ? rawAttempt.trim().toLowerCase() : '';
        if (!attemptLabel) attemptLabel = isGolfSession(meta) ? 'swing' : 'shot';
        const attemptLabelTitle = attemptLabel.charAt(0).toUpperCase() + attemptLabel.slice(1);
        const attemptLabelPlural = attemptLabel === 'swing' ? 'Swings' : 'Shots';
        const rawAttemptsLabel = meta?.attemptsLabel;
        const attemptsLabel = typeof rawAttemptsLabel === 'string' && rawAttemptsLabel.trim()
            ? rawAttemptsLabel.trim()
            : (attemptLabel === 'swing' ? 'Swings Taken' : 'Shots Taken');
        return { attemptLabel, attemptLabelTitle, attemptLabelPlural, attemptsLabel };
    }

    function getActiveAttemptMeta() {
        return getAttemptMeta(state.activeDetail || {});
    }

    function buildFilters(posts) {
        const map = new Map();
        posts.forEach(post => {
            const label = (post.projectName || post.project || post.dataset || 'Sessions').trim();
            const id = slugify(label);
            if (!map.has(id)) {
                map.set(id, { id, label });
            }
        });
        const filters = [
            { id: 'all', label: 'All' },
            { id: 'favorites', label: 'Favorites', requiresAuth: true },
            { id: 'mine', label: 'Yours', requiresAuth: true },
            ...map.values(),
        ];
        state.filters = filters;
        if (!filters.some(f => f.id === state.activeFilter)) {
            state.activeFilter = 'all';
        }
    }

    function buildTagFilters(posts) {
        const tagSet = new Map();
        posts.forEach(post => {
            (post.tags || []).forEach(tag => {
                const clean = String(tag || '').trim();
                if (!clean) return;
                const id = `tag-${slugify(clean)}`;
                if (!tagSet.has(id)) {
                    tagSet.set(id, { id, label: clean, raw: clean });
                }
            });
        });
        const tags = [{ id: 'all', label: 'All tags', raw: null }, ...tagSet.values()];
        if (!tags.some(t => t.id === state.activeTag)) {
            state.activeTag = 'all';
        }
        state.tagFilters = tags;
        return tags;
    }

    function updateTagToggle(hasTags) {
        const showTags = hasTags && state.tagsOpen;
        if (tagToggleBtn) {
            tagToggleBtn.disabled = !hasTags;
            tagToggleBtn.setAttribute('aria-expanded', showTags ? 'true' : 'false');
        }
        if (tagFilterEl) {
            tagFilterEl.style.display = showTags ? 'flex' : 'none';
        }
        if (filterBarEl) {
            filterBarEl.classList.toggle('show-tags', showTags);
        }
    }

    function toggleTagFilters(force) {
        const hasTags = (state.tagFilters || []).length > 1;
        if (!hasTags) {
            state.tagsOpen = false;
            updateTagToggle(false);
            return;
        }
        state.tagsOpen = typeof force === 'boolean' ? force : !state.tagsOpen;
        updateTagToggle(true);
    }

    function setActiveFilter(filterId, { updateUrl = true } = {}) {
        state.activeFilter = filterId || 'all';
        if (updateUrl) {
            updateQueryParam('view', state.activeFilter !== 'all' ? state.activeFilter : '');
        }
        renderFilters();
        renderFeed();
        updateNavActive();
    }

    function setActiveTag(tagId, { updateUrl = true } = {}) {
        state.activeTag = tagId || 'all';
        const tagObj = (state.tagFilters || []).find(t => t.id === state.activeTag);
        const rawTag = tagObj?.raw || tagObj?.label;
        if (updateUrl) {
            updateQueryParam('tag', rawTag ? String(rawTag) : '');
        }
        renderTagFilters(state.tagFilters || []);
        renderFeed();
    }

    function renderFilters() {
        if (!filterEl) return;
        filterEl.textContent = '';
        state.filters.forEach(filter => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'filter-btn' + (filter.id === state.activeFilter ? ' active' : '');
            btn.textContent = filter.label;
            btn.setAttribute('aria-pressed', filter.id === state.activeFilter ? 'true' : 'false');
            btn.addEventListener('click', async () => {
                if (filter.requiresAuth) {
                    const viewUrl = new URL(window.location.href);
                    viewUrl.searchParams.set('view', filter.id);
                    const ok = await ensureAuthOrRedirect(viewUrl.toString());
                    if (!ok) return;
                }
                setActiveFilter(filter.id);
            });
            filterEl.appendChild(btn);
        });
    }

    function renderTagFilters(tags) {
        if (!tagFilterEl) return;
        tagFilterEl.textContent = '';
        if (tags.length <= 1) {
            state.tagsOpen = false;
            updateTagToggle(false);
            return;
        }
        if (state.activeTag !== 'all') {
            state.tagsOpen = true;
        }
        updateTagToggle(true);
        tags.forEach(tag => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'tag-filter-btn' + (tag.id === state.activeTag ? ' active' : '');
            const label = tag.id === 'all'
                ? tag.label
                : (tag.label.startsWith('#') ? tag.label : `#${tag.label}`);
            btn.textContent = label;
            btn.addEventListener('click', () => {
                setActiveTag(tag.id);
            });
            tagFilterEl.appendChild(btn);
        });
    }

    function getPreviewUrl(post) {
        if (!post || !post.preview) return null;
        if (post.previewRev) return `${post.preview}?rev=${post.previewRev}`;
        return `${post.preview}?cb=${Date.now()}`;
    }

    function getClipPath(clip) {
        if (!clip) return null;
        if (typeof clip === 'string') return clip;
        if (typeof clip === 'object') {
            const raw = clip.path || clip.url || clip.href;
            return typeof raw === 'string' ? raw : null;
        }
        return null;
    }

    function getClipDimensions(shot) {
        const clip = shot?.clip;
        if (!clip || typeof clip !== 'object') return null;
        const width = Number(clip.width ?? clip.w ?? clip.clipWidth ?? clip.videoWidth);
        const height = Number(clip.height ?? clip.h ?? clip.clipHeight ?? clip.videoHeight);
        if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
        return { width, height };
    }

    function applyPortraitFix(videoEl, shot) {
        if (!videoEl) return;
        const dims = getClipDimensions(shot);
        const width = dims?.width ?? Number(videoEl.videoWidth);
        const height = dims?.height ?? Number(videoEl.videoHeight);
        if (!Number.isFinite(width) || !Number.isFinite(height)) {
            videoEl.classList.remove('video-portrait-fix');
            return;
        }
        videoEl.classList.toggle('video-portrait-fix', height > width);
    }

    function matchesFilter(post) {
        if (state.activeFilter === 'favorites') {
            const actionState = getActionState(post);
            if (!actionState.liked) return false;
        } else if (state.activeFilter === 'mine') {
            if (!isPostByUser(post)) return false;
        } else if (state.activeFilter !== 'all') {
            const label = (post.projectName || post.project || post.dataset || '').trim();
            const slug = slugify(label);
            if (slug !== state.activeFilter) return false;
        }
        if (state.activeTag !== 'all') {
            const tagObj = (state.tagFilters || []).find(t => t.id === state.activeTag);
            const rawTag = (tagObj?.raw || tagObj?.label || state.activeTag.replace(/^tag-/, '')).toLowerCase();
            const tags = (post.tags || []).map(t => String(t || '').trim().toLowerCase());
            if (!tags.includes(rawTag)) return false;
        }
        return true;
    }

    function renderFeed() {
        if (!feedEl) return;
        feedEl.textContent = '';
        const posts = state.posts.filter(matchesFilter);
        if (!posts.length) {
            if (emptyStateEl) emptyStateEl.hidden = false;
            if (emptyStateEl) {
                if (state.activeFilter === 'favorites') {
                    emptyStateEl.textContent = state.authed
                        ? 'No favorites yet. Tap the heart to save a session.'
                        : 'Log in to see your favorites.';
                } else if (state.activeFilter === 'mine') {
                    emptyStateEl.textContent = state.authed
                        ? 'No sessions from you yet. Start one and share it.'
                        : 'Log in to see your sessions.';
                } else {
                    emptyStateEl.textContent = 'Sessions will appear as soon as someone wraps a training run.';
                }
            }
            return;
        }
        if (emptyStateEl) emptyStateEl.hidden = true;
        posts.forEach(post => feedEl.appendChild(createPostCard(post)));
    }

    function updateNavActive() {
        if (!navItems.length) return;
        navItems.forEach(item => item.classList.remove('is-active'));
        if (state.activeFilter === 'favorites') {
            const fav = navItems.find(item => item.dataset.nav === 'favorites');
            if (fav) fav.classList.add('is-active');
        }
    }

    function openStartSheet() {
        if (!startSheetEl) return;
        startSheetEl.classList.add('open');
        document.body.classList.add('modal-open');
        updateQueryParam('start', '');
    }

    function closeStartSheet() {
        if (!startSheetEl) return;
        startSheetEl.classList.remove('open');
        document.body.classList.remove('modal-open');
    }

    async function handleStartSession() {
        const returnUrl = new URL(window.location.href);
        returnUrl.searchParams.set('start', '1');
        const ok = await ensureAuthOrRedirect(returnUrl.toString());
        if (!ok) return;
        openStartSheet();
    }

    async function handleNavClick(event) {
        const btn = event.target?.closest?.('[data-nav]');
        if (!btn) return;
        const nav = btn.dataset.nav;
        const target = btn.dataset.navTarget;
        if (nav === 'start') {
            await handleStartSession();
            return;
        }
        if (!target) return;
        const ok = await ensureAuthOrRedirect(target);
        if (!ok) return;
        const targetUrl = new URL(target, window.location.origin);
        if (targetUrl.pathname === window.location.pathname) {
            const view = targetUrl.searchParams.get('view');
            if (view) {
                setActiveFilter(view, { updateUrl: true });
                return;
            }
        }
        window.location.href = target;
    }

    function applyInitialView() {
        try {
            const params = new URLSearchParams(window.location.search);
            const viewRaw = (params.get('view') || params.get('filter') || '').toLowerCase();
            const tagRaw = params.get('tag');
            if (viewRaw) {
                if (['favorites', 'favorite', 'fav', 'likes', 'liked'].includes(viewRaw)) {
                    state.activeFilter = 'favorites';
                } else if (['mine', 'yours', 'my', 'my-sessions'].includes(viewRaw)) {
                    state.activeFilter = 'mine';
                } else {
                    state.activeFilter = slugify(viewRaw);
                }
            }
            if (tagRaw) {
                const normalized = slugify(tagRaw);
                state.activeTag = normalized ? `tag-${normalized}` : 'all';
            }
            state.startRequested = params.get('start') === '1';
        } catch { }
    }

    function maybeOpenStartFromQuery() {
        if (!state.startRequested) return;
        if (!state.authed) return;
        state.startRequested = false;
        openStartSheet();
    }

    function maybeOpenSharedSession() {
        if (!state.pendingShareSid) return;
        const sid = String(state.pendingShareSid);
        state.pendingShareSid = null;
        const target = state.posts.find(post => getPostSessionId(post) === sid);
        if (target) {
            openPostDetail(target);
        } else {
            showPrompt('Shared session not found.');
        }
    }

    function createPostCard(post) {
        const card = document.createElement('article');
        card.className = 'post';
        card.dataset.sessionId = post.sessionId || post.id || '';

        const preview = getPreviewUrl(post);
        const media = document.createElement('div');
        media.className = 'post-media';
        if (preview) {
            const img = document.createElement('img');
            img.src = preview;
            img.alt = `${post.projectName || post.title || 'Session'} preview`;
            media.appendChild(img);
        } else {
            const placeholder = document.createElement('div');
            placeholder.className = 'post-media-placeholder';
            placeholder.textContent = 'Preview coming soon';
            media.appendChild(placeholder);
        }

        const body = document.createElement('div');
        body.className = 'post-body';

        const meta = document.createElement('div');
        meta.className = 'post-meta';
        const author = document.createElement('span');
        author.textContent = post.author || 'Player';
        const time = document.createElement('span');
        time.textContent = formatRelativeTime(post.createdAt);
        meta.append(author, time);

        const title = document.createElement('h3');
        title.className = 'post-title';
        title.textContent = post.title || `${post.projectName || post.project || 'Session'} recap`;

        const summary = document.createElement('p');
        summary.className = 'post-summary';
        const trimmed = (post.summary || '').trim();
        summary.textContent = trimmed || 'Recap ready. Tap to review the session.';

        const stats = document.createElement('ul');
        stats.className = 'post-stats';
        const attempts = Number(post?.stats?.attempts) || 0;
        const accuracy = post?.stats?.accuracy;
        const poseAvg = post?.stats?.poseAverage;
        const isGolf = isGolfSession(post);
        stats.appendChild(renderStatItem(isGolf ? 'Swings' : 'Attempts', attempts ? String(attempts) : '—'));
        if (!isGolf) {
            stats.appendChild(renderStatItem('Accuracy', Number.isFinite(accuracy) ? `${accuracy}%` : '—'));
        }
        stats.appendChild(renderStatItem('Pose avg', Number.isFinite(poseAvg) ? `${poseAvg}` : '—'));

        const tagsBar = document.createElement('div');
        tagsBar.className = 'tags';
        (post.tags || []).forEach(tag => {
            const chip = document.createElement('span');
            chip.className = 'tag';
            chip.textContent = tag;
            tagsBar.appendChild(chip);
        });

        const actionState = getActionState(post);
        const actions = document.createElement('div');
        actions.className = 'post-actions';

        const likeBtn = buildActionButton('like', 'Like', ACTION_ICONS.like, actionState.likes);
        if (actionState.liked) likeBtn.classList.add('is-active');
        likeBtn.addEventListener('click', async ev => {
            ev.stopPropagation();
            const prevLiked = actionState.liked;
            const prevLikes = actionState.likes;
            const nextLiked = !actionState.liked;
            const delta = nextLiked ? 1 : -1;
            actionState.liked = nextLiked;
            actionState.likes = Math.max(0, (Number(actionState.likes) || 0) + delta);
            saveActionState(actionState);
            likeBtn.classList.toggle('is-active', actionState.liked);
            setActionCount(likeBtn, actionState.likes);
            const payload = await updateCommunityAction(post, { action: 'like', delta });
            if (payload) {
                applyActionPayload(actionState, payload);
                setActionCount(likeBtn, actionState.likes);
            } else {
                actionState.liked = prevLiked;
                actionState.likes = prevLikes;
                saveActionState(actionState);
                likeBtn.classList.toggle('is-active', actionState.liked);
                setActionCount(likeBtn, actionState.likes);
                showPrompt('Unable to update like right now.');
            }
        });

        const commentBtn = buildActionButton('comment', 'Comment', ACTION_ICONS.comment, getCommentCount(actionState));
        commentBtn.addEventListener('click', ev => {
            ev.stopPropagation();
            openCommentModal(post, actionState, updated => {
                setActionCount(commentBtn, getCommentCount(updated));
            });
        });

        const shareBtn = buildActionButton('share', 'Share', ACTION_ICONS.share, actionState.shares);
        shareBtn.addEventListener('click', ev => {
            ev.stopPropagation();
            handleShare(post, actionState, shareBtn);
        });

        const alertsBtn = buildActionButton('alerts', 'Subscribe', ACTION_ICONS.alerts, actionState.subscribers ?? 0);
        alertsBtn.classList.toggle('is-active', actionState.subscribed);
        alertsBtn.addEventListener('click', async ev => {
            ev.stopPropagation();
            const prevSubscribed = actionState.subscribed;
            const prevSubscribers = actionState.subscribers;
            const nextSubscribed = !actionState.subscribed;
            const delta = nextSubscribed ? 1 : -1;
            actionState.subscribed = nextSubscribed;
            actionState.subscribers = Math.max(0, (Number(actionState.subscribers) || 0) + delta);
            saveActionState(actionState);
            alertsBtn.classList.toggle('is-active', actionState.subscribed);
            setActionCount(alertsBtn, actionState.subscribers);
            const payload = await updateCommunityAction(post, { action: 'subscribe', delta });
            if (payload) {
                applyActionPayload(actionState, payload);
                setActionCount(alertsBtn, actionState.subscribers);
            } else {
                actionState.subscribed = prevSubscribed;
                actionState.subscribers = prevSubscribers;
                saveActionState(actionState);
                alertsBtn.classList.toggle('is-active', actionState.subscribed);
                setActionCount(alertsBtn, actionState.subscribers);
                showPrompt('Unable to update alerts right now.');
                return;
            }
            showPrompt(actionState.subscribed ? 'Alerts enabled.' : 'Alerts paused.');
        });

        actions.append(likeBtn, commentBtn, shareBtn, alertsBtn);

        const footer = document.createElement('div');
        footer.className = 'post-footer';
        footer.append(actions);

        body.append(meta, title, summary, stats, tagsBar, footer);
        card.append(media, body);
        card.setAttribute('role', 'button');
        card.tabIndex = 0;
        card.addEventListener('click', () => openPostDetail(post));
        card.addEventListener('keydown', ev => {
            if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                openPostDetail(post);
            }
        });
        return card;
    }

    function renderStatItem(label, value) {
        const item = document.createElement('li');
        const span = document.createElement('span');
        span.textContent = label;
        const strong = document.createElement('strong');
        strong.textContent = value;
        item.append(span, strong);
        return item;
    }

    async function loadFeed() {
        if (!feedEl) return;
        const authReady = getAuthState();
        try {
            feedEl.dataset.loading = '1';
            const res = await fetch(FEED_ENDPOINT, { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const posts = Array.isArray(data?.posts) ? data.posts : [];
            posts.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
            state.posts = posts;
            buildFilters(posts);
            const tagFilters = buildTagFilters(posts);
            renderFilters();
            renderTagFilters(tagFilters);
            renderFeed();
            updateNavActive();
            maybeOpenSharedSession();
        } catch (err) {
            console.error('[community] load feed failed', err);
            if (emptyStateEl) {
                emptyStateEl.hidden = false;
                emptyStateEl.textContent = 'Unable to load the community feed. Please refresh to retry.';
            }
        } finally {
            authReady.then(() => {
                renderFilters();
                renderFeed();
                updateNavActive();
                maybeOpenStartFromQuery();
            });
            if (feedEl) delete feedEl.dataset.loading;
        }
    }

    async function openPostDetail(post) {
        if (!modalEl || state.loadingDetail) return;
        state.loadingDetail = true;
        showModal();
        setModalLoading(post);
        clearClipCache();
        state.loadingClipToken = null;
        if (modalVideoEl) {
            modalVideoEl.pause();
            if (modalVideoEl.src && modalVideoEl.src.startsWith('blob:')) {
                try { URL.revokeObjectURL(modalVideoEl.src); } catch { }
            }
            modalVideoEl.removeAttribute('src');
            modalVideoEl.load();
        }
        try {
            const res = await fetch(DETAIL_ENDPOINT(post.sessionId || post.id), { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const detail = await res.json();
            populateModal(detail, post);
            startPlayback(detail);
        } catch (err) {
            console.error('[community] detail load failed', err);
            setModalError('Unable to load this session recap. Please try again later.');
        } finally {
            state.loadingDetail = false;
        }
    }

    function setModalLoading(post) {
        if (!modalTitleEl) return;
        modalTitleEl.textContent = post?.title || 'Session recap';
        if (modalMetaEl) {
            modalMetaEl.textContent = 'Loading recap...';
        }
        if (modalSummaryEl) {
            modalSummaryEl.textContent = '';
        }
        if (modalHighlightsEl) {
            modalHighlightsEl.textContent = '';
        }
        if (modalStatsEl) {
            modalStatsEl.innerHTML = '';
        }
        if (modalShotListEl) {
            modalShotListEl.innerHTML = '';
        }
        if (modalOverlayEl) {
            modalOverlayEl.classList.remove('show');
            modalOverlayEl.innerHTML = '';
        }
        if (modalVideoEl) {
            modalVideoEl.pause();
            modalVideoEl.classList.remove('video-portrait-fix');
            modalVideoEl.removeAttribute('src');
            modalVideoEl.load();
        }
    }

    function setModalError(message) {
        if (modalSummaryEl) modalSummaryEl.textContent = message || 'Unable to load session.';
        if (modalShotListEl) modalShotListEl.innerHTML = '';
        if (modalVideoEl) {
            modalVideoEl.pause();
            modalVideoEl.removeAttribute('src');
            modalVideoEl.load();
        }
    }

    function populateModal(detail, fallbackPost) {
        state.activeDetail = detail;
        state.activeShotIndex = -1;
        const attemptMeta = getAttemptMeta(detail || fallbackPost || {});
        if (modalTitleEl) {
            modalTitleEl.textContent = detail?.title || fallbackPost?.title || 'Session recap';
        }
        if (modalMetaEl) {
            const authored = detail?.author || fallbackPost?.author || 'Player';
            const time = formatRelativeTime(detail?.createdAt || fallbackPost?.createdAt);
            modalMetaEl.textContent = `${authored}${time ? ` - ${time}` : ''}`;
        }
        if (modalSummaryEl) {
            const text = (detail?.summary || fallbackPost?.summary || '').trim();
            modalSummaryEl.textContent = text || 'Recap generated from the latest session.';
        }
        if (modalHighlightsEl) {
            modalHighlightsEl.innerHTML = '';
            const highlights = Array.isArray(detail?.highlights) ? detail.highlights : [];
            if (highlights.length) {
                highlights.forEach(item => {
                    const li = document.createElement('li');
                    li.textContent = item;
                    modalHighlightsEl.appendChild(li);
                });
                modalHighlightsEl.parentElement?.removeAttribute('hidden');
            } else {
                modalHighlightsEl.parentElement?.setAttribute('hidden', 'hidden');
            }
        }
        if (modalStatsEl) {
            modalStatsEl.innerHTML = '';
            const stats = detail?.stats || fallbackPost?.stats || {};
            const showAccuracy = attemptMeta.attemptLabel !== 'swing';
            const statItems = [
                { label: attemptMeta.attemptsLabel, value: stats.attempts },
                ...(showAccuracy ? [{ label: 'Accuracy', value: Number.isFinite(stats.accuracy) ? `${stats.accuracy}%` : null }] : []),
                { label: 'Avg pose', value: Number.isFinite(stats.poseAverage) ? stats.poseAverage : null },
            ];
            statItems.forEach(stat => {
                const div = document.createElement('div');
                div.className = 'stat-item';
                div.innerHTML = `<span>${stat.label}</span><strong>${stat.value ?? '—'}</strong>`;
                modalStatsEl.appendChild(div);
            });
        }
        if (modalShotListEl) {
            modalShotListEl.innerHTML = '';
            const shots = Array.isArray(detail?.shots) ? detail.shots : [];
            if (shots.length) {
                const shotSection = modalShotListEl.parentElement;
                const shotHeading = shotSection?.querySelector?.('h4');
                if (shotHeading) shotHeading.textContent = attemptMeta.attemptLabelPlural;
                shots.forEach((shot, idx) => {
                    const row = document.createElement('li');
                    row.className = 'shot-row';
                    row.dataset.index = String(idx);
                    const label = document.createElement('span');
                    label.textContent = `${attemptMeta.attemptLabelTitle} ${shot.idx ?? idx + 1}`;
                    const note = document.createElement('p');
                    note.textContent = shot.coachNote || 'Pose summary pending';
                    const score = document.createElement('span');
                    score.className = 'shot-score';
                    score.textContent = Number.isFinite(shot.poseScore) ? `${shot.poseScore}` : '—';
                    row.append(label, score, note);
                    row.addEventListener('click', () => jumpToShot(idx));
                    modalShotListEl.appendChild(row);
                });
                modalShotListEl.parentElement?.removeAttribute('hidden');
            } else {
                    modalShotListEl.parentElement?.setAttribute('hidden', 'hidden');
            }
        }
        if (modalActionBarEl) {
            modalActionBarEl.dataset.visible = '1';
        }
    }

    function jumpToShot(index) {
        if (!state.activeDetail) return;
        stopOverlayTimer();
        state.activeShotIndex = index - 1;
        nextShot();
    }

    function startPlayback(detail) {
        if (!modalVideoEl) return;
        modalVideoEl.setAttribute('playsinline', '');
        modalVideoEl.preload = 'metadata';
        modalVideoEl.controls = true;
        modalVideoEl.muted = true;
        modalVideoEl.autoplay = true;
        modalVideoEl.defaultPlaybackRate = PLAYBACK_RATE;
        modalVideoEl.playbackRate = PLAYBACK_RATE;
        stopOverlayTimer();
        state.activeShotIndex = -1;
        modalVideoEl.addEventListener('ended', onVideoEnded);
        modalVideoEl.addEventListener('error', onVideoError);
        nextShot().catch(err => console.warn('[community] playback start failed', err));
    }

    function onVideoEnded() {
        showShotOverlay();
    }

    function onVideoError() {
        console.warn('[community] video playback error', modalVideoEl?.error);
        const attemptLabel = getActiveAttemptMeta().attemptLabel;
        showShotOverlay(`Playback error. Skipping to next ${attemptLabel}.`);
    }

    function stopOverlayTimer() {
        if (state.overlayTimer) {
            clearTimeout(state.overlayTimer);
            state.overlayTimer = null;
        }
    }

    async function nextShot() {
        if (!state.activeDetail || !modalVideoEl) return;
        const shots = Array.isArray(state.activeDetail.shots) ? state.activeDetail.shots : [];
        const targetIndex = state.activeShotIndex + 1;
        if (targetIndex >= shots.length) {
            state.activeShotIndex = shots.length;
            showSessionSummary();
            return;
        }
        state.activeShotIndex = targetIndex;
        const shot = shots[state.activeShotIndex];
        highlightShotRow(state.activeShotIndex);
        const attemptLabel = getActiveAttemptMeta().attemptLabel;
        const clipPath = getClipPath(shot?.clip);
        if (!shot || !clipPath) {
            showShotOverlay(`Clip missing for this ${attemptLabel}. Moving on.`, true);
            return;
        }
        showBufferingOverlay(`Loading ${attemptLabel}...`);
        const token = Symbol('clip');
        state.loadingClipToken = token;
        let clipUrl = clipPath;
        try {
            clipUrl = await resolveClipSource(clipPath);
        } catch (err) {
            if (state.loadingClipToken !== token) return;
            console.warn('[community] clip load failed', err);
            showShotOverlay('Unable to load clip. Moving on.', true);
            return;
        }
        if (state.loadingClipToken !== token) return;
        hideOverlay();
        modalVideoEl.pause();
        modalVideoEl.removeAttribute('src');
        modalVideoEl.classList.remove('video-portrait-fix');
        modalVideoEl.src = clipUrl;
        modalVideoEl.load();
        modalVideoEl.addEventListener('loadedmetadata', () => {
            applyPortraitFix(modalVideoEl, shot);
        }, { once: true });
        modalVideoEl.defaultPlaybackRate = PLAYBACK_RATE;
        modalVideoEl.playbackRate = PLAYBACK_RATE;

        await new Promise(resolve => {
            let settled = false;
            const settle = () => {
                if (settled) return;
                settled = true;
                window.clearTimeout(timer);
                modalVideoEl.removeEventListener('playing', onPlaying);
                resolve();
            };
            const onPlaying = () => settle();
            modalVideoEl.addEventListener('playing', onPlaying, { once: true });
            const timer = window.setTimeout(() => settle(), 1200);
            const playPromise = modalVideoEl.play();
            if (playPromise && typeof playPromise.catch === 'function') {
                playPromise.catch(err => {
                    console.warn('[community] auto play blocked', err);
                    try {
                        modalVideoEl.muted = true;
                        modalVideoEl.play().catch(() => {});
                    } catch { }
                });
            }
        });
        const upcoming = shots[state.activeShotIndex + 1];
        const upcomingPath = getClipPath(upcoming?.clip);
        if (upcomingPath) prefetchClip(upcomingPath);
    }

    function highlightShotRow(index) {
        if (!modalShotListEl) return;
        modalShotListEl.querySelectorAll('.shot-row').forEach(row => {
            if (Number(row.dataset.index) === index) row.classList.add('active');
            else row.classList.remove('active');
        });
    }

    function hideOverlay() {
        if (!modalOverlayEl) return;
        modalOverlayEl.classList.remove('show');
        modalOverlayEl.innerHTML = '';
    }

    function showBufferingOverlay(message) {
        if (!modalOverlayEl) return;
        stopOverlayTimer();
        modalOverlayEl.innerHTML = '';
        const container = document.createElement('div');
        container.className = 'overlay-content loading';
        const paragraph = document.createElement('p');
        const attemptLabel = getActiveAttemptMeta().attemptLabel;
        paragraph.textContent = message || `Loading ${attemptLabel}...`;
        container.appendChild(paragraph);
        modalOverlayEl.appendChild(container);
        modalOverlayEl.classList.add('show');
    }

    function showShotOverlay(message, skipToNext) {
        if (!modalOverlayEl) return;
        const shots = Array.isArray(state.activeDetail?.shots) ? state.activeDetail.shots : [];
        const shot = shots[state.activeShotIndex] || null;
        const idx = shot?.idx ?? state.activeShotIndex + 1;
        const note = message || shot?.coachNote || 'Pose breakdown ready';
        const poseScore = Number.isFinite(shot?.poseScore) ? shot.poseScore : null;
        const attemptMeta = getActiveAttemptMeta();
        modalOverlayEl.innerHTML = '';
        const container = document.createElement('div');
        container.className = 'overlay-content';
        const title = document.createElement('h4');
        title.textContent = `${attemptMeta.attemptLabelTitle} ${idx}`;
        const paragraph = document.createElement('p');
        paragraph.textContent = note;
        container.append(title, paragraph);
        if (poseScore !== null) {
            const score = document.createElement('div');
            score.className = 'overlay-score';
            const label = document.createElement('span');
            label.textContent = 'Pose score ';
            const value = document.createElement('strong');
            value.textContent = String(poseScore);
            score.append(label, value);
            container.appendChild(score);
        }
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'overlay-next';
        btn.setAttribute('data-overlay-next', '');
        btn.textContent = `Next ${attemptMeta.attemptLabel}`;
        container.appendChild(btn);
        modalOverlayEl.appendChild(container);
        modalOverlayEl.classList.add('show');
        stopOverlayTimer();
        btn.addEventListener('click', skipToNext ? showNextAfterSkip : () => {
            stopOverlayTimer();
            nextShot().catch(err => console.warn('[community] overlay advance failed', err));
        });
        if (skipToNext) {
            state.overlayTimer = setTimeout(() => {
                state.overlayTimer = null;
                nextShot().catch(err => console.warn('[community] overlay auto advance failed', err));
            }, 1500);
        } else {
            state.overlayTimer = setTimeout(() => {
                state.overlayTimer = null;
                nextShot().catch(err => console.warn('[community] overlay auto advance failed', err));
            }, OVERLAY_HOLD_MS);
        }
    }

    function showNextAfterSkip() {
        stopOverlayTimer();
        nextShot().catch(err => console.warn('[community] overlay skip failed', err));
    }

    function showSessionSummary() {
        if (!modalOverlayEl) return;
        const stats = state.activeDetail?.stats || {};
        const attemptMeta = getActiveAttemptMeta();
        const showAccuracy = attemptMeta.attemptLabel !== 'swing';
        const highlights = Array.isArray(state.activeDetail?.highlights) ? state.activeDetail.highlights : [];
        const summaryText = (state.activeDetail?.summary || '').trim();
        modalOverlayEl.innerHTML = '';
        const container = document.createElement('div');
        container.className = 'overlay-content final';
        const title = document.createElement('h4');
        title.textContent = 'Session complete';
        container.appendChild(title);

        const statList = document.createElement('ul');
        statList.className = 'overlay-stats';
        const attemptLi = document.createElement('li');
        const attemptLabel = document.createElement('span');
        attemptLabel.textContent = attemptMeta.attemptsLabel;
        const attemptValue = document.createElement('strong');
        attemptValue.textContent = String(stats.attempts ?? '—');
        attemptLi.append(attemptLabel, attemptValue);
        const poseLi = document.createElement('li');
        const poseLabel = document.createElement('span');
        poseLabel.textContent = 'Avg pose';
        const poseValue = document.createElement('strong');
        poseValue.textContent = Number.isFinite(stats.poseAverage) ? String(stats.poseAverage) : '—';
        poseLi.append(poseLabel, poseValue);
        if (showAccuracy) {
            const accuracyLi = document.createElement('li');
            const accuracyLabel = document.createElement('span');
            accuracyLabel.textContent = 'Accuracy';
            const accuracyValue = document.createElement('strong');
            accuracyValue.textContent = Number.isFinite(stats.accuracy) ? `${stats.accuracy}%` : '—';
            accuracyLi.append(accuracyLabel, accuracyValue);
            statList.append(attemptLi, accuracyLi, poseLi);
        } else {
            statList.append(attemptLi, poseLi);
        }
        container.appendChild(statList);

        if (summaryText) {
            const p = document.createElement('p');
            p.textContent = summaryText;
            container.appendChild(p);
        }
        if (highlights.length) {
            const highlightList = document.createElement('ul');
            highlightList.className = 'overlay-highlights';
            highlights.forEach(h => {
                const li = document.createElement('li');
                li.textContent = h;
                highlightList.appendChild(li);
            });
            container.appendChild(highlightList);
        }
        const actions = document.createElement('div');
        actions.className = 'overlay-actions';
        const replayBtn = document.createElement('button');
        replayBtn.type = 'button';
        replayBtn.setAttribute('data-overlay-replay', '');
        replayBtn.textContent = 'Replay';
        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.setAttribute('data-modal-close', '');
        closeBtn.textContent = 'Close';
        actions.append(replayBtn, closeBtn);
        container.appendChild(actions);

        modalOverlayEl.appendChild(container);
        modalOverlayEl.classList.add('show');
        replayBtn.addEventListener('click', () => {
            stopOverlayTimer();
            state.activeShotIndex = -1;
            modalOverlayEl.classList.remove('show');
            nextShot();
        });
        closeBtn.addEventListener('click', closeModal);
    }

    function showModal() {
        if (!modalEl) return;
        modalEl.classList.add('open');
        document.body.classList.add('modal-open');
    }

    function closeModal() {
        if (!modalEl) return;
        stopOverlayTimer();
        state.activeDetail = null;
        state.activeShotIndex = -1;
        state.loadingClipToken = null;
        if (modalVideoEl) {
            modalVideoEl.pause();
            if (modalVideoEl.src && modalVideoEl.src.startsWith('blob:')) {
                try { URL.revokeObjectURL(modalVideoEl.src); } catch { }
            }
            modalVideoEl.classList.remove('video-portrait-fix');
            modalVideoEl.removeAttribute('src');
            modalVideoEl.load();
            modalVideoEl.removeEventListener('ended', onVideoEnded);
            modalVideoEl.removeEventListener('error', onVideoError);
        }
        clearClipCache();
        modalEl.classList.remove('open');
        document.body.classList.remove('modal-open');
    }

    function bindModalEvents() {
        if (modalCloseEl) modalCloseEl.addEventListener('click', closeModal);
        if (modalBackdropEl) modalBackdropEl.addEventListener('click', closeModal);
        document.addEventListener('keydown', ev => {
            if (ev.key === 'Escape' && modalEl?.classList.contains('open')) {
                closeModal();
            }
        });
        if (modalActionBarEl) {
            modalActionBarEl.addEventListener('click', ev => {
                const action = ev.target?.getAttribute?.('data-action');
                if (action === 'next') {
                    stopOverlayTimer();
                    nextShot().catch(err => console.warn('[community] next action failed', err));
                } else if (action === 'previous') {
                    stopOverlayTimer();
                    const prev = Math.max(-1, state.activeShotIndex - 2);
                    state.activeShotIndex = prev;
                    nextShot().catch(err => console.warn('[community] previous action failed', err));
                }
            });
        }
    }

    function init() {
        if (!feedEl) return;
        actionStore = loadActionStore();
        applyInitialView();
        try {
            const params = new URLSearchParams(window.location.search);
            const sid = params.get('sid');
            if (sid) state.pendingShareSid = sid;
        } catch { }
        bindModalEvents();
        if (tagToggleBtn) {
            tagToggleBtn.addEventListener('click', () => toggleTagFilters());
        }
        if (startCardBtn) {
            startCardBtn.addEventListener('click', () => handleStartSession());
        }
        if (mobileNavEl) {
            mobileNavEl.addEventListener('click', handleNavClick);
        }
        if (startSheetCloseEls.length) {
            startSheetCloseEls.forEach(btn => {
                btn.addEventListener('click', closeStartSheet);
            });
        }
        if (startSheetEl) {
            startSheetEl.addEventListener('click', ev => {
                if (ev.target === startSheetEl) closeStartSheet();
            });
        }
        document.addEventListener('keydown', ev => {
            if (ev.key === 'Escape' && startSheetEl?.classList.contains('open')) {
                closeStartSheet();
            }
        });
        loadFeed();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
