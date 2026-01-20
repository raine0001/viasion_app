// video_ui.js -- UI only. iOS-safe. No cap enforcement, no auto-end, no recording, no server writes.
// Owns: HUD, prompts, camera switcher, summary table rendering, UI updates from events.
// Exposes: showPromptMessage, ensureHudRoot, mountSessionHUD, updateSessionHUD, renderFullShotTable,
//          autoEndSessionAndSummarize (callable, not auto-triggered).

import { setOverlayInteractive, syncOverlayToVideo } from './fix_overlay_display.js';
import { enableHoopPickOnce } from './app.js';
import { getLockedHoopBox, handleHoopSelection, canonHoop } from '/static/arc_mm/hoop_tracker.js';

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

function getSessionTerminology() {
    const workflow = getWorkflowConfig();
    const attemptLabel = workflow.attemptLabel || 'shot';
    const attemptsLabel = workflow.attemptsLabel || (attemptLabel === 'swing' ? 'Swings Taken' : 'Shots Taken');
    const requiresTargetSelection = workflow.requiresTargetSelection !== false;
    const countdownSeconds = Number.isFinite(Number(workflow.countdownSeconds))
        ? Number(workflow.countdownSeconds)
        : (attemptLabel === 'swing' ? 5 : 5);
    const readyPrompt = workflow.readyPrompt || (attemptLabel === 'swing' ? 'Swing when ready.' : 'Shoot when ready.');
    return { attemptLabel, attemptsLabel, requiresTargetSelection, countdownSeconds, readyPrompt };
}

function requiresTargetSelection() {
    return getSessionTerminology().requiresTargetSelection;
}

function getCountdownSeconds() {
    const terms = getSessionTerminology();
    return Number.isFinite(terms.countdownSeconds) ? terms.countdownSeconds : 5;
}

function getReadyPrompt() {
    return getSessionTerminology().readyPrompt || 'Shoot when ready.';
}



// Soft demo toggles (ignored by logic that could conflict)
window.DEMO = true;
window.DEMO_MINIMAL_TABLE = true;

// Make these helpers reachable to other modules
window.getLockedHoopBox = getLockedHoopBox;
window.handleHoopSelection = handleHoopSelection;
window.startLandscapeRecorder = startLandscapeRecorder;
try { window.__HUD_MANAGES_COUNTDOWN = true; } catch { }

// stop the compositor when the session ends or page unloads
window.addEventListener('hud:end-session', async () => {
    try {
        const baseMs = Number(window.__MICROCLIP_MS) || 0;
        const waitMs = Math.max(2000, baseMs + 1500);
        await window.__landscapeRecController?.waitForIdle?.(waitMs);
    } catch { }
    try { await window.__landscapeRecController?.stop(); } catch { }
    window.__landscapeRecController = null;
    try { window.__landscapeRecPrimed = false; } catch { }
    try { window.__landscapeRecStarting = null; } catch { }
});
window.addEventListener('beforeunload', () => {
    try { window.__landscapeRecController?.stop(); } catch { }
    try { window.__landscapeRecPrimed = false; } catch { }
    try { window.__landscapeRecStarting = null; } catch { }
});

/* ----------------------- iOS viewport + basics ----------------------- */
(function installMobileViewportFixes() {
    let tag = document.querySelector('meta[name="viewport"]');
    if (!tag) { tag = document.createElement('meta'); tag.name = 'viewport'; document.head.appendChild(tag); }
    tag.setAttribute('content', [
        'width=device-width',
        'initial-scale=1',
        'maximum-scale=1',
        'viewport-fit=cover',
        'user-scalable=no'
    ].join(','));

    if (!document.getElementById('visaion-mobile-css')) {
        const css = document.createElement('style');
        css.id = 'visaion-mobile-css';
        css.textContent = `
      html, body { margin:0; padding:0; height:100%; background:#000; overscroll-behavior:none; }
      .session-container, #videoPlayer { width:100%; height:100svh; object-fit:cover; }
      #hudRoot { inset: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
      .hud-card, .hud-pill { -webkit-tap-highlight-color: transparent; }
      .hud-controls { display:flex; gap:12px; align-items:center; }
      .hud-icon-btn { width:48px; height:48px; border-radius:14px; border:1px solid rgba(255,255,255,0.12); background:rgba(0,0,0,0.55); color:#fff; display:flex; align-items:center; justify-content:center; font-size:22px; line-height:1; padding:0; transition:background .2s, color .2s, box-shadow .2s, border-color .2s; }
      .hud-icon-btn .icon { display:flex; }
      .hud-icon-btn.rotate-hint .icon { transform:rotate(90deg); }
      .hud-icon-btn.is-alert { background:rgba(255,170,0,0.18); border-color:rgba(255,170,0,0.6); color:#ffd89d; box-shadow:0 0 18px rgba(255,170,0,0.5); }
      #orientationHintBanner { position:absolute; bottom:calc(env(safe-area-inset-bottom,0) + 100px); left:50%; transform:translateX(-50%); background:rgba(0,0,0,0.82); color:#fff; padding:14px 18px; border-radius:16px; font:600 16px/1.35 system-ui,-apple-system,sans-serif; box-shadow:0 18px 40px rgba(0,0,0,0.45); max-width:min(480px,80vw); text-align:center; display:none; pointer-events:none; z-index:10002; }
      #orientationHintBanner .line-secondary { font-size:13px; opacity:0.8; margin-top:4px; font-weight:500; }
      #orientationHintBanner.is-visible { display:block; }
      #videoPlayer.is-letterbox { object-fit:contain !important; background:#000; transition:object-fit .25s ease; }
      button.vc-btn { font: 600 12px system-ui; border-radius: 10px; padding: 6px 10px; }
    `;
        document.head.appendChild(css);
    }

    window.addEventListener('load', () => {
        const v = document.getElementById('videoPlayer');
        if (v) { v.setAttribute('playsinline', ''); v.muted = true; }
    });
})();

/* ---------------------------- HUD root ----------------------------- */
export function ensureHudRoot() {
    const video = document.getElementById('videoPlayer');
    const host = video?.parentElement || document.body;
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';

    let root = document.getElementById('hudRoot');
    if (!root) {
        root = document.createElement('div');
        root.id = 'hudRoot';
        host.appendChild(root);
    }
    Object.assign(root.style, { position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: 10000 });
    return root;
}
window.ensureHudRoot = ensureHudRoot;

/* ----------------------------- Prompts ----------------------------- */
function getPromptEl() {
    const root = ensureHudRoot();
    let el = document.getElementById('overlayPrompt') || document.getElementById('promptBar');
    if (!el) {
        el = document.createElement('div');
        el.id = 'promptBar';
        root.appendChild(el);
    } else if (!root.contains(el)) {
        root.appendChild(el);
    }
    Object.assign(el.style, {
        position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
        background: 'rgba(0,0,0,0.75)', color: '#fff', padding: '18px 28px',
        borderRadius: '18px', font: '700 20px/1.4 system-ui, sans-serif',
        textAlign: 'center', minWidth: '320px',
        display: 'none', pointerEvents: 'none', zIndex: '10001',
        boxShadow: '0 12px 30px rgba(0,0,0,0.35)'
    });
    return el;
}

export function showPromptMessage(text, duration = 3000) {
    const el = getPromptEl();
    el.textContent = text;
    el.style.display = 'block';
    el.style.opacity = '1';
    clearTimeout(el.__t);
    el.__t = setTimeout(() => {
        el.style.opacity = '0';
        setTimeout(() => (el.style.display = 'none'), 300);
    }, duration);
}
window.showPromptMessage = showPromptMessage;

function hidePromptMessage() {
    const el = document.getElementById('overlayPrompt') || document.getElementById('promptBar');
    if (!el) return;
    clearTimeout(el.__t);
    el.style.display = 'none';
}

/* ----------------------------- HUD bar ----------------------------- */
const ICON_AUDIO_ON = '\u{1F50A}';
const ICON_AUDIO_OFF = '\u{1F507}';
const ICON_CAMERA_FRONT = '\u{1F4F8}';
const ICON_CAMERA_BACK = '\u{1F4F7}';
const ICON_CAMERA_SWITCH = '\u{1F503}';
const ICON_ROTATE_DEVICE = '\u{1F4F1}';
const SYMBOL_INFINITY = '\u{221E}';

function formatCapDisplay(cap) {
    const n = Number(cap);
    return (Number.isFinite(n) && n > 0) ? String(n) : SYMBOL_INFINITY;
}
function currentFacingLabel() {
    try {
        const f = (localStorage.getItem('visaion_camera_facing') || '').toLowerCase();
        if (f === 'user' || f === 'front') return 'Front';
        if (f === 'environment' || f === 'back' || f === 'rear') return 'Back';
    } catch { }
    return 'Back';
}
function formatHudCameraIcon(lab) {
    return (lab === 'Back') ? ICON_CAMERA_BACK : ICON_CAMERA_FRONT;
}

function isPortraitOrientation() {
    try {
        if (window.matchMedia?.('(orientation: portrait)')?.matches) return true;
        if (window.matchMedia?.('(orientation: landscape)')?.matches) return false;
    } catch { }
    try {
        if (typeof screen?.orientation?.type === 'string') {
            return screen.orientation.type.startsWith('portrait');
        }
    } catch { }
    const angle = (typeof screen?.orientation?.angle === 'number')
        ? screen.orientation.angle
        : (typeof window.orientation === 'number' ? window.orientation : null);
    if (typeof angle === 'number') {
        const normalized = ((angle % 360) + 360) % 360;
        return normalized === 0 || normalized === 180;
    }
    return window.innerHeight >= window.innerWidth;
}

const orientationHintState = {
    button: null,
    tipEl: null,
    autoShown: false,
    listenersInstalled: false,
    resizeTimer: null
};

function ensureOrientationTipEl() {
    const root = ensureHudRoot();
    let el = document.getElementById('orientationHintBanner');
    if (!el) {
        el = document.createElement('div');
        el.id = 'orientationHintBanner';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        el.innerHTML = `
      <div class="line-primary">Rotate your phone</div>
      <div class="line-secondary">Landscape tracking works best.</div>
    `;
        root.appendChild(el);
    } else if (!root.contains(el)) {
        root.appendChild(el);
    }
    return el;
}

function setOrientationTipText(el, heading, detail) {
    if (!el) return;
    const head = el.querySelector('.line-primary');
    const sub = el.querySelector('.line-secondary');
    if (head && typeof heading === 'string') head.textContent = heading;
    if (sub && typeof detail === 'string') sub.textContent = detail;
}

function showOrientationTip({ heading, detail, autoHideMs = 4000 } = {}) {
    const el = orientationHintState.tipEl || ensureOrientationTipEl();
    orientationHintState.tipEl = el;
    const h = heading ?? 'Rotate your phone';
    const d = detail ?? 'Landscape tracking works best.';
    setOrientationTipText(el, h, d);
    el.classList.add('is-visible');
    if (autoHideMs > 0) {
        clearTimeout(el.__hideTimer);
        el.__hideTimer = setTimeout(() => {
            el.classList.remove('is-visible');
        }, autoHideMs);
    }
}

function hideOrientationTip() {
    const el = orientationHintState.tipEl;
    if (!el) return;
    el.classList.remove('is-visible');
    if (el.__hideTimer) {
        clearTimeout(el.__hideTimer);
        el.__hideTimer = null;
    }
}

function updateOrientationHint(opts = {}) {
    const btn = orientationHintState.button;
    if (!btn) return;
    orientationHintState.tipEl ||= ensureOrientationTipEl();

    const portrait = isPortraitOrientation();
    btn.classList.toggle('is-alert', portrait);
    btn.setAttribute('aria-pressed', portrait ? 'true' : 'false');
    btn.setAttribute('aria-label', portrait ? 'Rotate your phone for landscape tracking' : 'Device is in landscape');
    btn.title = portrait ? 'Rotate your phone for landscape tracking' : 'Landscape ready';

    const video = document.getElementById('videoPlayer');
    if (video) video.classList.toggle('is-letterbox', portrait);

    if (portrait) {
        if (opts.forcePrompt || !orientationHintState.autoShown) {
            showOrientationTip({});
            orientationHintState.autoShown = true;
        }
    } else {
        orientationHintState.autoShown = false;
        hideOrientationTip();
    }
}

