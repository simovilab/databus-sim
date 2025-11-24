"""
Mini GTFS WebSocket Server with Perturbations
Reads GTFS data from GTFS_bUCR.zip and simulates vehicle positions via WebSocket
Includes realistic perturbations: delays, detours, holdings
Tracks metrics: velocity, OTP, headway adherence, gaps
"""
import asyncio
import json
import zipfile
import csv
from pathlib import Path
from datetime import datetime, timedelta
import random
from typing import Dict, List, Optional
from enum import Enum
import websockets
import paho.mqtt.client as mqtt

# Path to GTFS data
GTFS_ZIP_PATH = Path(__file__).parent / "assets" / "GTFS_bUCR.zip"

# Simulation constants
BASE_SPEED_KMH = 25  # Base speed in km/h
UPDATE_INTERVAL = 2  # seconds between updates
SCHEDULED_HEADWAY = 600  # 10 minutes in seconds

# MQTT Configuration
MQTT_BROKER = "mqtt.simovi.org"
MQTT_PORT = 1883
MQTT_USERNAME = "admin"
MQTT_PASSWORD = "admin"
MQTT_TOPIC_PREFIX = "vehicle/"  # Topic format: vehicle/{vehicle_id}

class PerturbationType(Enum):
    """Types of perturbations that can affect vehicles"""
    NORMAL = "normal"
    DELAY = "delay"
    DETOUR = "detour"
    HOLDING = "holding"
    BREAKDOWN = "breakdown"

class Perturbation:
    """Represents a perturbation affecting a vehicle"""
    
    def __init__(self, type: PerturbationType, duration: int, severity: float, description: str):
        self.type = type
        self.duration = duration  # seconds
        self.severity = severity  # 0.0 to 1.0
        self.description = description
        self.start_time = datetime.now()
        self.remaining_duration = duration
    
    def is_active(self) -> bool:
        """Check if perturbation is still active"""
        return self.remaining_duration > 0
    
    def update(self, delta_seconds: int):
        """Update perturbation duration"""
        self.remaining_duration = max(0, self.remaining_duration - delta_seconds)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'type': self.type.value,
            'description': self.description,
            'severity': self.severity,
            'remaining_duration': self.remaining_duration,
            'duration': self.duration
        }

class GTFSData:
    """Load and store GTFS data"""
    
    def __init__(self, zip_path: Path):
        self.zip_path = zip_path
        self.routes = []
        self.stops = []
        self.trips = []
        self.shapes = []
        self.stop_times = []
        self.load_gtfs_data()
    
    def load_gtfs_data(self):
        """Load relevant GTFS files from zip"""
        with zipfile.ZipFile(self.zip_path, 'r') as z:
            # Load routes
            with z.open('routes.txt') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                self.routes = list(reader)
            
            # Load stops
            with z.open('stops.txt') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                self.stops = list(reader)
            
            # Load trips
            with z.open('trips.txt') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                self.trips = list(reader)
            
            # Load shapes (for route paths)
            with z.open('shapes.txt') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                self.shapes = list(reader)
            
            # Load stop times
            with z.open('stop_times.txt') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                self.stop_times = list(reader)
        
        print(f"Loaded GTFS data:")
        print(f"  - {len(self.routes)} routes")
        print(f"  - {len(self.stops)} stops")
        print(f"  - {len(self.trips)} trips")
        print(f"  - {len(self.shapes)} shape points")
    
    def get_shape_for_trip(self, trip_id: str) -> List[Dict]:
        """Get shape points for a specific trip"""
        trip = next((t for t in self.trips if t['trip_id'] == trip_id), None)
        if not trip or 'shape_id' not in trip:
            return []
        
        shape_id = trip['shape_id']
        shape_points = [s for s in self.shapes if s['shape_id'] == shape_id]
        
        # Sort by sequence
        shape_points.sort(key=lambda x: int(x.get('shape_pt_sequence', 0)))
        
        return shape_points


