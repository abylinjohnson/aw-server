from apscheduler.schedulers.background import BackgroundScheduler
import logging
import requests
import platform
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List
from aw_core.models import Event
from aw_client import ActivityWatchClient
from aw_datastore import Datastore


logger = logging.getLogger(__name__)

SYNC_API_URL = "https://activity-watch-server.abylinjohnson2002.workers.dev/api/sync"

def get_system_info() -> Dict:
    return {
        "hostname": platform.node(),
        "system": platform.system(),
        "version": platform.version()
    }

def get_events_for_sync(aw_client: ActivityWatchClient, last_sync: datetime) -> Dict:
    buckets = aw_client.get_buckets()
    sync_data = []
    total_events = 0

    for bucket_id, bucket in buckets.items():
        if bucket_id == "sync-info":  # Skip sync bucket
            continue
        events = aw_client.get_events(bucket_id, start=last_sync)
        if events:
            logger.info(f"Found {len(events)} events in bucket {bucket_id}")
            total_events += len(events)
            sync_data.append({
                "bucket_id": bucket_id,
                "bucket_name": bucket["name"],
                "bucket_type": bucket["type"],
                "bucket_client": bucket["client"],
                "events": [{
                    "id": event["id"],
                    "timestamp": event["timestamp"].isoformat(),
                    "duration": event["duration"].total_seconds(),
                    "data": event["data"]
                } for event in events]
            })
    
    logger.info(f"Found {total_events} events from {len(sync_data)} buckets")
    return {
        "system_info": get_system_info(),
        "data": sync_data,
        "sync_timestamp": datetime.now(timezone.utc).isoformat()
    }

def sync_events(app):
    try:
        logger.info("Starting sync operation...")
        # Initialize client
        aw_client = ActivityWatchClient("aw-server-sync")
        
        # Get database from app
        db = app.api.db
        
        # Create sync bucket if it doesn't exist
        sync_bucket_id = "sync-info"
        hostname = platform.node()
        
        try:
            buckets = db.buckets()
        except Exception as e:
            logger.warning(f"Failed to get buckets, creating new sync bucket: {e}")
            buckets = {}
            
        if sync_bucket_id not in buckets:
            try:
                db.create_bucket(
                    bucket_id=sync_bucket_id,
                    type="sync-metadata",
                    client="aw-server-sync",
                    hostname=hostname,
                    created=datetime.now(timezone.utc),
                    name="Sync Information",
                    data={"description": "Stores synchronization metadata"}
                )
                logger.info("Created sync info bucket")
            except Exception as e:
                logger.error(f"Failed to create sync bucket: {e}")
                return
        
        # Get last sync time from events in sync bucket
        try:
            events = app.api.get_events(sync_bucket_id, limit=1)
            if not events:
                logger.info("No previous sync events found, using default start time")
                last_sync = datetime.now(timezone.utc) - timedelta(days=1)
            else:
                print("Events : ", events)
                # Convert string timestamp from event data to datetime object
                last_sync = datetime.fromisoformat(events[0]['data']['timestamp'])
        except Exception as e:
            logger.warning(f"Failed to get last sync time: {e}")
            last_sync = datetime.now(timezone.utc) - timedelta(days=1)
            
        logger.info(f"Last sync time: {last_sync}")
    
        # Get events since last sync
        sync_data = get_events_for_sync(aw_client, last_sync)
        
        try:
            response = requests.post(
                SYNC_API_URL,
                json=sync_data,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            logger.info(f"Successfully sent sync data to {SYNC_API_URL}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send sync data to API: {e}")
            return
        
        # Store sync timestamp as an event
        try:
            event = Event(
                timestamp=datetime.fromisoformat(sync_data["sync_timestamp"]),
                duration=timedelta(microseconds=0),
                data={
                    "sync_successful": True,
                    "timestamp": str(datetime.fromisoformat(sync_data["sync_timestamp"]))
                }
            )
            logger.info(f"Storing sync event: {event}")
            app.api.create_events(sync_bucket_id, event)
            logger.info(f"Sync completed successfully at {sync_data['sync_timestamp']}")
        except Exception as e:
            logger.error(f"Failed to store sync event: {e}")
            
    except Exception as e:
        logger.exception(f"Error during sync: {str(e)}")

def init_sync(app):
    logger.info("Initializing sync scheduler...")
    scheduler = BackgroundScheduler()   
    scheduler.add_job(lambda: sync_events(app), 'interval', seconds=300)  # Run every 5 minutes
    scheduler.start()
    logger.info("Sync scheduler started - will sync every 5 minutes")
