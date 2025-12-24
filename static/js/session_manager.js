// session_manager.js — single-owner: start/end + cap + server persistence.
// Owns: session start/stop, cap truth, POST /start and /shot
// Listens: hud:start-session, hud:end-session, shot:summary
// Emits:  hud:start-session (on explicit start), hud:end-session (on end)
// Does NOT: generate releases, record clips, enforce UI, open tables automatically.

import { visaionSpeak, primeCoachAudio, listenForEndSession } from '/static/js/coach_voice.js';

/* ------------------------ project helpers ------------------------ */
function getActiveProjectMeta() {
    try {
        const mgr = window.visaionProjectManager;
        if (mgr && typeof mgr.getActiveProject === 'function') {
            const project = mgr.getActiveProject();
            if (project) return project;
        }
    } catch { /* ignore */ }
    const fallback = window.__visaion_ACTIVE_PROJECT;
    if (!fallback) return null;
    if (typeof fallback === 'object' && fallback) return fallback;
    if (typeof fallback === 'string') return { slug: fallback };
    return null;
}

function getWorkflowConfig() {
    const project = getActiveProjectMeta();
    return (project && typeof project === 'object' && project.workflow) ? project.workflow : {};
}

function getWorkflowAttemptLabel() {
    const workflow = getWorkflowConfig();
    const raw = typeof workflow.attemptLabel === 'string' ? workflow.attemptLabel.trim() : '';
    return raw || 'shot';
}

function projectRequiresTargetSelection() {
    const workflow = getWorkflowConfig();
    return workflow.requiresTargetSelection !== false;
}

function getWorkflowCountdownSeconds() {
    const workflow = getWorkflowConfig();
    const val = Number(workflow.countdownSeconds);
    return Number.isFinite(val) && val > 0 ? val : 5;
}

function getWorkflowReadyPrompt() {
    const workflow = getWorkflowConfig();
    if (typeof workflow.readyPrompt === 'string') {
        const trimmed = workflow.readyPrompt.trim();
        if (trimmed) return trimmed;
    }
    const attempt = getWorkflowAttemptLabel();
    return attempt === 'swing' ? 'Swing when ready.' : 'Shoot when ready.';
}

/* ------------------------ tiny helpers ------------------------ */
async function postJSON(url, body) {
    const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
        credentials: 'include'
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json().catch(() => ({}));
}

// Unified cap reader/writer (session_manager is the only writer)
function setSessionCap(n) {
    const cap = Number(n);
    const v = Number.isFinite(cap) && cap > 0 ? cap : undefined;
    try {
        if (v) {
            window.__SESSION_CAP = v;
            window.SESSION_SIZE = v;               // UI reads this to show N/Cap
            localStorage.setItem('visaion.sessionCap', String(v));
        } else {
            window.__SESSION_CAP = undefined;
            window.SESSION_SIZE = undefined;
            localStorage.removeItem('visaion.sessionCap');
        }
    } catch { }
}
function getSessionCap() {
    try {
        if (Number.isFinite(window.__SESSION_CAP)) return Number(window.__SESSION_CAP);
        if (Number.isFinite(window.SESSION_SIZE)) return Number(window.SESSION_SIZE);
        const ls = Number(localStorage.getItem('visaion.sessionCap'));
        if (Number.isFinite(ls) && ls > 0) return ls;
    } catch { }
    return 10;
}
window.getSessionCap = getSessionCap; // let others read

/* ------------------------ internal shot accounting ------------------------ */
/**
 * Stop depending on UI arrays to count shots. We keep our own counter and
 * a dedupe set of processed summary keys.
 */
let __shotCounter = 0; // monotonic per session
const __processedSummaries = new Set(); // keys: `sid|shotId` (or frame fallback)

// Returns the next 1-based shot index owned by the session manager
function nextShotIndex() {
    __shotCounter = Number(__shotCounter || 0) + 1;
    window.__SESSION_SHOT_COUNT = __shotCounter;
    return __shotCounter;
}

// Best-effort finalized count for display; prefer our counter
function getFinalizedCount() {
    if (Number.isFinite(__shotCounter) && __shotCounter > 0) return __shotCounter;
    try {
        const recs = typeof window.getShotRecords === 'function' ? (window.getShotRecords() || []) : [];
        if (recs.length) return recs.length;
    } catch { }
    try {
        const list = window.__shotList || [];
        return list.filter(s => s && s.pending === false).length;
    } catch { }
    return Number(window.__SESSION_SHOT_COUNT || 0);
}

