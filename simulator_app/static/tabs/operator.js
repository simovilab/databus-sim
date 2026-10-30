// Operator tab — on-demand run request form + pending-confirmations list.
// Submit → POST /api/create-run/ (databus). Confirm/Reject → POST /api/update-run/.
// Pending list is persisted in localStorage.recentRuns and polled every 2s.

import { createDatabusClient } from '../lib/databus_api.js';
import { createSimClient }     from '../lib/sim_api.js';

const databusClient = createDatabusClient();
const simClient     = createSimClient();

const RECENT_RUNS_KEY = 'simovi_recentRuns';
const POLL_INTERVAL_MS = 2000;

const PENDING_STATES = new Set(['Initialized', 'Requested', 'Validated', 'Confirmed']);

let _root    = null;
let _polls   = new Map(); // run_id → intervalId
let _unsub   = null;

export function mount(root, ctx) {
    _root = root;
    root.innerHTML = `
        <div class="op-section">
            <h2 class="section-title">Request Run</h2>
            <p style="font-size:0.85rem;color:#475569;margin:0 0 0.75rem;">
                Create an on-demand run request. After submission, confirm it in the Pending list below.
            </p>
            <button class="btn-primary" id="btn-request-run">Request Run</button>
        </div>
        <div class="op-section">
            <h2 class="section-title">Pending Confirmations</h2>
            <div id="pending-list"></div>
        </div>
    `;

    root.querySelector('#btn-request-run').addEventListener('click', () => {
        handleRequestRun(ctx);
    });

    renderPendingList();
    startPollingAll();
}

export function unmount(root) {
    stopAllPolls();
    _unsub?.();
    _unsub = null;
    root.innerHTML = '';
}

// ---- Request a run ------------------------------------------------------

async function handleRequestRun(ctx) {
    const vehicles = ctx.store.get().fleet?.vehicles ?? [];
    try {
        const { open } = await import('../modals/run_request.js');
        const payload = await open({ vehicles, databusClient });
        if (!payload) return;

        let result;
        try {
            result = await databusClient.createRun(payload);
        } catch (e) {
            alert(`create-run failed: ${e.message}`);
            return;
        }

        const runId = result.run_id;
        if (!runId) {
            alert('create-run succeeded but returned no run_id.');
            return;
        }

        // Register the binding with the simulator so RunBinder picks it up.
        // Without this, v.bound_trip_id / v.terminal_stop_id stay null and the
        // databus `is_at_terminal_stop` guard rejects complete_run forever.
        try {
            await simClient.trackRun({
                run_id:     runId,
                vehicle_id: payload.vehicle_id,
                trip_id:    payload.trip_id,
                shape_id:   payload.shape_id,
            });
        } catch (e) {
            console.warn('simulator track-run failed:', e.message);
            alert(
                `Run created in databus but simulator failed to track it.\n` +
                `The bus will not complete automatically.\n\n${e.message}`
            );
        }

        addRecentRun({
            run_id:     runId,
            vehicle_id: payload.vehicle_id,
            trip_id:    payload.trip_id,
            route_id:   payload.route_id,
            created_at: new Date().toISOString(),
        });

        renderPendingList();
        startPolling(runId);
    } catch (e) {
        alert(`Error: ${e.message}`);
    }
}

// ---- Pending list --------------------------------------------------------

function renderPendingList() {
    const el = document.getElementById('pending-list');
    if (!el) return;
    const runs = getRecentRuns().filter(r => PENDING_STATES.has(r.lifecycle_state ?? 'Initialized'));

    if (runs.length === 0) {
        el.innerHTML = '<p class="empty-msg">No pending runs. Request one above.</p>';
        return;
    }

    el.innerHTML = '';
    for (const run of runs) {
        el.appendChild(buildPendingItem(run));
    }
}

function buildPendingItem(run) {
    const div = document.createElement('div');
    div.className = 'pending-item';
    div.dataset.runId = run.run_id;
    const state = run.lifecycle_state ?? 'Initialized';
    const stateClass = stateToChipClass(state);

    div.innerHTML = `
        <span class="pending-vehicle">${esc(run.vehicle_id)}</span>
        <span class="pending-trip">${esc(run.trip_id)}</span>
        <span class="pending-run-id">${esc(run.run_id.slice(0, 12))}...</span>
        <span class="pending-state state-chip ${stateClass}" data-field="state">${esc(state)}</span>
        <div class="pending-actions">
            <button class="btn-secondary btn-confirm">Confirm</button>
            <button class="btn-danger btn-reject">Reject</button>
        </div>
    `;

    div.querySelector('.btn-confirm').addEventListener('click', async () => {
        await handleUpdateRun(run.run_id, 'run_confirmed_by_operator', {});
    });

    div.querySelector('.btn-reject').addEventListener('click', async () => {
        try {
            const { openModal } = await import('../modals/_base.js');
            const reason = await openModal({
                title: 'Reject Run',
                bodyHTML: `
                    <div class="form-group">
                        <label class="form-label" for="rej-reason">Rejection reason (optional)</label>
                        <input id="rej-reason" type="text" class="form-input" placeholder="e.g. vehicle unavailable">
                    </div>
                `,
                onSubmit: dlg => dlg.querySelector('#rej-reason').value.trim(),
            });
            if (reason === null) return;
            await handleUpdateRun(run.run_id, 'run_rejected', {
                actor_role: 'dispatcher',
                rejection_reason: reason,
            });
        } catch (e) {
            console.error('reject modal failed', e);
        }
    });

    return div;
}

async function handleUpdateRun(runId, event, details) {
    try {
        const result = await databusClient.updateRun({ run_id: runId, event, details });
        updateRecentRunState(runId, result.run_lifecycle_state ?? event);
        renderPendingList();
    } catch (e) {
        alert(`update-run (${event}) failed: ${e.message}`);
    }
}

// ---- Run state polling ---------------------------------------------------

function startPollingAll() {
    const runs = getRecentRuns();
    for (const run of runs) {
        if (PENDING_STATES.has(run.lifecycle_state ?? 'Initialized')) {
            startPolling(run.run_id);
        }
    }
}

function startPolling(runId) {
    if (_polls.has(runId)) return;
    const id = setInterval(async () => {
        try {
            const data = await simClient.getRun(runId);
            const state = data.run_lifecycle_state;
            updateRecentRunState(runId, state);
            updatePendingItemState(runId, state);
            if (!PENDING_STATES.has(state)) {
                stopPolling(runId);
                renderPendingList();
            }
        } catch { /* ignore transient failures */ }
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

function updatePendingItemState(runId, state) {
    const el = document.querySelector(`[data-run-id="${runId}"] [data-field="state"]`);
    if (!el) return;
    el.textContent = state ?? '—';
    el.className = `pending-state state-chip ${stateToChipClass(state)}`;
}

// ---- localStorage helpers -----------------------------------------------

function getRecentRuns() {
    try {
        return JSON.parse(localStorage.getItem(RECENT_RUNS_KEY) ?? '[]');
    } catch {
        return [];
    }
}

function addRecentRun(info) {
    const runs = getRecentRuns();
    if (!runs.find(r => r.run_id === info.run_id)) {
        runs.unshift({ ...info, lifecycle_state: 'Initialized' });
        localStorage.setItem(RECENT_RUNS_KEY, JSON.stringify(runs));
    }
}

function updateRecentRunState(runId, state) {
    const runs = getRecentRuns();
    const run = runs.find(r => r.run_id === runId);
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
