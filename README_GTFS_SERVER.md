# GTFS WebSocket Server with Realistic Perturbations

This mini server reads GTFS data from `GTFS_bUCR.zip` and serves real-time vehicle positions via WebSocket to the dashboard with realistic perturbations and comprehensive performance metrics.

## Features

### Core Functionality
- Loads GTFS data (routes, stops, trips, shapes) from the UCR bus system
- Simulates vehicle positions along actual route shapes
- Broadcasts vehicle positions to connected clients via WebSocket
- Real-time updates every 2 seconds

### Realistic Perturbations
The simulation includes random scenarios that disturb regular vehicle behavior:

- **Delays**: Traffic congestion causing reduced speed (1-5 minutes, 30-80% speed reduction)
- **Holdings**: Stops where buses must wait (30 seconds - 3 minutes)
- **Detours**: Route deviations requiring alternative paths (2-10 minutes, 70% speed)
- **Breakdowns**: Vehicle mechanical failures requiring extended stops (5-15 minutes)

Each perturbation has:
- Type and severity (0.0 to 1.0)
- Duration (seconds)
- Description
- Real-time countdown

### Vehicle Metrics

Each vehicle tracks comprehensive performance metrics:

1. **Velocity**: Current speed in km/h
2. **OTP (On-Time Performance)**: Percentage adherence to schedule (100% = perfect)
   - Within 5 minutes: 100%
   - Outside 5 minutes: Decreases with delay magnitude
3. **Delay**: Seconds ahead (-) or behind (+) schedule
4. **Headway Adherence**: Percentage adherence to scheduled headway (100% = perfect)
5. **Gap**: Time separation to next vehicle on same route (seconds)
6. **Distance**: Total kilometers traveled
7. **Stops Completed**: Number of stops served

### Dashboard Features

- **Color-coded vehicle markers** by status:
  - Blue: On time
  - Orange: Delayed
  - Red: Holding
  - Purple: Detour
  - Dark red: Breakdown

- **Detailed vehicle cards** showing:
  - Vehicle ID and OTP badge
  - Active perturbation badges
  - Real-time metrics (speed, delay, gap, headway)
  - Route and trip information

- **Interactive popups** with:
  - Full performance metrics
  - Active perturbation descriptions
  - Position coordinates

## Installation

1. Install the required Python packages:
```bash
pip install websockets paho-mqtt
```

Or using the requirements file:
```bash
pip install -r requirements_gtfs_server.txt
```

## Usage

1. Start the WebSocket server:
```bash
python gtfs_websocket_server.py
```

The server will:
- Load GTFS data from `assets/GTFS_bUCR.zip`
- Start a WebSocket server on `ws://localhost:8765`
- Create 8 simulated vehicles on different routes with staggered starts
- Broadcast vehicle positions every 2 seconds
- Randomly apply perturbations (5% chance per update per vehicle)
- Track comprehensive performance metrics

2. Open the dashboard:
```bash
# Open dashboard/dashboard.html in your web browser
# The dashboard will automatically connect to ws://localhost:8765
```

3. Monitor the simulation:
- Watch vehicles move along UCR bus routes
- See real-time perturbations (delays, holdings, detours, breakdowns)
- Monitor performance metrics (OTP, velocity, headway, gaps)
- Click on vehicles for detailed information
- Observe color changes as status changes

## Architecture

### Server (`gtfs_websocket_server.py`)

- **GTFSData**: Loads and stores GTFS data from the zip file
- **VehicleSimulator**: Creates and updates simulated vehicle positions along route shapes
- **WebSocket Server**: Handles client connections and broadcasts vehicle positions
- **MQTT Publisher**: Publishes each vehicle's data to MQTT broker topics

### Data Flow

1. **GTFS Data Loading**: Reads routes, stops, trips, and shapes from `GTFS_bUCR.zip`
2. **Vehicle Simulation**: Creates vehicles with realistic perturbations and metrics
3. **Dual Broadcasting**:
   - **WebSocket**: Sends all vehicle positions to dashboard clients
   - **MQTT**: Publishes individual vehicle data to broker topics
4. **Real-time Updates**: Every 2 seconds, positions are updated and broadcast

### MQTT Integration

- **Broker**: mqtt.simovi.org:1883
- **Authentication**: Username/Password (admin/admin)
- **Topic Structure**: `vehicle/{vehicle_id}` (e.g., `vehicle/VEH_001`)
- **QoS**: 1 (at least once delivery)
- **Retain**: False (no message retention)
- **Message Format**: JSON with full vehicle data including metrics and perturbations