function installOrientationHint(button) {
    if (!button) return;
    orientationHintState.button = button;
    orientationHintState.tipEl = ensureOrientationTipEl();

    if (!button.__orientationClickHandler) {
        button.__orientationClickHandler = () => {
            const portrait = isPortraitOrientation();
            showOrientationTip({
                heading: portrait ? 'Rotate your phone' : 'Landscape mode ready',
                detail: portrait ? 'Landscape tracking works best.' : 'You are already set for landscape tracking.',
                autoHideMs: 3200
            });
            if (portrait) orientationHintState.autoShown = true;
        };
        button.addEventListener('click', button.__orientationClickHandler);
    }

    if (!orientationHintState.listenersInstalled) {
        window.addEventListener('orientationchange', () => {
            orientationHintState.autoShown = false;
            updateOrientationHint({ forcePrompt: true });
        }, { passive: true });
        window.addEventListener('resize', () => {
            clearTimeout(orientationHintState.resizeTimer);
            orientationHintState.resizeTimer = setTimeout(() => updateOrientationHint({}), 120);
        });
        orientationHintState.listenersInstalled = true;
    }

    updateOrientationHint({ forcePrompt: true });
}

export function mountSessionHUD() {
    const root = ensureHudRoot();
    let bar = document.getElementById('sessionHUD');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'sessionHUD';
        bar.className = 'hud-card hud-pill';
        Object.assign(bar.style, {
            position: 'absolute', left: '50%', transform: 'translateX(-50%)',
            bottom: 'calc(env(safe-area-inset-bottom, 0px) + 18px)', gap: '20px', pointerEvents: 'auto'
        });

        const terms = getSessionTerminology();
        const attemptsLabel = terms.attemptsLabel || 'Shots Taken';

        bar.innerHTML = `
      <div class="hud-controls">
        <button id="hudVoiceToggle" class="hud-icon-btn voice-toggle is-on" data-muted="0" aria-pressed="true" aria-label="Toggle voice">
          <span class="icon-on" aria-hidden="true">${ICON_AUDIO_ON}</span>
          <span class="icon-off" aria-hidden="true">${ICON_AUDIO_OFF}</span>
        </button>
        <button id="hudCamFlip" class="hud-icon-btn" aria-label="Switch camera" title="Switch camera">
          <span class="icon" aria-hidden="true">${formatHudCameraIcon(currentFacingLabel())}</span>
        </button>
        <button id="hudRotateHint" class="hud-icon-btn rotate-hint" aria-pressed="false" aria-label="Landscape tips" title="Landscape tips">
          <span class="icon" aria-hidden="true">${ICON_ROTATE_DEVICE}</span>
        </button>
      </div>
      <div class="hud-metric" id="mShots"><div class="num">0/${formatCapDisplay(window.SESSION_SIZE)}</div><div class="label">${attemptsLabel}</div></div>
      <div class="hud-metric" id="mTime"><div class="num">0:00</div><div class="label">Time Elapsed</div></div>
      <button id="hudEndSession" class="hud-end-btn" type="button" aria-label="End session">End Session</button>
    `;
        root.appendChild(bar);


        const camBtn = bar.querySelector('#hudCamFlip');
        const endBtn = bar.querySelector('#hudEndSession');
        if (endBtn && !endBtn.__endWired) {
            endBtn.__endWired = true;
            endBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                try {
                    if (typeof window.visaionSession?.end === 'function') {
                        window.visaionSession.end('manual');
                    } else {
                        window.dispatchEvent(new CustomEvent('hud:end-session', { detail: { reason: 'manual' } }));
                    }
                } catch { }
            });
        }

        // ===== Voice toggle (no innerHTML stomping + iOS-safe) =====
        (() => {
            // iOS unlock, idempotent
            async function __unlockIOSAudioOnce() {
                if (window.__iosAudioUnlocked) return true;
                let unlocked = false;

                const markUnlocked = () => { unlocked = true; };

                try {
                    if (typeof window.primeCoachAudio === 'function') {
                        let primeResult = window.primeCoachAudio();
                        if (primeResult && typeof primeResult.then === 'function') {
                            primeResult = await primeResult.catch(() => false);
                        }
                        if (primeResult !== false) markUnlocked();
                    }
                } catch (err) {
                    try { console.warn('[hud] primeCoachAudio unlock failed', err); } catch { }
                }

                // WebAudio path
                try {
                    const Ctx = window.AudioContext || window.webkitAudioContext;
                    if (Ctx) {
                        const ctx = window.__coachPrimeCtx || (window.__coachPrimeCtx = new Ctx());
                        if (ctx.state === 'suspended' && ctx.resume) {
                            try { await ctx.resume(); markUnlocked(); } catch { }
                        } else if (ctx.state === 'running') {
                            markUnlocked();
                        }
                        const src = ctx.createBufferSource();
                        src.buffer = ctx.createBuffer(1, 1, 22050);
                        const gain = ctx.createGain(); gain.gain.value = 0;
                        src.connect(gain); gain.connect(ctx.destination);
                        try { src.start(0); src.stop(0); markUnlocked(); } catch { }
                    }
                } catch (err) {
                    try { console.warn('[hud] AudioContext unlock failed', err); } catch { }
                }

                // HTMLMediaElement path
                try {
                    let el = window.__coachAudioEl;
                    if (!el) {
                        el = document.createElement('audio');
                        el.style.display = 'none';
                        el.setAttribute('playsinline', ''); el.playsInline = true;
                        document.body.appendChild(el);
                        window.__coachAudioEl = el;
                    }
                    el.muted = false; el.volume = 1;
                    el.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=';
                    let playResult = el.play?.();
                    if (playResult && typeof playResult.then === 'function') {
                        playResult = await playResult.catch(() => false);
                    }
                    if (playResult !== false) markUnlocked();
                    try { el.pause?.(); el.removeAttribute('src'); el.load?.(); } catch { }
                } catch (err) {
                    try { console.warn('[hud] HTMLAudio unlock failed', err); } catch { }
                }

                if (!unlocked) {
                    try { window.__iosAudioUnlocked = false; } catch { }
                    return false;
                }

                try { window.__iosAudioUnlocked = true; } catch { }
                try { console.debug('[hud] iOS audio unlocked'); } catch { }
                return true;
            }


            function findBtn() {
                return document.getElementById('hudVoiceToggle')
                    || document.querySelector('[data-role="hud-voice-toggle"]')
                    || document.querySelector('#hud .voice-toggle, .hud .voice-toggle');
            }

            function setState(btn, muted) {
                // Do NOT touch innerHTML/textContent. CSS should react to these only:
                btn.dataset.muted = muted ? '1' : '0';
                btn.setAttribute('aria-pressed', muted ? 'false' : 'true');
                btn.classList.toggle('is-muted', !!muted);
                btn.classList.toggle('is-on', !muted);
                // force visible just in case
                btn.style.display = '';
                btn.style.visibility = 'visible';
            }

            function applyMute(btn, muted, announce = false) {
                setState(btn, muted);
                try { localStorage.setItem('visaion_muted', JSON.stringify(muted)); } catch { }
                try { window.__coachMuted = muted; } catch { }
                try { window.dispatchEvent(new CustomEvent('hud:mute-toggle', { detail: { muted } })); } catch { }

                if (!announce) return;

                if (!muted) {
                    try {
                        const maybe = __unlockIOSAudioOnce();
                        if (maybe && maybe.catch) maybe.catch(() => { });
                    } catch { }
                    try { window.CoachAudio?.unlock?.(); } catch { }
                    if (window.PREF_ALLOW_MIC === true) {
                        try { window.__startCoachVoiceRecognition?.(); } catch { }
                        try { window.dispatchEvent(new CustomEvent('coach:voice-rec-start', { detail: { via: 'hud-voice-toggle' } })); } catch { }
                    }
                }

                if (typeof window.visaionSpeak === 'function') {
                    try { window.visaionSpeak(muted ? 'Voice off.' : 'Voice on.'); } catch { }
                }
            }

            function wire(btn) {
                // restore saved
                let savedMuted = false;
                try {
                    const raw = localStorage.getItem('visaion_muted');
                    if (raw != null) savedMuted = JSON.parse(raw);
                } catch { }
                applyMute(btn, savedMuted, false);

                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    try {
                        const maybe = __unlockIOSAudioOnce();
                        if (maybe && typeof maybe.then === 'function') {
                            maybe.catch(() => { });
                        }
                    } catch { }

                    const wasMuted = btn.dataset.muted === '1';
                    applyMute(btn, !wasMuted, true);
                }, { passive: true });

                // one-time unlock for early touch
                window.addEventListener('touchstart', () => {
                    try { __unlockIOSAudioOnce(); } catch { }
                }, { once: true, passive: true });
                window.addEventListener('pointerdown', () => {
                    try { __unlockIOSAudioOnce(); } catch { }
                }, { once: true, passive: true });
                window.addEventListener('mousedown', () => {
                    try { __unlockIOSAudioOnce(); } catch { }
                }, { once: true, passive: true });
                window.addEventListener('hud:start-session', () => {
                    try {
                        const fn = (typeof window.unlockIOSAudio === 'function') ? window.unlockIOSAudio : __unlockIOSAudioOnce;
                        const maybe = (typeof fn === 'function') ? fn() : null;
                        if (maybe && typeof maybe.catch === 'function') maybe.catch(() => { });
                    } catch { }
                }, { once: true });
            }

            // Wait for button if HUD mounts late
            const btnNow = findBtn();
            if (btnNow) { if (!btnNow.__voiceWired) { btnNow.__voiceWired = true; wire(btnNow); } }
            else {
                const obs = new MutationObserver(() => {
                    const b = findBtn();
                    if (b) {
                        if (!b.__voiceWired) { b.__voiceWired = true; wire(b); }
                        obs.disconnect();
                    }
                });
                try { obs.observe(document.documentElement, { childList: true, subtree: true }); } catch { }
            }
        })();


        // Camera flip
        const updateHudCamButton = () => {
            const facing = currentFacingLabel();
            const icon = formatHudCameraIcon(facing);
            camBtn.dataset.facing = facing;
            camBtn.innerHTML = `<span class="icon" aria-hidden="true">${icon}</span>`;
            const next = facing === 'Back' ? 'front' : 'back';
            camBtn.setAttribute('aria-label', `Switch to ${next} camera`);
            camBtn.title = `Switch to ${next} camera`;
        };
        updateHudCamButton();
        window.addEventListener('camera:facing-changed', updateHudCamButton);

        camBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                const cur = (localStorage.getItem('visaion_camera_facing') || 'environment').toLowerCase();
                const next = (cur === 'user' || cur === 'front') ? 'environment' : 'user';
                localStorage.setItem('visaion_camera_facing', next);
                if (typeof window.setPreferredFacing === 'function') await window.setPreferredFacing(next);
                else if (typeof window.flipCamera === 'function') await window.flipCamera();
            } catch (err) {
                console.warn('[hud] flip camera failed', err);
            } finally {
                updateHudCamButton();
            }
        });
    }
    installOrientationHint(bar.querySelector('#hudRotateHint'));
    return bar;
}
window.mountSessionHUD = mountSessionHUD;

export function updateSessionHUD({ taken = 0, made = 0, accuracy = 0, elapsedSec = 0 } = {}) {
    const bar = mountSessionHUD();
    const $ = (id) => bar.querySelector(`#${id} .num`);
    const mm = Math.floor(elapsedSec / 60);
    const ss = Math.floor(elapsedSec % 60).toString().padStart(2, '0');
    const labelEl = bar.querySelector('#mShots .label');
    if (labelEl) {
        labelEl.textContent = getSessionTerminology().attemptsLabel || 'Shots Taken';
    }

    // Use FINALIZED rows only, never overlay pulses
    try {
        const list = Array.isArray(window.__shotList) ? window.__shotList : [];
        const finalized = list.filter(s => s && s.pending === false).length;
        taken = Math.max(Number(taken || 0), finalized);
    } catch { }

    const elShots = $('mShots');
    const elTime = $('mTime');
    if (elShots) elShots.textContent = `${taken}/${(Number(window.SESSION_SIZE) || '∞')}`;
    if (elTime) elTime.textContent = `${mm}:${ss}`;
}
window.updateSessionHUD = updateSessionHUD;

