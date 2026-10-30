// Fleet tab — per-vehicle cards with live state + control publishing.
// Reads from sim/state/fleet (via store) and publishes to sim/control/<id>/...

const FLEET_ORDER = ['unit-01', 'unit-02', 'unit-03', 'unit-04', 'unit-05', 'unit-06'];

const STATE_CHIP = {
    Requested:   'chip--requested',
    Validated:   'chip--requested',
    Initialized: 'chip--requested',
    Confirmed:      'chip--confirmed',
    Tracking:       'chip--tracking',
    'In Progress':  'chip--inprogress',
    'No Signal':    'chip--nosignal',
    Completed:      'chip--terminal',
    Cancelled:      'chip--terminal',
    Interrupted:    'chip--terminal',
    'Short Turned': 'chip--terminal',
};

let _unsub = null;
let _root  = null;
let _ctx   = null;
const _cardMap = new Map(); // vehicle_id → card element

export function mount(root, ctx) {
    _root = root;
    _ctx  = ctx;
    _cardMap.clear();

    root.innerHTML = '<div class="fleet-grid" id="fleet-grid"></div>';
    const grid = root.querySelector('#fleet-grid');

    renderGrid(grid, ctx.store.get(), ctx);

    _unsub = ctx.store.subscribe(state => {
        updateCards(state, ctx);
    });
}

export function unmount(root) {
    _unsub?.();
    _unsub = null;
    _cardMap.clear();
    root.innerHTML = '';
}

// ---- Render helpers -----------------------------------------------------

function renderGrid(grid, state, ctx) {
    _cardMap.clear();
    const vehicles = sortedVehicles(state.fleet?.vehicles);

    if (vehicles.length === 0) {
        grid.innerHTML = '<p class="fleet-empty">No fleet state yet — waiting for MQTT connection.</p>';
        return;
    }

    grid.innerHTML = '';
    for (const v of vehicles) {
        const card = createCard(v, state.telemetry?.[v.vehicle_id] || {}, ctx);
        grid.appendChild(card);
        _cardMap.set(v.vehicle_id, card);
    }
}

function updateCards(state, ctx) {
    const grid = _root?.querySelector('#fleet-grid');
    if (!grid) return;

    const vehicles = sortedVehicles(state.fleet?.vehicles);

    if (vehicles.length === 0) {
        grid.innerHTML = '<p class="fleet-empty">No fleet state yet — waiting for MQTT connection.</p>';
        _cardMap.clear();
        return;
    }

    // First render after empty state
    if (_cardMap.size === 0) {
        renderGrid(grid, state, ctx);
        return;
    }

    const incomingIds = new Set(vehicles.map(v => v.vehicle_id));
    const existingIds = new Set(_cardMap.keys());

    // Remove stale cards
    for (const id of existingIds) {
        if (!incomingIds.has(id)) {
            _cardMap.get(id).remove();
            _cardMap.delete(id);
        }
    }

    // Update or add
    for (const v of vehicles) {
        const telem = state.telemetry?.[v.vehicle_id] || {};
        if (_cardMap.has(v.vehicle_id)) {
            patchCard(_cardMap.get(v.vehicle_id), v, telem);
        } else {
            const card = createCard(v, telem, ctx);
            grid.appendChild(card);
            _cardMap.set(v.vehicle_id, card);
        }
    }
}

function sortedVehicles(vehicles) {
    if (!vehicles) return [];
    return [...vehicles].sort((a, b) =>
        FLEET_ORDER.indexOf(a.vehicle_id) - FLEET_ORDER.indexOf(b.vehicle_id)
    );
}

// ---- Card creation ------------------------------------------------------

function createCard(v, telem, ctx) {
    const el = document.createElement('div');
    el.className = 'vehicle-card';
    el.dataset.vehicleId = v.vehicle_id;
    el.innerHTML = cardHTML(v, telem);
    wireCardControls(el, v, ctx);
    return el;
}

