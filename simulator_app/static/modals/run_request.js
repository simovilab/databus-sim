// Modal: on-demand run request form.
// open({ vehicles, databusClient }) → Promise<createRunPayload | null>

import { openModal } from './_base.js';

const ROUTE_SHAPES = {
    bUCR_L1: ['hacia_artes', 'hacia_educacion', 'desde_artes_sin_milla', 'desde_educacion_sin_milla'],
    bUCR_L2: ['desde_artes_con_milla', 'desde_educacion_con_milla'],
};

const VEHICLE_ROUTES = {
    'unit-01': 'bUCR_L1', 'unit-02': 'bUCR_L1', 'unit-03': 'bUCR_L1',
    'unit-04': 'bUCR_L2', 'unit-05': 'bUCR_L2', 'unit-06': 'bUCR_L2',
};

export async function open({ vehicles, databusClient }) {
    // Fetch trips in background — non-blocking, failures degrade gracefully
    let trips = [];
    try {
        trips = await databusClient.listTrips();
    } catch { /* offline — user types trip_id manually */ }

    const vehicleOptions = vehicles.map(v =>
        `<option value="${v.vehicle_id}" data-route="${v.route_id}">${v.vehicle_id} (${v.route_id})</option>`
    ).join('');

    const defaultVehicle = vehicles[0]?.vehicle_id ?? 'unit-01';
    const defaultRoute   = VEHICLE_ROUTES[defaultVehicle] ?? 'bUCR_L1';
    const defaultShapes  = buildShapeOptions(defaultRoute, null);

    const tripsByRoute = {};
    for (const t of trips) {
        const r = t.route_id ?? 'unknown';
        (tripsByRoute[r] ||= []).push(t);
    }
    const tripOptions = (routeId) => {
        const list = tripsByRoute[routeId] ?? [];
        if (list.length === 0) return '<option value="">— no trips for this route —</option>';
        return list.map(t =>
            `<option value="${t.trip_id}" data-shape="${t.shape_id ?? ''}" data-direction="${t.direction_id ?? 0}">${t.trip_id}</option>`
        ).join('');
    };

    const bodyHTML = `
        <div class="form-group">
            <label class="form-label" for="rr-vehicle">Vehicle</label>
            <select id="rr-vehicle" class="form-select">${vehicleOptions}</select>
        </div>
        <div class="form-group">
            <label class="form-label" for="rr-route">Route</label>
            <input id="rr-route" type="text" class="form-input" value="${defaultRoute}" readonly>
        </div>
        <div class="form-group">
            <label class="form-label" for="rr-trip">Trip</label>
            <select id="rr-trip" class="form-select" required>${tripOptions(defaultRoute)}</select>
            ${trips.length === 0 ? '<small style="color:#dc2626">Could not fetch trips from databus.</small>' : ''}
        </div>
        <div class="form-group">
            <label class="form-label" for="rr-shape">Shape (auto from trip)</label>
            <input id="rr-shape" type="text" class="form-input" readonly>
        </div>
        <div class="form-group">
            <label class="form-label" for="rr-direction">Direction (auto from trip)</label>
            <input id="rr-direction" type="text" class="form-input" readonly>
        </div>
        <div class="form-group">
            <label class="form-label" for="rr-sched-rel">Schedule relationship</label>
            <select id="rr-sched-rel" class="form-select">
                <option value="SCHEDULED" selected>SCHEDULED</option>
                <option value="ADDED">ADDED</option>
                <option value="UNSCHEDULED">UNSCHEDULED</option>
            </select>
        </div>
        <div class="form-group">
            <label class="form-label" for="rr-operator">Operator ID</label>
            <input id="rr-operator" type="text" class="form-input" placeholder="op-001" value="op-001" required>
        </div>
    `;

    return openModal({
        title:    'Request Run',
        bodyHTML,
        onMount(dlg) {
            const vehicleSel  = dlg.querySelector('#rr-vehicle');
            const routeInput  = dlg.querySelector('#rr-route');
            const tripSel     = dlg.querySelector('#rr-trip');
            const shapeInput  = dlg.querySelector('#rr-shape');
            const dirInput    = dlg.querySelector('#rr-direction');

            function syncFromTrip() {
                const opt = tripSel.selectedOptions[0];
                shapeInput.value = opt?.dataset.shape ?? '';
                dirInput.value   = opt?.dataset.direction ?? '';
            }
            function syncFromVehicle() {
                const opt = vehicleSel.selectedOptions[0];
                const route = opt?.dataset.route ?? VEHICLE_ROUTES[vehicleSel.value] ?? 'bUCR_L1';
                routeInput.value = route;
                tripSel.innerHTML = tripOptions(route);
                syncFromTrip();
            }

            vehicleSel.addEventListener('change', syncFromVehicle);
            tripSel.addEventListener('change', syncFromTrip);
            syncFromTrip();
        },
        onSubmit(dlg) {
            const vehicle_id  = dlg.querySelector('#rr-vehicle').value;
            const route_id    = dlg.querySelector('#rr-route').value.trim();
            const trip_id     = dlg.querySelector('#rr-trip').value.trim();
            const shape_id    = dlg.querySelector('#rr-shape').value.trim();
            const direction_id = parseInt(dlg.querySelector('#rr-direction').value, 10);
            const schedule_relationship = dlg.querySelector('#rr-sched-rel').value;
            const operator_id = dlg.querySelector('#rr-operator').value.trim();

            if (!vehicle_id || !trip_id || !shape_id || !operator_id || !route_id || isNaN(direction_id)) {
                return null;
            }

            return { vehicle_id, operator_id, route_id, trip_id, direction_id, shape_id, schedule_relationship };
        },
    });
}

function buildShapeOptions(routeId, currentShapeId) {
    const shapes = ROUTE_SHAPES[routeId] ?? [];
    return shapes.map(s =>
        `<option value="${s}" ${s === currentShapeId ? 'selected' : ''}>${s}</option>`
    ).join('');
}
