// Modal: create or edit a schedule entry.
// open({ mode: 'create'|'edit', entry?: ScheduleEntry }) → Promise<entry | null>

import { openModal } from './_base.js';

const ROUTE_SHAPES = {
    bUCR_L1: ['hacia_artes', 'hacia_educacion', 'desde_artes_sin_milla', 'desde_educacion_sin_milla'],
    bUCR_L2: ['desde_artes_con_milla', 'desde_educacion_con_milla'],
};

const ALL_VEHICLES = ['unit-01', 'unit-02', 'unit-03', 'unit-04', 'unit-05', 'unit-06'];
const VEHICLE_ROUTES = {
    'unit-01': 'bUCR_L1', 'unit-02': 'bUCR_L1', 'unit-03': 'bUCR_L1',
    'unit-04': 'bUCR_L2', 'unit-05': 'bUCR_L2', 'unit-06': 'bUCR_L2',
};

export function open({ mode, entry }) {
    const isEdit     = mode === 'edit';
    const e          = entry ?? {};
    const defaultVid = e.vehicle_id ?? 'unit-01';
    const defaultRt  = e.route_id   ?? VEHICLE_ROUTES[defaultVid] ?? 'bUCR_L1';

    const vehicleOptions = ALL_VEHICLES.map(v =>
        `<option value="${v}" data-route="${VEHICLE_ROUTES[v]}" ${v === defaultVid ? 'selected' : ''}>${v}</option>`
    ).join('');

    const shapeOptions = buildShapeOptions(defaultRt, e.shape_id ?? null);

    const bodyHTML = `
        <div class="form-group">
            <label class="form-label" for="se-id">Entry ID</label>
            <input id="se-id" type="text" class="form-input"
                   value="${esc(e.id ?? '')}" placeholder="sched-001"
                   ${isEdit ? 'readonly' : ''} required>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-vehicle">Vehicle</label>
            <select id="se-vehicle" class="form-select">${vehicleOptions}</select>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-operator">Operator ID</label>
            <input id="se-operator" type="text" class="form-input"
                   value="${esc(e.operator_id ?? 'op-001')}" placeholder="op-001" required>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-route">Route ID</label>
            <input id="se-route" type="text" class="form-input"
                   value="${esc(defaultRt)}" readonly>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-trip">Trip ID</label>
            <input id="se-trip" type="text" class="form-input"
                   value="${esc(e.trip_id ?? '')}" placeholder="trip-bUCR_L1-0001" required>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-shape">Shape ID</label>
            <select id="se-shape" class="form-select">${shapeOptions}</select>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-direction">Direction</label>
            <select id="se-direction" class="form-select">
                <option value="0" ${(e.direction_id ?? 0) === 0 ? 'selected' : ''}>0 — outbound</option>
                <option value="1" ${e.direction_id === 1 ? 'selected' : ''}>1 — inbound</option>
            </select>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-sched-rel">Schedule relationship</label>
            <select id="se-sched-rel" class="form-select">
                <option value="SCHEDULED" ${(e.schedule_relationship ?? 'SCHEDULED') === 'SCHEDULED' ? 'selected' : ''}>SCHEDULED</option>
                <option value="ADDED" ${e.schedule_relationship === 'ADDED' ? 'selected' : ''}>ADDED</option>
                <option value="UNSCHEDULED" ${e.schedule_relationship === 'UNSCHEDULED' ? 'selected' : ''}>UNSCHEDULED</option>
            </select>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-start-time">Start time (ISO-8601)</label>
            <input id="se-start-time" type="text" class="form-input"
                   value="${esc(e.start_time ?? '')}"
                   placeholder="2026-05-19T16:00:00-06:00" required>
            <span class="form-hint">Include timezone offset, e.g. -06:00</span>
        </div>
        <div class="form-group form-group--row">
            <label class="radio-label">
                <input type="checkbox" id="se-auto-confirm" ${e.auto_confirm ? 'checked' : ''}>
                Auto-confirm (scheduler POSTs confirmation)
            </label>
        </div>
        <div class="form-group">
            <label class="form-label" for="se-auto-motion">Auto start motion after (s)</label>
            <input id="se-auto-motion" type="number" class="form-input"
                   value="${e.auto_start_motion_after_s ?? ''}" placeholder="leave blank to disable" min="0">
        </div>
        <p class="modal-error" id="se-error" style="display:none;"></p>
    `;

    return openModal({
        title:   isEdit ? `Edit Entry — ${e.id}` : 'Add Schedule Entry',
        bodyHTML,
        onMount(dlg) {
            const vehicleSel = dlg.querySelector('#se-vehicle');
            const routeInput = dlg.querySelector('#se-route');
            const shapeSel   = dlg.querySelector('#se-shape');

            vehicleSel.addEventListener('change', () => {
                const opt   = vehicleSel.selectedOptions[0];
                const route = opt?.dataset.route ?? VEHICLE_ROUTES[vehicleSel.value] ?? 'bUCR_L1';
                routeInput.value    = route;
                shapeSel.innerHTML  = buildShapeOptions(route, null);
            });
        },
        onSubmit(dlg) {
            const id         = dlg.querySelector('#se-id').value.trim();
            const vehicle_id = dlg.querySelector('#se-vehicle').value;
            const operator_id = dlg.querySelector('#se-operator').value.trim();
            const route_id   = dlg.querySelector('#se-route').value.trim();
            const trip_id    = dlg.querySelector('#se-trip').value.trim();
            const shape_id   = dlg.querySelector('#se-shape').value;
            const direction_id = parseInt(dlg.querySelector('#se-direction').value, 10);
            const schedule_relationship = dlg.querySelector('#se-sched-rel').value;
            const start_time = dlg.querySelector('#se-start-time').value.trim();
            const auto_confirm = dlg.querySelector('#se-auto-confirm').checked;
            const motionVal  = dlg.querySelector('#se-auto-motion').value.trim();
            const auto_start_motion_after_s = motionVal ? parseInt(motionVal, 10) : null;
            const errEl = dlg.querySelector('#se-error');

            if (!id || !vehicle_id || !operator_id || !trip_id || !shape_id || !start_time) {
                errEl.textContent = 'All required fields must be filled.';
                errEl.style.display = 'block';
                return null;
            }
            if (!isIso8601(start_time)) {
                errEl.textContent = 'start_time must be a valid ISO-8601 datetime with timezone.';
                errEl.style.display = 'block';
                return null;
            }

            errEl.style.display = 'none';
            return {
                id, vehicle_id, operator_id, route_id, trip_id, shape_id,
                direction_id, schedule_relationship, start_time,
                auto_confirm, auto_start_motion_after_s,
            };
        },
    });
}

function buildShapeOptions(routeId, currentShapeId) {
    const shapes = ROUTE_SHAPES[routeId] ?? [];
    return shapes.map(s =>
        `<option value="${s}" ${s === currentShapeId ? 'selected' : ''}>${s}</option>`
    ).join('');
}

function isIso8601(str) {
    return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)$/.test(str);
}

function esc(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