/* ------------------------ Camera switcher UI ------------------------ */
(function installCameraSwitcher() {
    if (window.__cameraSwitcherInstalled) return; window.__cameraSwitcherInstalled = true;

    function formatCameraFacing(label) {
        return ((label === 'Back') ? ICON_CAMERA_BACK : ICON_CAMERA_FRONT) + ' ' + label;
    }
    function readPref() { try { return localStorage.getItem('cam_facing') || 'Back'; } catch { return 'Back'; } }
    function writePref(v) {
        try { localStorage.setItem('cam_facing', v); } catch { }
        try { localStorage.setItem('visaion_camera_facing', v === 'Back' ? 'environment' : 'user'); } catch { }
    }

    function stopStream() {
        const v = document.getElementById('videoPlayer');
        const s = v && v.srcObject;
        if (s?.getTracks) s.getTracks().forEach(t => { try { t.stop(); } catch { } });
        if (v) v.srcObject = null;
    }
    function labelToFacing(label) { return (String(label).toLowerCase().startsWith('b')) ? 'environment' : 'user'; }

    async function startWithConstraints(cons, opts = {}) {
        const v = document.getElementById('videoPlayer');
        if (!v) return null;

        const preferredSizing = {
            width: { ideal: 1280 },
            height: { ideal: 720 },
            frameRate: { ideal: 30 }
        };

        const normalize = (raw) => {
            if (!raw) return {};
            if (typeof raw === 'string') return { facingMode: raw };
            if (typeof raw === 'object') return { ...raw };
            return {};
        };

        const buildAttempts = (raw) => {
            const base = normalize(raw);
            const attempts = [];
            const seen = new Set();
            const strictDeviceId = !!(opts.strictDeviceId && base.deviceId);
            const pushUnique = (obj) => {
                if (!obj) return;
                const sig = JSON.stringify(obj);
                if (seen.has(sig)) return;
                seen.add(sig);
                attempts.push(obj);
            };
            pushUnique({ ...preferredSizing, ...base });
            pushUnique(base);
            if (!strictDeviceId) {
                pushUnique({ ...preferredSizing });
            }
            return attempts;
        };

        const attempts = buildAttempts(cons);
        for (const videoCons of attempts) {
            let stream = null;
            try {
                stream = await navigator.mediaDevices.getUserMedia({ video: videoCons, audio: false });
                try {
                    v.setAttribute('playsinline', '');
                    v.playsInline = true;
                    v.muted = true;
                    v.autoplay = true;
                } catch { }
                v.srcObject = stream;

                if (v.readyState < 1 || !v.videoWidth || !v.videoHeight) {
                    await new Promise((resolve) => {
                        let done = false;
                        let timer = null;
                        const cleanup = () => {
                            if (done) return;
                            done = true;
                            if (timer != null) {
                                try { clearTimeout(timer); } catch { }
                            }
                            try { v.removeEventListener('loadedmetadata', onMeta); } catch { }
                            try { v.removeEventListener('error', onError); } catch { }
                            resolve();
                        };
                        const onMeta = () => cleanup();
                        const onError = () => cleanup();
                        timer = setTimeout(cleanup, 650);
                        v.addEventListener('loadedmetadata', onMeta, { once: true });
                        v.addEventListener('error', onError, { once: true });
                    });
                }

                try {
                    await v.play();
                } catch (err) {
                    try { console.warn('[camera] video play blocked', err); } catch { }
                    throw err;
                }

                try { syncOverlayToVideo?.(); } catch { }
                try {
                    requestAnimationFrame(() => {
                        try { syncOverlayToVideo?.(); } catch { }
                    });
                } catch { }
                try { window.scheduleSyncOverlay?.(); } catch { }

                return stream;
            } catch (err) {
                const isLastAttempt = (videoCons === attempts[attempts.length - 1]);
                try {
                    const log = isLastAttempt ? (console.warn || console.log) : (console.debug || console.log);
                    if (typeof log === 'function') {
                        log.call(console, isLastAttempt ? '[camera] getUserMedia failed' : '[camera] getUserMedia retry', err);
                    }
                } catch { }
                if (stream?.getTracks) {
                    try { stream.getTracks().forEach((t) => { try { t.stop(); } catch { }; }); } catch { }
                }
                if (v.srcObject === stream) {
                    try { v.srcObject = null; } catch { }
                }
            }
        }
        return null;
    }

    function getStreamDeviceId(stream) {
        try {
            const track = stream?.getVideoTracks?.()[0];
            const settings = track?.getSettings?.();
            return settings?.deviceId || null;
        } catch { }
        return null;
    }

    async function getVideoDevices() {
        if (!navigator.mediaDevices?.enumerateDevices) return [];
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const videos = devices.filter(device => device.kind === 'videoinput');
            const real = videos.filter(device => device.deviceId && device.deviceId !== 'default' && device.deviceId !== 'communications');
            return real.length ? real : videos;
        } catch { }
        return [];
    }

    function pickDeviceForLabel(devices, label) {
        if (!devices.length) return null;
        const wantBack = String(label || '').toLowerCase().startsWith('b');
        const labelText = (device) => String(device?.label || '').toLowerCase();
        const backKeys = ['back', 'rear', 'environment', 'world'];
        const frontKeys = ['front', 'user', 'face', 'selfie'];
        const keys = wantBack ? backKeys : frontKeys;
        const matches = devices.filter(device => {
            const text = labelText(device);
            return keys.some(key => text.includes(key));
        });
        if (matches.length) return matches[0];
        return wantBack ? devices[devices.length - 1] : devices[0];
    }

    async function ensurePreferredDevice(label, currentStream) {
        const devices = await getVideoDevices();
        if (!devices.length) return true;
        const hasLabels = devices.some(device => device.label);
        if (!hasLabels) return true;
        const target = pickDeviceForLabel(devices, label);
        if (!target?.deviceId) return true;

        const activeId = getStreamDeviceId(currentStream);
        if (activeId && activeId === target.deviceId) return true;

        stopStream();
        const switched = await startWithConstraints({ deviceId: { exact: target.deviceId } }, { strictDeviceId: true });
        if (switched) return true;

        const facing = labelToFacing(label);
        const fallback = await startWithConstraints({ facingMode: { exact: facing } })
            || await startWithConstraints({ facingMode: facing });
        return !!fallback;
    }

    async function restartCamera(label) {
        if (!navigator.mediaDevices?.getUserMedia) return false;
        // prefer facingMode path on iOS; deviceId after we learned labels
        try {
            stopStream();
            const facing = labelToFacing(label);
            const stream = await startWithConstraints({ facingMode: { exact: facing } });
            if (stream) {
                const ok = await ensurePreferredDevice(label, stream);
                if (ok) return true;
            }
        } catch { }
        try {
            stopStream();
            const stream = await startWithConstraints({ facingMode: labelToFacing(label) });
            if (stream) {
                const ok = await ensurePreferredDevice(label, stream);
                if (ok) return true;
            }
        } catch { }
        return false;
    }

    window.getCameraFacing = () => window.__CAM_FACING || readPref();
    window.setCameraFacing = async function (label) {
        const current = window.getCameraFacing();
        const target = (label === 'Front' || label === 'Back') ? label : (current === 'Back' ? 'Front' : 'Back');
        const ok = await restartCamera(target);
        if (ok) {
            window.__CAM_FACING = target;
            writePref(target);
            try {
                const hud = document.getElementById('hudCamFlip');
                if (hud) {
                    hud.dataset.facing = target;
                    const icon = formatHudCameraIcon(target);
                    hud.innerHTML = `<span class="icon" aria-hidden="true">${icon}</span>`;
                    const next = target === 'Back' ? 'front' : 'back';
                    hud.setAttribute('aria-label', `Switch to ${next} camera`);
                    hud.title = `Switch to ${next} camera`;
                }
            } catch { }
            try { window.dispatchEvent(new Event('camera:facing-changed')); } catch { }
            try { window.dispatchEvent(new CustomEvent('camera:changed', { detail: { label: target } })); } catch { }
        }
        return ok;
    };

    // Legacy helpers to support older callsites expecting the previous camera API.
    window.setPreferredFacing = async function (pref) {
        const facing = String(pref || '').toLowerCase();
        const label = (facing === 'user' || facing === 'front') ? 'Front' : 'Back';
        try {
            const ok = await window.setCameraFacing(label);
            return ok;
        } catch (err) {
            console.warn('[camera] setPreferredFacing failed', err);
            return false;
        }
    };

    window.flipCamera = async function () {
        try {
            const current = window.getCameraFacing();
            const target = current === 'Back' ? 'Front' : 'Back';
            return await window.setCameraFacing(target);
        } catch (err) {
            console.warn('[camera] flipCamera failed', err);
            return false;
        }
    };

    // Wrap startCamera so initial start honors preference
    (function wrapStartCamera() {
        const orig = window.startCamera;
        window.startCamera = async function () {
            const label = window.getCameraFacing();
            const ok = await restartCamera(label);
            if (!ok && typeof orig === 'function') return orig();
            return ok;
        };
    })();
})();

/* ------------------------ Session status badge ------------------------ */
export function setSessionStatus(text = '') {
    const root = ensureHudRoot();
    let badge = document.getElementById('sessionStatusBadge');
    if (!badge) {
        badge = document.createElement('div');
        badge.id = 'sessionStatusBadge';
        badge.className = 'hud-card';
        Object.assign(badge.style, {
            position: 'absolute', bottom: '90px', left: '50%', transform: 'translateX(-50%)',
            padding: '6px 10px', font: '600 12px system-ui', letterSpacing: '0.04em', pointerEvents: 'none'
        });
        root.appendChild(badge);
    }
    badge.textContent = text || 'SESSION IN PROGRESS…';
    badge.style.display = text === null ? 'none' : 'block';
}
window.setSessionStatus = setSessionStatus;

/* ------------------------ Summary table (UI) ------------------------ */
const SHOT_SUMMARY_TEXT = {
    modalTitle: 'Shot Summary',
    finalizing: 'Finalizing...',
    exportCsv: 'Export CSV',
    close: 'Close',
    pending: 'Pending',
    noValue: '--',
    buttons: { make: 'Make', miss: 'Miss', replay: 'Replay', ai: 'AI Review' }
};

function ensureShotTableStyles() {
    if (document.getElementById('shotTableStyles')) return;
    const css = document.createElement('style');
    css.id = 'shotTableStyles';
    css.textContent = `
    #fullShotModal{ display:flex; flex-direction:column; width:min(92vw, 960px); max-width:92vw; min-width:0; }
    #fullShotModal .shot-summary-header{ display:flex; align-items:center; justify-content:space-between; gap:10px; position:sticky; top:0; z-index:3; background:rgba(0,0,0,0.88); padding:6px 0 8px; }
    #fullShotModal .shot-summary-title{ font-weight:600; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    #fullShotModal .shot-summary-body{ flex:1 1 auto; min-height:0; overflow:auto; -webkit-overflow-scrolling: touch; }
    #fullShotModal .shot-summary-table-wrap{ overflow-x:auto; }
    #fullShotModal .shot-summary-table-wrap::-webkit-scrollbar{ height:8px; }
    #fullShotModal .shot-summary-table-wrap::-webkit-scrollbar-thumb{ background:rgba(255,255,255,.18); border-radius:999px; }
    #fullShotModal .hud-table{ width:100%; border-collapse:collapse; table-layout:fixed; }
    #fullShotModal .hud-table col#cNum{ width:42px; } #fullShotModal .hud-table col#cCoach{ width:auto; }
    #fullShotModal .hud-table col#cClip{ width:90px; text-align:center; }
    #fullShotModal .hud-table thead th{ position:sticky; top:0; background:rgba(0,0,0,0.85); z-index:2; backdrop-filter:blur(2px); }
    #fullShotModal .hud-table th, #fullShotModal .hud-table td{ padding:8px 10px; vertical-align:top; text-align:left; border-bottom:1px solid rgba(255,255,255,.12); }
    #fullShotModal .hud-table tbody tr:nth-child(even) td{ background:rgba(255,255,255,.03); }
    #fullShotModal td.num, #fullShotModal td.score, #fullShotModal td.clip { text-align:center; }
    #fullShotModal td.coach{ white-space:normal; word-break:break-word; line-height:1.25; }
    #fullShotModal .hud-table #cScore { width:70px; }
    #summaryProgressOverlay{ position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); width:min(92vw, 420px); padding:16px; text-align:center; z-index:10065; display:none; pointer-events:none; }
    #summaryProgressOverlay .summary-progress-title{ font:700 14px/1.2 system-ui, -apple-system, Segoe UI, Arial; margin-bottom:8px; }
    #summaryProgressOverlay .summary-progress-bar{ width:100%; height:8px; background:rgba(255,255,255,.18); border-radius:999px; overflow:hidden; }
    #summaryProgressOverlay .summary-progress-bar span{ display:block; height:100%; width:0%; background:var(--hud-accent); transition:width .4s ease; }
    #summaryProgressOverlay .summary-progress-label{ margin-top:8px; font:600 12px/1.2 system-ui, -apple-system, Segoe UI, Arial; opacity:.9; }
    @media (max-width: 720px){
      #fullShotModal{ top:6%; width:96vw; max-width:96vw; max-height:82vh; }
      #fullShotModal .hud-table col#cNum{ width:32px; }
      #fullShotModal .hud-table #cScore{ width:56px; }
      #fullShotModal .hud-table col#cClip{ width:64px; }
      #fullShotModal .hud-table th, #fullShotModal .hud-table td{ padding:6px 8px; font-size:12px; }
      #summaryProgressOverlay{ width:min(92vw, 320px); }
    }
  `;
    document.head.appendChild(css);
}

const SUMMARY_PROGRESS_STEPS = [
    { pct: 50, label: 'Analyzing swings...', delay: 1600 },
    { pct: 70, label: 'Building recap...', delay: 3000 },
    { pct: 90, label: 'Finishing coach summary...', delay: 4800 }
];
let __summaryProgressTimers = [];
let __summaryProgressActive = false;

