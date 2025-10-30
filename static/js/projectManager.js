(function () {
    const manifestEndpoint = '/api/projects';
    const state = {
        manifest: null,
        activeProjectSlug: null,
        listeners: new Set(),
        moduleRegistry: new Map()
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
                        root: 'datasets/viason_seg',
                        frameCacheRoot: 'frame_cache',
                        framesRoot: 'frames',
                        labelTrainRoot: 'datasets/viason_seg/labels/train',
                        imagesTrainRoot: 'datasets/viason_seg/images/train',
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
        try {
            const res = await fetch(manifestEndpoint, { cache: 'no-store' });
            if (!res.ok) throw new Error(`Manifest HTTP ${res.status}`);
            state.manifest = await res.json();
        } catch (err) {
            console.warn('[ProjectManager] failed to load manifest; using fallback', err);
            state.manifest = fallbackManifest;
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
        try {
            window.__VIASON_ACTIVE_PROJECT = project || null;
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
        if (typeof cb !== 'function') return () => {};
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

    Object.defineProperty(window, 'viasonProjectManager', {
        value: manager,
        enumerable: false
    });

    loadManifest();
})();
