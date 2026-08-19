// Thin fetch wrappers for the simulator HTTP control endpoint (:8081).
// See CONTRACTS.md §2. Signatures are pinned by P0 — do not rename.

// Same-origin via the web nginx reverse proxy → simulator HTTP control.
const DEFAULT_BASE = '/sim';

export function createSimClient(baseUrl = DEFAULT_BASE) {
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
        async getFleet() {
            return request('GET', '/fleet');
        },
        async getSchedule() {
            return request('GET', '/schedule');
        },
        async putSchedule(doc) {
            return request('PUT', '/schedule', doc);
        },
        async reloadSchedule() {
            return request('POST', '/schedule/reload');
        },
        async getRun(runId) {
            return request('GET', `/run/${encodeURIComponent(runId)}`);
        },
        async trackRun({ run_id, vehicle_id, trip_id, shape_id }) {
            return request('POST', '/runs/track', { run_id, vehicle_id, trip_id, shape_id });
        },
    };
}
