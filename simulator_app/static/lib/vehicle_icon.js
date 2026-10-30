// vehicle_icon.js — shared Leaflet divIcon builder for bearing-rotated
// directional vehicle arrows. Used by both the simulated fleet markers
// (app.js) and the NavSat overlay markers (navsat_selector.js) so the two
// look and rotate identically.

/**
 * Build a Leaflet divIcon that renders a bearing-rotated directional arrow
 * (inline SVG chevron). Clearly larger than a stop dot (22×22 px vs radius-4
 * stop circles) and visually distinct via shape, dark outline, and white
 * drop-shadow halo.
 *
 * @param {string}      color    hex fill color
 * @param {number|null} bearing  travel direction in degrees clockwise from north;
 *                               null/undefined falls back to 0 (pointing up)
 * @returns {L.DivIcon}
 */
export function makeArrowIcon(color, bearing) {
    const deg = (bearing != null && isFinite(bearing)) ? bearing : 0;
    // Upward-pointing filled arrowhead path in a 22×22 viewBox.
    // Centered at (11,11); tip at top, base at bottom with a small notch.
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">`
        + `<path d="M11 2 L18 18 L11 14 L4 18 Z"`
        + ` fill="${color}"`
        + ` stroke="#1e293b"`
        + ` stroke-width="1.5"`
        + ` stroke-linejoin="round"/>`
        + `</svg>`;
    return L.divIcon({
        className: '',
        html: `<div style="width:22px;height:22px;transform:rotate(${deg}deg);filter:drop-shadow(0 0 2px #fff) drop-shadow(0 1px 3px rgba(0,0,0,.55));">${svg}</div>`,
        iconSize:    [22, 22],
        iconAnchor:  [11, 11],
        tooltipAnchor: [11, -11],
    });
}

/**
 * Great-circle initial bearing from (lat1,lon1) to (lat2,lon2), in degrees
 * clockwise from north. Mirrors simulator_app/domain/kinematics.py:bearing_deg
 * so NavSat's client-computed heading matches the simulated fleet's convention.
 *
 * @param {number} lat1
 * @param {number} lon1
 * @param {number} lat2
 * @param {number} lon2
 * @returns {number} degrees in [0, 360)
 */
export function bearingDeg(lat1, lon1, lat2, lon2) {
    const p1 = lat1 * Math.PI / 180;
    const p2 = lat2 * Math.PI / 180;
    const dl = (lon2 - lon1) * Math.PI / 180;
    const x = Math.sin(dl) * Math.cos(p2);
    const y = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    return ((Math.atan2(x, y) * 180 / Math.PI) + 360) % 360;
}