### Dashboard (`dashboard/dashboard.html`)

- Connects to the local WebSocket server (instead of MQTT)
- Displays vehicles on an interactive map using Leaflet.js
- Shows active vehicle count and list
- Auto-updates vehicle positions in real-time

## Message Format

The server sends messages in JSON format via both WebSocket and MQTT.

### MQTT Topics

Each vehicle publishes to its own topic:
- Topic pattern: `vehicle/{vehicle_id}`
- Examples: `vehicle/VEH_001`, `vehicle/VEH_002`, etc.

### Info Message (WebSocket only, on connection)
```json
{
  "type": "info",
  "message": "Connected to GTFS WebSocket Server",
  "routes": 10,
  "stops": 50,
  "trips": 20
}
```

### Vehicle Positions (periodic updates)

**WebSocket**: All vehicles sent together
```json
{
  "type": "vehicle_positions",
  "timestamp": "2025-11-08T10:30:45.123456",
  "vehicles": [...]
}
```

**MQTT**: Each vehicle published to separate topic `vehicle/{vehicle_id}`
```json
{
  "vehicle_id": "VEH_001",
  "trip_id": "bUCR_L1_001",
  "route_id": "bUCR_L1",
  "lat": 9.9356,
  "lon": -84.0490,
  "timestamp": "2025-11-08T10:30:45.123456",
  "metrics": {
    "velocity_kmh": 23.5,
    "otp_percent": 98.5,
    "delay_seconds": -45,
    "headway_adherence_percent": 95.2,
    "gap_seconds": 580,
    "distance_km": 3.45,
    "stops_completed": 5
  },
  "perturbations": [
    {
      "type": "delay",
      "description": "Traffic delay: 3 minutes",
      "severity": 0.6,
      "remaining_duration": 120,
      "duration": 180
    }
  ],
  "status": "delayed"
}
```

### Possible Status Values
- `on_time`: Vehicle operating normally
- `delayed`: Traffic or other delay affecting speed
- `holding`: Vehicle stopped at station/stop
- `detour`: Vehicle on alternate route
- `breakdown`: Vehicle experiencing mechanical failure

## Configuration

You can modify these parameters in `gtfs_websocket_server.py`:

### Server Settings
- **WebSocket host/port**: Change `host` and `port` in `main()` function (default: `localhost:8765`)
- **Update interval**: Change `UPDATE_INTERVAL` constant (default: 2 seconds)
- **Number of vehicles**: Modify `sample_trips = gtfs_data.trips[:8]` in `initialize_vehicles()` (default: 8 vehicles)

### MQTT Settings
- **Broker**: Change `MQTT_BROKER` constant (default: `mqtt.simovi.org`)
- **Port**: Change `MQTT_PORT` constant (default: 1883)
- **Username**: Change `MQTT_USERNAME` constant (default: `admin`)
- **Password**: Change `MQTT_PASSWORD` constant (default: `admin`)
- **Topic prefix**: Change `MQTT_TOPIC_PREFIX` constant (default: `vehicle/`)
- **QoS**: Modify `qos` parameter in `publish()` call (default: 1)

### Simulation Parameters
- **Base speed**: Change `BASE_SPEED_KMH` constant (default: 25 km/h)
- **Scheduled headway**: Change `SCHEDULED_HEADWAY` constant (default: 600 seconds / 10 minutes)
- **Perturbation probability**: Modify check in `apply_random_perturbation()` (default: 5% per update)

### Perturbation Settings
Adjust in `apply_random_perturbation()` method:

**Delay**:
- Duration: `random.randint(60, 300)` → 1-5 minutes
- Severity: `random.uniform(0.3, 0.8)` → 30-80% speed reduction
- Probability weight: 0.5 (50%)

**Holding**:
- Duration: `random.randint(30, 180)` → 30 seconds - 3 minutes
- Severity: 1.0 (complete stop)
- Probability weight: 0.25 (25%)

**Detour**:
- Duration: `random.randint(120, 600)` → 2-10 minutes
- Severity: `random.uniform(0.4, 0.7)` → 40-70% speed reduction
- Fixed speed: 0.7 (70% of normal)
- Probability weight: 0.15 (15%)

**Breakdown**:
- Duration: `random.randint(300, 900)` → 5-15 minutes
- Severity: 1.0 (complete stop)
- Probability weight: 0.10 (10%)

