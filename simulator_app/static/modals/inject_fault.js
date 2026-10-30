// Modal: inject fault into a vehicle's telemetry stream.
// open({ vehicleId }) → Promise<{ kind, duration_ticks } | null>

import { openModal } from './_base.js';

export function open({ vehicleId }) {
    return openModal({
        title: `Inject Fault — ${vehicleId}`,
        bodyHTML: `
            <p style="font-size:0.82rem;color:#64748b;margin:0 0 0.25rem;">
                The fault will be applied for the specified number of ticks.
            </p>
            <div class="form-group">
                <span class="form-label">Fault kind</span>
                <div class="radio-group">
                    <label class="radio-label">
                        <input type="radio" name="fault-kind" value="stale_ts" checked>
                        stale_ts — timestamp N seconds in the past
                    </label>
                    <label class="radio-label">
                        <input type="radio" name="fault-kind" value="out_of_bounds">
                        out_of_bounds — position outside geographic bounds
                    </label>
                    <label class="radio-label">
                        <input type="radio" name="fault-kind" value="malformed">
                        malformed — missing required field
                    </label>
                </div>
            </div>
            <div class="form-group">
                <label class="form-label" for="fault-ticks">Duration (ticks)</label>
                <input id="fault-ticks" type="number" class="form-input" min="1" max="60" value="3">
            </div>
        `,
        onSubmit(dlg) {
            const kind   = dlg.querySelector('input[name="fault-kind"]:checked')?.value;
            const ticks  = parseInt(dlg.querySelector('#fault-ticks').value, 10);
            if (!kind || isNaN(ticks) || ticks < 1) return null;
            return { kind, duration_ticks: ticks };
        },
    });
}
