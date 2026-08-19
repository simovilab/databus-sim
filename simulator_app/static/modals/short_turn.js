// Modal: short-turn a run at an early stop.
// open({ runId }) → Promise<{ stop_id: string } | null>

import { openModal } from './_base.js';

export function open({ runId }) {
    return openModal({
        title: 'Short-turn Run',
        bodyHTML: `
            <p style="font-size:0.82rem;color:#64748b;margin:0 0 0.25rem;">
                End the run early at a stop before the terminal.
                The stop must appear in the trip's stop sequence and must not be the terminal stop.
            </p>
            <div class="form-group">
                <label class="form-label" for="short-turn-stop">Short-turn stop ID</label>
                <input id="short-turn-stop" type="text" class="form-input" placeholder="e.g. bUCR_LB" required>
                <span class="form-hint">Run ID: ${runId?.slice(0, 16)}...</span>
            </div>
        `,
        onSubmit(dlg) {
            const stop_id = dlg.querySelector('#short-turn-stop').value.trim();
            if (!stop_id) return null;
            return { stop_id };
        },
    });
}
