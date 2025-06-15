"""
Measurement service for NoSQL document operations.
Replaces SQL measurement operations with document-based operations using embedded device info.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from database.local import get_nosql_adapter
from .device_service import get_device_service

logger = logging.getLogger(__name__)


class MeasurementService:
    """Service for managing measurement documents with embedded device info"""
    
    def __init__(self, db_path: str = "recycling.db"):
        self.db_path = db_path
        self.adapter = get_nosql_adapter(db_path)
        self.device_service = get_device_service(db_path)
    
    def store_measurement(
        self,
        device_id: str,
        gateway_id: str,
        measurement_type: str,
        payload: Dict[str, Any],
        timestamp: Optional[str] = None
    ) -> int:
        """Store a measurement with embedded device info (equivalent to store_measurement)"""
        try:
            if not timestamp:
                timestamp = datetime.now().isoformat()
            
            # Get device info for embedding
            device = self.device_service.get_device(device_id)
            if not device:
                # Create minimal device info if device doesn't exist
                logger.warning(f"Device {device_id} not found, creating minimal device info")
                device_info = {
                    "device_id": device_id,
                    "gateway_id": gateway_id,
                    "device_type": "unknown",
                    "name": None,
                    "location": None,
                    "status": "unknown"
                }
            else:
                # Extract device info for embedding
                device_info = {
                    "device_id": device['device_id'],
                    "gateway_id": device['gateway_id'],
                    "device_type": device['device_type'],
                    "name": device.get('name'),
                    "location": device.get('location'),
                    "status": device['status']
                }
            
            measurement_doc = {
                "measurement_id": None,  # Will be set by adapter
                "device_info": device_info,
                "measurement_type": measurement_type,
                "timestamp": timestamp,
                "processed": False,
                "uploaded_to_cloud": False,
                "payload": payload
            }
            
            measurement_id = self.adapter.create_document('measurements', measurement_doc)
            
            # Update the device's last_measurement time
            self.device_service.update_device_measurement_time(device_id, timestamp)
            
            logger.info(f"Stored measurement document: {measurement_id} for device {device_id}")
            return measurement_id
            
        except Exception as e:
            logger.error(f"Error storing measurement: {e}")
            raise
    
    def get_measurement(self, measurement_id: int) -> Optional[Dict[str, Any]]:
        """Get measurement document by ID"""
        try:
            return self.adapter.get_document('measurements', measurement_id)
        except Exception as e:
            logger.error(f"Error getting measurement document: {e}")
            raise
    
    def get_measurements(
        self,
        device_id: Optional[str] = None,
        gateway_id: Optional[str] = None,
        measurement_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get measurements with flexible filtering (equivalent to get_measurements)"""
        try:
            # Build query for document filtering
            query = {}
            
            if device_id:
                query['device_info.device_id'] = device_id
            
            if gateway_id:
                query['device_info.gateway_id'] = gateway_id
            
            if measurement_type:
                query['measurement_type'] = measurement_type
            
            # Get documents from adapter
            measurements = self.adapter.query_documents('measurements', query, limit)
            
            # Apply date filtering in Python (could be optimized with custom SQL in adapter)
            if start_date or end_date:
                filtered_measurements = []
                for measurement in measurements:
                    try:
                        # Parse timestamp for comparison
                        timestamp_str = measurement.get('timestamp', '')
                        if timestamp_str:
                            # Handle ISO format timestamps
                            timestamp_str = timestamp_str.replace('Z', '+00:00')
                            measurement_time = datetime.fromisoformat(timestamp_str)
                            
                            # Apply date filters
                            if start_date:
                                start_time = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                                if measurement_time < start_time:
                                    continue
                            
                            if end_date:
                                end_time = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                                if measurement_time > end_time:
                                    continue
                            
                            filtered_measurements.append(measurement)
                    except Exception as parse_error:
                        logger.warning(f"Error parsing timestamp for measurement filtering: {parse_error}")
                        # Include measurement if timestamp parsing fails
                        filtered_measurements.append(measurement)
                
                measurements = filtered_measurements
            
            return measurements
            
        except Exception as e:
            logger.error(f"Error getting measurements: {e}")
            raise
    
    def get_measurements_by_device(
        self,
        device_id: str,
        limit: int = 100,
        measurement_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get measurements for a specific device"""
        return self.get_measurements(
            device_id=device_id,
            measurement_type=measurement_type,
            limit=limit
        )
    
    def get_measurements_by_gateway(
        self,
        gateway_id: str,
        limit: int = 100,
        measurement_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get measurements for all devices of a gateway"""
        return self.get_measurements(
            gateway_id=gateway_id,
            measurement_type=measurement_type,
            limit=limit
        )
    
    def get_recent_measurements(
        self,
        device_id: Optional[str] = None,
        gateway_id: Optional[str] = None,
        hours: int = 24,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent measurements within specified hours"""
        try:
            # Calculate start time
            start_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_time = start_time.replace(hour=start_time.hour - hours)
            start_date = start_time.isoformat()
            
            return self.get_measurements(
                device_id=device_id,
                gateway_id=gateway_id,
                start_date=start_date,
                limit=limit
            )
        except Exception as e:
            logger.error(f"Error getting recent measurements: {e}")
            raise
    
    def update_measurement_status(self, measurement_id: int, processed: bool, uploaded: bool = False) -> bool:
        """Update measurement processing status"""
        try:
            measurement = self.get_measurement(measurement_id)
            if not measurement:
                return False
            
            measurement['processed'] = processed
            measurement['uploaded_to_cloud'] = uploaded
            
            success = self.adapter.update_document('measurements', measurement_id, measurement)
            if success:
                logger.info(f"Updated measurement status: {measurement_id}")
            return success
            
        except Exception as e:
            logger.error(f"Error updating measurement status: {e}")
            raise
    
    def mark_measurement_processed(self, measurement_id: int) -> bool:
        """Mark measurement as processed"""
        return self.update_measurement_status(measurement_id, processed=True)
    
    def mark_measurement_uploaded(self, measurement_id: int) -> bool:
        """Mark measurement as uploaded to cloud"""
        return self.update_measurement_status(measurement_id, processed=True, uploaded=True)
    
    def get_unprocessed_measurements(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get unprocessed measurements"""
        try:
            measurements = self.adapter.query_documents('measurements', {'processed': False}, limit)
            return measurements
        except Exception as e:
            logger.error(f"Error getting unprocessed measurements: {e}")
            raise
    
    def get_measurement_summary(
        self,
        field_name: str,
        gateway_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        measurement_type: str = "weight_measurement"
    ) -> List[Dict[str, Any]]:
        """Get summary of measurements by a specific field (equivalent to get_measurement_summary)"""
        try:
            # Get all measurements with filters
            measurements = self.get_measurements(
                gateway_id=gateway_id,
                measurement_type=measurement_type,
                start_date=start_date,
                end_date=end_date,
                limit=10000  # Large limit for aggregation
            )
            
            # Perform aggregation in Python
            summary_data = {}
            
            for measurement in measurements:
                payload = measurement.get('payload', {})
                
                # Extract field value from payload
                field_value = None
                if field_name in payload:
                    field_value = payload[field_name]
                elif 'payload' in payload and field_name in payload['payload']:
                    field_value = payload['payload'][field_name]
                
                # Extract weight for aggregation
                weight_kg = None
                if 'weight_kg' in payload:
                    weight_kg = payload['weight_kg']
                elif 'payload' in payload and 'weight_kg' in payload['payload']:
                    weight_kg = payload['payload']['weight_kg']
                
                # Skip if we don't have both field value and weight
                if field_value is None or weight_kg is None:
                    continue
                
                try:
                    weight_kg = float(weight_kg)
                except (ValueError, TypeError):
                    continue
                
                # Aggregate data
                if field_value not in summary_data:
                    summary_data[field_value] = {
                        'field_value': field_value,
                        'measurement_count': 0,
                        'total_weight_kg': 0.0,
                        'weights': []
                    }
                
                summary_data[field_value]['measurement_count'] += 1
                summary_data[field_value]['total_weight_kg'] += weight_kg
                summary_data[field_value]['weights'].append(weight_kg)
            
            # Calculate final statistics
            result = []
            for field_value, data in summary_data.items():
                weights = data['weights']
                result.append({
                    'field_value': field_value,
                    'measurement_count': data['measurement_count'],
                    'total_weight_kg': data['total_weight_kg'],
                    'avg_weight_kg': data['total_weight_kg'] / data['measurement_count'],
                    'min_weight_kg': min(weights),
                    'max_weight_kg': max(weights)
                })
            
            # Sort by total weight descending
            result.sort(key=lambda x: x['total_weight_kg'], reverse=True)
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting measurement summary: {e}")
            raise
    
    def delete_measurement(self, measurement_id: int) -> bool:
        """Delete measurement document"""
        try:
            success = self.adapter.delete_document('measurements', measurement_id)
            if success:
                logger.info(f"Deleted measurement document: {measurement_id}")
            return success
        except Exception as e:
            logger.error(f"Error deleting measurement document: {e}")
            raise
    
    def count_measurements(
        self,
        device_id: Optional[str] = None,
        gateway_id: Optional[str] = None,
        measurement_type: Optional[str] = None
    ) -> int:
        """Count measurements with filters"""
        try:
            query = {}
            
            if device_id:
                query['device_info.device_id'] = device_id
            
            if gateway_id:
                query['device_info.gateway_id'] = gateway_id
            
            if measurement_type:
                query['measurement_type'] = measurement_type
            
            return self.adapter.count_documents('measurements', query)
            
        except Exception as e:
            logger.error(f"Error counting measurements: {e}")
            raise


# Global service instance
_measurement_service = None

def get_measurement_service(db_path: str = "recycling.db") -> MeasurementService:
    """Get or create measurement service instance"""
    global _measurement_service
    if _measurement_service is None or _measurement_service.db_path != db_path:
        _measurement_service = MeasurementService(db_path)
    return _measurement_service