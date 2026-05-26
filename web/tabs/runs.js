// Runs tab — active/recent run list with lifecycle state + cancel/interrupt/short-turn.
// Run IDs come from localStorage.recentRuns ∪ bound_run_id fields in sim/state/fleet.

import { createDatabusClient } from '../lib/databus_api.js';
import { createSimClient }     from '../lib/sim_api.js';

const databusClient = createDatabusClient();
const simClient     = createSimClient();

const RECENT_RUNS_KEY = 'simovi_recentRuns';
const POLL_INTERVAL_MS = 2000;
// Must match databus's RunLifecycleStates.*.value verbatim (note the spaces).
const TERMINAL_STATES = new Set(['Completed', 'Cancelled', 'Interrupted', 'Short Turned']);

let _root   = null;
let _ctx    = null;
let _unsub  = null;
let _polls  = new Map(); // run_id → intervalId
let _states = new Map(); // run_id → lifecycle_state string
let _expanded = new Set(); // run_ids that are expanded

export function mount(root, ctx) {
    _root = root;
    _ctx  = ctx;

    root.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.75rem;">
            <h2 class="section-title" style="margin:0;">Active Runs</h2>
            <button class="btn-secondary" id="runs-clear-done" style="font-size:0.78rem;">Clear completed</button>
        </div>
        <div id="runs-list"></div>
    `;

    root.querySelector('#runs-clear-done').addEventListener('click', clearCompleted);

    buildRunList();

    _unsub = ctx.store.subscribe(() => {
        buildRunList();
    });
}

export function unmount(root) {
    stopAllPolls();
    _unsub?.();
    _unsub = null;
    _states.clear();
    _expanded.clear();
    root.innerHTML = '';
}

// ---- Build run list -----------------------------------------------------

function buildRunList() {
    const runIds = collectRunIds();
    renderList(runIds);
    for (const id of runIds) startPolling(id);
}

function collectRunIds() {
    const ids = new Set();

    // From localStorage
    for (const r of getRecentRuns()) ids.add(r.run_id);

    // From fleet bound_run_id
    const vehicles = _ctx?.store.get().fleet?.vehicles ?? [];
    for (const v of vehicles) {
        if (v.bound_run_id) ids.add(v.bound_run_id);
    }

    return [...ids];
}

function renderList(runIds) {
    const el = document.getElementById('runs-list');
    if (!el) return;

    if (runIds.length === 0) {
        el.innerHTML = '<p class="empty-msg">No active or recent runs.</p>';
        return;
    }

    el.innerHTML = '';
    const recentMap  = new Map(getRecentRuns().map(r => [r.run_id, r]));
    const fleetMap   = buildFleetRunMap();

    for (const runId of runIds) {
        const info  = recentMap.get(runId) ?? fleetMap.get(runId) ?? { run_id: runId };
        const state = _states.get(runId) ?? info.lifecycle_state ?? '—';
        el.appendChild(buildRunItem(runId, info, state));
    }
}

function buildFleetRunMap() {
    const m = new Map();
    const vehicles = _ctx?.store.get().fleet?.vehicles ?? [];
    for (const v of vehicles) {
        if (v.bound_run_id) {
            m.set(v.bound_run_id, {
                run_id:     v.bound_run_id,
                vehicle_id: v.vehicle_id,
                route_id:   v.route_id,
                trip_id:    v.bound_trip_id ?? '—',
                lifecycle_state: v.lifecycle_state,
            });
        }
    }
    return m;
}

function buildRunItem(runId, info, state) {
    const div = document.createElement('div');
    div.className = 'run-item';
    div.dataset.runId = runId;
    const isExpanded = _expanded.has(runId);
    const stateClass = stateToChipClass(state);
    const isTerminal = TERMINAL_STATES.has(state);

    // Optimistic UI: show every action regardless of state. Databus is the
    // FSM authority — if a transition isn't allowed, the 422 response is
    // surfaced inline below the buttons. No transition rules in this file.
    div.innerHTML = `
        <div class="run-item__header">
            <span class="run-vehicle">${esc(info.vehicle_id ?? '—')}</span>
            <span class="run-id">${esc(runId.slice(0, 16))}...</span>
            <span class="state-chip ${stateClass}" data-field="state">${esc(state)}</span>
            <span class="run-expand">${isExpanded ? '▲' : '▼'}</span>
        </div>
        <div class="run-item__actions" style="display:${isExpanded ? 'flex' : 'none'};flex-direction:column;gap:0.5rem;">
            ${isTerminal ? '' : `
                <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
                    <button class="btn-danger"    data-action="cancel">Cancel</button>
                    <button class="btn-secondary" data-action="interrupt">Interrupt</button>
                    <button class="btn-secondary" data-action="short-turn">Short-turn</button>
                </div>
            `}
            <div data-field="action-error" style="font-size:0.78rem;color:#dc2626;min-height:1em;"></div>

            <div class="run-history" style="margin-top:0.5rem;">
                <button class="btn-secondary" data-action="history" style="font-size:0.78rem;">Show history</button>
                <div data-field="history-panel" style="display:none;margin-top:0.5rem;font-size:0.78rem;"></div>
            </div>

            ${isTerminal ? '<span style="font-size:0.78rem;color:#94a3b8;">Run is in a terminal state.</span>' : ''}
        </div>
    `;

    const errorEl  = div.querySelector('[data-field="action-error"]');
    const histBtn  = div.querySelector('[data-action="history"]');
    const histPanel = div.querySelector('[data-field="history-panel"]');

    div.querySelector('.run-item__header').addEventListener('click', () => {
        if (_expanded.has(runId)) _expanded.delete(runId);
        else _expanded.add(runId);
        const actions = div.querySelector('.run-item__actions');
        const arrow   = div.querySelector('.run-expand');
        actions.style.display = _expanded.has(runId) ? 'flex' : 'none';
        if (arrow) arrow.textContent = _expanded.has(runId) ? '▲' : '▼';
    });

    if (!isTerminal) {
        div.querySelector('[data-action="cancel"]').addEventListener('click', async () => {
            if (!confirm(`Cancel run ${runId.slice(0, 12)}...?`)) return;
            await sendRunEvent(runId, 'cancel_run', { actor_role: 'dispatcher' }, errorEl);
        });

        div.querySelector('[data-action="interrupt"]').addEventListener('click', async () => {
            if (!confirm(`Interrupt run ${runId.slice(0, 12)}...?`)) return;
            await sendRunEvent(runId, 'interrupt_run', { actor_role: 'dispatcher' }, errorEl);
        });

        div.querySelector('[data-action="short-turn"]').addEventListener('click', async () => {
            try {
                const { open } = await import('../modals/short_turn.js');
                const result = await open({ runId });
                if (!result) return;
                await sendRunEvent(runId, 'short_turn_run', {
                    actor_role: 'dispatcher',
                    short_turn_stop_id: result.stop_id,
                }, errorEl);
            } catch (e) {
                console.error('short_turn modal failed', e);
            }
        });
    }

    histBtn.addEventListener('click', async () => {
        const open = histPanel.style.display !== 'none';
        if (open) {
            histPanel.style.display = 'none';
            histBtn.textContent = 'Show history';
            return;
        }
        histPanel.style.display = 'block';
        histBtn.textContent = 'Hide history';
        histPanel.innerHTML = '<em style="color:#64748b;">Loading...</em>';
        try {
            const res = await fetch(`/databus/api/runs/${encodeURIComponent(runId)}/history/`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            histPanel.innerHTML = renderHistory(data.transitions ?? []);
        } catch (e) {
            histPanel.innerHTML = `<span style="color:#dc2626;">Failed to load history: ${esc(e.message)}</span>`;
        }
    });

    return div;
}

function renderHistory(transitions) {
    if (transitions.length === 0) {
        return '<em style="color:#94a3b8;">No transitions recorded.</em>';
    }
    const rows = transitions.map(t => {
        const ts = new Date(t.timestamp).toLocaleTimeString();
        const from = t.from_state ?? '∅';
        const to   = t.to_state   ?? '∅';
        return `
            <li style="margin-bottom:0.35rem;display:grid;grid-template-columns:80px 1fr;gap:0.5rem;">
                <span style="color:#64748b;font-variant-numeric:tabular-nums;">${esc(ts)}</span>
                <span><strong>${esc(t.event)}</strong> · ${esc(from)} → ${esc(to)}</span>
            </li>
        `;
    }).join('');
    return `<ol style="list-style:none;padding:0;margin:0;">${rows}</ol>`;
}

async function sendRunEvent(runId, event, details, errorEl) {
    if (errorEl) errorEl.textContent = '';
    try {
        const result = await databusClient.updateRun({ run_id: runId, event, details });
        const newState = result.run_lifecycle_state;
        _states.set(runId, newState);
        updateRecentRunState(runId, newState);
        buildRunList();
    } catch (e) {
        // Surface databus's `detail` text inline. databusClient throws an
        // Error whose message includes the response body.
        const msg = extractDatabusDetail(e.message) ?? e.message;
        if (errorEl) errorEl.textContent = `${event}: ${msg}`;
        else alert(`${event} failed: ${msg}`);
    }
}

function extractDatabusDetail(msg) {
    // databusClient throws "POST /api/runs/{id}/update/: HTTP 422 — { ... }"
    const idx = msg.indexOf('—');
    if (idx === -1) return null;
    const body = msg.slice(idx + 1).trim();
    try {
        const parsed = JSON.parse(body);
        return parsed?.errors?.detail ?? parsed?.detail ?? null;
    } catch {
        return null;
    }
}

// ---- Polling ------------------------------------------------------------

function startPolling(runId) {
    if (_polls.has(runId)) return;
    if (TERMINAL_STATES.has(_states.get(runId))) return;

    const id = setInterval(async () => {
        let state;
        try {
            const data = await simClient.getRun(runId);
            state = data.run_lifecycle_state;
            if (!state) return;
        } catch (e) {
            // 404 = run hash gone from Redis (cancel paths delete it).
            // Treat as terminal so the UI doesn't get stuck.
            if (e?.message?.includes('HTTP 404')) {
                state = 'Cancelled';
            } else {
                return; // genuine transient error
            }
        }

        const prev = _states.get(runId);
        _states.set(runId, state);
        updateRecentRunState(runId, state);

        if (state !== prev) updateRunItemState(runId, state);

        if (TERMINAL_STATES.has(state)) stopPolling(runId);
    }, POLL_INTERVAL_MS);

    _polls.set(runId, id);
}

function stopPolling(runId) {
    const id = _polls.get(runId);
    if (id != null) clearInterval(id);
    _polls.delete(runId);
}

function stopAllPolls() {
    _polls.forEach(id => clearInterval(id));
    _polls.clear();
}

function updateRunItemState(runId, state) {
    const el = document.querySelector(`[data-run-id="${runId}"] [data-field="state"]`);
    if (!el) return;
    el.textContent = state;
    el.className = `state-chip ${stateToChipClass(state)}`;
}

// ---- Clear completed ----------------------------------------------------

function clearCompleted() {
    const runs = getRecentRuns().filter(r => !TERMINAL_STATES.has(r.lifecycle_state));
    localStorage.setItem(RECENT_RUNS_KEY, JSON.stringify(runs));
    buildRunList();
}

// ---- localStorage helpers -----------------------------------------------

function getRecentRuns() {
    try { return JSON.parse(localStorage.getItem(RECENT_RUNS_KEY) ?? '[]'); }
    catch { return []; }
}

function updateRecentRunState(runId, state) {
    const runs = getRecentRuns();
    const run  = runs.find(r => r.run_id === runId);
    if (run) {
        run.lifecycle_state = state;
        localStorage.setItem(RECENT_RUNS_KEY, JSON.stringify(runs));
    }
}

// ---- Helpers ------------------------------------------------------------

function stateToChipClass(state) {
    const MAP = {
        Initialized: 'chip--requested', Requested: 'chip--requested', Validated: 'chip--requested',
        Confirmed:        'chip--confirmed',
        Tracking:         'chip--tracking',
        'In Progress':    'chip--inprogress',
        'No Signal':      'chip--nosignal',
        Completed:        'chip--terminal', Cancelled: 'chip--terminal',
        Interrupted:      'chip--terminal', 'Short Turned': 'chip--terminal',
    };
    return MAP[state] ?? 'chip--idle';
}

function esc(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
