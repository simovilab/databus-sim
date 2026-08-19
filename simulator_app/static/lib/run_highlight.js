// run_highlight.js — Leaflet control for highlighting an active run's trip.
// Subscribes to the live fleet store, lists active runs in a dropdown, and
// draws the selected run's shape polyline + stops on the map.

import { ACTIVE_LIFECYCLE } from './lifecycle_colors.js';
import { runColor } from './run_palette.js';

/**
 * Create and add an "Active run trip" highlight control to the given map.
 *
 * @param {L.Map} map
 * @param {{ get: () => object, subscribe: (cb: function) => function }} store
 */
export function initRunHighlight(map, store) {
    // ---- Module-level state -----------------------------------------------

    /** @type {Map<string, { latlngs: Array, stops: Array }>} shapeId → geometry */
    const _shapeIndex = new Map();

    /** @type {boolean} true once /sim/geometry has been fetched (once only) */
    let _geometryLoaded = false;

    /** @type {L.LayerGroup} holds the current run-trip highlight layers */
    const _tripGroup = L.layerGroup().addTo(map);

    /**
     * Last-rendered state used to skip redundant redraws.
     * @type {{ vehicleId: string|null, shapeId: string|null, lifecycleState: string|null }}
     */
    const _lastRender = { vehicleId: null, shapeId: null, lifecycleState: null };

    // ---- Build Leaflet custom control -------------------------------------

    /** @type {HTMLSelectElement|null} */
    let _select = null;

    const RunHighlightControl = L.Control.extend({
        options: { position: 'topright' },

        onAdd() {
            const container = L.DomUtil.create('div', 'route-highlight-control run-highlight-control');

            const label = L.DomUtil.create('label', 'rhc-label', container);
            label.textContent = 'Active run trip';
            label.htmlFor = 'run-highlight-select';

            const select = L.DomUtil.create('select', 'rhc-select', container);
            select.id = 'run-highlight-select';
            _select = select;

            // Prevent map interaction from bleeding through the control.
            L.DomEvent.disableClickPropagation(container);
            L.DomEvent.disableScrollPropagation(container);

            // Default "no selection" option.
            _addOption(select, '', '— None —');

            select.addEventListener('change', () => {
                _onSelectionChange(select.value);
            });

            // Kick off geometry fetch once.
            _fetchGeometry();

            return container;
        },
    });

    new RunHighlightControl().addTo(map);

    // Subscribe to live store updates.
    store.subscribe(_onStoreUpdate);

    // ---- Internal helpers -------------------------------------------------

    /**
     * Append an <option> element to a <select>.
     *
     * @param {HTMLSelectElement} select
     * @param {string} value
     * @param {string} text
     */
    function _addOption(select, value, text) {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = text;
        select.appendChild(opt);
    }

    /**
     * Fetch /sim/geometry once and build the shapeId → {latlngs, stops} index.
     */
    async function _fetchGeometry() {
        try {
            const res = await fetch('/sim/geometry');
            if (!res.ok) {
                console.warn(`run_highlight: /sim/geometry returned HTTP ${res.status}`);
                return;
            }
            const data = await res.json();
            const routes = (data && Array.isArray(data.routes)) ? data.routes : [];

            if (routes.length === 0) {
                console.warn('run_highlight: /sim/geometry returned no routes');
                return;
            }

            for (const route of routes) {
                const shapes = Array.isArray(route.shapes) ? route.shapes : [];
                for (const shape of shapes) {
                    if (!shape.shape_id) continue;
                    _shapeIndex.set(shape.shape_id, {
                        latlngs: Array.isArray(shape.latlngs) ? shape.latlngs : [],
                        stops:   Array.isArray(shape.stops)   ? shape.stops   : [],
                    });
                }
            }

            _geometryLoaded = true;

            // Re-apply any selection that may have been made while loading.
            if (_select && _select.value) {
                _onSelectionChange(_select.value);
            }
        } catch (err) {
            console.warn('run_highlight: failed to load geometry', err);
        }
    }

    /**
     * Derive the list of active runs from the current store state.
     * An active run is a vehicle with a truthy bound_shape_id whose
     * lifecycle_state is in ACTIVE_LIFECYCLE.
     *
     * @returns {Array<{ vehicleId: string, runIdShort: string, vehicle: object }>}
     */
    function _activeRuns() {
        const vehicles = store.get().fleet?.vehicles ?? [];
        const runs = [];
        for (const v of vehicles) {
            if (v.bound_shape_id && ACTIVE_LIFECYCLE.has(v.lifecycle_state)) {
                const runIdShort = v.bound_run_id
                    ? String(v.bound_run_id).slice(0, 8)
                    : v.vehicle_id;
                runs.push({ vehicleId: v.vehicle_id, runIdShort, vehicle: v });
            }
        }
        return runs;
    }

    /**
     * Refresh the <select> options to reflect the current active-run list.
     * Preserves the user's current selection when the vehicle is still active;
     * resets to "None" (and clears layers) if it has become inactive.
     */
    function _refreshDropdown() {
        if (!_select) return;

        const prevValue = _select.value;
        const runs = _activeRuns();
        const activeVehicleIds = new Set(runs.map(r => r.vehicleId));

        // Rebuild options list.
        _select.innerHTML = '';
        _addOption(_select, '', '— None —');
        for (const run of runs) {
            _addOption(_select, run.vehicleId, `${run.vehicleId} · ${run.runIdShort}`);
        }

        if (prevValue && activeVehicleIds.has(prevValue)) {
            // Keep the previous selection.
            _select.value = prevValue;
        } else if (prevValue && !activeVehicleIds.has(prevValue)) {
            // Selected vehicle is no longer active — reset and clear.
            _select.value = '';
            _clearTripLayers();
            _resetLastRender();
        }
        // If prevValue was '' we leave it as '' (already the first option).
    }

    /**
     * Called on dropdown change. Triggers a trip redraw for the selected
     * vehicle (or clears layers when "None").
     *
     * @param {string} vehicleId
     */
    function _onSelectionChange(vehicleId) {
        if (!vehicleId) {
            _clearTripLayers();
            _resetLastRender();
            return;
        }
        _drawTrip(vehicleId, /* force= */ true);
    }

    /**
     * Called on every store update.  Refreshes the dropdown (cheap) and
     * conditionally redraws the active-run trip only when something material
     * changed — guards against per-tick churn (~200 ms updates).
     *
     * @param {object} _state  (unused; we read via store.get() instead)
     */
    function _onStoreUpdate(_state) {
        _refreshDropdown();

        const selected = _select ? _select.value : '';
        if (!selected) return;

        _drawTrip(selected, /* force= */ false);
    }

    /**
     * Draw (or re-draw when something changed) the trip for the given vehicleId.
     * When force=false the render is skipped unless vehicleId, shapeId, or
     * lifecycleState differ from the last render.
     *
     * @param {string}  vehicleId
     * @param {boolean} force
     */
    function _drawTrip(vehicleId, force) {
        const vehicles = store.get().fleet?.vehicles ?? [];
        const vehicle  = vehicles.find(v => v.vehicle_id === vehicleId);

        if (!vehicle) {
            _clearTripLayers();
            _resetLastRender();
            return;
        }

        const shapeId       = vehicle.bound_shape_id;
        const lifecycleState = vehicle.lifecycle_state;

        // Guard: skip if nothing material has changed and force is false.
        if (!force &&
            _lastRender.vehicleId === vehicleId &&
            _lastRender.shapeId === shapeId &&
            _lastRender.lifecycleState === lifecycleState) {
            return;
        }

        if (!shapeId) {
            _clearTripLayers();
            _resetLastRender();
            return;
        }

        if (!_geometryLoaded) {
            // Geometry not yet available; will retry once fetch completes.
            return;
        }

        const geometry = _shapeIndex.get(shapeId);
        if (!geometry) {
            console.warn(`run_highlight: no geometry for shape_id "${shapeId}"`);
            _clearTripLayers();
            _resetLastRender();
            return;
        }

        // Update last-render tracker before drawing.
        _lastRender.vehicleId     = vehicleId;
        _lastRender.shapeId       = shapeId;
        _lastRender.lifecycleState = lifecycleState;

        _applyTripLayers(geometry, runColor(vehicleId));
    }

    /**
     * Clear existing trip layers and draw new polyline + stop markers for the
     * given geometry in the given color.
     *
     * @param {{ latlngs: Array, stops: Array }} geometry
     * @param {string} color  hex color
     */
    function _applyTripLayers(geometry, color) {
        _clearTripLayers();

        const { latlngs, stops } = geometry;

        if (Array.isArray(latlngs) && latlngs.length > 0) {
            L.polyline(latlngs, {
                color,
                weight:  3,
                opacity: 0.95,
                interactive: false,
            }).addTo(_tripGroup);
        }

        const safeStops = Array.isArray(stops) ? stops : [];
        for (const stop of safeStops) {
            if (stop.lat == null || stop.lon == null) continue;
            L.circleMarker([stop.lat, stop.lon], {
                radius:      4,
                color:       '#fff',
                weight:      2,
                fillColor:   color,
                fillOpacity: 1,
            })
                .bindTooltip(
                    `${stop.stop_id} — ${stop.name} · seq ${stop.stop_sequence}`,
                    { permanent: false, direction: 'top', opacity: 0.95 },
                )
                .addTo(_tripGroup);
        }
    }

    /** Remove all layers from the trip group. */
    function _clearTripLayers() {
        _tripGroup.clearLayers();
    }

    /** Reset the last-render tracker to a clean state. */
    function _resetLastRender() {
        _lastRender.vehicleId      = null;
        _lastRender.shapeId        = null;
        _lastRender.lifecycleState = null;
    }
}
