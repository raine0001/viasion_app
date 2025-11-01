(() => {
    const script = document.currentScript;
    if (!script) return;
    const guard = (script.dataset.guard || 'auth').toLowerCase();
    if (guard !== 'auth') return;

    const allow = (script.dataset.allow || '').split(',')
        .map(part => part.trim().toLowerCase())
        .filter(Boolean);
    if (allow.includes('public')) return;

    const loginUrl = '/static/login.html';
    const communityUrl = '/static/community.html';

    const redirectToLogin = () => {
        const qs = new URLSearchParams();
        qs.set('return', '/static/my_sessions.html');
        window.location.replace(`${loginUrl}?${qs.toString()}`);
    };

    try {
        fetch('/api/auth/me', { credentials: 'include', cache: 'no-store' })
            .then(res => (res.ok ? res.json() : null))
            .then(payload => {
                if (payload?.user) return;
                const path = (window.location.pathname || '').toLowerCase();
                if (path.includes('/static/community')) {
                    return;
                }
                redirectToLogin();
            })
            .catch(() => redirectToLogin());
    } catch {
        redirectToLogin();
    }
})();