function ensureSummaryProgressOverlay() {
    const root = ensureHudRoot();
    let el = document.getElementById('summaryProgressOverlay');
    if (!el) {
        el = document.createElement('div');
        el.id = 'summaryProgressOverlay';
        el.className = 'hud-card';
        el.innerHTML = `
      <div class="summary-progress-title">Preparing session summary</div>
      <div class="summary-progress-bar"><span></span></div>
      <div class="summary-progress-label">Finalizing clips... (30%)</div>
    `;
        root.appendChild(el);
    } else if (!root.contains(el)) {
        root.appendChild(el);
    }
    return el;
}

function clearSummaryProgressTimers() {
    __summaryProgressTimers.forEach((id) => clearTimeout(id));
    __summaryProgressTimers = [];
}

function setSummaryProgress(pct, label) {
    const el = ensureSummaryProgressOverlay();
    const bar = el.querySelector('.summary-progress-bar span');
    const text = el.querySelector('.summary-progress-label');
    const safePct = Math.max(0, Math.min(100, Number(pct) || 0));
    if (bar) bar.style.width = `${safePct}%`;
    if (text) text.textContent = `${label || 'Working...'} (${safePct}%)`;
}

function showSummaryProgress() {
    const el = ensureSummaryProgressOverlay();
    clearSummaryProgressTimers();
    __summaryProgressActive = true;
    el.dataset.active = 'true';
    el.style.display = 'flex';
    el.style.flexDirection = 'column';
    el.style.alignItems = 'center';
    el.style.gap = '8px';
    setSummaryProgress(30, 'Finalizing clips...');
    SUMMARY_PROGRESS_STEPS.forEach((step) => {
        const id = setTimeout(() => {
            if (el.dataset.active !== 'true') return;
            setSummaryProgress(step.pct, step.label);
        }, step.delay);
        __summaryProgressTimers.push(id);
    });
}

function completeSummaryProgress(label = 'Coach summary done') {
    if (!__summaryProgressActive) return;
    const el = ensureSummaryProgressOverlay();
    setSummaryProgress(100, label);
    __summaryProgressActive = false;
    el.dataset.active = 'false';
    clearSummaryProgressTimers();
    const id = setTimeout(() => {
        hideSummaryProgress();
    }, 700);
    __summaryProgressTimers.push(id);
}

function hideSummaryProgress() {
    const el = document.getElementById('summaryProgressOverlay');
    if (!el) return;
    __summaryProgressActive = false;
    el.dataset.active = 'false';
    clearSummaryProgressTimers();
    el.style.display = 'none';
}

function applyShotSummaryLayout(modal) {
    if (!modal) return;
    const isMobile = (Number(window.innerWidth) || 0) <= 720;
    modal.style.width = isMobile ? '96vw' : 'min(92vw, 960px)';
    modal.style.maxWidth = isMobile ? '96vw' : '92vw';
    modal.style.minWidth = '0';
    modal.style.maxHeight = isMobile ? '82vh' : '78vh';
    modal.style.overflow = 'hidden';
    modal.style.padding = isMobile ? '10px 10px 8px' : '12px 12px 10px';
}

function positionShotSummaryModal(modal) {
    if (!modal) return;
    applyShotSummaryLayout(modal);
    const viewportH = Number(window.innerHeight) || 0;
    const isMobile = (Number(window.innerWidth) || 0) <= 720;
    const defaultTopPx = viewportH ? Math.round(viewportH * (isMobile ? 0.06 : 0.12)) : 80;
    let topPx = defaultTopPx;
    const coach = document.getElementById('coachNotes');
    if (coach && isElementVisible(coach)) {
        const rect = coach.getBoundingClientRect();
        if (rect && rect.height) {
            topPx = Math.max(topPx, Math.round(rect.bottom + 12));
        }
    }
    const maxTopPx = viewportH ? Math.round(viewportH * (isMobile ? 0.25 : 0.35)) : 220;
    if (topPx > maxTopPx) topPx = maxTopPx;
    modal.style.top = `${topPx}px`;
}

function deriveShotScore(shot) {
    if (!shot || typeof shot !== 'object') return null;
    const candidates = [
        shot.poseScore,
        shot.pose_score,
        shot.score,
        shot.weightedScore,
        shot.weighted_score,
        shot.arcmm?.summary?.poseScore,
        shot.arcmm?.poseScore,
        shot.pose?.score,
    ];
    for (const candidate of candidates) {
        const val = Number(candidate);
        if (Number.isFinite(val)) {
            const normalized = val <= 1 ? val * 100 : val;
            return normalized;
        }
    }
    return null;
}

function normalizeShotScore(shot) {
    if (!shot || typeof shot !== 'object') return shot;
    const val = deriveShotScore(shot);
    if (val != null) shot.poseScore = val;
    return shot;
}

function updateShotTableTotalsFromDOM(modal) {
    if (!modal) return;
    const tbody = modal.querySelector('tbody');
    if (!tbody) return;
    let total = 0;
    let count = 0;
    tbody.querySelectorAll('tr[data-shot-idx] .score').forEach((cell) => {
        const val = Number(cell.textContent);
        if (Number.isFinite(val)) {
            total += val;
            count += 1;
        }
    });
    let totalRow = tbody.querySelector('tr.totals');
    if (count === 0) {
        if (totalRow) totalRow.remove();
        return;
    }
    if (!totalRow) {
        totalRow = document.createElement('tr');
        totalRow.className = 'totals';
        totalRow.innerHTML = '<td class="num">Σ</td><td class="coach">Total Pose Score</td><td class="score"></td><td class="clip"></td>';
    } else {
        totalRow.remove();
    }
    const scoreCell = totalRow.querySelector('.score');
    if (scoreCell) scoreCell.textContent = Math.round(total);
    const reviewRow = tbody.querySelector('#sessionReviewRow');
    if (reviewRow) tbody.insertBefore(totalRow, reviewRow);
    else tbody.appendChild(totalRow);
}

function getClipHrefForShot(idx1Based, shot) {
    if (shot?.clip?.path) return shot.clip.path;
    if (typeof shot?.clip === 'string') return shot.clip;
    return null;
}

export function renderFullShotTable(opts = {}) {
    const list = (window.__shotList ||= []);
    list.forEach(normalizeShotScore);
    const root = ensureHudRoot();
    list.forEach(normalizeShotScore);
    const minimal = true; // skinny table only

    ensureShotTableStyles();
    const showModal = opts?.show !== false;
    let modal = document.getElementById('fullShotModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'fullShotModal';
        modal.className = 'hud-card';
        Object.assign(modal.style, {
            position: 'absolute', left: '50%', transform: 'translateX(-50%)', top: '12%',
            zIndex: 10020, pointerEvents: 'auto'
        });
        root.appendChild(modal);
    }
    applyShotSummaryLayout(modal);

    modal.innerHTML = `
    <div class="shot-summary-header">
      <div class="shot-summary-title">
        <span>Shot Summary (${list.length}/${formatCapDisplay(window.SESSION_SIZE)})</span>
        <span id="sessFinalBadge" style="display:none; padding:3px 8px; border-radius:10px; font:600 11px system-ui; background:#f59e0b; color:#111;">Finalizing...</span>
      </div>
      <div class="shot-summary-actions">
        <button id="closeFull" class="vc-btn">Close</button>
      </div>
    </div>
    <div class="shot-summary-body">
      <div id="sessReviewLine" style="display:none;opacity:.95;margin:4px 0 10px;line-height:1.35"></div>
      <div class="shot-summary-table-wrap">
        <table class="hud-table">
          <colgroup><col id="cNum"><col id="cCoach"><col id="cScore"><col id="cClip"></colgroup>
          <thead><tr><th>#</th><th>Coach Pose Assessment</th><th>Score</th><th>Clip</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  `;

    const tbody = modal.querySelector('tbody');
    tbody.textContent = '';
    list.forEach((shot, idx) => {
        const coachSource = shot && !shot.pending
            ? (shot.visaion || shot.coach || shot.coachText || shot.feedback || shot.summary || shot.text || '')
            : '';
        const coachText = coachSource ? coachSource : SHOT_SUMMARY_TEXT.pending;

        const tr = document.createElement('tr');
        tr.setAttribute('data-shot-idx', idx + 1);
        tr.innerHTML = `<td class="num">${idx + 1}</td><td class="coach"></td><td class="score"></td><td class="clip"></td>`;
        tr.querySelector('.coach').textContent = coachText;

        const tdScore = tr.querySelector('.score');
        const scoreVal = deriveShotScore(shot);
        if (scoreVal != null) {
            const display = Math.round(scoreVal);
            tdScore.textContent = display;
            tdScore.dataset.value = display;
        } else {
            tdScore.textContent = '--';
        }

        const tdClip = tr.querySelector('.clip');
        const href = getClipHrefForShot(idx + 1, shot);
        if (href) {
            const a = document.createElement('a'); a.href = href; a.target = '_blank'; a.rel = 'noopener'; a.textContent = 'clip';
            tdClip.appendChild(a);
        } else {
            const status = shot?.clip?.status;
            tdClip.textContent = status === 'recording' ? 'recording…' : 'processing…';
        }
        tbody.appendChild(tr);
    });
    updateShotTableTotalsFromDOM(modal);

    modal.querySelector('#closeFull').onclick = () => { modal.style.display = 'none'; };
    modal.style.display = showModal ? 'flex' : 'none';
    try { modal.style.zIndex = '10060'; } catch { }
    try { positionShotSummaryModal(modal); } catch { }
    if (showModal) {
        try { completeSummaryProgress(); } catch { }
    }

    try {
        const detail = window.__SESSION_REVIEW_LAST;
        if (detail?.summary) {
            let row = modal.querySelector('#sessionReviewRow');
            if (!row) {
                row = document.createElement('tr');
                row.id = 'sessionReviewRow';
                row.innerHTML = '<td class="num">~</td><td class="coach session-review"></td><td class="score"></td><td class="clip"></td>';
                modal.querySelector('tbody')?.appendChild(row);
            }
            const cell = row.querySelector('.coach');
            if (cell) cell.textContent = detail.summary;
            const scoreCell = row.querySelector('.score');
            if (scoreCell) scoreCell.textContent = '--';
            row.style.display = 'table-row';
            row.dataset.visible = 'true';
        }
    } catch { }
    updateShotTableTotalsFromDOM(modal);

    return modal;
}
window.renderFullShotTable = renderFullShotTable;

/* ------------------- UI sink for finalized shots ------------------- */
function computeTotals(list) {
    const taken = list.length;
    const made = list.filter(s => s.made).length;
    const acc = taken ? (made / taken) * 100 : 0;
    return { taken, made, acc };
}

// Count finalized shots only
window.__finalizedShotIds ||= new Set();


