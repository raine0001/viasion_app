// static/js/community_page.js
// Dynamic community feed + session replay logic for viasion
// Renders posts from /api/community/feed and plays shot clips sequentially.

(() => {
    const FEED_ENDPOINT = '/api/community/feed';
    const DETAIL_ENDPOINT = sid => `/api/community/session/${encodeURIComponent(sid)}`;
    const OVERLAY_HOLD_MS = 6000;
    const PLAYBACK_RATE = 0.3;

    const feedEl = document.querySelector('[data-community-feed]');
    const filterEl = document.querySelector('[data-community-filter]');
    const tagFilterEl = document.querySelector('[data-community-tag-filter]');
    const emptyStateEl = document.querySelector('[data-community-empty]');

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
        activeDetail: null,
        activeShotIndex: -1,
        overlayTimer: null,
        loadingDetail: false,
        loadingClipToken: null,
        clipLoads: new Map(),
    };

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

    function buildFilters(posts) {
        const map = new Map();
        posts.forEach(post => {
            const label = (post.projectName || post.project || post.dataset || 'Sessions').trim();
            const id = slugify(label);
            if (!map.has(id)) {
                map.set(id, { id, label });
            }
        });
        const filters = [{ id: 'all', label: 'All sessions' }, ...map.values()];
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

    function renderFilters() {
        if (!filterEl) return;
        filterEl.textContent = '';
        state.filters.forEach(filter => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'filter-btn' + (filter.id === state.activeFilter ? ' active' : '');
            btn.textContent = filter.label;
            btn.addEventListener('click', () => {
                state.activeFilter = filter.id;
                renderFilters();
                renderFeed();
            });
            filterEl.appendChild(btn);
        });
    }

    function renderTagFilters(tags) {
        if (!tagFilterEl) return;
        tagFilterEl.textContent = '';
        if (tags.length <= 1) {
            tagFilterEl.style.display = 'none';
            return;
        }
        tagFilterEl.style.display = 'flex';
        tags.forEach(tag => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'tag-filter-btn' + (tag.id === state.activeTag ? ' active' : '');
            const label = tag.id === 'all'
                ? tag.label
                : (tag.label.startsWith('#') ? tag.label : `#${tag.label}`);
            btn.textContent = label;
            btn.addEventListener('click', () => {
                state.activeTag = tag.id;
                renderTagFilters(tags);
                renderFeed();
            });
            tagFilterEl.appendChild(btn);
        });
    }

    function getPreviewUrl(post) {
        if (!post || !post.preview) return null;
        if (post.previewRev) return `${post.preview}?rev=${post.previewRev}`;
        return `${post.preview}?cb=${Date.now()}`;
    }

    function matchesFilter(post) {
        if (state.activeFilter !== 'all') {
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
            return;
        }
        if (emptyStateEl) emptyStateEl.hidden = true;
        posts.forEach(post => feedEl.appendChild(createPostCard(post)));
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
        stats.appendChild(renderStatItem('Attempts', attempts ? String(attempts) : '—'));
        stats.appendChild(renderStatItem('Accuracy', Number.isFinite(accuracy) ? `${accuracy}%` : '—'));
        stats.appendChild(renderStatItem('Pose avg', Number.isFinite(poseAvg) ? `${poseAvg}` : '—'));

        const tagsBar = document.createElement('div');
        tagsBar.className = 'tags';
        (post.tags || []).forEach(tag => {
            const chip = document.createElement('span');
            chip.className = 'tag';
            chip.textContent = tag;
            tagsBar.appendChild(chip);
        });

        const footer = document.createElement('div');
        footer.className = 'post-footer';
        const viewBtn = document.createElement('button');
        viewBtn.type = 'button';
        viewBtn.className = 'cta';
        viewBtn.textContent = 'View recap';
        viewBtn.addEventListener('click', ev => {
            ev.stopPropagation();
            openPostDetail(post);
        });
        footer.appendChild(viewBtn);

        body.append(meta, title, summary, stats, tagsBar, footer);
        card.append(media, body);
        card.addEventListener('click', () => openPostDetail(post));
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
        } catch (err) {
            console.error('[community] load feed failed', err);
            if (emptyStateEl) {
                emptyStateEl.hidden = false;
                emptyStateEl.textContent = 'Unable to load the community feed. Please refresh to retry.';
            }
        } finally {
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
        if (modalTitleEl) {
            modalTitleEl.textContent = detail?.title || fallbackPost?.title || 'Session recap';
        }
        if (modalMetaEl) {
            const authored = detail?.author || fallbackPost?.author || 'Player';
            const time = formatRelativeTime(detail?.createdAt || fallbackPost?.createdAt);
            modalMetaEl.textContent = `${authored}${time ? ` · ${time}` : ''}`;
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
            const statItems = [
                { label: 'Attempts', value: stats.attempts },
                { label: 'Accuracy', value: Number.isFinite(stats.accuracy) ? `${stats.accuracy}%` : null },
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
                shots.forEach((shot, idx) => {
                    const row = document.createElement('li');
                    row.className = 'shot-row';
                    row.dataset.index = String(idx);
                    const label = document.createElement('span');
                    label.textContent = `Swing ${shot.idx ?? idx + 1}`;
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
        showShotOverlay('Playback error. Skipping to next swing.');
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
        if (!shot || !shot.clip) {
            showShotOverlay('Clip missing for this swing. Moving on.', true);
            return;
        }
        showBufferingOverlay('Loading swing...');
        const token = Symbol('clip');
        state.loadingClipToken = token;
        let clipUrl = shot.clip;
        try {
            clipUrl = await resolveClipSource(shot.clip);
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
        modalVideoEl.src = clipUrl;
        modalVideoEl.load();
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
        if (upcoming?.clip) prefetchClip(upcoming.clip);
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
        paragraph.textContent = message || 'Loading swing...';
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
        modalOverlayEl.innerHTML = '';
        const container = document.createElement('div');
        container.className = 'overlay-content';
        const title = document.createElement('h4');
        title.textContent = `Swing ${idx}`;
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
        btn.textContent = 'Next swing';
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
        attemptLabel.textContent = 'Attempts';
        const attemptValue = document.createElement('strong');
        attemptValue.textContent = String(stats.attempts ?? '—');
        attemptLi.append(attemptLabel, attemptValue);
        const accuracyLi = document.createElement('li');
        const accuracyLabel = document.createElement('span');
        accuracyLabel.textContent = 'Accuracy';
        const accuracyValue = document.createElement('strong');
        accuracyValue.textContent = Number.isFinite(stats.accuracy) ? `${stats.accuracy}%` : '—';
        accuracyLi.append(accuracyLabel, accuracyValue);
        const poseLi = document.createElement('li');
        const poseLabel = document.createElement('span');
        poseLabel.textContent = 'Avg pose';
        const poseValue = document.createElement('strong');
        poseValue.textContent = Number.isFinite(stats.poseAverage) ? String(stats.poseAverage) : '—';
        poseLi.append(poseLabel, poseValue);
        statList.append(attemptLi, accuracyLi, poseLi);
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
        bindModalEvents();
        loadFeed();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
