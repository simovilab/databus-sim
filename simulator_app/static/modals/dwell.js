// Modal: force a dwell (stop) at a given stop for N ticks.
// open({ vehicleId }) → Promise<{ stop_id: string|null, ticks: number } | null>

import { openModal } from './_base.js';

export function open({ vehicleId }) {
    return openModal({
        title: `Dwell — ${vehicleId}`,
        bodyHTML: `
            <p style="font-size:0.82rem;color:#64748b;margin:0 0 0.25rem;">
                Forces the vehicle to stop and dwell at the given stop.
                Leave stop ID blank to dwell at the current position.
            </p>
            <div class="form-group">
                <label class="form-label" for="dwell-stop">Stop ID (optional)</label>
                <input id="dwell-stop" type="text" class="form-input" placeholder="e.g. bUCR_LA">
                <span class="form-hint">Leave blank to dwell at current stop.</span>
            </div>
            <div class="form-group">
                <label class="form-label" for="dwell-ticks">Duration (ticks)</label>
                <input id="dwell-ticks" type="number" class="form-input" min="1" max="120" value="5">
            </div>
        `,
        onSubmit(dlg) {
            const stop_id = dlg.querySelector('#dwell-stop').value.trim() || null;
            const ticks   = parseInt(dlg.querySelector('#dwell-ticks').value, 10);
            if (isNaN(ticks) || ticks < 1) return null;
            return { stop_id, ticks };
        },
    });
}