/* ------------------------ state ------------------------ */
let __wired = false;
let __sid = null;
let __ended = false;
let __startPromise = null;
let __communityPendingSummary = null;
let __communitySessionFinalized = false;
let __communityPublishing = false;

// Display name for voice (fallbacks)
function getDisplayName() {
    try {
        const authed = window.__AUTHED === true;
        if (authed) {
            return window.__USER_NAME || localStorage.getItem('firstname') || 'Player';
        }
        const guest = window.__GUEST_NAME || sessionStorage.getItem('visaion_guest_name');
        if (guest) return guest;
        return 'Player';
    } catch {
        return window.__USER_NAME || 'Player';
    }
}
try { window.getVisaionDisplayName = getDisplayName; } catch { }

/* ------------------------ core actions ------------------------ */
async function startSession() {
    if (__sid) return __sid;      // already started
    if (__startPromise) return __startPromise;

    __startPromise = (async () => {
        __ended = false;
        __shotCounter = 0;
        __processedSummaries.clear();
        __communityPendingSummary = null;
        __communitySessionFinalized = false;
        __communityPublishing = false;
        try { window.__COMMUNITY_AUTOSHARE = null; } catch { }
        try { window.__SESSION_EVENT_FIRED = false; } catch { }

        // choose cap once per session (URL > env > LS > default)
        let cap = (() => {
            try {
                const q = new URLSearchParams(location.search || '');
                const qp = Number(q.get('cap'));
                if (Number.isFinite(qp) && qp > 0) return qp;
            } catch { }
            return Number(window.DEMO_SESSION_CAP ?? window.__SESSION_CAP ?? window.SESSION_CAP);
        })();
        if (!Number.isFinite(cap) || cap <= 0) cap = 10;
        setSessionCap(cap);

        let sessionId = null;
        try {
            const projectMeta = getActiveProjectMeta();
            const qs = new URLSearchParams(location.search || '');
            const datasetSlug = qs.get('dataset') || null;
            const payload = {
                device: navigator.userAgent,
                project: projectMeta?.slug || null,
                projectName: projectMeta?.name || null,
                dataset: datasetSlug || (projectMeta?.datasets?.[0]?.slug ?? null),
                tags: Array.isArray(projectMeta?.tags) ? projectMeta.tags : null
            };
            if (!payload.project) delete payload.project;
            if (!payload.projectName) delete payload.projectName;
            if (!payload.dataset) delete payload.dataset;
            if (!payload.tags) delete payload.tags;
            const res = await postJSON('/api/sessions/start', payload);
            sessionId = res?.id || null;
        } catch (err) {
            __sid = null;
            window.__SESSION_ID = null;
            window.__SESSION_ACTIVE = false;
            throw err;
        }

        __sid = sessionId;
        window.__SESSION_ID = sessionId || null;
        window.__SESSION_ACTIVE = true;
        window.__SESSION_SHOT_COUNT = 0;
        window.__sessionStart = Date.now();

        // nudge HUD
        try { window.mountSessionHUD?.(); window.setSessionStatus?.('SESSION IN PROGRESS'); } catch { }

        // tell everyone
        let muted = false;
        try { window.__coachMuted = false; } catch { }
        try {
            localStorage.setItem('visaion_muted', 'false');
            muted = localStorage.getItem('visaion_muted') === 'true';
        } catch {
            muted = false;
        }

        const shouldGreet = !muted;

        let resolveGreeting = null;
        let greetingPromise = null;
        const attemptLabel = getWorkflowAttemptLabel();
        const requiresTarget = projectRequiresTargetSelection();
        const readyPrompt = getWorkflowReadyPrompt();
        const countdownSeconds = getWorkflowCountdownSeconds();
        try {
            window.__sessionReadyPrompt = readyPrompt;
            window.__sessionCountdownSecs = countdownSeconds;
        } catch { /* ignore storage issues */ }
        if (shouldGreet) {
            try {
                greetingPromise = new Promise((resolve) => { resolveGreeting = resolve; });
                window.__GREETING_PROMISE = greetingPromise;
            } catch { resolveGreeting = null; greetingPromise = null; }
        } else {
            try { window.__GREETING_PROMISE = null; } catch { }
        }

        try {
            window.dispatchEvent(new CustomEvent('hud:start-session'));
            window.__SESSION_EVENT_FIRED = true;
        } catch { }

        const finishGreeting = () => {
            try { resolveGreeting?.(); } catch { }
            try { window.__GREETING_PROMISE = null; } catch { }
            try { window.dispatchEvent(new CustomEvent('coach:greeting-finished')); } catch { }
        };

        if (shouldGreet) {
            const noun = attemptLabel || 'shot';
            const fallbackPrompt = readyPrompt || 'Get into position when you are ready.';
            const displayName = getDisplayName();
            const hasName = window.__AUTHED === true || !!(window.__GUEST_NAME || sessionStorage.getItem('visaion_guest_name'));
            const startLine = hasName ? `${displayName}, let's get started.` : `Let's get started.`;
            const greeting = requiresTarget
                ? `${startLine} Tap the target area, then get into position for your first ${noun}.`
                : `${startLine} ${fallbackPrompt}`;
            try { await primeCoachAudio?.(); } catch { }
            try {
                if (typeof visaionSpeak === 'function') {
                    try {
                        const job = visaionSpeak(greeting);
                        if (job && typeof job.then === 'function') {
                            try { window.__GREETING_PROMISE = greetingPromise || job; } catch { }
                            const ok = await job;
                            if (!ok) console.warn('[coach:greeting] TTS failed');
                        } else {
                            console.warn('[coach:greeting] TTS handler returned non-promise');
                        }
                    } catch (err) {
                        console.warn('[coach:greeting] error', err);
                        throw err;
                    }
                } else {
                    console.warn('[coach:greeting] visaionSpeak not available');
                }
            } catch { }
            finally {
                finishGreeting();
            }
        } else {
            finishGreeting();
        }

        if (window.PREF_ALLOW_MIC !== false) {
            setTimeout(() => {
                try {
                    if (window.PREF_ALLOW_MIC === false) return;
                    if (window.__VOICE_READY !== true) {
                        window.showToast?.('Voice commands are sleeping—enable the microphone in Settings and say "Hey VISᵃION" to wake me.', 'warn', 5200);
                    }
                } catch { }
            }, 3600);
        }

        return sessionId;
    })();

    try {
        return await __startPromise;
    } finally {
        __startPromise = null;
    }
}