// Record a finalized shot summary (UI only, no server)
window.recordShotSummary = function recordShotSummary(summary) {
    summary = normalizeShotScore(summary);
    const list = (window.__shotList ||= []);
    let sid = Number(summary?.shotId || 0);
    if (!Number.isFinite(sid) || sid <= 0) {
        sid = 0;
    } else {
        const maxAllowed = list.length + 1;
        if (sid > maxAllowed) {
            console.warn('[shotId clamp] incoming shotId jumped', {
                incoming: sid,
                maxAllowed,
                listLen: list.length,
                sessionId: window.__SESSION_ID,
                via: summary?.via
            });
            sid = maxAllowed;
            summary.shotId = sid;
        }
    }
    if (window.SWING_DEBUG === true) {
        console.log('[recordShotSummary] incoming', {
            shotId: summary?.shotId,
            listLen: list.length,
            sessionId: window.__SESSION_ID,
            via: summary?.via
        });
    }
    const shotRecord = (Number.isFinite(summary?.shotId) && window.__shots instanceof Map && typeof window.__shots.get === 'function')
        ? window.__shots.get(summary.shotId)
        : null;
    if (!summary.clip && shotRecord?.clip) {
        summary.clip = (typeof shotRecord.clip === 'string')
            ? { path: shotRecord.clip }
            : { ...shotRecord.clip };
    }
    const originalWeighted = Number.isFinite(summary?.weightedScore) ? summary.weightedScore : null;
    const debugKey = Number.isFinite(summary?.shotId) ? String(summary.shotId) : null;
    const debugSnapshot = debugKey && window.__POSE_SCORE_DEBUG?.get?.(debugKey);
    if (debugSnapshot) summary.__poseDebug = debugSnapshot;
    delete summary.trailWeightedScore;

    if (!summary?.poseSnapshot && Number.isFinite(summary?.shotId)) {
        let snapFromStore = null;
        if (window.__poseIsFreshFor?.(summary.shotId)) {
            try { snapFromStore = window.poseStore?.get(summary.shotId) || null; } catch { }
        }
        if (snapFromStore) summary.poseSnapshot = snapFromStore;
    }

    try {
        if (typeof window.computePoseScoreFallback === 'function' && summary?.poseSnapshot) {
            const baseWeighted = null;
            const weightedSource = summary?.weightedScoreSource && typeof summary.weightedScoreSource === 'string'
                ? summary.weightedScoreSource
                : null;
            const recomputed = window.computePoseScoreFallback(
                summary.poseSnapshot,
                baseWeighted,
                summary?.shotId ?? summary?.id ?? null,
                weightedSource ? { weightedSource } : {}
            );
            if (Number.isFinite(recomputed)) {
                summary.poseScore = recomputed;
                summary.poseScoreSource = summary.poseScoreSource || 'pose-recalc';
                summary.weightedScore = Math.max(0, Math.min(1, recomputed / 100));
                summary.weightedScoreSource = 'pose-recalc';
                const updatedDebug = debugKey && window.__POSE_SCORE_DEBUG?.get?.(debugKey);
                if (updatedDebug) summary.__poseDebug = updatedDebug;
                if (window.SCORE_DEBUG === true) {
                    console.log('[score:recordShotSummary:recomputed]', {
                        shotId: summary?.shotId ?? summary?.id ?? null,
                        poseScore: summary.poseScore,
                        poseScoreSource: summary.poseScoreSource,
                        weightedScore: summary.weightedScore,
                        weightedScoreSource: summary.weightedScoreSource,
                        baseWeighted,
                        weightedSource
                    });
                }
            }
        }
    } catch (err) {
        console.warn('[score:recordShotSummary] recompute failed', err);
    }

    if (!summary?.poseSnapshot && window.SCORE_DEBUG === true) {
        console.warn('[score:recordShotSummary] no snapshot; skip pose-recalc', { shotId: summary?.shotId ?? null });
    }

    if (!Number.isFinite(summary.poseScore)) {
        const fallbackScore = deriveShotScore(summary);
        if (Number.isFinite(fallbackScore)) {
            summary.poseScore = fallbackScore;
            summary.poseScoreSource = summary.poseScoreSource || 'fallback-derive';
        }
    }
    if (!Number.isFinite(summary.weightedScore) && Number.isFinite(summary.poseScore)) {
        summary.weightedScore = Math.max(0, Math.min(1, summary.poseScore / 100));
        summary.weightedScoreSource = summary.weightedScoreSource || 'fallback-derive';
    }
    if (!summary.__poseDebug && debugKey) {
        const fallbackDebug = window.__POSE_SCORE_DEBUG?.get?.(debugKey);
        if (fallbackDebug) summary.__poseDebug = fallbackDebug;
    }
    try {
        const derived = deriveShotScore(summary);
        const shotId = summary?.shotId ?? summary?.id ?? null;
        const idxDbg = summary?.__idx ?? null;
        const dbg = {
            shotId,
            idx: idxDbg,
            poseScore: summary?.poseScore ?? null,
            poseScoreSource: summary?.poseScoreSource ?? null,
            weightedScore: summary?.weightedScore ?? summary?.weightScore ?? null,
            weightedScoreSource: summary?.weightedScoreSource ?? null,
            derived,
            poseDebug: summary.__poseDebug || null
        };
        if (summary?.arcmm?.summary?.poseScore != null) dbg.arcmmPose = summary.arcmm.summary.poseScore;
        if (summary?.data?.weightedScore != null) dbg.dataWeighted = summary.data.weightedScore;
        console.log('[score:recordShotSummary]', { ...dbg, poseDebug: summary.__poseDebug || null }, summary);
    } catch (err) {
        console.warn('[score:recordShotSummary] failed to inspect summary', err);
    }
    // de-dupe by shotId first (but allow richer follow-up updates)
    if (sid > 0) {
        if (!window.__finalizedShotIds.has(sid)) {
            window.__finalizedShotIds.add(sid);
        }
    }

    // de-dupe minor repeats by value signature but allow richer follow-ups
    const key = `${sid || '?'}|${+!!summary.made}|${Math.round(summary.arcHeight || 0)}|${summary.entryAngle}|${summary.releaseAngle}`;
    if (window.__lastShotKey === key) {
        const idxForKey = Number.isFinite(sid) && sid > 0 ? sid - 1 : -1;
        const existingForKey = idxForKey >= 0 ? list[idxForKey] : null;
        const incomingScoreVal = deriveShotScore(summary);
        const existingScoreVal = deriveShotScore(existingForKey);
        const addsPoseSnapshot = !!summary?.poseSnapshot && !existingForKey?.poseSnapshot;
        const addsWeighted = Number.isFinite(summary?.weightedScore) && !Number.isFinite(existingForKey?.weightedScore);
        const addsPoseScore = Number.isFinite(incomingScoreVal) && (
            !Number.isFinite(existingScoreVal) ||
            Math.abs(incomingScoreVal - existingScoreVal) >= 0.5
        );
        const hasNewInfo = addsPoseSnapshot || addsWeighted || addsPoseScore;
        if (!hasNewInfo) {
            return;
        }
    }
    window.__lastShotKey = key;

    // carry coach and via
    if (!summary.visaion && window.__lastCoachText) summary.visaion = window.__lastCoachText;
    if (!summary.via) summary.via = window.__lastReleaseVia || summary.via || '';

    const mergeSummary = (target = {}) => {
        Object.assign(target, summary);
        target.pending = false;
        if (Number.isFinite(summary.poseScore)) target.poseScore = summary.poseScore;
        if (Number.isFinite(summary.weightedScore)) target.weightedScore = summary.weightedScore;
        if (summary.weightedScoreSource) target.weightedScoreSource = summary.weightedScoreSource;
        if (summary.poseScoreSource) target.poseScoreSource = summary.poseScoreSource;
        return target;
    };

    let idx = sid;
    if (Number.isFinite(idx) && idx > 0) {
        while (list.length < idx) list.push({ pending: true });
        list[idx - 1] = mergeSummary(list[idx - 1] || {});
    } else {
        const p = list.findIndex(s => s?.pending === true);
        if (p !== -1) {
            list[p] = mergeSummary(list[p] || {});
            idx = p + 1;
        } else {
            list.push(mergeSummary({}));
            idx = list.length;
        }
    }
    summary.__idx = idx;

    // If the skinny table is open, refresh the row
    const modal = document.getElementById('fullShotModal');
    if (modal) {
        const tbody = modal.querySelector('tbody');
        let tr = tbody.querySelector(`tr[data-shot-idx="${idx}"]`);
        if (!tr) {
            tr = document.createElement('tr');
            tr.setAttribute('data-shot-idx', idx);
            tr.innerHTML = `<td class="num">${idx}</td><td class="coach"></td><td class="score"></td><td class="clip"></td>`;
            tbody.appendChild(tr);
        }
        const merged = (Number.isFinite(idx) && idx > 0 && list[idx - 1]) ? list[idx - 1] : summary;
        const coach = String(merged.visaion || '--');
        const tdCoach = tr.querySelector('.coach');
        if (tdCoach) { tdCoach.textContent = coach; tdCoach.title = coach; }

        const scoreCell = tr.querySelector('.score');
        if (scoreCell) {
            const scoreVal = deriveShotScore(merged);
            scoreCell.textContent = scoreVal != null ? Math.round(scoreVal) : '--';
            console.log('[score:table:update]', {
                shotIdx: idx,
                shotId: merged?.shotId ?? merged?.id ?? null,
                poseScore: merged?.poseScore ?? null,
                weightedScore: merged?.weightedScore ?? null,
                displayed: scoreCell.textContent
            });
        }

        const tdClip = tr.querySelector('.clip');
        if (tdClip) {
            tdClip.textContent = '';
            const href = merged.clip?.path || (function () {
                try { const sid = window.__SESSION_ID; return sid != null ? `/api/sessions/${sid}/shot_video?index=${idx - 1}` : null; } catch { return null; }
            })();
            if (href) {
                const a = document.createElement('a'); a.href = href; a.target = '_blank'; a.rel = 'noopener'; a.textContent = 'clip';
                tdClip.appendChild(a);
            } else {
                tdClip.textContent = merged.clip?.status === 'recording' ? 'recording…' : 'processing…';
            }
        }
        updateShotTableTotalsFromDOM(modal);
    }

    // HUD counters -- FINALIZED only
    try {
        const finalized = list.filter(s => s && s.pending === false).length;
        const start = (window.__sessionStart ||= Date.now());
        const elapsedSec = Math.floor((Date.now() - start) / 1000);
        updateSessionHUD?.({ taken: finalized, elapsedSec, ...computeTotals(list) });
    } catch { }
};

// capture the last release source for carry-forward
window.addEventListener('shot:release', (e) => { window.__lastReleaseVia = e?.detail?.via || ''; });



// Always reset playbackRate to 1× on summary (safari sanity)
window.addEventListener('shot:summary', () => {
    const v = document.getElementById('videoPlayer') || document.querySelector('video');
    if (v) { try { v.playbackRate = 1; } catch { } }
});

/* ------------------------ Session restart prompt ------------------------ */
const NEW_SESSION_PROMPT_DELAY_MS = 25000;
let __newSessionPromptTimer = null;
let __newSessionResetWatcher = null;
let __newSessionQuestionAsked = false;
let __newSessionFinalized = false;
let __newSessionAwaitingConfirm = false;

function setAwaitingNewSessionConfirm(state) {
    __newSessionAwaitingConfirm = !!state;
    try { window.__AWAITING_NEW_SESSION_CONFIRM = __newSessionAwaitingConfirm; } catch { }
}

function hideStartSessionOverlay() {
    try {
        const overlay = document.getElementById('startSessionOverlay');
        if (overlay) overlay.style.display = 'none';
    } catch { }
}

function showStartSessionOverlay() {
    try {
        const overlay = document.getElementById('startSessionOverlay');
        if (overlay) overlay.style.display = 'flex';
    } catch { }
}

function getPlayerDisplayNameForPrompt() {
    try {
        if (typeof window.getVisaionDisplayName === 'function') {
            const name = window.getVisaionDisplayName();
            if (name) return name;
        }
    } catch { }
    try {
        const nameLike = [
            window.__USER_NAME,
            window.__USER_DISPLAY_NAME,
            window.__PLAYER_NAME
        ].find((n) => typeof n === 'string' && n.trim());
        if (nameLike) return nameLike.trim();
    } catch { }
    try {
        if (window.__AUTHED !== true) {
            const guest = window.__GUEST_NAME || sessionStorage.getItem('visaion_guest_name');
            if (typeof guest === 'string' && guest.trim()) return guest.trim();
        }
    } catch { }
    try {
        if (window.__AUTHED === true) {
            const lsName = localStorage.getItem('firstname');
            if (typeof lsName === 'string' && lsName.trim()) return lsName.trim();
        }
    } catch { }
    try {
        if (window.__AUTHED === true) {
            const raw = localStorage.getItem('visaionProfile');
            if (raw) {
                const profile = JSON.parse(raw);
                const name = profile?.name || profile?.firstName;
                if (typeof name === 'string' && name.trim()) return name.trim();
            }
        }
    } catch { }
    return 'Player';
}

function isElementVisible(el) {
    if (!el) return false;
    if (el.hidden === true) return false;
    const display = (el.style && el.style.display) || '';
    if (display && display.toLowerCase() === 'none') return false;
    return true;
}

function clearNewSessionPromptTimers() {
    if (__newSessionPromptTimer) {
        clearTimeout(__newSessionPromptTimer);
        __newSessionPromptTimer = null;
    }
    if (__newSessionResetWatcher) {
        clearInterval(__newSessionResetWatcher);
        __newSessionResetWatcher = null;
    }
}

// keep session in landscape mode


