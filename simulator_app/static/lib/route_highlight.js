// route_highlight.js — Leaflet control for highlighting a selected route.
// Fetches /sim/geometry once, populates a dropdown, and draws the chosen
// route's polylines + stop circle-markers on demand.

/**
 * Create and add a "Highlight route" control to the given Leaflet map.
 *
 * @param {L.Map} map
 */
export function initRouteHighlight(map) {
    // Module-level state (one instance per page load).
    /** @type {Map<string, object>} routeId → route object from /sim/geometry */
    const _routes = new Map();

    /** @type {L.LayerGroup} holds the current highlight layers */
    const _highlightGroup = L.layerGroup().addTo(map);

    // ---- Build Leaflet custom control -------------------------------------

    const RouteHighlightControl = L.Control.extend({
        options: { position: 'topright' },

        onAdd() {
            const container = L.DomUtil.create('div', 'route-highlight-control');

            const label = L.DomUtil.create('label', 'rhc-label', container);
            label.textContent = 'Highlight route';
            label.htmlFor = 'rhc-select';

            const select = L.DomUtil.create('select', 'rhc-select', container);
            select.id = 'rhc-select';

            // Prevent map clicks/scroll from firing through the control.
            L.DomEvent.disableClickPropagation(container);
            L.DomEvent.disableScrollPropagation(container);

            // Default "no selection" option.
            _addOption(select, '', '— None —');

            select.addEventListener('change', () => {
                _applyHighlight(select.value);
            });

            // Kick off geometry fetch; populate the select once done.
            _fetchGeometry(select);

            return container;
        },
    });

    new RouteHighlightControl().addTo(map);

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
     * Fetch /sim/geometry and populate the dropdown.
     *
     * @param {HTMLSelectElement} select
     */
    async function _fetchGeometry(select) {
        try {
            const res = await fetch('/sim/geometry');
            if (!res.ok) {
                console.warn(`route_highlight: /sim/geometry returned HTTP ${res.status}`);
                return;
            }
            const data = await res.json();
            const routes = (data && Array.isArray(data.routes)) ? data.routes : [];

            if (routes.length === 0) {
                console.warn('route_highlight: /sim/geometry returned no routes');
                return;
            }

            for (const route of routes) {
                _routes.set(route.route_id, route);
                _addOption(select, route.route_id, route.short_name || route.route_id);
            }
        } catch (err) {
            console.warn('route_highlight: failed to load geometry', err);
        }
    }

    /**
     * Clear existing highlight layers and (if routeId is non-empty) draw the
     * selected route's shapes and stops.
     *
     * @param {string} routeId
     */
    function _applyHighlight(routeId) {
        _highlightGroup.clearLayers();

        if (!routeId) return;

        const route = _routes.get(routeId);
        if (!route) return;

        const color = '#' + (route.color || '3b82f6');

        // Draw each shape as a bold polyline.
        const shapes = Array.isArray(route.shapes) ? route.shapes : [];
        for (const shape of shapes) {
            if (!Array.isArray(shape.latlngs) || shape.latlngs.length === 0) continue;
            L.polyline(shape.latlngs, {
                color,
                weight: 3,
                opacity: 0.9,
                interactive: false,
            }).addTo(_highlightGroup);
        }

        // Draw each unique stop as a circle marker with a hover tooltip.
        const stops = Array.isArray(route.stops) ? route.stops : [];
        for (const stop of stops) {
            if (stop.lat == null || stop.lon == null) continue;
            L.circleMarker([stop.lat, stop.lon], {
                radius: 4,
                color,
                weight: 2,
                fillColor: '#fff',
                fillOpacity: 1,
            })
                .bindTooltip(`${stop.stop_id} — ${stop.name}`, {
                    permanent: false,
                    direction: 'top',
                    opacity: 0.95,
                })
                .addTo(_highlightGroup);
        }
    }
}
