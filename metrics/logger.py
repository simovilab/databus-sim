import json
import logging

logger = logging.getLogger("simulator")
handler = logging.FileHandler("simulation.log")
formatter = logging.Formatter('%(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def log_event(event_type, data):
    log_entry = {"event": event_type, **data}
    logger.info(json.dumps(log_entry))