### Metric Calculations
- **OTP Window**: Within 5 minutes = 100% OTP
- **Haversine Distance**: For accurate km calculation between GPS points
- **Velocity History**: Last 10 readings averaged for smooth velocity
- **Gap Calculation**: Position difference converted to time using scheduled headway

## Troubleshooting

**Dashboard shows "Disconnected"**
- Make sure the server is running: `python gtfs_websocket_server.py`
- Check the browser console for connection errors (F12)
- Verify the WebSocket URL in dashboard.html matches the server address

**MQTT not connecting**
- Verify MQTT broker is accessible: `mqtt.simovi.org:1883`
- Check username/password credentials (admin/admin)
- Server will continue with WebSocket only if MQTT fails
- Look for "✓ Connected to MQTT broker" message in console

**MQTT messages not received**
- Subscribe to topics: `vehicle/#` (all vehicles) or `vehicle/VEH_001` (specific)
- Use MQTT client like MQTT Explorer or mosquitto_sub to verify
- Check QoS settings match between publisher and subscriber

**No vehicles showing**
- Check server console output for errors loading GTFS data
- Ensure `GTFS_bUCR.zip` exists in the `assets/` directory
- Verify the GTFS files contain shape data

**Vehicles not moving**
- Check if vehicles are experiencing breakdown or holding perturbations (see console)
- Wait a few seconds - they will resume after perturbation expires
- Check the vehicle popup for perturbation details

**Connection refused**
- Ensure port 8765 is not blocked by firewall
- Try changing the port in both server and dashboard
- Check if another instance is already running

**Perturbations not appearing**
- They appear randomly (5% chance per update)
- Wait a few minutes to see various perturbations
- Check server console for "[PERTURBATION]" messages

**Metrics seem incorrect**
- OTP calculation requires time to stabilize
- Delay is calculated based on expected vs actual progress
- Gap calculation requires multiple vehicles on same route

## Extending

To add more features:

1. **Real GPS data**: Replace `VehicleSimulator` with actual GPS feed from vehicles
2. **Route selection**: Add UI controls to filter/toggle specific routes
3. **Historical playback**: Store positions and replay past vehicle movements
4. **Multiple clients**: Already supported! The server handles multiple simultaneous connections
5. **API endpoints**: Add HTTP REST endpoints for GTFS data queries
6. **Adjustable perturbations**: Add UI to control perturbation rates and types
7. **Stop-level tracking**: Enhanced stop times and dwell time calculations
8. **Passenger counts**: Simulate boarding/alighting at stops
9. **Metrics dashboard**: Real-time charts showing OTP trends, delays over time
10. **Export data**: CSV/JSON export of metrics for analysis

## Example Console Output

```
Loading GTFS data...
Loaded GTFS data:
  - 2 routes
  - 22 stops
  - 130 trips
  - 896 shape points
Initializing vehicle simulator...

Starting WebSocket server on ws://localhost:8765
Dashboard should connect to this address
Press Ctrl+C to stop the server

Created vehicle VEH_001 on route bUCR_L1 (starts in 0s)
Created vehicle VEH_002 on route bUCR_L1 (starts in 109s)
Created vehicle VEH_003 on route bUCR_L1 (starts in 238s)
...
[PERTURBATION] VEH_003: Traffic delay: 4 minutes
[PERTURBATION] VEH_001: Route detour: 6 minutes
[PERTURBATION] VEH_007: Traffic delay: 4 minutes
[PERTURBATION] VEH_008: Holding at stop for 150 seconds
```

## Performance Metrics Explained

### OTP (On-Time Performance)
- **100%**: Vehicle is within 5 minutes of schedule
- **95-99%**: Slight delay (5-10 minutes)
- **80-94%**: Moderate delay (10-20 minutes)
- **< 80%**: Significant delay (> 20 minutes)

### Headway Adherence
- **100%**: Perfect spacing from previous vehicle
- **90-99%**: Good spacing, minimal bunching
- **< 90%**: Bunching or gaps in service

### Gap
- Time separation to next vehicle on same route
- Ideally should match SCHEDULED_HEADWAY (10 minutes)
- Low gaps indicate bunching
- High gaps indicate service gaps

### Status Meanings
- **on_time**: Operating normally, no active perturbations
- **delayed**: Speed reduced due to traffic or conditions
- **holding**: Stopped at station or stop
- **detour**: Taking alternate route
- **breakdown**: Mechanical failure, extended stop
