// lifecycle_colors.js — shared lifecycle-state color constants.
// Single source of truth used by app.js and run_highlight.js.
//
// NOTE: lifecycle-state strings are the canonical backend values, which contain
// SPACES ("In Progress", "No Signal", "Short Turned") — see run_binder.py and
// runs.js/operator.js. Keys here must match those exactly or lookups silently miss.

/**
 * Map of lifecycle state name → hex color string.
 * @type {Object<string, string>}
 */
export const MARKER_COLORS = {
    Confirmed:        '#64748b',
    Tracking:         '#f59e0b',
    'In Progress':    '#16a34a',
    'No Signal':      '#f97316',
    Completed:        '#cbd5e1',
    Cancelled:        '#cbd5e1',
    Interrupted:      '#cbd5e1',
    'Short Turned':   '#cbd5e1',
};

/**
 * Return the hex color for a given lifecycle state, falling back to a neutral
 * grey for unrecognised states.
 *
 * @param {string} state
 * @returns {string}
 */
export function markerColor(state) {
    return MARKER_COLORS[state] ?? '#94a3b8';
}

/**
 * Set of lifecycle states that are considered "active" for a run/trip.
 * @type {Set<string>}
 */
export const ACTIVE_LIFECYCLE = new Set(['Confirmed', 'Tracking', 'In Progress', 'No Signal']);