async function startLandscapeRecorder(videoEl, opts = {}) {

    try { window.__HUD_MANAGES_COUNTDOWN = true; } catch { }

    const fps = opts.fps || 30;

    const wantW = opts.width || 1280;

    const wantH = opts.height || 720;

    const overlayEl = document.getElementById(opts.overlayId || 'overlay');

    const bufferWindowMs = opts.bufferWindowMs ?? Math.max(4000, (window.__MICROCLIP_MS ?? 3000) + (window.__MICROCLIP_PRE_MS ?? 360) + 1000);

    const sliceMs = Math.max(50, Math.round(1000 / fps));
    const MIN_FINAL_CHUNK_BYTES = 4096;
    const MIN_FINAL_CHUNK_DELAY = 160;



    const cvs = document.createElement('canvas');

    const ctx = cvs.getContext('2d', { alpha: false });



    function layoutForLandscape() {

        cvs.width = wantW;

        cvs.height = wantH;

        const vW = videoEl.videoWidth || wantW;

        const vH = videoEl.videoHeight || wantH;

        const isPortraitStream = vH > vW;

        const scaleCover = Math.max(wantW / vW, wantH / vH);

        return { vW, vH, isPortraitStream, scaleCover };

    }

    const getSourceMetrics = () => {
        const vW = Number(videoEl?.videoWidth) || 0;
        const vH = Number(videoEl?.videoHeight) || 0;
        const width = vW > 0 ? vW : wantW;
        const height = vH > 0 ? vH : wantH;
        return { width, height, isPortrait: height > width };
    };



    const stream = cvs.captureStream(fps);



    const mimeCandidates = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];

    const mimeType = mimeCandidates.find(m => {

        try { return MediaRecorder.isTypeSupported?.(m); } catch { return false; }

    }) || 'video/webm';



    const buffer = [];

    let initChunk = null;

    const activeCaptures = new Set();
    const captureByKey = new Map();
    const idleWaiters = new Set();

    const headerChunks = [];
    let recorder = new MediaRecorder(stream, { mimeType });
    let fallbackInitChunk = null;
    const initReadyWaiters = new Set();

    let requestTimer = null;

    let lastChunkTime = performance.now();

    const stopWaiters = new Set();
    const CLUSTER_SIGNATURE = [0x1f, 0x43, 0xb6, 0x75];
    let initNotified = false;
    let headerCaptured = false;

    function resolveInitWaiters() {
        if (initNotified) return;
        initNotified = true;
        if (!initReadyWaiters.size) return;
        const waiters = Array.from(initReadyWaiters);
        initReadyWaiters.clear();
        waiters.forEach((fn) => {
            try { fn(); } catch { }
        });
    }

    async function splitWebmHeaderBlob(blob) {
        try {
            const buf = await blob.arrayBuffer();
            const bytes = new Uint8Array(buf);
            const sigLen = CLUSTER_SIGNATURE.length;
            const limit = Math.max(0, bytes.length - sigLen);
            for (let i = 0; i <= limit; i++) {
                let match = true;
                for (let j = 0; j < sigLen; j++) {
                    if (bytes[i + j] !== CLUSTER_SIGNATURE[j]) {
                        match = false;
                        break;
                    }
                }
                if (match) {
                    const header = blob.slice(0, i);
                    const payload = blob.slice(i);
                    if (header.size > 0) {
                        return { header, payload };
                    }
                    break;
                }
            }
        } catch (err) {
            if (window.DEBUG_MICROCLIP === true) {
                console.warn('[landscapeRecorder] header split failed', err);
            }
        }
        return { header: blob, payload: null };
    }

    function clearRequestTimer() {
        if (requestTimer) {
            clearInterval(requestTimer);
            requestTimer = null;
        }
    }

    function notifyStopWaiters() {
        if (!stopWaiters.size) return;
        const waiters = Array.from(stopWaiters);
        stopWaiters.clear();
        waiters.forEach(fn => {
            try { fn(); } catch { }
        });
    }

    function resolveIdleWaiters() {
        if (activeCaptures.size) return;
        if (!idleWaiters.size) return;
        const waiters = Array.from(idleWaiters);
        idleWaiters.clear();
        waiters.forEach((fn) => {
            try { fn(); } catch { }
        });
    }

    function finalizeActiveCapture(capture, reason = 'complete') {
        if (!capture || capture.finalized) return null;
        capture.finalized = true;
        capture.flushRequested = false;
        if (capture.finalizeTimer) {
            clearTimeout(capture.finalizeTimer);
            capture.finalizeTimer = null;
        }
        if (capture.stopTimer) {
            clearTimeout(capture.stopTimer);
            capture.stopTimer = null;
        }
        let parts = [];
        const headerBlob = initChunk || fallbackInitChunk;
        try {
            if (typeof capture.buildParts === 'function') {
                parts = capture.buildParts(capture.liveChunks || []);
            } else {
                const live = Array.isArray(capture.liveChunks)
                    ? capture.liveChunks.slice()
                    : [];
                parts = headerBlob
                    ? [headerBlob, ...live.filter(blob => blob !== headerBlob)]
                    : live;
            }
        } catch (err) {
            console.warn('[landscapeRecorder] finalize failed to build parts', err, { reason });
            parts = [];
        }
        if (!parts.length && headerBlob) parts = [headerBlob];
        if (window.DEBUG_MICROCLIP === true) {
            const sizes = parts.map(p => p?.size ?? 0);
            const headBytesPromise = (async () => {
                try {
                    const buf = await parts[0]?.slice?.(0, 4)?.arrayBuffer?.();
                    return buf ? Array.from(new Uint8Array(buf)) : null;
                } catch { return null; }
            })();
            Promise.resolve(headBytesPromise).then((headBytes) => {
                console.log('[landscapeRecorder] finalize', reason, {
                    initSize: initChunk?.size ?? 0,
                    partSizes: sizes,
                    estimatedBytes: sizes.reduce((a, b) => a + b, 0),
                    headBytes
                });
            });
        }
        const clipBlob = new Blob(parts, { type: mimeType });
        const resolver = capture.resolve;
        const rejecter = capture.reject;
        activeCaptures.delete(capture);
        if (capture.key) {
            captureByKey.delete(String(capture.key));
        }
        if (!activeCaptures.size) resolveIdleWaiters();
        if (clipBlob.size > 0) {
            resolver?.(clipBlob);
            return clipBlob;
        }
        rejecter?.(new Error('empty clip'));
        return null;
    }

    async function handleData(r, e) {
        if (recorder !== r) return;
        if (!e?.data || !e.data.size) return;
        if (window.DEBUG_MICROCLIP === true) {
            const anyFlushRequested = Array.from(activeCaptures).some((capture) => capture.flushRequested);
            console.log('[landscapeRecorder] chunk', {
                size: e.data.size,
                flushRequested: anyFlushRequested
            });
        }
        let blobData = e.data;
        const now = performance.now();
        let duration = Math.max(1, now - lastChunkTime);
        lastChunkTime = now;
        let headerJustCaptured = false;

        if (!headerCaptured) {
            headerChunks.push(blobData);
            const combined =
                headerChunks.length === 1
                    ? headerChunks[0]
                    : new Blob(headerChunks, { type: blobData.type || mimeType });
            try {
                const { header, payload } = await splitWebmHeaderBlob(combined);
                const headerBlob = header && header.size ? header : combined;
                initChunk = headerBlob;
                fallbackInitChunk = headerBlob;
                if (!payload || !payload.size) {
                    if (window.DEBUG_MICROCLIP === true) {
                        console.log('[landscapeRecorder] waiting for first Cluster chunk');
                    }
                    return;
                }
                headerCaptured = true;
                headerJustCaptured = true;
                resolveInitWaiters();
                headerChunks.length = 0;
                blobData = payload;
            } catch (err) {
                if (window.DEBUG_MICROCLIP === true) {
                    console.warn('[landscapeRecorder] header parse pending', err);
                }
                return;
            }
        }
        if (headerJustCaptured) {
            const capMs = Math.max(120, sliceMs * 3);
            if (duration > capMs) {
                if (window.DEBUG_MICROCLIP === true) {
                    console.log('[landscapeRecorder] header duration clamp', {
                        duration: Math.round(duration),
                        capMs
                    });
                }
                duration = capMs;
            }
        }

        const entry = { blob: blobData, duration, ts: now };
        buffer.push(entry);
        while (buffer.length && (now - buffer[0].ts) > bufferWindowMs) buffer.shift();

        if (activeCaptures.size) {
            for (const capture of Array.from(activeCaptures)) {
                capture.liveChunks.push(blobData);
                capture.remainingMs -= duration;
                if (capture.flushRequested) {
                    const sinceFlush = now - (capture.flushRequestTime || now);
                    if (!capture.finalized && (blobData.size >= MIN_FINAL_CHUNK_BYTES || sinceFlush >= MIN_FINAL_CHUNK_DELAY)) {
                        finalizeActiveCapture(capture, 'flush-complete');
                    }
                } else if (capture.remainingMs <= 0) {
                    capture.flushRequested = true;
                    capture.flushRequestTime = now;
                    if (capture.finalizeTimer) {
                        clearTimeout(capture.finalizeTimer);
                        capture.finalizeTimer = null;
                    }
                    capture.finalizeTimer = setTimeout(() => {
                        if (capture && !capture.finalized) {
                            finalizeActiveCapture(capture, 'flush-timeout');
                        }
                    }, Math.max(300, sliceMs * 6));
                }
            }
        }
    }

    recorder.ondataavailable = (e) => {
        const maybe = handleData(recorder, e);
        if (maybe && typeof maybe.catch === 'function') {
            maybe.catch((err) => {
                if (window.DEBUG_MICROCLIP === true) {
                    console.warn('[landscapeRecorder] handleData failed', err);
                }
            });
        }
    };
    recorder.addEventListener('stop', () => {
        notifyStopWaiters();
    });
    try { recorder.start(sliceMs); }
    catch {
        try { recorder.start(); } catch { }
        requestTimer = setInterval(() => {
            try { recorder.requestData?.(); } catch { }
        }, sliceMs);
    }


    let rafId = 0;

    const draw = () => {

        const { vW, vH, isPortraitStream, scaleCover } = layoutForLandscape();

        const w = cvs.width;

        const h = cvs.height;



        ctx.clearRect(0, 0, w, h);

        ctx.save();



        if (isPortraitStream) {

            ctx.translate(w, 0);

            ctx.rotate(Math.PI / 2);



            const drawW = h / scaleCover;

            const drawH = w / scaleCover;

            const x = -((drawW - vW) / 2);

            const y = -((drawH - vH) / 2);

            ctx.drawImage(videoEl, x, y, drawW, drawH);

            if (overlayEl && overlayEl.width > 0 && overlayEl.height > 0) {

                ctx.drawImage(overlayEl, x, y, drawW, drawH);

            }

        } else {

            const drawW = vW * scaleCover;

            const drawH = vH * scaleCover;

            const x = (w - drawW) / 2;

            const y = (h - drawH) / 2;

            ctx.drawImage(videoEl, x, y, drawW, drawH);

            if (overlayEl && overlayEl.width > 0 && overlayEl.height > 0) {

                ctx.drawImage(overlayEl, x, y, drawW, drawH);

            }

        }



        ctx.restore();

        rafId = requestAnimationFrame(draw);

    };



    if (videoEl.readyState >= 2) draw();

    else videoEl.addEventListener('loadedmetadata', draw, { once: true });



    const waitForInitChunk = () => {
        const headerBlob = initChunk || fallbackInitChunk;
        if (initNotified && headerBlob && headerBlob.size) {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            initReadyWaiters.add(resolve);
        });
    };

    const waitForIdle = (timeoutMs = 0) => {
        if (!activeCaptures.size) return Promise.resolve();
        return new Promise((resolve) => {
            const finish = () => {
                idleWaiters.delete(finish);
                resolve();
            };
            idleWaiters.add(finish);
            const waitMs = Number(timeoutMs);
            if (Number.isFinite(waitMs) && waitMs > 0) {
                setTimeout(() => {
                    if (idleWaiters.has(finish)) {
                        idleWaiters.delete(finish);
                        resolve();
                    }
                }, waitMs);
            }
        });
    };

    const captureClip = ({ preMs, totalMs, key } = {}) => {

        const total = Math.max(200, Number.isFinite(totalMs) ? totalMs : (window.__MICROCLIP_MS ?? 3000));

        const pre = Math.max(0, Math.min(Number.isFinite(preMs) ? preMs : (window.__MICROCLIP_PRE_MS ?? 360), total));



        const preChunks = [];
        const now = performance.now();
        const cutoff = now - pre;
        let earliestTs = null;

        for (let i = buffer.length - 1; i >= 0; i--) {
            const entry = buffer[i];
            if (!entry || !Number.isFinite(entry.ts)) continue;
            if (entry.ts < cutoff) break;
            preChunks.unshift(entry.blob);
            earliestTs = entry.ts;
        }

        const buildParts = (liveChunks = []) => {
            const parts = [];
            const headerBlob = initChunk || fallbackInitChunk;
            if (headerBlob) parts.push(headerBlob);
            for (const blob of preChunks) {
                if (!headerBlob || blob !== headerBlob) parts.push(blob);
            }
            for (const blob of liveChunks) {
                if (!headerBlob || blob !== headerBlob) parts.push(blob);
            }
            return parts;
        };

        const coveredPreMs = earliestTs != null ? Math.max(0, now - earliestTs) : 0;
        let remainingMs = Math.max(0, total - coveredPreMs);
        const ready = waitForInitChunk();

        if (remainingMs <= 0) {
            return ready.then(() => new Blob(buildParts(), { type: mimeType }));
        }

        return ready.then(() => new Promise((resolve, reject) => {
            const liveChunks = [];
            let currentCapture = null;

            const clearTimer = () => {
                if (currentCapture && currentCapture.finalizeTimer) {
                    clearTimeout(currentCapture.finalizeTimer);
                    currentCapture.finalizeTimer = null;
                }
                if (currentCapture && currentCapture.stopTimer) {
                    clearTimeout(currentCapture.stopTimer);
                    currentCapture.stopTimer = null;
                }
            };

            const logHead = (blob) => {
                if (window.DEBUG_MICROCLIP === true) {
                    try {
                        const headPromise = (async () => {
                            try {
                                const buf = await blob.slice(0, 4).arrayBuffer();
                                return Array.from(new Uint8Array(buf));
                            } catch { return null; }
                        })();
                        Promise.resolve(headPromise).then(headBytes => {
                            console.log('[landscapeRecorder] clip finalized', { headBytes });
                        });
                    } catch { }
                }
            };

            const resolveWithBlob = (blob) => {
                clearTimer();
                logHead(blob);
                resolve(blob);
            };

            const rejectWithError = (err) => {
                clearTimer();
                reject(err);
            };

            currentCapture = {
                preChunks,
                liveChunks,
                remainingMs,
                resolve: resolveWithBlob,
                reject: rejectWithError,
                buildParts,
                flushRequested: false,
                finalized: false,
                finalizeTimer: null,
                flushRequestTime: null,
                key: key != null ? String(key) : null
            };
            if (currentCapture.key) {
                captureByKey.set(String(currentCapture.key), currentCapture);
            }

            currentCapture.finalizeTimer = setTimeout(() => {
                if (currentCapture && !currentCapture.finalized) {
                    finalizeActiveCapture(currentCapture, 'deadline');
                }
            }, Math.max(700, remainingMs + 900));

            activeCaptures.add(currentCapture);
        }));

    };

    return {

        stream,

        captureClip,
        extendCapture: (key, extraMs = 0) => {
            const k = key != null ? String(key) : '';
            if (!k) return false;
            const capture = captureByKey.get(k);
            if (!capture || capture.finalized) return false;
            const addMs = Number(extraMs);
            if (!Number.isFinite(addMs) || addMs <= 0) return false;
            capture.remainingMs = Math.max(0, Number(capture.remainingMs) || 0) + addMs;
            capture.flushRequested = false;
            capture.flushRequestTime = null;
            if (capture.finalizeTimer) {
                clearTimeout(capture.finalizeTimer);
                capture.finalizeTimer = null;
            }
            capture.finalizeTimer = setTimeout(() => {
                if (capture && !capture.finalized) {
                    finalizeActiveCapture(capture, 'extend-deadline');
                }
            }, Math.max(700, capture.remainingMs + 900));
            return true;
        },
        waitForIdle,
        waitForInitChunk,
        getBufferCoverageMs: () => {
            const now = performance.now();
            const first = buffer[0];
            if (!first || !Number.isFinite(first.ts)) return 0;
            return Math.max(0, now - first.ts);
        },
        outputWidth: wantW,
        outputHeight: wantH,
        getSourceMetrics,

        stop: async () => {

            cancelAnimationFrame(rafId);

            clearRequestTimer();

            videoEl.removeEventListener?.('loadedmetadata', draw);

            try { recorder.stop(); } catch { }

            stream?.getTracks?.().forEach(track => track.stop());

            buffer.length = 0;

            const pending = Array.from(activeCaptures);
            pending.forEach((capture) => finalizeActiveCapture(capture, 'stop-call'));

        }

    };

}