async function persistShotFromSummary(detail) {
    console.debug('[persistShot] detail', detail);

    let shotId = Number(detail?.shotId);
    if (!Number.isFinite(shotId) || shotId <= 0) shotId = null;
    if (shotId != null) {
        const maxAllowed = Number(__shotCounter || 0) + 1;
        if (shotId > maxAllowed) {
            console.warn('[persistShot] shotId jump; clamping', {
                incoming: shotId,
                maxAllowed,
                sid: __sid || null,
                detail
            });
            shotId = maxAllowed;
            try { detail.shotId = shotId; } catch { }
        }
    }
    const hasShotId = Number.isFinite(shotId) && shotId > 0;
    const shotStoreEntry = (Number.isFinite(shotId) && window.__shots instanceof Map && typeof window.__shots.get === 'function')
        ? window.__shots.get(shotId)
        : null;
    const attemptLabel = getWorkflowAttemptLabel();
    if (attemptLabel === 'swing') {
        if (!hasShotId) {
            console.warn('[persistShot] drop swing summary without shotId', detail);
            return;
        }
        const lastId = Number(window.__LAST_RELEASE_SHOT_ID || 0);
        const lastAt = Number(window.__LAST_RELEASE_AT || 0);
        const ageMs = lastAt ? (Date.now() - lastAt) : null;
        if (!shotStoreEntry) {
            const stale = Number.isFinite(ageMs) && ageMs > 15000;
            if (shotId !== lastId || stale) {
                console.warn('[persistShot] drop swing summary without matching release', {
                    shotId,
                    lastId,
                    ageMs,
                    detail
                });
                return;
            }
        }
    }
    const releasePose = Number.isFinite(shotId)
        ? window.poseStore?.get(shotId) || null
        : null;

    let poseSnapshot = null;
    const poseSource = releasePose || detail?.poseSnapshot || null;
    if (poseSource && typeof poseSource === 'object') {
        try {
            poseSnapshot = typeof structuredClone === 'function'
                ? structuredClone(poseSource)
                : JSON.parse(JSON.stringify(poseSource));
        } catch {
            try { poseSnapshot = JSON.parse(JSON.stringify(poseSource)); }
            catch { poseSnapshot = null; }
        }
    }
    if (!poseSnapshot) {
        console.warn('[persistShot] missing canonical pose snapshot', { shotId });
        window.dispatchEvent(new CustomEvent('pose:capture-missing', { detail: { shotId, reason: 'no-pose-for-summary' } }));
    } else {
        detail.poseSnapshot = poseSnapshot;
        try { window.poseStore?.set(shotId, poseSnapshot, { source: 'persist', overwrite: false }); } catch { }
    }

    // Only persist if we have a session id; otherwise try to start one lazily
    if (!__sid) {
        try { await startSession(); } catch { }
    }
    if (!__sid) return;

    // Build a stable de-dupe key from what we actually have
    const sid = String(__sid || '');
    const fEnd = Number(detail?.frameEnd ?? detail?.endFrame ?? NaN);
    const fAny = Number(detail?.frame ?? NaN);
    let key = null;
    if (hasShotId) {
        key = `${sid}|shot:${shotId}`;
    } else if (Number.isFinite(fEnd) || Number.isFinite(fAny)) {
        const fEndKey = Number.isFinite(fEnd) ? fEnd : '';
        const fAnyKey = Number.isFinite(fAny) ? fAny : '';
        key = `${sid}|frame:${fEndKey}|${fAnyKey}`;
    } else {
        key = `${sid}|anon:${Date.now()}|${Math.random().toString(36).slice(2, 8)}`;
    }

    if (key && __processedSummaries.has(key)) {
        // we've already persisted this summary; ignore
        return;
    }

    // Compute shot numbering (1-based) and server index (0-based)
    let shotNumber = hasShotId ? shotId : nextShotIndex();
    if (!Number.isFinite(shotNumber) || shotNumber <= 0) shotNumber = nextShotIndex();
    const serverIdx = Math.max(0, shotNumber - 1);

    let weightedScoreRaw = Number(detail?.weightedScore);
    let poseScoreRaw = Number(detail?.poseScore);

    const tryNumber = (val) => {
        const n = Number(val);
        return Number.isFinite(n) ? n : null;
    };
    const idForLookup = Number.isFinite(shotId) ? shotId : tryNumber(detail?.id);
    if (!Number.isFinite(weightedScoreRaw) || !Number.isFinite(poseScoreRaw)) {
        const seen = new WeakSet();
        const consider = (source, requireIdMatch = false) => {
            if (!source || typeof source !== 'object') return;
            if (seen.has(source)) return;
            if (requireIdMatch) {
                const srcId = tryNumber(source.id ?? source.shotId);
                if (Number.isFinite(idForLookup) && Number.isFinite(srcId) && srcId !== idForLookup) return;
            }
            seen.add(source);
            if (!Number.isFinite(poseScoreRaw)) {
                const poseCandidate = [
                    source.poseScore,
                    source.pose_score,
                    source.score,
                    source.pose?.score
                ].map(tryNumber).find((v) => v != null);
                if (poseCandidate != null) poseScoreRaw = poseCandidate;
            }
            if (!Number.isFinite(weightedScoreRaw)) {
                const weightedCandidate = [
                    source.weightedScore,
                    source.weighted_score,
                    source.poseScoreRaw,
                    source.summary?.weightedScore,
                    source.data?.weightedScore
                ].map(tryNumber).find((v) => v != null);
                if (weightedCandidate != null) weightedScoreRaw = weightedCandidate;
            }
            const nestedSources = [
                source.summary,
                source.data,
                source.arcmm,
                source.arcmm?.summary
            ];
            for (const next of nestedSources) consider(next, false);
        };

        consider(detail);
        if (Number.isFinite(idForLookup)) {
            const list = Array.isArray(window.__shotList) ? window.__shotList : [];
            const listMatch = list.find((entry) => Number(entry?.id ?? entry?.idx) === idForLookup);
            consider(listMatch, true);

            const shotLog = Array.isArray(window.shotLog) ? window.shotLog : [];
            const logMatch = shotLog.find((entry) => Number(entry?.id) === idForLookup);
            consider(logMatch, true);

            const lastSummary = window.__lastSummary;
            consider(lastSummary, true);
        }
    }

    if (Number.isFinite(weightedScoreRaw) && weightedScoreRaw > 1) {
        weightedScoreRaw = weightedScoreRaw / 100;
    }
    if (Number.isFinite(poseScoreRaw) && poseScoreRaw <= 1) {
        poseScoreRaw = poseScoreRaw * 100;
    }
    if (!Number.isFinite(poseScoreRaw) && Number.isFinite(weightedScoreRaw)) {
        poseScoreRaw = weightedScoreRaw * 100;
    }
    if (!Number.isFinite(weightedScoreRaw) && Number.isFinite(poseScoreRaw)) {
        weightedScoreRaw = poseScoreRaw / 100;
    }

    if (!Number.isFinite(detail?.weightedScore) && Number.isFinite(weightedScoreRaw)) {
        detail.weightedScore = weightedScoreRaw;
    }
    if (!Number.isFinite(detail?.poseScore) && Number.isFinite(poseScoreRaw)) {
        detail.poseScore = poseScoreRaw;
    }

    try {
        console.debug('[score:persist:detail]', {
            shotId,
            poseScoreRaw,
            weightedScoreRaw,
            detailPose: detail?.poseScore ?? null,
            detailWeighted: detail?.weightedScore ?? null,
            source: {
                summary: detail?.summary?.poseScore ?? null,
                dataWeighted: detail?.data?.weightedScore ?? null
            }
        }, detail);
    } catch { }

    const hasWeighted = Number.isFinite(weightedScoreRaw);
    const hasPoseScore = Number.isFinite(poseScoreRaw);
    const normalizedPoseScore = hasPoseScore
        ? (poseScoreRaw <= 1 ? poseScoreRaw * 100 : poseScoreRaw)
        : null;
    const normalizedWeightedScore = hasWeighted
        ? (weightedScoreRaw <= 1 ? weightedScoreRaw * 100 : weightedScoreRaw)
        : null;

    const normalizeClipPath = (value) => {
        if (!value || typeof value !== 'string') return value;
        if (/^[a-z]+:/i.test(value)) return value;
        const trimmed = value.replace(/^\/+/, '');
        return `/${trimmed}`;
    };

    const coerceClip = (value) => {
        if (!value) return null;
        if (typeof value === 'string') {
            return { path: normalizeClipPath(value) };
        }
        if (typeof value === 'object') {
            const raw = value.path || value.url || value.href || null;
            const path = normalizeClipPath(raw);
            if (path) {
                const next = { ...value, path };
                if (next.source) next.source = normalizeClipPath(next.source);
                if (next.mp4) next.mp4 = normalizeClipPath(next.mp4);
                return next;
            }
        }
        return null;
    };
    let clipInfo =
        coerceClip(detail?.clip) ||
        coerceClip(detail?.clipPath) ||
        coerceClip(detail?.clipUrl) ||
        coerceClip(shotStoreEntry?.clip);
    if (!clipInfo && Number.isFinite(shotNumber)) {
        const listEntry = Array.isArray(window.__shotList) ? window.__shotList[shotNumber - 1] : null;
        clipInfo = coerceClip(listEntry?.clip);
    }
    // Do not guess clip URLs here; let the API fill based on real files.
    if (clipInfo && !clipInfo.status) {
        clipInfo.status = 'saved';
    }

    const payload = {
        idx: serverIdx,
        t: Date.now(),
        shotId: Number.isFinite(shotNumber) ? shotNumber : null,
        made: Number.isFinite(detail?.made) ? Number(detail.made) : null,
        arcHeight: Number.isFinite(detail?.arcHeight) ? Number(detail.arcHeight) : null,
        entryAngle: Number.isFinite(detail?.entryAngle) ? Number(detail.entryAngle) : null,
        releaseAngle: Number.isFinite(detail?.releaseAngle) ? Number(detail.releaseAngle) : null,
        pose: poseSnapshot || null   // optional, server can ignore
    };
    payload.replace = true;
    const coachLine = typeof detail?.visaion === 'string'
        ? detail.visaion.trim()
        : (typeof detail?.coachLine === 'string'
            ? detail.coachLine.trim()
            : (typeof detail?.text === 'string' ? detail.text.trim() : ''));
    if (coachLine) {
        payload.coachNote = coachLine;
    }
    if (clipInfo) {
        payload.clip = clipInfo;
    }
    if (normalizedPoseScore != null) {
        payload.poseScore = normalizedPoseScore;
    } else if (normalizedWeightedScore != null) {
        payload.poseScore = normalizedWeightedScore;
    }
    if (hasWeighted) {
        payload.weightedScore = weightedScoreRaw;
    }

    console.log('[score:persist:payload]', {
        shotId,
        shotNumber,
        idx: serverIdx,
        poseScore: payload.poseScore ?? null,
        weightedScore: payload.weightedScore ?? null,
        normalizedPoseScore,
        normalizedWeightedScore,
        hasPoseScore,
        hasWeighted
    });

    try {
        await postJSON(`/api/sessions/${__sid}/shot`, payload);
        if (key) __processedSummaries.add(key);
        // bump our counter to at least shotNumber
        if (shotNumber > __shotCounter) __shotCounter = shotNumber;
        window.__SESSION_SHOT_COUNT = __shotCounter;
        console.debug('[persistShot]', { idx: serverIdx, ok: true });
    } catch (err) {
        console.warn('[persistShot] failed', err, { idx: serverIdx, payload });
    }

    // Cap check owned here only
    try {
        const taken = getFinalizedCount();
        const cap = getSessionCap();
        if (Number.isFinite(cap) && taken >= cap) {
            // tiny grace so the last row/clip paths land before UI opens table
            setTimeout(() => { endSession('cap').catch(() => { }); }, Math.max(200, Number(window.CAP_SUMMARY_GRACE_MS || 600)));
        }
    } catch { }
}