function cardHTML(v, telem) {
    const stateClass = STATE_CHIP[v.lifecycle_state] ?? 'chip--idle';
    const stateLabel = v.lifecycle_state ?? 'idle';
    const runId      = v.bound_run_id ? `${v.bound_run_id.slice(0, 8)}...` : '—';

    const pos  = telem.position    ?? {};
    const prog = telem.progression ?? {};
    const occ  = telem.occupancy   ?? {};

    const speed    = pos.speed != null     ? `${pos.speed.toFixed(1)} m/s` : '—';
    const bearing  = pos.bearing != null   ? `${pos.bearing.toFixed(0)}°`  : '—';
    const status   = prog.current_status   ?? '—';
    const occPct   = occ.occupancy_percentage != null ? `${occ.occupancy_percentage}%` : '—';

    const speedDefault = v.speed_override    ?? 7.5;
    const occDefault   = v.occupancy_override ?? 50;

    return `
        <div class="vc-header">
            <span class="vc-id">${v.vehicle_id}</span>
            <span class="vc-route">${v.route_id}</span>
            <span class="vc-run-chip" title="${v.bound_run_id ?? 'no run'}">${runId}</span>
        </div>
        <div class="vc-state">
            <span class="state-chip ${stateClass}" data-field="state">${stateLabel}</span>
        </div>
        <div class="vc-telemetry">
            <span class="telem-item"><span class="telem-label">spd</span><span class="telem-val" data-field="speed">${speed}</span></span>
            <span class="telem-item"><span class="telem-label">brg</span><span class="telem-val" data-field="bearing">${bearing}</span></span>
            <span class="telem-item"><span class="telem-label">status</span><span class="telem-val" data-field="prog-status">${status}</span></span>
            <span class="telem-item"><span class="telem-label">occ</span><span class="telem-val" data-field="occ">${occPct}</span></span>
        </div>
        <div class="vc-controls">
            <div class="vc-toggles">
                <label class="toggle-label">
                    <input type="checkbox" class="ctrl-transmit" ${v.transmitting ? 'checked' : ''}>
                    Transmit
                </label>
                <label class="toggle-label">
                    <input type="checkbox" class="ctrl-moving" ${v.moving ? 'checked' : ''}>
                    Moving
                </label>
            </div>
            <div class="slider-group">
                <label class="slider-label">
                    Speed (m/s)
                    <input type="range" class="ctrl-speed" min="0" max="15" step="0.5" value="${speedDefault}">
                    <span class="slider-val" data-field="speed-slider">${speedDefault}</span>
                </label>
                <button class="btn-reset" data-ctrl="speed" title="Release speed override">reset</button>
            </div>
            <div class="slider-group">
                <label class="slider-label">
                    Occupancy (%)
                    <input type="range" class="ctrl-occ" min="0" max="100" step="1" value="${occDefault}">
                    <span class="slider-val" data-field="occ-slider">${occDefault}</span>
                </label>
                <button class="btn-reset" data-ctrl="occ" title="Release occupancy override">reset</button>
            </div>
            <div class="vc-btn-row">
                <button class="btn-action ctrl-start-motion">Start motion</button>
                <button class="btn-action ctrl-jump-terminal">Jump to terminal</button>
                <button class="btn-action ctrl-inject-fault">Inject fault</button>
                <button class="btn-action ctrl-dwell">Dwell</button>
            </div>
        </div>
    `;
}

// ---- Wire controls ------------------------------------------------------

