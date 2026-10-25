"""Local SortBot Bluetooth-serial integration.

The service is intentionally disabled unless SORTBOT_ENABLED=true and a
Windows COM port is configured. It sends short newline-terminated commands;
an accepted serial write is not treated as proof that sorting completed.
"""

import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Optional

import serial


CLASS_TO_BIN = {
    "PET": 1,
    "HDPE": 2,
    "LDPE": 2,
    "PP": 3,
    "PS": 4,
    "PVC": 4,
    "OTHER": 4,
}


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)).strip())
    except ValueError:
        return default


@dataclass
class SortOperation:
    operation_id: str
    plastic_type: str
    bin_number: int
    command: str
    status: str
    message: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "plastic_type": self.plastic_type,
            "bin": self.bin_number,
            "command": self.command,
            "status": self.status,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SortBotService:
    def __init__(self) -> None:
        self.enabled = _env_bool("SORTBOT_ENABLED", False)
        self.port = os.environ.get("SORTBOT_SERIAL_PORT", "").strip()
        self.baud_rate = _env_int("SORTBOT_BAUD_RATE", 9600)
        self.confidence_threshold = _env_float("SORTBOT_CONFIDENCE_THRESHOLD", 50.0)
        self.serial_timeout = _env_float("SORTBOT_SERIAL_TIMEOUT", 1.0)
        self.operation_timeout = _env_float("SORTBOT_OPERATION_TIMEOUT", 180.0)
        self.commands = {
            bin_number: os.environ.get(
                f"SORTBOT_BIN_{bin_number}_COMMAND", f"T{bin_number}"
            ).strip()
            for bin_number in range(1, 5)
        }
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._operations: dict[str, SortOperation] = {}
        self._active_operation_id: Optional[str] = None
        self._active_started_at = 0.0

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.port)

    def _configuration_message(self) -> str:
        if not self.enabled:
            return "SortBot control is disabled. Set SORTBOT_ENABLED=true to enable it."
        if not self.port:
            return "SortBot serial port is not configured. Set SORTBOT_SERIAL_PORT, for example COM5."
        return ""

    def _connect_locked(self) -> None:
        if self._serial and self._serial.is_open:
            return
        if not self.configured:
            raise RuntimeError(self._configuration_message())
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.serial_timeout,
                write_timeout=self.serial_timeout,
            )
        except serial.SerialException as exc:
            self._serial = None
            raise RuntimeError(f"Unable to open SortBot port {self.port}: {exc}") from exc

    def _close_locked(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            connected = bool(self._serial and self._serial.is_open)
            active = (
                self._operations.get(self._active_operation_id)
                if self._active_operation_id
                else None
            )
            if (
                active
                and monotonic() - self._active_started_at > self.operation_timeout
            ):
                active.status = "timeout"
                active.message = (
                    "No completion acknowledgement was received before the "
                    "configured operation timeout."
                )
                active.updated_at = datetime.now(timezone.utc).isoformat()
                self._active_operation_id = None
                active = None
            return {
                "enabled": self.enabled,
                "configured": self.configured,
                "port": self.port or None,
                "baud_rate": self.baud_rate,
                "connected": connected,
                "busy": active is not None
                and active.status in {"connecting", "command_sent", "acknowledged"},
                "active_operation": active.as_dict() if active else None,
                "message": (
                    "Connected."
                    if connected
                    else self._configuration_message()
                    or "Not connected. Send a sort command to connect."
                ),
            }

    def prediction_target(
        self, plastic_type: str, confidence: float
    ) -> dict[str, Any]:
        normalized_type = str(plastic_type).strip().upper()
        bin_number = CLASS_TO_BIN.get(normalized_type)
        if bin_number is None:
            return {
                "eligible": False,
                "bin": None,
                "reason": f"Plastic class {normalized_type or 'unknown'} is not mapped to a bin.",
            }
        if confidence < self.confidence_threshold:
            return {
                "eligible": False,
                "bin": bin_number,
                "reason": (
                    f"Confidence {confidence:.2f}% is below the "
                    f"{self.confidence_threshold:.2f}% sorting threshold."
                ),
            }
        if not self.configured:
            return {
                "eligible": False,
                "bin": bin_number,
                "reason": self._configuration_message(),
            }
        return {"eligible": True, "bin": bin_number, "reason": ""}

    def send_sort(self, plastic_type: str, confidence: float) -> dict[str, Any]:
        normalized_type = str(plastic_type).strip().upper()
        target = self.prediction_target(normalized_type, confidence)
        bin_number = target["bin"]
        if not target["eligible"]:
            raise ValueError(target["reason"])

        command = self.commands[bin_number]
        if not command:
            raise ValueError(f"No command is configured for Bin {bin_number}.")
        with self._lock:
            if self._active_operation_id:
                active = self._operations.get(self._active_operation_id)
                if active and active.status in {
                    "connecting",
                    "command_sent",
                    "acknowledged",
                }:
                    if monotonic() - self._active_started_at <= self.operation_timeout:
                        raise RuntimeError(
                            "SortBot is busy with another sorting operation."
                        )
                    active.status = "timeout"
                    active.message = "Operation timed out without Arduino confirmation."
                    active.updated_at = datetime.now(timezone.utc).isoformat()

            operation = SortOperation(
                operation_id=uuid.uuid4().hex,
                plastic_type=normalized_type,
                bin_number=bin_number,
                command=command,
                status="connecting",
                message="Opening the configured Bluetooth serial port.",
            )
            self._operations[operation.operation_id] = operation
            self._active_operation_id = operation.operation_id
            self._active_started_at = monotonic()

            try:
                self._connect_locked()
                assert self._serial is not None
                self._serial.write(f"{command}\n".encode("ascii"))
                self._serial.flush()
            except (serial.SerialException, OSError, RuntimeError) as exc:
                operation.status = "error"
                operation.message = str(exc)
                operation.updated_at = datetime.now(timezone.utc).isoformat()
                self._close_locked()
                self._active_operation_id = None
                raise RuntimeError(operation.message) from exc

            operation.status = "command_sent"
            operation.message = (
                f"Sent {command}. Waiting for Arduino confirmation; "
                "the write alone does not prove completion."
            )
            operation.updated_at = datetime.now(timezone.utc).isoformat()
            return operation.as_dict()

    def get_operation(self, operation_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            operation = self._operations.get(operation_id)
            return operation.as_dict() if operation else None


sortbot_service = SortBotService()
