// navsat_selector.js — Leaflet control for selecting which NavSat (real-world)
// buses appear on the map, alongside the simulated fleet.
//
// Each row shows the vendor's raw `estado` value verbatim in parentheses
// (e.g. "SJB1234 (movimiento)" / "SJB1234 (detenido)") — not reinterpreted
// or translated. Selection is in-memory only (module state), reset on reload.

const NAVSAT_MARKER_COLOR = '#7c3aed'; // distinct from sim-fleet chevrons

/**
 * Create and add a "NavSat buses" selector control to the given map.
 *
 * @param {L.Map} map
 * @param {{ get: () => object, subscribe: (cb: function) => function }} store
 *   store.get().navsat is expected to be an array of
 *   { plate_number, latitude, longitude, estado } or undefined/absent.
 */
export function initNavsatSelector(map, store) {
    const _selected = new Set(); // plate_number
    const _markers = new Map(); // plate_number → L.CircleMarker
    const _markerLayer = L.layerGroup().addTo(map);

    /** @type {HTMLDivElement|null} */
    let _panel = null;
    let _open = false;

    // ---- Build Leaflet custom control -------------------------------------

    const NavsatSelectorControl = L.Control.extend({
        options: { position: 'topright' },

        onAdd() {
            const container = L.DomUtil.create('div', 'route-highlight-control navsat-selector-control');

            const toggle = L.DomUtil.create('button', 'navsat-toggle', container);
            toggle.type = 'button';
            toggle.textContent = 'NavSat buses ▾';

            const panel = L.DomUtil.create('div', 'navsat-panel', container);
            panel.hidden = true;
            _panel = panel;

            L.DomEvent.disableClickPropagation(container);
            L.DomEvent.disableScrollPropagation(container);

            toggle.addEventListener('click', () => {
                _open = !_open;
                panel.hidden = !_open;
            });

            _renderPanel();

            return container;
        },
    });

    new NavsatSelectorControl().addTo(map);

    store.subscribe(_onStoreUpdate);
    _onStoreUpdate(store.get());

    // ---- Internal helpers ---------------------------------------------------

    /** @returns {Array<{plate_number:string, latitude:number, longitude:number, estado:string}>} */
    function _knownVehicles() {
        const navsat = store.get().navsat;
        return Array.isArray(navsat) ? navsat : [];
    }

    /** Rebuild the checkbox list from the current known-vehicle list. */
    function _renderPanel() {
        if (!_panel) return;
        const vehicles = _knownVehicles();

        // Drop selections for plates that are no longer reported.
        const knownPlates = new Set(vehicles.map(v => v.plate_number));
        for (const plate of Array.from(_selected)) {
            if (!knownPlates.has(plate)) _selected.delete(plate);
        }

        _panel.innerHTML = '';

        if (vehicles.length === 0) {
            const empty = document.createElement('p');
            empty.className = 'navsat-empty';
            empty.textContent = 'No NavSat data yet.';
            _panel.appendChild(empty);
            return;
        }

        for (const v of vehicles) {
            const label = document.createElement('label');
            label.className = 'navsat-row';

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = _selected.has(v.plate_number);
            cb.addEventListener('change', () => {
                if (cb.checked) _selected.add(v.plate_number);
                else _selected.delete(v.plate_number);
                _updateMarkers();
            });

            const text = document.createElement('span');
            text.textContent = ` ${v.plate_number} (${v.estado || 'unknown'})`;

            label.appendChild(cb);
            label.appendChild(text);
            _panel.appendChild(label);
        }
    }

    /** Add/update/remove map markers to match the current selection. */
    function _updateMarkers() {
        const vehicles = _knownVehicles();
        const byPlate = new Map(vehicles.map(v => [v.plate_number, v]));

        for (const [plate, marker] of Array.from(_markers.entries())) {
            if (!_selected.has(plate) || !byPlate.has(plate)) {
                marker.remove();
                _markers.delete(plate);
            }
        }

        for (const plate of _selected) {
            const v = byPlate.get(plate);
            if (!v || v.latitude == null || v.longitude == null) continue;

            const tooltipText = `${plate} (${v.estado || 'unknown'})`;

            if (_markers.has(plate)) {
                const marker = _markers.get(plate);
                marker.setLatLng([v.latitude, v.longitude]);
                marker.setTooltipContent(tooltipText);
            } else {
                const marker = L.circleMarker([v.latitude, v.longitude], {
                    radius: 7,
                    color: '#fff',
                    weight: 2,
                    fillColor: NAVSAT_MARKER_COLOR,
                    fillOpacity: 0.95,
                }).addTo(_markerLayer);
                marker.bindTooltip(tooltipText, { permanent: false, direction: 'top', opacity: 0.9 });
                _markers.set(plate, marker);
            }
        }
    }

    /**
     * Called on every store update (fleet/telemetry/schedule/navsat all share
     * one notify channel). Cheap rebuild, same tradeoff run_highlight.js makes
     * for its dropdown.
     *
     * @param {object} _state (unused; we read via store.get() instead)
     */
    function _onStoreUpdate(_state) {
        _renderPanel();
        _updateMarkers();
    }
}
