// Schedule tab — editable table backed by :8081 HTTP control endpoint.
// Reads GET /schedule, saves PUT /schedule, reloads POST /schedule/reload.
// Live status column updates from sim/state/schedule via store.

import { createSimClient } from '../lib/sim_api.js';

const simClient = createSimClient();

let _unsub  = null;
let _root   = null;
let _doc    = null; // full ScheduleSnapshot from server

export function mount(root, ctx) {
    _root = root;
    root.innerHTML = '<p class="loading-msg">Loading schedule...</p>';

    loadSchedule(root, ctx);

    _unsub = ctx.store.subscribe(state => {
        updateStatusColumn(state.schedule?.entries ?? []);
    });
}

export function unmount(root) {
    _unsub?.();
    _unsub = null;
    _doc  = null;
    root.innerHTML = '';
}

// ---- Load + render ------------------------------------------------------

async function loadSchedule(root, ctx) {
    try {
        _doc = await simClient.getSchedule();
        renderSchedule(root, ctx);
    } catch (e) {
        root.innerHTML = `<p class="error-msg">Failed to load schedule: ${e.message}</p>`;
    }
}

function renderSchedule(root, ctx) {
    const runs = _doc?.runs ?? [];

    root.innerHTML = `
        <div class="sched-toolbar">
            <button class="btn-primary" id="sched-add">Add run</button>
            <button class="btn-secondary" id="sched-reload">Reload from disk</button>
            <button class="btn-primary" id="sched-save">Save</button>
        </div>
        <div class="sched-table-wrap">
            <table class="sched-table" id="sched-table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Vehicle</th>
                        <th>Trip</th>
                        <th>Route</th>
                        <th>Shape</th>
                        <th>Start time</th>
                        <th>Auto confirm</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="sched-tbody"></tbody>
            </table>
        </div>
        <p class="empty-msg" id="sched-empty" style="display:${runs.length ? 'none' : 'block'}">
            No schedule entries. Click "Add run" to create one.
        </p>
    `;

    renderRows(runs);

    root.querySelector('#sched-add').addEventListener('click', async () => {
        try {
            const { open } = await import('../modals/schedule_entry.js');
            const entry = await open({ mode: 'create' });
            if (!entry) return;
            if (!_doc) _doc = { defaults: {}, runs: [] };
            _doc.runs.push(entry);
            renderRows(_doc.runs);
            toggleEmpty(_doc.runs);
        } catch (e) {
            alert(`Error opening modal: ${e.message}`);
        }
    });

    root.querySelector('#sched-reload').addEventListener('click', async () => {
        try {
            await simClient.reloadSchedule();
            _doc = await simClient.getSchedule();
            renderRows(_doc?.runs ?? []);
            toggleEmpty(_doc?.runs ?? []);
        } catch (e) {
            alert(`Reload failed: ${e.message}`);
        }
    });

    root.querySelector('#sched-save').addEventListener('click', async () => {
        if (!_doc) return;
        const body = buildSaveBody(_doc);
        try {
            await simClient.putSchedule(body);
            alert('Schedule saved.');
        } catch (e) {
            alert(`Save failed: ${e.message}`);
        }
    });
}

function renderRows(runs) {
    const tbody = document.getElementById('sched-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    for (const run of runs) {
        const tr = document.createElement('tr');
        tr.dataset.runId = run.id;
        tr.innerHTML = `
            <td>${esc(run.id)}</td>
            <td>${esc(run.vehicle_id)}</td>
            <td>${esc(run.trip_id)}</td>
            <td>${esc(run.route_id)}</td>
            <td>${esc(run.shape_id)}</td>
            <td>${esc(run.start_time)}</td>
            <td>${run.auto_confirm ? 'yes' : 'no'}</td>
            <td class="sched-status"><span class="status-${esc(run.status ?? 'pending')}">${esc(run.status ?? 'pending')}</span></td>
            <td class="sched-actions">
                <button class="btn-table" data-action="edit">Edit</button>
                <button class="btn-table btn-table--danger" data-action="remove">Remove</button>
            </td>
        `;

        tr.querySelector('[data-action="edit"]').addEventListener('click', async () => {
            try {
                const { open } = await import('../modals/schedule_entry.js');
                const updated = await open({ mode: 'edit', entry: { ...run } });
                if (!updated) return;
                const idx = _doc.runs.findIndex(r => r.id === run.id);
                if (idx !== -1) { _doc.runs[idx] = updated; run = updated; }
                renderRows(_doc.runs);
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        });

        tr.querySelector('[data-action="remove"]').addEventListener('click', () => {
            if (!confirm(`Remove schedule entry "${run.id}"?`)) return;
            _doc.runs = _doc.runs.filter(r => r.id !== run.id);
            renderRows(_doc.runs);
            toggleEmpty(_doc.runs);
        });

        tbody.appendChild(tr);
    }
}

function toggleEmpty(runs) {
    const el = document.getElementById('sched-empty');
    if (el) el.style.display = runs.length ? 'none' : 'block';
}

function updateStatusColumn(scheduleEntries) {
    if (!scheduleEntries?.length) return;
    const statusByBoundRun = new Map(scheduleEntries.map(e => [e.id, e.status]));
    document.querySelectorAll('#sched-tbody tr').forEach(tr => {
        const id = tr.dataset.runId;
        if (!id) return;
        const status = statusByBoundRun.get(id);
        if (!status) return;
        const cell = tr.querySelector('.sched-status');
        if (cell) cell.innerHTML = `<span class="status-${esc(status)}">${esc(status)}</span>`;
    });
}

function buildSaveBody(doc) {
    return {
        defaults: doc.defaults ?? {},
        runs: (doc.runs ?? []).map(r => {
            const { status, bound_run_id, ...rest } = r;
            return rest;
        }),
    };
}

function esc(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