function warmLandscapeRecorder() {
    if (window.__landscapeRecController) return;
    if (window.__landscapeRecStarting) return;
    if (window.USE_MICROCLIP === false || window.__CLIPS_AVAILABLE === false) return;
    if (typeof window.startLandscapeRecorder !== 'function') return;

    const videoEl = document.getElementById('videoPlayer');
    if (!videoEl) return;

    const start = async () => {
        if (window.__landscapeRecController || window.__landscapeRecStarting) return;
        try {
            const startPromise = window.startLandscapeRecorder(videoEl, { width: 1280, height: 720, fps: 30 });
            window.__landscapeRecStarting = startPromise;
            const comp = await startPromise;
            if (comp) {
                window.__landscapeRecController = comp;
                try { primeLandscapeRecorder(); } catch { }
            }
        } catch (err) {
            console.warn('[hud] landscape recorder warm failed', err);
        } finally {
            try { window.__landscapeRecStarting = null; } catch { }
        }
    };

    if (videoEl.readyState >= 2) {
        start();
    } else {
        videoEl.addEventListener('loadedmetadata', start, { once: true });
    }
}

function primeLandscapeRecorder() {
    if (window.__landscapeRecPrimed) return;
    if (window.USE_MICROCLIP === false || window.__CLIPS_AVAILABLE === false) return;
    const comp = window.__landscapeRecController;
    if (!comp) return;
    window.__landscapeRecPrimed = true;
    try { comp.waitForIdle?.(500); } catch { }
    if (window.DEBUG_MICROCLIP === true) {
        console.log('[landscapeRecorder] primed');
    }
}

async function ensureLandscapeRecorderReady(preMs) {
    if (window.USE_MICROCLIP === false || window.__CLIPS_AVAILABLE === false) return null;
    let comp = window.__landscapeRecController;
    if (!comp && window.__landscapeRecStarting) {
        try { await window.__landscapeRecStarting; } catch { }
        comp = window.__landscapeRecController;
    }
    if (!comp && typeof window.startLandscapeRecorder === 'function') {
        const videoEl = document.getElementById('videoPlayer');
        if (videoEl) {
            try {
                const startPromise = window.startLandscapeRecorder(videoEl, { width: 1280, height: 720, fps: 30 });
                window.__landscapeRecStarting = startPromise;
                comp = await startPromise;
                if (comp) {
                    window.__landscapeRecController = comp;
                    try { primeLandscapeRecorder(); } catch { }
                }
            } catch (err) {
                console.warn('[hud] landscape recorder warm failed', err);
            } finally {
                try { window.__landscapeRecStarting = null; } catch { }
            }
        }
    }
    if (!comp) return null;
    const initPromise = (typeof comp.waitForInitChunk === 'function') ? comp.waitForInitChunk() : null;
    if (initPromise && typeof initPromise.then === 'function') {
        await Promise.race([initPromise, new Promise((resolve) => setTimeout(resolve, 1500))]);
    }
    const preSetting = Number(preMs);
    if (!Number.isFinite(preSetting) || preSetting <= 0) return comp;
    const getCoverage = comp.getBufferCoverageMs;
    if (typeof getCoverage !== 'function') return comp;
    const startWait = performance.now();
    const maxWait = Math.max(900, Math.min(3500, preSetting + 900));
    while (performance.now() - startWait < maxWait) {
        if (getCoverage() >= Math.max(0, preSetting - 80)) break;
        await new Promise(r => setTimeout(r, 60));
    }
    return comp;
}
window.ensureLandscapeRecorderReady = ensureLandscapeRecorderReady;

async function waitForClipWarm(preMs) {
    if (window.USE_MICROCLIP === false || window.__CLIPS_AVAILABLE === false) return;
    try { await ensureLandscapeRecorderReady(preMs); } catch { }
}

// Reset to start overlay state
function finalizeToStartOverlay() {
    if (__newSessionFinalized !== false) return;
    __newSessionFinalized = true;
    clearNewSessionPromptTimers();
    setAwaitingNewSessionConfirm(false);
    try { hideSummaryProgress(); } catch { }
    try {
        const blk = document.getElementById('endBlackout');
        if (blk) blk.style.display = 'none';
    } catch { }
    try {
        const modal = document.getElementById('fullShotModal');
        if (modal) modal.style.display = 'none';
    } catch { }
    try {
        const coach = document.getElementById('coachNotes');
        if (coach) {
            coach.style.display = 'none';
            coach.dataset.dismissed = 'false';
            if (coach.dataset.baseZ) coach.style.zIndex = coach.dataset.baseZ;
        }
    } catch { }
    try { setSessionStatus?.(null); } catch { }
    try { updateSessionHUD?.({ taken: 0, made: 0, accuracy: 0, elapsedSec: 0 }); } catch { }
    try { window.visaionSession?.reset?.(); } catch { }
    try { clearInterval(window.__coachPoseInterval); window.__coachPoseInterval = null; } catch { }
    try { cancelAnimationFrame(window.__coachPaintRaf); window.__coachPaintRaf = null; } catch { }
    showStartSessionOverlay();
    __newSessionQuestionAsked = false;
    try { window.__SESSION_REVIEW_SPOKEN = false; } catch { }
    try { window.__NEW_SESSION_PROMPTED = false; } catch { }
}

function scheduleNewSessionResetWatcher() {
    if (__newSessionResetWatcher) return;
    __newSessionResetWatcher = setInterval(() => {
        if (!__newSessionQuestionAsked) return;
        const summaryVisible = isElementVisible(document.getElementById('fullShotModal'));
        const coachVisible = isElementVisible(document.getElementById('coachNotes'));
        if (!summaryVisible && !coachVisible) {
            finalizeToStartOverlay();
        }
    }, 650);
}

function ensureCoachFeedbackVisible() {
    try {
        const coach = document.getElementById('coachNotes');
        if (!coach) return;
        coach.style.display = 'block';
        coach.dataset.dismissed = 'false';
        if (!coach.dataset.baseZ) coach.dataset.baseZ = coach.style.zIndex || '10050';
        coach.style.zIndex = '10070';
        const modal = document.getElementById('fullShotModal');
        if (modal && isElementVisible(modal)) {
            try { positionShotSummaryModal(modal); } catch { }
        }
    } catch { }
}

async function speakNewSessionInvite(line) {
    if (!line) return;
    if (typeof window.visaionSpeak === 'function') {
        try {
            await window.visaionSpeak(line);
            return;
        } catch { }
    }
}

function requestNewSessionPrompt(options = {}) {
    if (__newSessionPromptTimer || __newSessionQuestionAsked) return;
    const baseDelay = Number(options.delayMs ?? window.NEW_SESSION_PROMPT_DELAY_MS ?? NEW_SESSION_PROMPT_DELAY_MS) || 0;
    const minDelay = Number(window.NEW_SESSION_PROMPT_MIN_MS ?? 6000);
    const delayMs = Math.max(minDelay, baseDelay);
    __newSessionFinalized = false;
    __newSessionQuestionAsked = false;

    __newSessionPromptTimer = setTimeout(async () => {
        __newSessionPromptTimer = null;
        const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, Math.max(0, ms || 0)));
        try {
            if (typeof window.waitForCoachSpeech === 'function') {
                await window.waitForCoachSpeech();
            }
        } catch { }
        try {
            const lastEnded = Number(window.__COACH_LAST_SPEECH_ENDED_AT || 0);
            const minGap = Number(window.NEW_SESSION_PROMPT_MIN_MS ?? 6000);
            if (minGap > 0 && lastEnded > 0) {
                const remaining = minGap - (Date.now() - lastEnded);
                if (remaining > 0) await sleep(remaining);
            }
        } catch { }
        if (__newSessionFinalized || __newSessionQuestionAsked) return;
        try { window.__NEW_SESSION_PROMPTED = true; } catch { }

        let modal = null;
        try {
            modal = renderFullShotTable?.();
        } catch { }
        if (!modal) {
            try { modal = document.getElementById('fullShotModal'); }
            catch { modal = null; }
        }
        if (modal) {
            try { modal.dataset.pendingNewSession = ''; } catch { }
            try { modal.style.display = 'block'; } catch { }
            try { modal.style.zIndex = '10060'; } catch { }
        }

        ensureCoachFeedbackVisible();
        scheduleNewSessionResetWatcher();

        const name = getPlayerDisplayNameForPrompt();
        const line = `${name}, do you want to start a new session?`;
        try {
            await speakNewSessionInvite(line);
        } finally {
            __newSessionQuestionAsked = true;
            setAwaitingNewSessionConfirm(true);
            try { window.__startCoachVoiceRecognition?.(); } catch { }
            try { window.dispatchEvent(new CustomEvent('coach:voice-rec-start', { detail: { via: 'new-session-prompt' } })); } catch { }
        }
    }, delayMs);
}

if (typeof window.requestNewSessionPrompt !== 'function') {
    window.requestNewSessionPrompt = requestNewSessionPrompt;
}