async function endSession(reason = 'normal') {
    if (__ended) return;
    __ended = true;

    // single finalizer: dim + open table (UI-owned)
    try { await window.autoEndSessionAndSummarize?.(); } catch { }

    // flip flags
    try { window.__SESSION_ACTIVE = false; } catch { }
    try { window.__SESSION_EVENT_FIRED = false; } catch { }

    // optional voice cue
    try {
        try { localStorage.setItem('visaion_muted', 'false'); window.__coachMuted = false; } catch { }
        if (localStorage.getItem('visaion_muted') !== 'true') {
            const line = 'Session ended.';
            try { await primeCoachAudio?.(); } catch { }
            try {
                if (typeof visaionSpeak === 'function') {
                    await visaionSpeak(line);
                } else {
                    console.warn('[coach:end-session] visaionSpeak not available');
                }
            } catch {
                console.warn('[coach:end-session] TTS failed');
            }
        }
    } catch { }

    if (__sid) {
        try {
            const res = await postJSON(`/api/sessions/${__sid}/end`, { reason });
            if (res?.totals) {
                try { window.__sessionTotals = res.totals; } catch { }
            }
        } catch (err) {
            console.warn('[session] finalize endpoint failed', err);
        }
        __communitySessionFinalized = true;
        publishCommunityRecapIfReady();
    }

    return true;
}
function resetSessionForNewStart() {
    __sid = null;
    __ended = false;
    __shotCounter = 0;
    __processedSummaries.clear();
    try {
        window.__SESSION_ID = null;
        window.__SESSION_ACTIVE = false;
        window.__SESSION_SHOT_COUNT = 0;
        window.__sessionStart = null;
        window.__SESSION_EVENT_FIRED = false;
    } catch { }
    return true;
}



