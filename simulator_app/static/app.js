// app.js — boot script for the SIMOVI simulator control panel.
// Wires the Django Channels WebSocket, the reactive store, map pane, and tab router.

import { connectMqtt } from './lib/ws_client.js';
import { createSimClient } from './lib/sim_api.js';
import { initRouteHighlight } from './lib/route_highlight.js';
import { initRunHighlight } from './lib/run_highlight.js';
import { initNavsatSelector } from './lib/navsat_selector.js';
import { initMapFullscreen } from './lib/map_fullscreen.js';
import { runColor } from './lib/run_palette.js';
import * as fleetTab    from './tabs/fleet.js';
import * as scheduleTab from './tabs/schedule.js';
import * as operatorTab from './tabs/operator.js';
import * as runsTab     from './tabs/runs.js';

// ---- Reactive store -----------------------------------------------------

const _state = {
    fleet: { vehicles: [] },
    schedule: { entries: [] },
    telemetry: {}, // vehicle_id → { position?, progression?, occupancy? }
    navsat: [], // [{ plate_number, latitude, longitude, estado }] — optional overlay
};

const _subs = new Set();

function _notify() {
    _subs.forEach(fn => {
        try { fn(_state); } catch (e) { console.error('store subscriber error', e); }
    });
}

export const store = {
    get: () => _state,
    subscribe(cb) {
        _subs.add(cb);
        return () => _subs.delete(cb);
    },
};

// ---- Controls -----------------------------------------------------------

let _wsClient = null;

export const controls = {
    publish(topic, payload) {
        _wsClient?.publish(topic, payload);
    },
};

// ---- Tab routing --------------------------------------------------------

const TABS = { fleet: fleetTab, schedule: scheduleTab, operator: operatorTab, runs: runsTab };
let _currentTabName = null;

function switchTab(name) {
    if (!(name in TABS)) return;

    if (_currentTabName && _currentTabName !== name) {
        const prevPanel = document.querySelector(`[data-tab-panel="${_currentTabName}"]`);
        TABS[_currentTabName]?.unmount?.(prevPanel);
        if (prevPanel) {
            prevPanel.hidden = true;
            prevPanel.classList.remove('tab-panel--active');
        }
    }

    const panel = document.querySelector(`[data-tab-panel="${name}"]`);
    if (!panel) return;
    panel.hidden = false;
    panel.classList.add('tab-panel--active');

    const ctx = { store, controls, map: _map };
    TABS[name].mount(panel, ctx);
    _currentTabName = name;

    document.querySelectorAll('#tab-strip .tab').forEach(btn => {
        const active = btn.dataset.tab === name;
        btn.classList.toggle('tab--active', active);
        btn.setAttribute('aria-selected', String(active));
    });
}

// ---- Map ----------------------------------------------------------------

let _map = null;
const _vehicleMarkers = new Map(); // vehicle_id → L.Marker

/**
 * Build a Leaflet divIcon that renders a bearing-rotated directional arrow
 * (inline SVG chevron) for a vehicle marker.  Clearly larger than a stop dot
 * (22×22 px vs radius-4 stop circles) and visually distinct via shape, dark
 * outline, and white drop-shadow halo.
 *
 * @param {string}      color    hex fill color (from runColor)
 * @param {number|null} bearing  travel direction in degrees clockwise from north;
 *                               null/undefined falls back to 0 (pointing up)
 * @returns {L.DivIcon}
 */