function handleHudStartSession(event) {
    clearNewSessionPromptTimers();
    setAwaitingNewSessionConfirm(false);
    __newSessionQuestionAsked = false;
    __newSessionFinalized = false;
    try { hideSummaryProgress(); } catch { }
    try { window.__NEW_SESSION_PROMPTED = false; } catch { }
    try { window.__SESSION_REVIEW_SPOKEN = false; } catch { }
    try { window.__SESSION_REVIEW_LAST = null; } catch { }
    try { window.__SESSION_REVIEW_DONE = false; } catch { }
    try { window.__SESSION_REVIEW_PROMISE = null; } catch { }
    try { window.__releaseEvaluating = false; } catch { }
    try { window.__releaseReject = null; } catch { }
    try { window.__releaseEventSent = false; } catch { }

    hideStartSessionOverlay();
    try {
        const blk = document.getElementById('endBlackout');
        if (blk) blk.style.display = 'none';
    } catch { }
    try {
        const modal = document.getElementById('fullShotModal');
        if (modal) modal.style.display = 'none';
    } catch { }
    try {
        const coach = document.getElementById('coachNotes');
        if (coach) {
            coach.style.display = 'none';
            coach.dataset.dismissed = 'false';
            if (coach.dataset.baseZ) coach.style.zIndex = coach.dataset.baseZ;
        }
    } catch { }

    try { window.__shotList = []; } catch { }
    try {
        if (window.__finalizedShotIds instanceof Set) window.__finalizedShotIds.clear();
        else window.__finalizedShotIds = new Set();
    } catch { }
    try {
        window.__shots = new Map();
        window.__SHOT_ID = 0;
        window.__sessionTotals = { attempts: 0, made: 0 };
    } catch { }
    try { updateSessionHUD?.({ taken: 0, made: 0, accuracy: 0, elapsedSec: 0 }); } catch { }

    try { window.__sessionStart = Date.now(); } catch { }
    try { window.__SESSION_ACTIVE = true; } catch { }
    try { window.__SESSION_SHOT_COUNT = 0; } catch { }
    try { window.__armCountdownActive = false; } catch { }
    try { warmLandscapeRecorder(); } catch { }

    try { setSessionStatus?.('SESSION IN PROGRESS…'); } catch { }
    try { hidePromptMessage(); } catch { }
    try { window.visaionVoice?.on?.(); } catch { }

    const terms = getSessionTerminology();
    const countdownSec = Number.isFinite(terms.countdownSeconds) ? terms.countdownSeconds : 5;
    const readyPrompt = terms.readyPrompt || 'Shoot when ready.';
    const targetRequired = terms.requiresTargetSelection;
    try {
        window.__sessionTerminology = terms;
        window.__sessionCountdownSecs = countdownSec;
        window.__sessionReadyPrompt = readyPrompt;
    } catch { }

    const hoopBox = targetRequired ? (() => {
        try { return getLockedHoopBox?.(); } catch { return null; }
    })() : null;
    const hoopWasLocked = targetRequired ? (() => {
        try { return !!hoopBox || window.__hoopConfirmed === true || !!window.__lockedHoopBox; }
        catch { return !!hoopBox; }
    })() : false;

    try { window.__shotTrackingArmed = false; } catch { }
    try { window.__armCountdownActive = false; } catch { }
    try { window.__RELEASE_LOCK_UNTIL = 0; window.__REL_LAST_FIRE_MS = 0; window.__releaseLatchUntil = 0; window.__LAST_FIRED_FRAME = null; } catch { }
    try { window.__SAMPLER_BLOCK_UNTIL = 0; } catch { }

    if (!targetRequired) {
        try { window.__hoopConfirmed = true; } catch { }
        try { window.resumeHoopTracking?.(); } catch { }
        setTimeout(() => {
            (async () => {
                try {
                    if (String(terms.attemptLabel || '').toLowerCase() === 'swing') {
                        await waitForClipWarm(window.__MICROCLIP_PRE_MS);
                    }
                } catch { }
                try {
                    window.startShotTrackingCountdown?.(countdownSec, readyPrompt);
                } catch (err) {
                    console.warn('[hud] countdown failed', err);
                }
                const delayMs = Math.max(0, countdownSec * 1000 + 60);
                setTimeout(() => {
                    try { window.scheduleArmWhenReady?.(0); } catch { }
                }, delayMs);
            })();
        }, 100);
    } else if (hoopWasLocked) {
        try { window.__hoopConfirmed = true; } catch { }
        try { window.resumeHoopTracking?.(); } catch { }
        setTimeout(() => {
            try {
                window.dispatchEvent(new CustomEvent('hoop:locked', { detail: { via: 'session-restart' } }));
            } catch { }
            try { window.scheduleArmWhenReady?.(0); } catch { }
        }, 0);
    } else {
        try { window.__hoopConfirmed = false; } catch { }
        if (window.__pickingHoop === true || window.__hoopPromptSpeakTimer || window.__hoopPromptRepeatTimer) {
            return;
        }
        setTimeout(() => {
            try { enableHoopPickOnce?.(); } catch { }
        }, 120);
    }
}

window.addEventListener('hud:start-session', handleHudStartSession);

/* -------------------- Center prompt + countdown -------------------- */
function showCenterPrompt(msg) {
    let el = document.getElementById('overlayPrompt');
    if (!el) {
        el = document.createElement('div');
        el.id = 'overlayPrompt';
        Object.assign(el.style, {
            position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)',
            background: 'rgba(0,0,0,0.72)', color: '#fff', padding: '20px 28px',
            borderRadius: '12px', font: '700 32px/1.15 system-ui, -apple-system, Segoe UI, Arial', zIndex: 10020,
            textShadow: '0 2px 8px rgba(0,0,0,0.45)', pointerEvents: 'none', display: 'none'
        });
        ensureHudRoot().appendChild(el);
    }
    el.textContent = msg;
    el.style.display = 'block';
    return el;
}
window.showCenterPrompt = showCenterPrompt;

function startShotTrackingCountdown(sec = 5, readyText, _options) {
    let promptOverride = readyText;
    if (readyText && typeof readyText === 'object' && !Array.isArray(readyText)) {
        promptOverride = undefined;
    }
    const countdown = Number.isFinite(sec) ? sec : (Number(window.__sessionCountdownSecs) || getCountdownSeconds());
    const prompt = promptOverride || window.__sessionReadyPrompt || getReadyPrompt();

    if (window.__armCountdownActive) return;
    window.__armCountdownActive = true;

    try { window.__shotTrackingArmed = false; } catch { }
    try { window.dispatchEvent(new CustomEvent('hud:arm-countdown', { detail: { sec: countdown } })); } catch { }

    const root = ensureHudRoot();
    let box = document.getElementById('countdownOverlay');
    if (!box) {
        box = document.createElement('div');
        box.id = 'countdownOverlay';
        Object.assign(box.style, {
            position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)',
            background: 'rgba(0,0,0,0.45)', color: '#fff', padding: '24px 32px', borderRadius: '16px',
            font: '900 120px/1 system-ui, -apple-system, Segoe UI, Arial',
            textShadow: '0 6px 18px rgba(0,0,0,.55)', zIndex: 10040, pointerEvents: 'none', display: 'none'
        });
        root.appendChild(box);
    }
    const showNum = (t) => { box.style.display = 'block'; box.textContent = String(t); };
    const showGo = () => { box.style.display = 'block'; box.textContent = 'GO'; };
    const hide = () => { box.style.display = 'none'; };

    (async () => {
        try {
            for (let i = countdown; i >= 1; i--) { showNum(i); await new Promise(r => setTimeout(r, 1000)); }
            showGo(); setTimeout(hide, 700);
            window.__shotTrackingArmed = true;
            try { window.dispatchEvent(new CustomEvent('hud:armed')); } catch { }
            try {
                if (typeof window.visaionSpeak === 'function') {
                    await window.visaionSpeak(prompt);
                } else {
                    console.warn('[countdown] visaionSpeak unavailable for cue');
                }
            } catch (err) {
                console.warn('[countdown] cue failed', err);
            }
            try { window.__releaseEventSent = false; } catch { }
        } finally {
            window.__armCountdownActive = false;
        }
    })();
}
if (typeof window.startShotTrackingCountdown !== 'function') window.startShotTrackingCountdown = startShotTrackingCountdown;

/* ----------------------- Callable finalizer only ----------------------- */
// This DOES NOT trigger automatically. Call from session_manager.js when ending.
async function autoEndSessionAndSummarize() {
    // Dim background a bit for readability
    try {
        const root = ensureHudRoot?.() || document.body;
        let blk = document.getElementById('endBlackout');
        if (!blk) {
            blk = document.createElement('div'); blk.id = 'endBlackout';
            Object.assign(blk.style, { position: 'absolute', inset: '0', background: '#000', opacity: '0.65', zIndex: 10040, pointerEvents: 'none' });
            root.appendChild(blk);
        } else {
            blk.style.display = 'block'; blk.style.opacity = '0.65'; blk.style.zIndex = '10040'; blk.style.pointerEvents = 'none';
        }
    } catch { }

    try {
        const modal = renderFullShotTable?.({ show: false });
        if (modal) {
            modal.dataset.pendingNewSession = '1';
            modal.style.display = 'none';
        }
    } catch { }
    try { showSummaryProgress(); } catch { }
    try { window.dispatchEvent(new CustomEvent('hud:end-session')); } catch { }

    setTimeout(() => {
        try {
            if (!window.__NEW_SESSION_PROMPTED) {
                const postSummaryDelay = Number(window.NEW_SESSION_PROMPT_POST_SUMMARY_MS ?? 8000);
                requestNewSessionPrompt?.({ delayMs: postSummaryDelay });
            }
        } catch { }
    }, Math.max(0, Number(window.NEW_SESSION_PROMPT_FALLBACK_MS || 26000)));
}
if (typeof window.autoEndSessionAndSummarize !== 'function') window.autoEndSessionAndSummarize = autoEndSessionAndSummarize;

/* --------------------------- Video HUD init --------------------------- */
export function initHUDForVideo(videoEl) {
    window.__videoEl = videoEl;
    ensureHudRoot();

    const anchor = document.querySelector('.session-container') || document.body;
    if (!window.__hudMo) {
        window.__hudMo = new MutationObserver(() => ensureHudRoot());
        window.__hudMo.observe(anchor, { childList: true, subtree: true });
    }

    const boot = () => {
        ensureHudRoot();
        mountSessionHUD();
        setSessionStatus('SESSION IN PROGRESS???');
        setOverlayInteractive(true);
        if (requiresTargetSelection()) {
            try { enableHoopPickOnce?.(); } catch { }
        }
    };
    if (videoEl?.readyState >= 2) boot();

    videoEl?.addEventListener('play', ensureHudRoot);
    videoEl?.addEventListener('pause', ensureHudRoot);

    // Elapsed time ticker
    if (window.__hudTimeTimer) clearInterval(window.__hudTimeTimer);
    window.__hudTimeTimer = setInterval(() => {
        try {
            const start = window.__sessionStart;
            if (!start) return;
            const list = (window.__shotList || window.shotLog || []);
            const taken = Array.isArray(list) ? list.length : 0;
            const elapsedSec = Math.floor((Date.now() - start) / 1000);
            updateSessionHUD({ taken, elapsedSec });
        } catch { }
    }, 1000);

    window.addEventListener('hud:end-session', () => {
        try { if (window.__hudTimeTimer) { clearInterval(window.__hudTimeTimer); window.__hudTimeTimer = null; } } catch { }
        try { window.__armCountdownActive = false; } catch { }
    });
    window.addEventListener('session:reset', () => {
        try { window.__armCountdownActive = false; } catch { }
    });
}
window.initHUDForVideo = initHUDForVideo;

function kickoffCountdownArmFromHoop() {
    if (!requiresTargetSelection()) return;
    const sec = Number(window.__sessionCountdownSecs || getCountdownSeconds());
    const prompt = window.__sessionReadyPrompt || getReadyPrompt();
    try {
        window.startShotTrackingCountdown?.(sec, prompt);
    } catch (err) {
        console.warn('[hud] countdown failed', err);
    }
    const delayMs = Math.max(0, (Number.isFinite(sec) ? sec : 5) * 1000 + 60);
    setTimeout(() => {
        try { window.scheduleArmWhenReady?.(0); } catch { }
    }, delayMs);
}

/* ------------------------- Hoop lock listeners ------------------------- */
window.addEventListener('hoop:locked', () => {
    if (!requiresTargetSelection()) return;
    window.__hoopConfirmed = true;
    try { window.__SESSION_ACTIVE = true; } catch { }
    hidePromptMessage();
    try { const v = document.getElementById('videoPlayer') || document.querySelector('video'); if (v) v.playbackRate = 1; } catch { }
    // If not armed and no countdown active, start countdown
    try {
        if (window.__shotTrackingArmed !== true && !window.__armCountdownActive) {
            window.__shotTrackingArmed = false;
            kickoffCountdownArmFromHoop();
        }
    } catch { }
});

/* ------------------- Update UI on every shot summary ------------------- */
window.addEventListener('shot:summary', () => {
    try {
        const list = window.__shotList || window.shotLog || [];
        const taken = Array.isArray(list) ? list.length : 0;
        const start = (window.__sessionStart ||= Date.now());
        const elapsedSec = Math.floor((Date.now() - start) / 1000);
        updateSessionHUD({ taken, elapsedSec });
    } catch { }
});