function wireCardControls(el, v, ctx) {
    const pub = (topic, payload) => ctx.controls.publish(topic, payload);
    const vid = v.vehicle_id;

    el.querySelector('.ctrl-transmit').addEventListener('change', e => {
        pub(`sim/control/${vid}/transmit`, { on: e.target.checked });
    });

    el.querySelector('.ctrl-moving').addEventListener('change', e => {
        pub(`sim/control/${vid}/moving`, { on: e.target.checked });
    });

    const speedSlider = el.querySelector('.ctrl-speed');
    const speedVal    = el.querySelector('[data-field="speed-slider"]');
    speedSlider.addEventListener('input', () => {
        speedVal.textContent = speedSlider.value;
    });
    speedSlider.addEventListener('change', () => {
        pub(`sim/control/${vid}/speed`, { value: parseFloat(speedSlider.value) });
    });

    const occSlider = el.querySelector('.ctrl-occ');
    const occVal    = el.querySelector('[data-field="occ-slider"]');
    occSlider.addEventListener('input', () => {
        occVal.textContent = occSlider.value;
    });
    occSlider.addEventListener('change', () => {
        pub(`sim/control/${vid}/occupancy`, { value: parseInt(occSlider.value, 10) });
    });

    el.querySelectorAll('.btn-reset').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.ctrl === 'speed') {
                pub(`sim/control/${vid}/speed`, { value: null });
            } else if (btn.dataset.ctrl === 'occ') {
                pub(`sim/control/${vid}/occupancy`, { value: null });
            }
        });
    });

    el.querySelector('.ctrl-start-motion').addEventListener('click', () => {
        pub('sim/control/global/start_run', { vehicle_id: vid });
    });

    el.querySelector('.ctrl-jump-terminal').addEventListener('click', () => {
        pub(`sim/control/${vid}/jump_to_terminal`, {});
    });

    el.querySelector('.ctrl-inject-fault').addEventListener('click', async () => {
        try {
            const { open } = await import('../modals/inject_fault.js');
            const result = await open({ vehicleId: vid });
            if (result) pub(`sim/control/${vid}/inject_fault`, result);
        } catch (e) {
            console.error('inject_fault modal failed', e);
        }
    });

    el.querySelector('.ctrl-dwell').addEventListener('click', async () => {
        try {
            const { open } = await import('../modals/dwell.js');
            const result = await open({ vehicleId: vid });
            if (result) pub(`sim/control/${vid}/dwell`, result);
        } catch (e) {
            console.error('dwell modal failed', e);
        }
    });
}

// ---- In-place patch -----------------------------------------------------

function patchCard(el, v, telem) {
    const chip = el.querySelector('[data-field="state"]');
    if (chip) {
        const label = v.lifecycle_state ?? 'idle';
        chip.textContent = label;
        chip.className = `state-chip ${STATE_CHIP[v.lifecycle_state] ?? 'chip--idle'}`;
    }

    const runChip = el.querySelector('.vc-run-chip');
    if (runChip) {
        runChip.title = v.bound_run_id ?? 'no run';
        runChip.textContent = v.bound_run_id ? `${v.bound_run_id.slice(0, 8)}...` : '—';
    }

    const pos  = telem.position    ?? {};
    const prog = telem.progression ?? {};
    const occ  = telem.occupancy   ?? {};

    setText(el, '[data-field="speed"]', pos.speed != null ? `${pos.speed.toFixed(1)} m/s` : '—');
    setText(el, '[data-field="bearing"]', pos.bearing != null ? `${pos.bearing.toFixed(0)}°` : '—');
    setText(el, '[data-field="prog-status"]', prog.current_status ?? '—');
    setText(el, '[data-field="occ"]', occ.occupancy_percentage != null ? `${occ.occupancy_percentage}%` : '—');

    const transmitToggle = el.querySelector('.ctrl-transmit');
    if (transmitToggle) transmitToggle.checked = !!v.transmitting;

    const movingToggle = el.querySelector('.ctrl-moving');
    if (movingToggle) movingToggle.checked = !!v.moving;

    if (v.speed_override != null) {
        const s = el.querySelector('.ctrl-speed');
        const sv = el.querySelector('[data-field="speed-slider"]');
        if (s) s.value = v.speed_override;
        if (sv) sv.textContent = v.speed_override;
    }

    if (v.occupancy_override != null) {
        const o = el.querySelector('.ctrl-occ');
        const ov = el.querySelector('[data-field="occ-slider"]');
        if (o) o.value = v.occupancy_override;
        if (ov) ov.textContent = v.occupancy_override;
    }
}

function setText(parent, selector, text) {
    const el = parent.querySelector(selector);
    if (el) el.textContent = text;
}
