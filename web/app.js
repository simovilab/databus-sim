// app.js — boot script for the SIMOVI simulator control panel.
// Wires MQTT-WS, the reactive store, map pane, and tab router.

import { connectMqtt } from './lib/mqtt_client.js';
import { createSimClient } from './lib/sim_api.js';
import * as fleetTab    from './tabs/fleet.js';
import * as scheduleTab from './tabs/schedule.js';
import * as operatorTab from './tabs/operator.js';
import * as runsTab     from './tabs/runs.js';

// ---- Reactive store -----------------------------------------------------

const _state = {
    fleet: { vehicles: [] },
    schedule: { entries: [] },
    telemetry: {}, // vehicle_id → { position?, progression?, occupancy? }
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

let _mqttClient = null;

export const controls = {
    publish(topic, payload) {
        _mqttClient?.publish(topic, payload);
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
let _shapes = null;
const _vehicleMarkers = new Map(); // vehicle_id → L.Marker
const _shapePolylines = new Map(); // shape_id → L.Polyline
const _routeShapeIds  = new Map(); // route_id  → string[]
let _shapesLoaded = false;

const ACTIVE_LIFECYCLE = new Set(['Confirmed', 'Tracking', 'InProgress', 'NoSignal']);

const MARKER_COLORS = {
    Confirmed:   '#64748b',
    Tracking:    '#f59e0b',
    InProgress:  '#16a34a',
    NoSignal:    '#f97316',
    Completed:   '#cbd5e1',
    Cancelled:   '#cbd5e1',
    Interrupted: '#cbd5e1',
    ShortTurned: '#cbd5e1',
};

function markerColor(state) {
    return MARKER_COLORS[state] ?? '#94a3b8';
}

function makeIcon(color) {
    return L.divIcon({
        className: '',
        html: `<div style="background:${color};width:12px;height:12px;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.45);"></div>`,
        iconSize: [12, 12],
        iconAnchor: [6, 6],
        tooltipAnchor: [6, -6],
    });
}

function initMap() {
    _map = L.map('map', { zoomControl: true }).setView([9.9365, -84.0511], 16);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
    }).addTo(_map);
}

async function loadShapes() {
    try {
        const res = await fetch('shapes.json');
        if (!res.ok) throw new Error(`shapes.json: HTTP ${res.status}`);
        _shapes = await res.json();
        _shapesLoaded = true;

        for (const [shapeId, coords] of Object.entries(_shapes.shapes)) {
            const latlngs = coords.map(([lat, lon]) => [lat, lon]);
            const poly = L.polyline(latlngs, {
                color: '#3b82f6',
                weight: 2.5,
                opacity: 0.5,
                interactive: false,
            });
            _shapePolylines.set(shapeId, poly);
        }

        for (const route of _shapes.routes) {
            _routeShapeIds.set(route.route_id, route.shape_ids);
        }

        updateMapRoutes();
    } catch (e) {
        console.warn('shapes.json load failed — map routes disabled', e);
    }
}

function updateMapVehicle(vehicleId, transmitting, lifecycleState, position) {
    if (!_map) return;

    if (!transmitting) {
        const marker = _vehicleMarkers.get(vehicleId);
        if (marker) { marker.remove(); _vehicleMarkers.delete(vehicleId); }
        return;
    }

    if (!position) return;
    const { latitude, longitude } = position;
    if (latitude == null || longitude == null) return;

    const icon = makeIcon(markerColor(lifecycleState));

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

function updateMapRoutes() {
    if (!_shapesLoaded || !_map) return;

    const vehicles = _state.fleet.vehicles || [];
    const activeRoutes = new Set();
    for (const v of vehicles) {
        if (v.bound_run_id && ACTIVE_LIFECYCLE.has(v.lifecycle_state)) {
            activeRoutes.add(v.route_id);
        }
    }

    for (const [routeId, shapeIds] of _routeShapeIds.entries()) {
        const show = activeRoutes.has(routeId);
        for (const sid of shapeIds) {
            const poly = _shapePolylines.get(sid);
            if (!poly) continue;
            if (show && !_map.hasLayer(poly)) poly.addTo(_map);
            else if (!show && _map.hasLayer(poly)) poly.remove();
        }
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
    loadShapes();

    const mqtt = connectMqtt({
        onStatus(connected) {
            setBadge('broker-status', connected, `broker: ${connected ? 'connected' : 'disconnected'}`);
        },
    });
    _mqttClient = mqtt;

    // sim/state/fleet → store + map
    mqtt.subscribe('sim/state/fleet', (_topic, payload) => {
        if (!payload || typeof payload !== 'object') return;
        _state.fleet = payload;
        _notify();
        updateMapRoutes();
        for (const v of _state.fleet.vehicles || []) {
            const pos = _state.telemetry[v.vehicle_id]?.position;
            updateMapVehicle(v.vehicle_id, v.transmitting, v.lifecycle_state, pos);
        }
    });

    // sim/state/schedule → store
    mqtt.subscribe('sim/state/schedule', (_topic, payload) => {
        if (!payload || typeof payload !== 'object') return;
        _state.schedule = payload;
        _notify();
    });

    // transit/vehicle/+/+ → telemetry + map position
    mqtt.subscribe('transit/vehicle/+/+', (topic, payload) => {
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
