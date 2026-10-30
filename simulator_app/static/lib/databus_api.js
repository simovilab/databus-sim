// Thin fetch wrappers for the databus orchestrator. CORS-friendly.
// See CONTRACTS.md §5. Signatures are pinned by P0 — do not rename.

// Same-origin via the web nginx reverse proxy → databus orchestrator.
// Avoids CORS without modifying databus.
const DEFAULT_BASE = '/databus';
const TRIPS_CACHE_KEY = 'simovi_trips_cache';
const TRIPS_CACHE_TTL_MS = 5 * 60 * 1000;

export function createDatabusClient(baseUrl = DEFAULT_BASE) {
    async function request(method, path, body) {
        const opts = { method, headers: {} };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const res = await fetch(`${baseUrl}${path}`, opts);
        if (!res.ok) {
            const text = await res.text().catch(() => res.statusText);
            throw new Error(`${method} ${path}: HTTP ${res.status} — ${text}`);
        }
        return res.json();
    }

    return {
        async createRun(payload) {
            return request('POST', '/api/create-run/', payload);
        },

        async updateRun(payload) {
            // payload: { run_id, event, details }
            // run_id is sent in the URL path; body carries { event, details }.
            const { run_id, ...body } = payload;
            return request('POST', `/api/runs/${run_id}/update/`, body);
        },

        async getRunState(runId) {
            // Returns { status, run_lifecycle_state } from databus.
            return request('GET', `/api/runs/${runId}/state/`);
        },

        async listTrips() {
            try {
                const cached = sessionStorage.getItem(TRIPS_CACHE_KEY);
                if (cached) {
                    const { expires, data } = JSON.parse(cached);
                    if (Date.now() < expires) return data;
                }
            } catch { /* ignore */ }

            const data = await request('GET', '/api/trips/');

            try {
                sessionStorage.setItem(TRIPS_CACHE_KEY, JSON.stringify({
                    expires: Date.now() + TRIPS_CACHE_TTL_MS,
                    data,
                }));
            } catch { /* quota exceeded — ignore */ }

            return data;
        },
    };
}