/* ------------------------ wiring ------------------------ */
(function wireOnce() {
    if (__wired) return; __wired = true;

    // Buttons are optional
    const btnStart = document.getElementById('btnStartSession');
    const btnEnd = document.getElementById('btnEndSession');
    if (btnStart) btnStart.addEventListener('click', () => { startSession().catch(() => { }); });
    if (btnEnd) btnEnd.addEventListener('click', () => { endSession().catch(() => { }); });

    // HUD bridge: treat these as canonical controls
    window.addEventListener('hud:start-session', () => { startSession().catch(() => { }); });
    window.addEventListener('hud:end-session', () => { endSession().catch(() => { }); });

    // Persist every finalized shot (deduped here), then check cap
    window.addEventListener('shot:summary', (e) => {
        const detail = e?.detail || {};
        // Never gate summaries on "armed"; capture is upstream.
        persistShotFromSummary(detail).catch(() => { });
    }, { passive: true });

    // Voice exit (optional; ignores if voice isn’t available)
    try {
        const stopListen = listenForEndSession?.('hey visaion, end the session', async () => { await endSession('voice'); });
        window.__voiceEndHandle = stopListen;
    } catch { }
})();

/* ------------------------ exports (optional) ------------------------ */
window.visaionSession = {
    start: startSession,
    end: endSession,
    reset: resetSessionForNewStart,
    getCap: getSessionCap,
    setCap: setSessionCap,
    get id() { return __sid; }
};

