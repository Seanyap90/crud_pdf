"""AWS Worker - Stateless worker for Lambda deployment using HTTP adapter"""
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from .base import BaseWorker
from database.sqlite_http_adapter import SQLiteHTTPAdapter
from ..db_layer import get_gateway_service, get_config_service

logger = logging.getLogger(__name__)


class AWSWorker(BaseWorker):
    """Stateless worker for AWS Lambda deployment.

    Uses SQLiteHTTPAdapter to connect to the EC2 SQLite database via HTTP.
    No Docker, MQTT, or local filesystem dependencies.

    In AWS mode the Step Functions state machines own workflow orchestration
    (IoT provisioning, config delivery, state transitions).  This worker is
    responsible only for:
      - Initial DB record creation on gateway/config POST requests
      - Read-model queries used by API GET endpoints
    """

    def __init__(self, db_host: str, db_port: int = 8080):
        self.db_host = db_host
        self.db_port = db_port
        self.db_path = f"http://{db_host}:{db_port}"
        self.adapter = None
        self.running = False
        logger.info(f"AWSWorker initialized for {self.db_path}")

    async def start(self):
        if not self.running:
            self.adapter = SQLiteHTTPAdapter(self.db_host, self.db_port)
            self.running = True
            logger.info("AWSWorker started with HTTP adapter")

    async def stop(self):
        if self.running and self.adapter:
            self.adapter.close()
            self.running = False
            logger.info("AWSWorker stopped")

    # ------------------------------------------------------------------
    # Task processing — write the initial DB record then hand off to SFN
    # ------------------------------------------------------------------

    async def process_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        task_type = task_data.get("type", "")
        logger.info(f"AWSWorker processing task: {task_type}")

        if task_type == "create_gateway":
            return self._create_gateway_record(task_data)
        if task_type == "config_update":
            return self._create_config_record(task_data)

        # All MQTT events (status, heartbeat, etc.) are handled by IoT Rules →
        # Step Functions in AWS mode.  Do NOT write directly to gateways_docs
        # here — that would overwrite the authoritative read model managed by
        # the update_gateway_read_model Lambda and corrupt timestamps/status.
        gateway_id = task_data.get("gateway_id")
        if gateway_id:
            gw = get_gateway_service(self.db_path).get_gateway_status(gateway_id)
            if gw:
                return gw
        return {"status": "processed"}

    def _create_gateway_record(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        gateway_id = task_data.get("gateway_id")
        if not gateway_id:
            raise ValueError("gateway_id is required")
        svc = get_gateway_service(self.db_path)
        existing = svc.get_gateway(gateway_id)
        if existing:
            raise ValueError(f"Gateway {gateway_id} already exists")
        return svc.create_gateway(
            gateway_id=gateway_id,
            name=task_data.get("name", "Unnamed Gateway"),
            location=task_data.get("location", "Unknown"),
        )

    def _handle_mqtt_status(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        gateway_id = task_data.get("gateway_id")
        if not gateway_id:
            raise ValueError("gateway_id is required")
        payload = task_data.get("payload", {})
        svc = get_gateway_service(self.db_path)
        gw = svc.get_gateway(gateway_id)
        if not gw:
            raise ValueError(f"Gateway {gateway_id} not found")

        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        reported_status = payload.get("status", "")
        cert_status = payload.get("certificate_status", "")

        if reported_status == "offline":
            gw["status"] = "disconnected"
            gw["disconnected_at"] = now
        elif reported_status == "online" or cert_status == "installed":
            gw["status"] = "connected"
            gw["connected_at"] = now
        elif cert_status == "removed":
            gw["status"] = "provisioned"

        gw["last_updated"] = now
        svc.adapter.update_document("gateways", gateway_id, gw)
        logger.info(f"AWSWorker updated gateway {gateway_id} status to {gw['status']}")
        return svc.get_gateway_status(gateway_id)

    def _create_config_record(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        update_id = task_data.get("update_id")
        gateway_id = task_data.get("gateway_id")
        svc = get_config_service(self.db_path)
        return svc.create_config_update(
            update_id=update_id,
            gateway_id=gateway_id,
            state="stored",
            config_hash=task_data.get("config_hash"),
            yaml_config=task_data.get("yaml_config"),
        )

    # ------------------------------------------------------------------
    # Read-model queries — used by API GET endpoints
    # ------------------------------------------------------------------

    def get_gateway_status(self, gateway_id: str) -> Optional[Dict[str, Any]]:
        return get_gateway_service(self.db_path).get_gateway_status(gateway_id)

    def list_gateways(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        return get_gateway_service(self.db_path).list_gateways(include_deleted=include_deleted)

    def get_config_update(self, update_id: str, include_config: bool = False) -> Optional[Dict[str, Any]]:
        svc = get_config_service(self.db_path)
        update = svc.get_config_update(update_id)
        if update and not include_config:
            update.pop("yaml_config", None)
        return update

    def list_config_updates(
        self,
        gateway_id: Optional[str] = None,
        include_completed: bool = True,
    ) -> List[Dict[str, Any]]:
        return get_config_service(self.db_path).list_config_updates(
            gateway_id=gateway_id,
            include_completed=include_completed,
        )

    def get_latest_config(self, gateway_id: str, include_config: bool = True) -> Optional[Dict[str, Any]]:
        svc = get_config_service(self.db_path)
        update = svc.get_latest_config_for_gateway(gateway_id)
        if update and not include_config:
            update.pop("yaml_config", None)
        return update

    # ------------------------------------------------------------------
    # No-ops — Step Functions own event sourcing / state transitions
    # ------------------------------------------------------------------

    def append_event(self, event: Dict[str, Any]) -> None:
        pass

    def read_events(self, aggregate_id: str) -> List[Dict[str, Any]]:
        return []

    def get_current_version(self, aggregate_id: str) -> int:
        return -1

    def update_read_model(self, gateway_id: str) -> None:
        pass

    def update_config_read_model(self, update_id: str) -> None:
        pass

    async def check_and_process_timeouts(self) -> None:
        pass