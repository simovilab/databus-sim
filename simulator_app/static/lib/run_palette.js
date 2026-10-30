// run_palette.js — stable per-vehicle color palette for map markers and run-trip
// highlights.  Each vehicle consistently gets the same color across the whole app.

/**
 * 6 visually distinct, high-contrast colors — one per unit (unit-01 … unit-06).
 * Deliberately avoids pure green (#16a34a) which is already used for lifecycle
 * "In Progress" chips, and tuned for reasonable colorblind legibility
 * (red/blue/orange/purple/teal/brown are separable in most CVD simulations).
 *
 * @type {string[]}
 */
export const RUN_PALETTE = [
    '#e6194B', // unit-01 — vivid red
    '#4363d8', // unit-02 — strong blue
    '#f58231', // unit-03 — orange
    '#911eb4', // unit-04 — purple
    '#008080', // unit-05 — teal
    '#9A6324', // unit-06 — brown
];

/**
 * Return a stable hex color for a vehicle id.
 *
 * Mapping rules (deterministic, never changes for a given id):
 *  - If the id matches the pattern "unit-NN" (e.g. "unit-01", "unit-06"),
 *    use index (NN - 1) % palette.length.
 *  - Otherwise hash the character codes of the id string and map to an index.
 *
 * @param {string} vehicleId
 * @returns {string}  hex color from RUN_PALETTE
 */
export function runColor(vehicleId) {
    const unitMatch = /^unit-(\d+)$/.exec(vehicleId);
    if (unitMatch) {
        const n = parseInt(unitMatch[1], 10);
        return RUN_PALETTE[(n - 1) % RUN_PALETTE.length];
    }

    // Generic string hash → palette index.
    let hash = 0;
    for (let i = 0; i < vehicleId.length; i++) {
        hash = (hash * 31 + vehicleId.charCodeAt(i)) >>> 0; // keep unsigned 32-bit
    }
    return RUN_PALETTE[hash % RUN_PALETTE.length];
}
