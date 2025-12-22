import * as hoopTracker from '/static/arc_mm/hoop_tracker.js';
void hoopTracker;

export * from '/static/arc_mm/hoop_tracker.js';

try {
    const mgr = window?.visaionProjectManager;
    if (mgr?.registerModule) {
        mgr.registerModule('basketball/hoop-tracker', {
            project: 'basketball',
            init() {
                // underlying module is imported at load time; no extra boot needed.
            }
        });
    }
} catch { }