function makeIcon(color, bearing) {
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

function initMap() {
    _map = L.map('map', { zoomControl: true }).setView([9.9365, -84.0511], 16);
    // CARTO Positron — a designed monochrome basemap (soft grays, light labels),
    // not a raw OSM tile with a grayscale filter slapped on top.
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 20,
        subdomains: 'abcd',
        attribution: '&copy; OpenStreetMap contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    }).addTo(_map);
}

function updateMapVehicle(vehicleId, transmitting, lifecycleState, position) {
    if (!_map) return;

    if (!transmitting) {
        const marker = _vehicleMarkers.get(vehicleId);
        if (marker) { marker.remove(); _vehicleMarkers.delete(vehicleId); }
        return;
    }

    if (!position) return;
    const { latitude, longitude, bearing } = position;
    if (latitude == null || longitude == null) return;

    const icon = makeIcon(runColor(vehicleId), bearing);

    if (_vehicleMarkers.has(vehicleId)) {
        const m = _vehicleMarkers.get(vehicleId);
        m.setLatLng([latitude, longitude]);
        m.setIcon(icon);
    } else {
        const m = L.marker([latitude, longitude], { icon }).addTo(_map);
        m.bindTooltip(vehicleId, { permanent: false, direction: 'top', opacity: 0.9 });
        _vehicleMarkers.set(vehicleId, m);
    }
}

// ---- Status badges -------------------------------------------------------

function setBadge(id, on, label) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = label;
    el.className = `badge ${on ? 'badge--on' : 'badge--off'}`;
}

// ---- Sim health probe ---------------------------------------------------

const _simClient = createSimClient();

function probeSimHealth() {
    _simClient.getFleet()
        .then(() => setBadge('sim-status', true, 'sim: online'))
        .catch(() => setBadge('sim-status', false, 'sim: offline'));
}

// ---- Main ---------------------------------------------------------------

async function main() {
    initMap();
    initRouteHighlight(_map);
    initRunHighlight(_map, store);
    initNavsatSelector(_map, store);
    initMapFullscreen(_map);

    const ws = connectMqtt({
        onStatus(connected) {
            setBadge('broker-status', connected, `broker: ${connected ? 'connected' : 'disconnected'}`);
        },
    });
    _wsClient = ws;

    // sim/state/fleet → store + map
    ws.subscribe('sim/state/fleet', (_topic, payload) => {
        if (!payload || typeof payload !== 'object') return;
        _state.fleet = payload;
        _notify();
        for (const v of _state.fleet.vehicles || []) {
            const pos = _state.telemetry[v.vehicle_id]?.position;
            updateMapVehicle(v.vehicle_id, v.transmitting, v.lifecycle_state, pos);
        }
    });

    // sim/state/navsat → store (optional overlay; array of {plate_number, latitude, longitude, estado})
    ws.subscribe('sim/state/navsat', (_topic, payload) => {
        if (!Array.isArray(payload)) return;
        _state.navsat = payload;
        _notify();
    });

    // sim/state/schedule → store
    // ws_client already normalises the new "runs" key into "entries" so that
    // schedule.js updateStatusColumn (which reads state.schedule.entries) works.
    ws.subscribe('sim/state/schedule', (_topic, payload) => {
        if (!payload || typeof payload !== 'object') return;
        _state.schedule = payload;
        _notify();
    });

    // transit/vehicle/+/+ → telemetry + map position
    ws.subscribe('transit/vehicle/+/+', (topic, payload) => {
        const parts = topic.split('/');
        if (parts.length < 4) return;
        const vehicleId = parts[2];
        const leaf = parts[3];
        if (!_state.telemetry[vehicleId]) _state.telemetry[vehicleId] = {};
        _state.telemetry[vehicleId][leaf] = payload;
        _notify();

        if (leaf === 'position') {
            const v = (_state.fleet.vehicles || []).find(x => x.vehicle_id === vehicleId);
            if (v) updateMapVehicle(vehicleId, v.transmitting, v.lifecycle_state, payload);
        }
    });

    // Tab strip
    document.getElementById('tab-strip').addEventListener('click', ev => {
        const btn = ev.target.closest('.tab');
        if (btn?.dataset.tab) switchTab(btn.dataset.tab);
    });

    // Default tab
    switchTab('fleet');

    // Probe sim health
    probeSimHealth();
    setInterval(probeSimHealth, 30_000);
}

main().catch(err => console.error('boot error', err));
