/* Golf swing metrics module
 * Listens for shot releases/summaries while the golf project is active
 * and derives pose-based metrics needed by the golf coaching responses.
 */

(function installGolfSwingMetrics() {
    const MODULE_ID = 'golf/swing-metrics';
    const PROJECT_SLUG = 'golf';
    const POST_CAPTURE_DELAY_MS = 650;
    const HISTORY_LIMIT = 90;
    const mgr = window.viasionProjectManager;

    if (!mgr?.registerModule) {
        console.warn('[golf-metrics] project manager not present; module skipped');
        return;
    }

    const frameStore = new Map();

    const IDX = {
        LEFT_SHOULDER: 11,
        RIGHT_SHOULDER: 12,
        LEFT_HIP: 23,
        RIGHT_HIP: 24,
        LEFT_WRIST: 15,
        RIGHT_WRIST: 16,
        LEFT_KNEE: 25,
        RIGHT_KNEE: 26,
        LEFT_ANKLE: 27,
        RIGHT_ANKLE: 28,
    };

    function isGolfActive() {
        try {
            const active = mgr.getActiveProject?.();
            const slug = active?.slug || window.__viasion_ACTIVE_PROJECT?.slug || window.__viasion_ACTIVE_PROJECT;
            return String(slug || '').toLowerCase() === PROJECT_SLUG;
        } catch {
            return false;
        }
    }

    function toNumber(val, fallback = null) {
        const num = Number(val);
        return Number.isFinite(num) ? num : fallback;
    }

    function clonePoint(pt) {
        if (!pt || !Number.isFinite(pt.x) || !Number.isFinite(pt.y)) return null;
        return {
            x: Number(pt.x),
            y: Number(pt.y),
            z: Number.isFinite(pt.z) ? Number(pt.z) : null,
            visibility: Number.isFinite(pt.visibility)
                ? Number(pt.visibility)
                : (Number.isFinite(pt.score) ? Number(pt.score) : null)
        };
    }

    function cloneFrame(entry) {
        if (!entry || typeof entry !== 'object') return null;
        const keypoints = Array.isArray(entry.keypoints)
            ? entry.keypoints.map(clonePoint)
            : null;
        return {
            frame: Number.isFinite(entry.frame) ? Number(entry.frame) : null,
            ts: Number.isFinite(entry.ts) ? Number(entry.ts)
                : Number.isFinite(entry.timestamp) ? Number(entry.timestamp)
                    : Number.isFinite(entry.tMs) ? Number(entry.tMs)
                        : Date.now(),
            keypoints
        };
    }

    function captureHistory(limit = HISTORY_LIMIT) {
        const history = Array.isArray(window.playerState?.frameHistory)
            ? window.playerState.frameHistory
            : [];
        return history.slice(-limit).map(cloneFrame).filter(Boolean);
    }

    function mergeTimeline(primary = [], extra = []) {
        const merged = [];
        const seen = new Set();
        for (const frame of [...primary, ...extra]) {
            if (!frame) continue;
            const key = `${frame.frame ?? 'nf'}|${Math.round(frame.ts || 0)}`;
            if (seen.has(key)) continue;
            seen.add(key);
            merged.push(frame);
        }
        merged.sort((a, b) => {
            const fa = Number.isFinite(a.frame) ? a.frame : Infinity;
            const fb = Number.isFinite(b.frame) ? b.frame : Infinity;
            if (fa === fb) return (a.ts || 0) - (b.ts || 0);
            return fa - fb;
        });
        return merged;
    }

    function avgPoint(a, b) {
        if (a && b) {
            return {
                x: (a.x + b.x) / 2,
                y: (a.y + b.y) / 2,
                visibility: Math.min(
                    Number.isFinite(a.visibility) ? a.visibility : 1,
                    Number.isFinite(b.visibility) ? b.visibility : 1
                )
            };
        }
        return a || b || null;
    }

    function pointVisible(pt, min = 0.15) {
        if (!pt) return false;
        const vis = Number.isFinite(pt.visibility) ? pt.visibility
            : Number.isFinite(pt.score) ? pt.score
                : Number.isFinite(pt.presence) ? pt.presence
                    : 1;
        return vis >= min;
    }

    function angleBetween(a, b) {
        if (!a || !b) return null;
        const dot = a.x * b.x + a.y * b.y;
        const magA = Math.hypot(a.x, a.y);
        const magB = Math.hypot(b.x, b.y);
        if (!magA || !magB) return null;
        const cos = Math.min(1, Math.max(-1, dot / (magA * magB)));
        return Math.acos(cos) * 180 / Math.PI;
    }

    function pickSwingSide(kp) {
        if (!Array.isArray(kp)) return null;
        const sides = [
            {
                side: 'right',
                wrist: kp[IDX.RIGHT_WRIST],
                shoulder: kp[IDX.RIGHT_SHOULDER],
                hip: kp[IDX.RIGHT_HIP],
            },
            {
                side: 'left',
                wrist: kp[IDX.LEFT_WRIST],
                shoulder: kp[IDX.LEFT_SHOULDER],
                hip: kp[IDX.LEFT_HIP],
            }
        ];
        let best = null;
        let bestScore = -Infinity;
        for (const candidate of sides) {
            if (!candidate.wrist || !candidate.shoulder || !candidate.hip) continue;
            const visSum = [candidate.wrist, candidate.shoulder, candidate.hip]
                .map(p => Number.isFinite(p?.visibility) ? p.visibility : 0.5)
                .reduce((s, v) => s + v, 0);
            if (visSum > bestScore) {
                best = candidate;
                bestScore = visSum;
            }
        }
        return best;
    }

    function planeDeviation(entry) {
        if (!entry?.keypoints) return null;
        const side = pickSwingSide(entry.keypoints);
        if (!side) return null;
        if (!pointVisible(side.wrist) || !pointVisible(side.shoulder, 0.1)) return null;
        const swingVec = {
            x: side.wrist.x - side.shoulder.x,
            y: side.wrist.y - side.shoulder.y
        };
        const torsoVec = side.hip && pointVisible(side.hip, 0.2)
            ? { x: side.hip.x - side.shoulder.x, y: side.hip.y - side.shoulder.y }
            : { x: 0, y: 1 }; // fallback vertical reference
        const diff = angleBetween(torsoVec, swingVec);
        if (!Number.isFinite(diff)) return null;
        const target = 45; // ideal neutral plane reference
        return Number(Math.abs(diff - target).toFixed(1));
    }

    function spineAngle(entry) {
        if (!entry?.keypoints) return null;
        const kp = entry.keypoints;
        const hip = avgPoint(kp[IDX.LEFT_HIP], kp[IDX.RIGHT_HIP]);
        const shoulder = avgPoint(kp[IDX.LEFT_SHOULDER], kp[IDX.RIGHT_SHOULDER]);
        if (!hip || !shoulder || !pointVisible(hip, 0.1) || !pointVisible(shoulder, 0.1)) return null;
        const torsoVec = { x: hip.x - shoulder.x, y: hip.y - shoulder.y };
        const vertical = { x: 0, y: 1 };
        const angle = angleBetween(torsoVec, vertical);
        return Number.isFinite(angle) ? Number(Math.abs(angle).toFixed(1)) : null;
    }

    function hipCenter(entry) {
        if (!entry?.keypoints) return null;
        return avgPoint(entry.keypoints[IDX.LEFT_HIP], entry.keypoints[IDX.RIGHT_HIP]);
    }

    function hipWidth(entry) {
        if (!entry?.keypoints) return null;
        const lh = entry.keypoints[IDX.LEFT_HIP];
        const rh = entry.keypoints[IDX.RIGHT_HIP];
        if (!pointVisible(lh, 0.1) || !pointVisible(rh, 0.1)) return null;
        return Math.hypot(lh.x - rh.x, lh.y - rh.y);
    }

    function finishBalance(timeline, releaseIdx) {
        const releaseEntry = timeline[releaseIdx];
        if (!releaseEntry) return null;
        const finishEntry = timeline[Math.min(timeline.length - 1, releaseIdx + 12)] || releaseEntry;
        const startHip = hipCenter(releaseEntry);
        const finishHip = hipCenter(finishEntry);
        if (!startHip || !finishHip) return null;
        const deltaX = Math.abs(finishHip.x - startHip.x);
        const width = hipWidth(releaseEntry) || 1;
        const cm = (deltaX / width) * 40; // assume ~40cm hip width baseline
        return Number(cm.toFixed(1));
    }

    function minWristY(entry) {
        if (!entry?.keypoints) return null;
        const wrists = [
            entry.keypoints[IDX.LEFT_WRIST],
            entry.keypoints[IDX.RIGHT_WRIST]
        ].filter(pt => pointVisible(pt, 0.15));
        if (!wrists.length) return null;
        return Math.min(...wrists.map(pt => pt.y));
    }

    function avgHipY(entry) {
        if (!entry?.keypoints) return null;
        const hips = [
            entry.keypoints[IDX.LEFT_HIP],
            entry.keypoints[IDX.RIGHT_HIP]
        ].filter(pt => pointVisible(pt, 0.15));
        if (!hips.length) return null;
        return hips.reduce((sum, pt) => sum + pt.y, 0) / hips.length;
    }

    function deriveSwingPhases(timeline, releaseIdx) {
        const wristSeries = timeline.map(minWristY);
        const hipSeries = timeline.map(avgHipY);
        const releaseWrist = wristSeries[releaseIdx] ?? null;

        let peakIdx = -1;
        let minVal = Infinity;
        for (let i = 0; i <= releaseIdx; i++) {
            const v = wristSeries[i];
            if (!Number.isFinite(v)) continue;
            if (v < minVal) {
                minVal = v;
                peakIdx = i;
            }
        }
        if (peakIdx < 0) return null;

        let startIdx = null;
        for (let i = peakIdx; i >= 0; i--) {
            const wrist = wristSeries[i];
            const hip = hipSeries[i];
            if (!Number.isFinite(wrist) || !Number.isFinite(hip)) continue;
            if (wrist >= hip - 8) {
                startIdx = i;
                break;
            }
        }
        if (startIdx == null) startIdx = Math.max(0, peakIdx - 12);

        let downIdx = null;
        if (Number.isFinite(releaseWrist)) {
            for (let i = peakIdx + 1; i <= releaseIdx; i++) {
                const wrist = wristSeries[i];
                if (!Number.isFinite(wrist)) continue;
                if (Math.abs(wrist - releaseWrist) <= 12) {
                    downIdx = i;
                    break;
                }
            }
        }
        if (downIdx == null) downIdx = Math.max(peakIdx + 1, releaseIdx);

        const backswingFrames = Math.max(1, peakIdx - startIdx + 1);
        const downswingFrames = Math.max(1, releaseIdx - downIdx + 1);
        const tempoRatio = Number((backswingFrames / downswingFrames).toFixed(2));

        return {
            startIdx,
            peakIdx,
            downIdx,
            tempoRatio
        };
    }

    function computeMetrics(record, summary) {
        const history = mergeTimeline(record?.history, record?.post);
        if (!history.length) return null;

        let releaseFrame = Number.isFinite(record?.releaseFrame)
            ? Number(record.releaseFrame)
            : null;

        if (!Number.isFinite(releaseFrame) && Number.isFinite(summary?.frame)) {
            releaseFrame = Number(summary.frame);
        }

        const releaseIdx = (() => {
            if (Number.isFinite(releaseFrame)) {
                const idx = history.findIndex(f => f.frame === releaseFrame);
                if (idx >= 0) return idx;
            }
            return history.length - 1;
        })();

        const windowStart = Math.max(0, releaseIdx - 70);
        const windowEnd = Math.min(history.length, releaseIdx + 15);
        const timeline = history.slice(windowStart, windowEnd);
        const relIdx = Math.min(timeline.length - 1, releaseIdx - windowStart);
        if (relIdx < 0) return null;

        const phases = deriveSwingPhases(timeline, relIdx);
        if (!phases) return null;

        const metrics = {};
        metrics.tempoRatio = phases.tempoRatio;

        const backPlane = planeDeviation(timeline[phases.peakIdx]);
        if (backPlane != null) metrics.backswingPlaneDeg = backPlane;

        const downFrame = timeline[Math.min(timeline.length - 1, phases.downIdx)];
        const downPlane = planeDeviation(downFrame) ?? planeDeviation(timeline[relIdx]);
        if (downPlane != null) metrics.downswingPlaneDeg = downPlane;

        const impactSpine = spineAngle(timeline[relIdx]);
        if (impactSpine != null) metrics.impactSpineAngle = impactSpine;

        const balance = finishBalance(timeline, relIdx);
        if (balance != null) metrics.finishBalanceCm = balance;

        if (window.SWING_DEBUG === true) {
            console.debug('[golf-metrics]', {
                shotId: summary?.shotId ?? null,
                phases,
                impactSpine,
                metrics
            });
        }

        return metrics;
    }

    function applyMetrics(summary, metrics) {
        if (!summary || !metrics) return;
        summary.metrics = { ...(summary.metrics || {}), ...metrics };
        if (summary.poseSnapshot && typeof summary.poseSnapshot === 'object') {
            Object.assign(summary.poseSnapshot, metrics);
        }
        try {
            const list = window.__shotList;
            if (Array.isArray(list)) {
                const sid = Number(summary.shotId ?? summary.id);
                const match = list.find((row) => Number(row?.shotId ?? row?.id) === sid);
                if (match) {
                    match.metrics = { ...(match.metrics || {}), ...metrics };
                    if (match.poseSnapshot) Object.assign(match.poseSnapshot, metrics);
                }
            }
        } catch { }
        try {
            console.debug('[golf-metrics] applied', {
                shotId: summary?.shotId ?? summary?.id ?? null,
                metrics
            });
        } catch { }
    }

    function handleRelease(event) {
        if (!isGolfActive()) return;
        const detail = event?.detail || {};
        const shotId = Number(detail.shotId ?? detail.id);
        if (!Number.isFinite(shotId)) return;
        const record = {
            releaseFrame: Number.isFinite(detail.frame) ? Number(detail.frame) : null,
            capturedAt: Date.now(),
            history: captureHistory(),
            post: []
        };
        frameStore.set(shotId, record);
        setTimeout(() => {
            const current = frameStore.get(shotId);
            if (!current) return;
            current.post = captureHistory();
        }, POST_CAPTURE_DELAY_MS);
    }

    function handleSummary(event) {
        if (!isGolfActive()) return;
        const summary = event?.detail || {};
        const shotId = Number(summary.shotId ?? summary.id);
        if (!Number.isFinite(shotId)) return;
        const record = frameStore.get(shotId) || {
            releaseFrame: Number(summary.frame),
            history: captureHistory(),
            post: []
        };
        let metrics = computeMetrics(record, summary);
        if (!metrics && summary.poseSnapshot) {
            const pose = summary.poseSnapshot;
            const fallback = {};
            if (Number.isFinite(pose.shoulderToWristAngle)) {
                const shoulderToVertical = Math.max(0, 90 - pose.shoulderToWristAngle);
                fallback.downswingPlaneDeg = Number(shoulderToVertical.toFixed(1));
            }
            if (Number.isFinite(pose.torsoLeanAngle)) {
                fallback.impactSpineAngle = Number(pose.torsoLeanAngle);
            }
            if (Number.isFinite(pose.frameOffsetX)) {
                fallback.finishBalanceCm = Number(Math.abs(pose.frameOffsetX / 5).toFixed(1));
            }
            if (Number.isFinite(pose.followThroughHoldFrames)) {
                fallback.followThroughHoldFrames = Number(pose.followThroughHoldFrames);
            }
            if (Object.keys(fallback).length) metrics = fallback;
        }
        applyMetrics(summary, metrics);
        frameStore.delete(shotId);
    }

    function init() {
        window.addEventListener('shot:release', handleRelease, { passive: true });
        window.addEventListener('shot:summary', handleSummary, { passive: true });
        if (window.SWING_DEBUG === true) {
            console.log('[golf-metrics] module initialised');
        }
    }

    mgr.registerModule(MODULE_ID, {
        project: PROJECT_SLUG,
        init
    });
})();
