(function () {
    const manifestEndpoint = '/api/projects';
    const state = {
        manifest: null,
        activeProjectSlug: null,
        listeners: new Set(),
        moduleRegistry: new Map()
    };

    const defaultClipConfig = {
        totalMs: Number(window.__MICROCLIP_MS) || 3000,
        preMs: Number(window.__MICROCLIP_PRE_MS) || 360
    };

    const fallbackManifest = {
        defaultProject: 'basketball',
        projects: {
            basketball: {
                name: 'Basketball Shot Coaching',
                description: 'Fallback manifest if external config fails to load.',
                datasets: [
                    {
                        slug: 'basketball_pose',
                        label: 'Basketball Pose / Shot Dataset',
                        type: 'pose',
                        root: 'datasets/visaion_seg',
                        frameCacheRoot: 'frame_cache',
                        framesRoot: 'frames',
                        labelTrainRoot: 'datasets/visaion_seg/labels/train',
                        imagesTrainRoot: 'datasets/visaion_seg/images/train',
                        labels: ['basketball', 'hoop', 'net', 'player']
                    }
                ],
                modules: {
                    assistant: 'basketball/coach-assistant',
                    trackers: [
                        'basketball/ball-tracker',
                        'basketball/hoop-tracker',
                        'basketball/release-gate'
                    ],
                    metrics: ['basketball/shot-logger']
                }
            }
        }
    };

    async function loadManifest() {
        if (state.manifest) return state.manifest;
        const fetchManifest = async (url) => {
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(`Manifest HTTP ${res.status}`);
            return res.json();
        };
        try {
            state.manifest = await fetchManifest(manifestEndpoint);
        } catch (err) {
            try {
                state.manifest = await fetchManifest('/static/config/projects.json');
            } catch (err2) {
                console.warn('[ProjectManager] failed to load manifest; using fallback', err2);
                state.manifest = fallbackManifest;
            }
        }
        if (!state.activeProjectSlug) {
            const def = state.manifest.defaultProject || Object.keys(state.manifest.projects || {})[0];
            state.activeProjectSlug = def || null;
        }
        notifyListeners();
        tryActivateModules();
        return state.manifest;
    }

    function notifyListeners() {
        const project = getActiveProject();
        applyProjectGlobals(project);
        try {
            window.__visaion_ACTIVE_PROJECT = project || null;
        } catch {
            // ignore assignment failures
        }
        state.listeners.forEach((cb) => {
            try { cb(project); } catch (err) { console.error('[ProjectManager] listener error', err); }
        });
    }

    function tryActivateModules() {
        const project = getActiveProject();
        if (!project) return;
        state.moduleRegistry.forEach((entry, id) => {
            if (entry.initialized) return;
            if (entry.project && entry.project !== project.slug) return;
            if (typeof entry.init === 'function') {
                try {
                    entry.init({ project, manager });
                    entry.initialized = true;
                } catch (err) {
                    console.error(`[ProjectManager] module init failed (${id})`, err);
                }
            }
        });
    }

    function getActiveProject() {
        if (!state.manifest) return null;
        const slug = state.activeProjectSlug;
        if (!slug) return null;
        const project = state.manifest.projects?.[slug] || null;
        if (project) {
            return { slug, ...project };
        }
        return null;
    }

    function setActiveProject(slug) {
        if (!state.manifest) {
            state.activeProjectSlug = slug;
            return loadManifest();
        }
        if (!state.manifest.projects?.[slug]) {
            console.warn('[ProjectManager] attempt to activate unknown project', slug);
            return;
        }
        if (state.activeProjectSlug === slug) return;
        state.activeProjectSlug = slug;
        state.moduleRegistry.forEach((entry) => {
            entry.initialized = false;
        });
        notifyListeners();
        tryActivateModules();
    }

    function onProjectChange(cb) {
        if (typeof cb !== 'function') return () => { };
        state.listeners.add(cb);
        return () => state.listeners.delete(cb);
    }

    function registerModule(id, config) {
        if (!id) return;
        state.moduleRegistry.set(id, { ...(config || {}), initialized: false });
        tryActivateModules();
    }

    function getManifest() {
        return state.manifest;
    }

    function applyProjectGlobals(project) {
        const clip = project?.workflow?.clip || project?.clip || null;
        const total = Number(clip?.totalMs);
        if (Number.isFinite(total) && total > 0) {
            window.__MICROCLIP_MS = total;
        } else {
            window.__MICROCLIP_MS = defaultClipConfig.totalMs;
        }

        const maxPre = Math.max(0, (window.__MICROCLIP_MS || defaultClipConfig.totalMs) - 120);
        const attemptLabel = typeof project?.workflow?.attemptLabel === 'string'
            ? project.workflow.attemptLabel.trim().toLowerCase()
            : '';
        const minSwingPre = attemptLabel === 'swing' ? 1500 : 0;
        const pre = Number(clip?.preMs);
        const resolvedPre = Number.isFinite(pre) && pre >= 0 ? Math.min(pre, maxPre) : Math.min(defaultClipConfig.preMs, maxPre);
        window.__MICROCLIP_PRE_MS = Math.min(maxPre, Math.max(resolvedPre, minSwingPre));

        const poseStreakNeed = Number(project?.workflow?.poseStreakNeed);
        if (Number.isFinite(poseStreakNeed) && poseStreakNeed > 0) {
            window.POSE_STREAK_NEED = poseStreakNeed;
        } else {
            window.POSE_STREAK_NEED = window.POSE_STREAK_NEED || 2;
        }
    }

    async function createProject(payload) {
        const res = await fetch('/api/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        });
        if (!res.ok) {
            const txt = await res.text().catch(() => res.statusText);
            throw new Error(txt || `Create project failed (${res.status})`);
        }
        await manager.reload();
        return res.json();
    }

    async function updateProject(slug, payload) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        });
        if (!res.ok) {
            const txt = await res.text().catch(() => res.statusText);
            throw new Error(txt || `Update project failed (${res.status})`);
        }
        await manager.reload();
        return res.json();
    }

    const manager = {
        ready: loadManifest(),
        reload: async () => {
            state.manifest = null;
            state.moduleRegistry.forEach((entry) => (entry.initialized = false));
            return loadManifest();
        },
        getManifest,
        getActiveProject,
        setActiveProject,
        onProjectChange,
        registerModule,
        createProject,
        updateProject,
        getModuleConfig(id) {
            return state.moduleRegistry.get(id) || null;
        },
        listProjects() {
            if (!state.manifest) return [];
            return Object.entries(state.manifest.projects || {}).map(([slug, meta]) => ({ slug, ...meta }));
        }
    };

    Object.defineProperty(window, 'visaionProjectManager', {
        value: manager,
        enumerable: false
    });

    loadManifest();
})();
