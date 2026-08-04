// map_fullscreen.js — Leaflet control toggling the native Fullscreen API on
// the map container, so the map can be viewed without the surrounding UI.

/**
 * Create and add a fullscreen-toggle control to the given map.
 *
 * @param {L.Map} map
 */
export function initMapFullscreen(map) {
    const container = map.getContainer();

    const FullscreenControl = L.Control.extend({
        options: { position: 'topleft' },

        onAdd() {
            const wrapper = L.DomUtil.create('div', 'leaflet-bar map-fullscreen-control');
            const link = L.DomUtil.create('a', 'map-fullscreen-btn', wrapper);
            link.href = '#';
            link.title = 'View map fullscreen';
            link.setAttribute('role', 'button');
            link.textContent = '⛶';

            L.DomEvent.disableClickPropagation(wrapper);

            link.addEventListener('click', ev => {
                ev.preventDefault();
                _toggle();
            });

            document.addEventListener('fullscreenchange', () => {
                const active = document.fullscreenElement === container;
                link.textContent = active ? '⤢' : '⛶';
                link.title = active ? 'Exit fullscreen' : 'View map fullscreen';
                // Leaflet must recompute tile layout for the new container size.
                setTimeout(() => map.invalidateSize(), 50);
            });

            return wrapper;
        },
    });

    new FullscreenControl().addTo(map);

    function _toggle() {
        if (document.fullscreenElement === container) {
            document.exitFullscreen?.();
            return;
        }
        if (!container.requestFullscreen) {
            console.warn('map_fullscreen: Fullscreen API not supported in this browser');
            return;
        }
        container.requestFullscreen().catch(err => {
            console.warn('map_fullscreen: requestFullscreen failed', err);
        });
    }
}