async function publishCommunityRecap(detail) {
    const sid = window.__SESSION_ID;
    if (!sid) return;
    if (window.__COMMUNITY_AUTOSHARE === sid) return;
    const shotsList = Array.isArray(window.__shotList) ? window.__shotList : [];
    const attempts = shotsList.length;
    const shots = shotsList.map((shot, idx) => {
        const idx1 = Number.isFinite(shot?.shotId) && shot.shotId > 0 ? shot.shotId : (idx + 1);
        const clipPath = (shot?.clip && typeof shot.clip.path === 'string')
            ? shot.clip.path
            : null;
        const poseScore = Number.isFinite(shot?.poseScore) ? Math.round(shot.poseScore) : null;
        const weightedScore = Number.isFinite(shot?.weightedScore) ? shot.weightedScore : null;
        const coachNote = typeof shot?.visaion === 'string' && shot.visaion.trim()
            ? shot.visaion.trim()
            : (typeof shot?.coachLine === 'string' && shot.coachLine.trim() ? shot.coachLine.trim() : null);
        const payload = {
            idx: idx1,
            poseScore,
            weightedScore,
            coachNote
        };
        if (clipPath) payload.clip = clipPath;
        return payload;
    });
    const poseScores = shots.map(s => s.poseScore).filter(v => Number.isFinite(v));
    const avgPose = poseScores.length ? Math.round(poseScores.reduce((a, b) => a + b, 0) / poseScores.length) : null;
    const project = getActiveProjectMeta();
    const qs = new URLSearchParams(location.search || '');
    const datasetSlug = qs.get('dataset') || null;
    const tags = new Set();
    if (Array.isArray(detail?.tags)) detail.tags.forEach(t => tags.add(String(t)));
    if (project?.slug) tags.add(project.slug);
    if (datasetSlug) tags.add(datasetSlug);
    const payload = {
        sessionId: sid,
        title: detail?.title || (project?.name ? `${project.name} recap` : 'Session recap'),
        summary: detail?.summary || '',
        highlights: Array.isArray(detail?.lines) ? detail.lines.slice(0, 3) : [],
        author: getDisplayName(),
        tags: Array.from(tags).filter(Boolean),
        project: project?.slug || null,
        projectName: project?.name || null,
        dataset: datasetSlug,
        stats: {
            attempts,
            poseAverage: avgPose,
            accuracy: Number.isFinite(window.__sessionTotals?.accuracy) ? window.__sessionTotals.accuracy : null
        },
        shots
    };
    try {
        const res = await fetch('/api/community/publish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const msg = await res.text().catch(() => res.statusText || 'publish failed');
            throw new Error(msg || `HTTP ${res.status}`);
        }
        window.__COMMUNITY_AUTOSHARE = sid;
        console.info('[community] recap published', { sid });
    } catch (err) {
        console.warn('[community] publish failed', err, { sid, payload });
    }
}

