// ws_client.js — WebSocket transport replacing mqtt_client.js.
//
// Exposes the SAME interface shape that app.js uses today:
//   connectMqtt(opts) → { subscribe(topic, cb), publish(topic, payload), end() }
//
// Internally it opens ONE socket to ws[s]://<host>/ws/fleet/ and fans
// inbound {type, payload, ...} messages out to subscribers registered
// under the equivalent old MQTT topic strings.
//
// publish(topic, payload) maps sim/control/* topics to POST /sim/control/*
// fetches — no MQTT wire on the browser side.
//
// Reconnect: exponential back-off starting at 2 s (mirrors old reconnectPeriod).

// ---------------------------------------------------------------------------
// Control topic → POST path mapping
// ---------------------------------------------------------------------------

const CONTROL_TOPIC_RE = /^sim\/control\/(.+)$/;

/**
 * Translate an old MQTT sim/control topic into a fetch POST path.
 * Returns null if the topic is not a control topic.
 *
 * @param {string} topic
 * @returns {string|null}
 */
function controlPath(topic) {
    const m = topic.match(CONTROL_TOPIC_RE);
    if (!m) return null;
    // m[1] is either "global/<knob>" or "<vehicle_id>/<knob>"
    return `/sim/control/${m[1]}`;
}

// ---------------------------------------------------------------------------
// WebSocket message → old MQTT topic routing
// ---------------------------------------------------------------------------

/**
 * Given a WS envelope from the server, return an array of
 * { topic, payload } pairs that should be dispatched to MQTT-style subscribers.
 *
 * @param {{ type: string, payload?: unknown, vehicle_id?: string, leaf?: string }} msg
 * @returns {{ topic: string, payload: unknown }[]}
 */
function routeMessage(msg) {
    if (!msg || typeof msg !== 'object') return [];

    switch (msg.type) {
        case 'fleet':
            // old: sim/state/fleet → payload = fleet snapshot object
            return [{ topic: 'sim/state/fleet', payload: msg.payload }];

        case 'schedule': {
            // old: sim/state/schedule → payload had { entries: [...] }
            // new: payload has { defaults, runs: [{...entry, status, bound_run_id}] }
            // Normalise so tabs/schedule.js (which reads state.schedule.entries)
            // works without modification.
            const raw = msg.payload ?? {};
            const normalised = {
                ...raw,
                // Map "runs" → "entries" so the store's schedule.entries key
                // remains valid for the schedule tab status-column updater.
                entries: raw.runs ?? raw.entries ?? [],
            };
            return [{ topic: 'sim/state/schedule', payload: normalised }];
        }

        case 'telemetry':
            // old: transit/vehicle/<id>/<leaf> → payload = leaf data
            if (msg.vehicle_id && msg.leaf) {
                return [{
                    topic: `transit/vehicle/${msg.vehicle_id}/${msg.leaf}`,
                    payload: msg.payload,
                }];
            }
            return [];

        default:
            return [];
    }
}

// ---------------------------------------------------------------------------
// MQTT wildcard topic matcher (ported from mqtt_client.js)
// ---------------------------------------------------------------------------

/**
 * Returns true if `topic` matches the MQTT wildcard `pattern`.
 * Supports '+' (single level) and '#' (multi level, must be last).
 *
 * @param {string} pattern
 * @param {string} topic
 * @returns {boolean}
 */
function topicMatches(pattern, topic) {
    const ps = pattern.split('/');
    const ts = topic.split('/');
    for (let i = 0; i < ps.length; i++) {
        if (ps[i] === '#') return true;
        if (ps[i] === '+') {
            if (i >= ts.length) return false;
            continue;
        }
        if (ps[i] !== ts[i]) return false;
    }
    return ps.length === ts.length;
}

// ---------------------------------------------------------------------------
// connectMqtt-compatible factory
// ---------------------------------------------------------------------------

/**
 * @param {{ onStatus?: (connected: boolean) => void }} [opts]
 * @returns {{ subscribe(topic: string, cb: Function): () => void,
 *             publish(topic: string, payload: unknown): void,
 *             end(): void }}
 */
export function connectMqtt(opts = {}) {
    const subs = new Map(); // pattern → Set<callback>

    let ws = null;
    let _dead = false;
    let _backoff = 2000; // ms; doubles on each failure, capped at 30 s

    function wsUrl() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        return `${proto}://${location.host}/ws/fleet/`;
    }

    function dispatch(topic, payload) {
        for (const [pattern, callbacks] of subs.entries()) {
            if (topicMatches(pattern, topic)) {
                callbacks.forEach(cb => {
                    try { cb(topic, payload); }
                    catch (e) { console.error('ws_client subscriber error', e); }
                });
            }
        }
    }

    function connect() {
        if (_dead) return;

        ws = new WebSocket(wsUrl());

        ws.addEventListener('open', () => {
            _backoff = 2000; // reset on success
            opts.onStatus?.(true);
        });

        ws.addEventListener('message', ev => {
            let msg;
            try { msg = JSON.parse(ev.data); }
            catch { console.warn('ws_client: non-JSON frame', ev.data); return; }

            for (const { topic, payload } of routeMessage(msg)) {
                dispatch(topic, payload);
            }
        });

        ws.addEventListener('close', () => {
            opts.onStatus?.(false);
            if (!_dead) {
                setTimeout(connect, _backoff);
                _backoff = Math.min(_backoff * 2, 30_000);
            }
        });

        ws.addEventListener('error', err => {
            console.error('ws_client WebSocket error', err);
            // 'close' fires after 'error', so reconnect is handled there.
        });
    }

    connect();

    return {
        /**
         * Subscribe to an MQTT-style topic pattern (supports +, #).
         * Returns an unsubscribe function.
         */
        subscribe(topic, cb) {
            if (!subs.has(topic)) subs.set(topic, new Set());
            subs.get(topic).add(cb);
            return () => {
                const set = subs.get(topic);
                set?.delete(cb);
                if (set && set.size === 0) subs.delete(topic);
            };
        },

        /**
         * Publish a control command.
         * sim/control/* topics → POST /sim/control/...
         * All other topics are silently ignored (no MQTT wire here).
         */
        publish(topic, payload) {
            const path = controlPath(topic);
            if (!path) {
                console.warn('ws_client: publish to non-control topic ignored', topic);
                return;
            }
            fetch(path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload ?? {}),
            }).catch(err => console.error('ws_client control POST failed', err));
        },

        /** Tear down the WebSocket permanently (no reconnect). */
        end() {
            _dead = true;
            ws?.close();
        },
    };
}
