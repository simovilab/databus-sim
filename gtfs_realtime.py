from google.transit import gtfs_realtime_pb2
import time

def encode_vehicle_position(vehicle):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(time.time())

    entity = feed.entity.add()
    entity.id = vehicle.vehicle_id

    vp = entity.vehicle
    vp.trip.trip_id = vehicle.route
    lon, lat = vehicle.get_position()

    vp.position.latitude = lat
    vp.position.longitude = lon
    vp.position.speed = vehicle.current_speed

    return feed.SerializeToString()