function publishCommunityRecapIfReady() {
    if (__communityPublishing) return;
    if (!__communityPendingSummary) return;
    if (!__communitySessionFinalized) return;
    if (!window.__SESSION_ID) return;
    if (window.__COMMUNITY_AUTOSHARE === window.__SESSION_ID) return;
    __communityPublishing = true;
    publishCommunityRecap(__communityPendingSummary).finally(() => {
        __communityPublishing = false;
    });
}

function isTrialSession() {
    try {
        if (window.__SESSION_TRIAL === false) return false;
        if (window.__SESSION_TRIAL === true) return true;
    } catch { }
    try {
        const params = new URLSearchParams(location.search || '');
        const flag = (params.get('trial') || params.get('demo') || params.get('public') || '').toLowerCase();
        if (['1', 'true', 'yes', 'on'].includes(flag)) return true;
    } catch { }
    const cap = getSessionCap();
    return Number.isFinite(cap) && cap <= 3;
}

function shouldPromptTrialEmail() {
    if (window.__AUTHED === true) return false;
    if (!isTrialSession()) return false;
    if (!window.__SESSION_ID) return false;
    if (window.__TRIAL_EMAIL_PROMPTED) return false;
    return true;
}

function showTrialEmailPrompt(detail) {
    if (!shouldPromptTrialEmail()) return;
    window.__TRIAL_EMAIL_PROMPTED = true;
    const existing = document.getElementById('trialEmailPrompt');
    if (existing) return;

    const overlay = document.createElement('div');
    overlay.id = 'trialEmailPrompt';
    Object.assign(overlay.style, {
        position: 'fixed',
        inset: '0',
        zIndex: 10095,
        background: 'rgba(0,0,0,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
    });

    const panel = document.createElement('div');
    Object.assign(panel.style, {
        width: 'min(420px, 92vw)',
        background: 'rgba(16,18,24,0.96)',
        color: '#fff',
        borderRadius: '16px',
        border: '1px solid rgba(255,255,255,0.12)',
        padding: '18px 18px 16px',
        boxShadow: '0 18px 46px rgba(0,0,0,0.45)',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        font: '500 14px system-ui'
    });

    const title = document.createElement('div');
    title.textContent = 'Email your session summary?';
    title.style.cssText = 'font:700 16px system-ui;';

    const body = document.createElement('div');
    body.textContent = 'Want a copy of this free assessment? Drop your email and we will send the recap.';
    body.style.opacity = '0.85';

    const input = document.createElement('input');
    input.type = 'email';
    input.placeholder = 'you@example.com';
    input.autocomplete = 'email';
    Object.assign(input.style, {
        padding: '10px 12px',
        borderRadius: '10px',
        border: '1px solid rgba(255,255,255,0.18)',
        background: 'rgba(10,12,16,0.95)',
        color: '#fff',
        font: '500 14px system-ui'
    });

    const status = document.createElement('div');
    status.style.cssText = 'min-height:16px; font:600 12px system-ui; opacity:0.75;';

    const actions = document.createElement('div');
    actions.style.cssText = 'display:flex; gap:10px; justify-content:flex-end; flex-wrap:wrap;';

    const btnSkip = document.createElement('button');
    btnSkip.textContent = 'No thanks';
    btnSkip.style.cssText = 'border:1px solid rgba(255,255,255,0.18); background:transparent; color:#fff; padding:8px 14px; border-radius:999px; font:600 13px system-ui; cursor:pointer;';

    const btnSend = document.createElement('button');
    btnSend.textContent = 'Send me the recap';
    btnSend.style.cssText = 'border:0; background:#facc15; color:#1c1a05; padding:8px 16px; border-radius:999px; font:700 13px system-ui; cursor:pointer;';

    actions.append(btnSkip, btnSend);
    panel.append(title, body, input, status, actions);
    overlay.append(panel);
    document.body.appendChild(overlay);

    const close = () => { try { overlay.remove(); } catch { } };
    btnSkip.onclick = close;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

    btnSend.onclick = async () => {
        const email = (input.value || '').trim().toLowerCase();
        if (!email || !email.includes('@')) {
            status.textContent = 'Enter a valid email address.';
            return;
        }
        btnSend.disabled = true;
        btnSkip.disabled = true;
        status.textContent = 'Sending...';
        const sid = window.__SESSION_ID;
        const payload = {
            email,
            summary: detail?.summary || '',
            lines: Array.isArray(detail?.lines) ? detail.lines : [],
            project: window.visaionProjectManager?.getActiveProject?.()?.slug || null,
            projectName: window.visaionProjectManager?.getActiveProject?.()?.name || null,
            cap: getSessionCap(),
            trial: true
        };
        try {
            const res = await fetch(`/api/sessions/${encodeURIComponent(sid)}/email_summary`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                credentials: 'include'
            });
            if (!res.ok) {
                let msg = null;
                try {
                    const data = await res.json();
                    msg = data?.error || data?.message || null;
                } catch { }
                if (!msg) {
                    msg = await res.text().catch(() => res.statusText || 'Request failed');
                }
                throw new Error(msg || `HTTP ${res.status}`);
            }
            status.textContent = 'Thanks! We will email it shortly.';
            setTimeout(close, 1400);
        } catch (err) {
            status.textContent = err?.message || 'Unable to send right now.';
            btnSend.disabled = false;
            btnSkip.disabled = false;
        }
    };
}

window.addEventListener('visaion:session-review', (e) => {
    __communityPendingSummary = e?.detail || null;
    publishCommunityRecapIfReady();
    try { showTrialEmailPrompt(e?.detail); } catch { }
});

window.addEventListener('hud:start-session', () => {
    try { window.__TRIAL_EMAIL_PROMPTED = false; } catch { }
});
