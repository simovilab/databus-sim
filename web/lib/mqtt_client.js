// MQTT-WS client wrapper around MQTT.js (loaded globally from CDN as `mqtt`).
// See CONTRACTS.md §1 for the topic protocol.

const DEFAULT_WS_URL = () =>
    `ws://${location.hostname || 'localhost'}:8083/mqtt`;

/**
 * @param {{ url?: string, onStatus?: (connected: boolean) => void }} [opts]
 */
export function connectMqtt(opts = {}) {
    const url = opts.url || DEFAULT_WS_URL();
    const client = mqtt.connect(url, { reconnectPeriod: 2000 });
    const subs = new Map(); // topic -> Set<callback>

    client.on('connect', () => opts.onStatus?.(true));
    client.on('close', () => opts.onStatus?.(false));
    client.on('error', (err) => console.error('mqtt error', err));

    client.on('message', (topic, payload) => {
        let parsed = null;
        try {
            parsed = JSON.parse(payload.toString());
        } catch {
            parsed = payload.toString();
        }
        for (const [pattern, callbacks] of subs.entries()) {
            if (topicMatches(pattern, topic)) {
                callbacks.forEach((cb) => {
                    try {
                        cb(topic, parsed);
                    } catch (err) {
                        console.error('mqtt callback error', err);
                    }
                });
            }
        }
    });

    return {
        client,
        subscribe(topic, cb) {
            if (!subs.has(topic)) {
                subs.set(topic, new Set());
                client.subscribe(topic, { qos: 0 });
            }
            subs.get(topic).add(cb);
            return () => {
                const set = subs.get(topic);
                set?.delete(cb);
                if (set && set.size === 0) {
                    client.unsubscribe(topic);
                    subs.delete(topic);
                }
            };
        },
        publish(topic, payload, opts = {}) {
            const body = typeof payload === 'string' ? payload : JSON.stringify(payload);
            client.publish(topic, body, { qos: 0, retain: false, ...opts });
        },
        end() {
            client.end();
        },
    };
}

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
