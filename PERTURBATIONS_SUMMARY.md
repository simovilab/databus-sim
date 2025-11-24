# GTFS Simulation Enhancement Summary

## What Was Added

### 1. Realistic Perturbation System

The simulation now includes random scenarios that disturb regular vehicle behavior:

#### Perturbation Types:
- **DELAY** (50% probability): Traffic congestion, 1-5 minutes, reduces speed by 30-80%
- **HOLDING** (25% probability): Bus stops, 30 seconds - 3 minutes, complete stop
- **DETOUR** (15% probability): Route deviation, 2-10 minutes, 70% normal speed
- **BREAKDOWN** (10% probability): Mechanical failure, 5-15 minutes, complete stop

Each vehicle has a 5% chance per update (every 2 seconds) to experience a perturbation.

### 2. Comprehensive Vehicle Metrics

Each vehicle now tracks:

| Metric | Description | Calculation |
|--------|-------------|-------------|
| **Velocity** | Current speed in km/h | Haversine distance between points / time |
| **OTP (On-Time Performance)** | Schedule adherence percentage | 100% if within 5 min of schedule |
| **Delay** | Seconds ahead (-) or behind (+) | Actual vs expected progress |
| **Headway Adherence** | Spacing consistency percentage | Deviation from scheduled headway |
| **Gap** | Time to next vehicle (seconds) | Position difference converted to time |
| **Distance** | Total kilometers traveled | Cumulative Haversine distances |
| **Stops Completed** | Number of stops served | Counter (currently placeholder) |

### 3. Enhanced Dashboard UI

The dashboard now shows:

#### Vehicle Markers:
- **Color-coded by status**:
  - 🔵 Blue = On time
  - 🟠 Orange = Delayed
  - 🔴 Red = Holding
  - 🟣 Purple = Detour
  - 🔴 Dark red = Breakdown

#### Vehicle Information Cards:
- Vehicle ID with OTP badge (green/orange/red)
- Active perturbation badges
- Real-time metrics:
  - 🚌 Route & ⚡ Speed
  - ⏱️ Delay & 📊 Gap
  - 🎯 Headway Adherence & 📏 Distance

#### Detailed Popups:
- Full performance metrics
- Active perturbation descriptions
- Exact GPS coordinates

### 4. Server Enhancements

- Increased from 5 to 8 vehicles
- Staggered vehicle starts (1-2 minute intervals)
- Real-time perturbation logging in console
- Comprehensive metric calculations
- Status tracking for each vehicle

## Technical Implementation

### Classes Added:

1. **PerturbationType (Enum)**: Defines perturbation categories
2. **Perturbation**: Manages perturbation lifecycle and properties
3. **VehicleMetrics**: Tracks all performance metrics for each vehicle

### Key Algorithms:

- **Haversine Distance**: Accurate GPS distance calculation in kilometers
- **OTP Calculation**: Percentage based on delay magnitude with 5-minute window
- **Headway Deviation**: Comparison of actual vs scheduled headway
- **Gap Calculation**: Position-based time separation between vehicles on same route

## Configuration

All parameters are easily adjustable:

```python
# Simulation constants
BASE_SPEED_KMH = 25
UPDATE_INTERVAL = 2
SCHEDULED_HEADWAY = 600

# Perturbation probabilities
weights=[0.5, 0.25, 0.15, 0.1]  # DELAY, HOLDING, DETOUR, BREAKDOWN

# Perturbation chance per update
if random.random() > 0.05:  # 5% chance
```

## Message Format Example

```json
{
  "type": "vehicle_positions",
  "timestamp": "2025-11-08T10:30:45",
  "vehicles": [{
    "vehicle_id": "VEH_001",
    "route_id": "bUCR_L1",
    "lat": 9.9356,
    "lon": -84.0490,
    "metrics": {
      "velocity_kmh": 18.3,
      "otp_percent": 87.5,
      "delay_seconds": 180,
      "headway_adherence_percent": 92.1,
      "gap_seconds": 420,
      "distance_km": 2.45
    },
    "perturbations": [{
      "type": "delay",
      "description": "Traffic delay: 3 minutes",
      "severity": 0.6,
      "remaining_duration": 120
    }],
    "status": "delayed"
  }]
}
```

## Files Modified/Created

1. **gtfs_websocket_server.py** - Complete rewrite with perturbations and metrics
2. **dashboard/dashboard.html** - Enhanced UI with metric displays
3. **README_GTFS_SERVER.md** - Updated documentation
4. **PERTURBATIONS_SUMMARY.md** - This file

## Usage

```bash
# Start the server
python gtfs_websocket_server.py

# Open dashboard/dashboard.html in browser
# Watch vehicles experience real-time perturbations!
```

## Future Enhancements

Potential additions:
- Historical data logging to CSV/database
- Adjustable perturbation rates via API
- Stop-level dwell time tracking
- Passenger load simulation
- Weather-based perturbations
- Route-specific perturbation probabilities
- Real-time charts for metrics over time