class VehicleMetrics:
    """Track performance metrics for a vehicle"""
    
    def __init__(self, vehicle_id: str, scheduled_departure: datetime):
        self.vehicle_id = vehicle_id
        self.scheduled_departure = scheduled_departure
        self.actual_departure = None
        self.position_history = []
        self.velocity_history = []
        self.delay_seconds = 0
        self.total_distance = 0.0
        self.stops_completed = 0
        
    def calculate_otp(self) -> float:
        """Calculate On-Time Performance (OTP) as percentage"""
        if abs(self.delay_seconds) <= 300:  # Within 5 minutes
            return 100.0
        elif self.delay_seconds < 0:  # Early
            return max(0, 100 - (abs(self.delay_seconds) / 60))
        else:  # Late
            return max(0, 100 - (self.delay_seconds / 60))
    
    def calculate_velocity_kmh(self) -> float:
        """Calculate current velocity in km/h"""
        if len(self.velocity_history) < 2:
            return BASE_SPEED_KMH
        return sum(self.velocity_history[-10:]) / min(10, len(self.velocity_history))
    
    def add_position(self, lat: float, lon: float, timestamp: datetime):
        """Add position to history and calculate metrics"""
        if self.position_history:
            # Calculate distance and velocity
            prev = self.position_history[-1]
            dist = self._haversine_distance(prev['lat'], prev['lon'], lat, lon)
            time_diff = (timestamp - prev['timestamp']).total_seconds()
            
            if time_diff > 0:
                velocity_kmh = (dist / time_diff) * 3600  # Convert to km/h
                self.velocity_history.append(velocity_kmh)
                self.total_distance += dist
        
        self.position_history.append({
            'lat': lat,
            'lon': lon,
            'timestamp': timestamp
        })
    
    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two coordinates in km"""
        from math import radians, sin, cos, sqrt, atan2
        
        R = 6371  # Earth radius in km
        
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        
        return R * c


class VehicleSimulator:
    """Simulate vehicle positions along GTFS routes with perturbations"""
    
    def __init__(self, gtfs_data: GTFSData):
        self.gtfs_data = gtfs_data
        self.active_vehicles = {}
        self.vehicle_metrics = {}
        self.vehicle_counter = 0
        self.last_update_time = datetime.now()
    
    def create_vehicle(self, trip_id: str, delay_start: int = 0) -> Dict:
        """Create a new simulated vehicle with optional initial delay"""
        trip = next((t for t in self.gtfs_data.trips if t['trip_id'] == trip_id), None)
        if not trip:
            return None
        
        shape_points = self.gtfs_data.get_shape_for_trip(trip_id)
        if not shape_points:
            return None
        
        self.vehicle_counter += 1
        vehicle_id = f"VEH_{self.vehicle_counter:03d}"
        
        # Scheduled departure time
        scheduled_departure = datetime.now() + timedelta(seconds=delay_start)
        
        vehicle = {
            'vehicle_id': vehicle_id,
            'trip_id': trip_id,
            'route_id': trip['route_id'],
            'shape_points': shape_points,
            'current_point_index': 0,
            'base_speed_degrees': 0.0002,  # Base speed in degrees per second
            'current_speed_modifier': 1.0,
            'perturbations': [],
            'scheduled_departure': scheduled_departure,
            'actual_departure': datetime.now(),
            'last_position_time': datetime.now(),
            'holding_until': None,
            'detour_active': False,
            'breakdown_active': False
        }
        
        # Initialize metrics
        self.vehicle_metrics[vehicle_id] = VehicleMetrics(vehicle_id, scheduled_departure)
        
        self.active_vehicles[vehicle_id] = vehicle
        return vehicle
    
    def apply_random_perturbation(self, vehicle_id: str):
        """Randomly apply a perturbation to a vehicle"""
        vehicle = self.active_vehicles.get(vehicle_id)
        if not vehicle:
            return
        
        # 5% chance per update to get a perturbation
        if random.random() > 0.05:
            return
        
        # Skip if already has an active perturbation
        active_perturbations = [p for p in vehicle['perturbations'] if p.is_active()]
        if len(active_perturbations) > 0:
            return
        
        # Choose perturbation type
        perturbation_type = random.choices(
            [PerturbationType.DELAY, PerturbationType.HOLDING, 
             PerturbationType.DETOUR, PerturbationType.BREAKDOWN],
            weights=[0.5, 0.25, 0.15, 0.1]
        )[0]
        
        if perturbation_type == PerturbationType.DELAY:
            duration = random.randint(60, 300)  # 1-5 minutes
            severity = random.uniform(0.3, 0.8)
            perturbation = Perturbation(
                PerturbationType.DELAY,
                duration,
                severity,
                f"Traffic delay: {duration//60} minutes"
            )
            vehicle['current_speed_modifier'] = 1.0 - severity
            
        elif perturbation_type == PerturbationType.HOLDING:
            duration = random.randint(30, 180)  # 30s - 3 minutes
            severity = 1.0
            perturbation = Perturbation(
                PerturbationType.HOLDING,
                duration,
                severity,
                f"Holding at stop for {duration} seconds"
            )
            vehicle['holding_until'] = datetime.now() + timedelta(seconds=duration)
            
        elif perturbation_type == PerturbationType.DETOUR:
            duration = random.randint(120, 600)  # 2-10 minutes
            severity = random.uniform(0.4, 0.7)
            perturbation = Perturbation(
                PerturbationType.DETOUR,
                duration,
                severity,
                f"Route detour: {duration//60} minutes"
            )
            vehicle['detour_active'] = True
            vehicle['current_speed_modifier'] = 0.7
            
        elif perturbation_type == PerturbationType.BREAKDOWN:
            duration = random.randint(300, 900)  # 5-15 minutes
            severity = 1.0
            perturbation = Perturbation(
                PerturbationType.BREAKDOWN,
                duration,
                severity,
                f"Vehicle breakdown: {duration//60} minutes"
            )
            vehicle['breakdown_active'] = True
            vehicle['holding_until'] = datetime.now() + timedelta(seconds=duration)
        
        vehicle['perturbations'].append(perturbation)
        print(f"[PERTURBATION] {vehicle_id}: {perturbation.description}")
    
    def update_vehicle_position(self, vehicle_id: str) -> Dict:
        """Update vehicle position and return current data with metrics"""
        vehicle = self.active_vehicles.get(vehicle_id)
        if not vehicle:
            return None
        
        shape_points = vehicle['shape_points']
        if not shape_points:
            return None
        
        current_time = datetime.now()
        
        # Check if vehicle is holding
        if vehicle['holding_until'] and current_time < vehicle['holding_until']:
            # Vehicle is stationary
            current_point = shape_points[vehicle['current_point_index']]
            velocity_kmh = 0.0
        else:
            vehicle['holding_until'] = None
            vehicle['breakdown_active'] = False
            
            # Update perturbations
            for perturbation in vehicle['perturbations']:
                if perturbation.is_active():
                    perturbation.update(UPDATE_INTERVAL)
                    if not perturbation.is_active():
                        # Perturbation ended
                        if perturbation.type == PerturbationType.DETOUR:
                            vehicle['detour_active'] = False
                        vehicle['current_speed_modifier'] = 1.0
            
            # Apply random perturbation
            self.apply_random_perturbation(vehicle_id)
            
            # Move to next point in shape (speed affected by perturbations)
            speed_factor = vehicle['current_speed_modifier']
            if speed_factor > 0:
                points_to_move = max(1, int(3 * speed_factor))
                vehicle['current_point_index'] += points_to_move
            
            # Loop back to start if reached end
            if vehicle['current_point_index'] >= len(shape_points):
                vehicle['current_point_index'] = 0
                vehicle['perturbations'] = []  # Clear perturbations on route completion
            
            current_point = shape_points[vehicle['current_point_index']]
            velocity_kmh = BASE_SPEED_KMH * speed_factor
        
        # Get metrics
        metrics = self.vehicle_metrics[vehicle_id]
        
        # Update position history
        lat = float(current_point['shape_pt_lat'])
        lon = float(current_point['shape_pt_lon'])
        metrics.add_position(lat, lon, current_time)
        
        # Calculate delay
        time_since_scheduled = (current_time - vehicle['scheduled_departure']).total_seconds()
        expected_progress = (time_since_scheduled / (SCHEDULED_HEADWAY)) * len(shape_points)
        actual_progress = vehicle['current_point_index']
        metrics.delay_seconds = int((actual_progress - expected_progress) / len(shape_points) * SCHEDULED_HEADWAY)
        
        # Calculate headway metrics
        headway_deviation = self.calculate_headway_deviation(vehicle_id)
        gap = self.calculate_gap(vehicle_id)
        
        # Get active perturbations
        active_perturbations = [p.to_dict() for p in vehicle['perturbations'] if p.is_active()]
        
        return {
            'vehicle_id': vehicle_id,
            'trip_id': vehicle['trip_id'],
            'route_id': vehicle['route_id'],
            'lat': lat,
            'lon': lon,
            'timestamp': current_time.isoformat(),
            'metrics': {
                'velocity_kmh': round(velocity_kmh, 2),
                'otp_percent': round(metrics.calculate_otp(), 1),
                'delay_seconds': metrics.delay_seconds,
                'headway_adherence_percent': round(headway_deviation, 1),
                'gap_seconds': gap,
                'distance_km': round(metrics.total_distance, 2),
                'stops_completed': metrics.stops_completed
            },
            'perturbations': active_perturbations,
            'status': self._get_vehicle_status(vehicle)
        }
    
    def _get_vehicle_status(self, vehicle: Dict) -> str:
        """Get human-readable status of vehicle"""
        if vehicle['breakdown_active']:
            return "breakdown"
        elif vehicle['holding_until'] and datetime.now() < vehicle['holding_until']:
            return "holding"
        elif vehicle['detour_active']:
            return "detour"
        elif any(p.is_active() and p.type == PerturbationType.DELAY for p in vehicle['perturbations']):
            return "delayed"
        else:
            return "on_time"
    
    def calculate_headway_deviation(self, vehicle_id: str) -> float:
        """Calculate headway adherence as percentage (100% = perfect)"""
        # Simplified: compare with scheduled headway
        actual_headway = SCHEDULED_HEADWAY + self.vehicle_metrics[vehicle_id].delay_seconds
        deviation = abs(actual_headway - SCHEDULED_HEADWAY) / SCHEDULED_HEADWAY
        return max(0, 100 - (deviation * 100))
    
    def calculate_gap(self, vehicle_id: str) -> int:
        """Calculate gap to next vehicle in seconds"""
        vehicle = self.active_vehicles[vehicle_id]
        route_id = vehicle['route_id']
        
        # Find next vehicle on same route
        same_route_vehicles = [
            (vid, v) for vid, v in self.active_vehicles.items() 
            if v['route_id'] == route_id and vid != vehicle_id
        ]
        
        if not same_route_vehicles:
            return SCHEDULED_HEADWAY
        
        # Calculate gap based on position difference
        current_pos = vehicle['current_point_index']
        min_gap = SCHEDULED_HEADWAY
        
        for vid, other_vehicle in same_route_vehicles:
            other_pos = other_vehicle['current_point_index']
            pos_diff = (other_pos - current_pos) % len(vehicle['shape_points'])
            time_gap = int((pos_diff / len(vehicle['shape_points'])) * SCHEDULED_HEADWAY)
            min_gap = min(min_gap, time_gap) if time_gap > 0 else min_gap
        
        return min_gap
    
    def get_all_positions(self) -> List[Dict]:
        """Get current positions of all vehicles with metrics"""
        positions = []
        for vehicle_id in list(self.active_vehicles.keys()):
            pos = self.update_vehicle_position(vehicle_id)
            if pos:
                positions.append(pos)
        return positions


# Store connected clients
connected_clients = set()
gtfs_data = None
vehicle_simulator = None
mqtt_client = None


async def handle_client(websocket):
    """Handle WebSocket client connection"""
    connected_clients.add(websocket)
    client_id = id(websocket)
    print(f"Client {client_id} connected. Total clients: {len(connected_clients)}")
    
    try:
        # Send initial GTFS info
        await websocket.send(json.dumps({
            'type': 'info',
            'message': 'Connected to GTFS WebSocket Server',
            'routes': len(gtfs_data.routes),
            'stops': len(gtfs_data.stops),
            'trips': len(gtfs_data.trips)
        }))
        
        # Keep connection alive and listen for messages
        async for message in websocket:
            try:
                data = json.loads(message)
                print(f"Received from client {client_id}: {data}")
                
                # Handle client requests here if needed
                if data.get('type') == 'ping':
                    await websocket.send(json.dumps({'type': 'pong'}))
                
            except json.JSONDecodeError:
                print(f"Invalid JSON from client {client_id}")
    
    except websockets.exceptions.ConnectionClosed:
        print(f"Client {client_id} disconnected")
    
    finally:
        connected_clients.remove(websocket)
        print(f"Client {client_id} removed. Total clients: {len(connected_clients)}")


async def broadcast_vehicle_positions():
    """Periodically broadcast vehicle positions to all connected clients and MQTT"""
    while True:
        if vehicle_simulator:
            # Get updated positions with metrics
            positions = vehicle_simulator.get_all_positions()
            
            if positions:
                # Prepare WebSocket message
                ws_message = json.dumps({
                    'type': 'vehicle_positions',
                    'vehicles': positions,
                    'timestamp': datetime.now().isoformat()
                })
                
                # Send to WebSocket clients
                if connected_clients:
                    disconnected = set()
                    for client in connected_clients:
                        try:
                            await client.send(ws_message)
                        except websockets.exceptions.ConnectionClosed:
                            disconnected.add(client)
                    
                    # Remove disconnected clients
                    connected_clients.difference_update(disconnected)
                
                # Publish each vehicle to MQTT
                if mqtt_client and mqtt_client.is_connected():
                    for vehicle_data in positions:
                        vehicle_id = vehicle_data['vehicle_id']
                        topic = f"{MQTT_TOPIC_PREFIX}{vehicle_id}"
                        
                        # Publish vehicle data to MQTT
                        mqtt_client.publish(
                            topic,
                            json.dumps(vehicle_data),
                            qos=1,
                            retain=False
                        )
        
        # Update every UPDATE_INTERVAL seconds
        await asyncio.sleep(UPDATE_INTERVAL)


async def initialize_vehicles():
    """Initialize some vehicles on different routes with staggered starts"""
    await asyncio.sleep(2)  # Wait for server to start
    
    # Get some trip IDs to simulate
    sample_trips = gtfs_data.trips[:8]  # Take first 8 trips for more variety
    
    for i, trip in enumerate(sample_trips):
        # Stagger vehicle starts by 1-2 minutes to simulate headway
        delay_start = i * random.randint(60, 120)
        vehicle = vehicle_simulator.create_vehicle(trip['trip_id'], delay_start)
        if vehicle:
            print(f"Created vehicle {vehicle['vehicle_id']} on route {vehicle['route_id']} (starts in {delay_start}s)")


def on_mqtt_connect(client, userdata, flags, rc):
    """Callback when MQTT client connects"""
    if rc == 0:
        print("✓ Connected to MQTT broker")
    else:
        print(f"✗ Failed to connect to MQTT broker. Return code: {rc}")

def on_mqtt_disconnect(client, userdata, rc):
    """Callback when MQTT client disconnects"""
    if rc != 0:
        print(f"✗ Unexpected MQTT disconnection. Return code: {rc}")
    else:
        print("MQTT client disconnected")

def on_mqtt_publish(client, userdata, mid):
    """Callback when message is published to MQTT"""
    pass  # Silent success

def setup_mqtt_client():
    """Initialize and connect MQTT client"""
    global mqtt_client
    
    print(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}...")
    
    mqtt_client = mqtt.Client(client_id="gtfs_simulator", clean_session=True)
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    
    # Set callbacks
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_disconnect = on_mqtt_disconnect
    mqtt_client.on_publish = on_mqtt_publish
    
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()  # Start network loop in background thread
        return True
    except Exception as e:
        print(f"✗ Failed to connect to MQTT broker: {e}")
        return False

async def main():
    """Main server function"""
    global gtfs_data, vehicle_simulator
    
    # Load GTFS data
    print("Loading GTFS data...")
    gtfs_data = GTFSData(GTFS_ZIP_PATH)
    
    # Initialize vehicle simulator
    print("Initializing vehicle simulator...")
    vehicle_simulator = VehicleSimulator(gtfs_data)
    
    # Setup MQTT connection
    print("\nSetting up MQTT connection...")
    mqtt_connected = setup_mqtt_client()
    if mqtt_connected:
        print(f"MQTT publishing to topics: {MQTT_TOPIC_PREFIX}{{vehicle_id}}")
    else:
        print("Warning: MQTT not connected. Continuing with WebSocket only.")
    
    # Start WebSocket server
    host = "localhost"
    port = 8765
    
    print(f"\nStarting WebSocket server on ws://{host}:{port}")
    print("Dashboard should connect to this address")
    print("Press Ctrl+C to stop the server\n")
    
    async with websockets.serve(handle_client, host, port):
        # Start background tasks
        await asyncio.gather(
            initialize_vehicles(),
            broadcast_vehicle_positions()
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped by user")
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            print("MQTT client disconnected")
