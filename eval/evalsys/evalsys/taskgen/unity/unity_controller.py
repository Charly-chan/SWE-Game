


from __future__ import annotations

import json
import math
import re
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


PROTOCOL = "gamebench.unity-controller.v1"
MAX_MESSAGE_BYTES = 1024 * 1024
MESSAGE_TYPES = frozenset({
    "hello", "ready", "observation", "action", "action_ack",
    "action_batch", "action_batch_ack",
    "capture", "capture_ack", "stop", "stop_ack", "fatal",
})


class UnityControllerProtocolError(RuntimeError):
    pass


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = connection.recv(remaining)
        except (TimeoutError, socket.timeout) as exc:
            raise UnityControllerProtocolError("controller channel timed out") from exc
        if not chunk:
            raise UnityControllerProtocolError("peer closed the controller channel")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class LengthPrefixedJSONChannel:
    def __init__(self, connection: socket.socket, *, timeout: float = 10.0):
        self.connection = connection
        self.deadline = time.monotonic() + timeout
        self._apply_remaining_timeout()

    def _apply_remaining_timeout(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise UnityControllerProtocolError("controller channel timed out")
        self.connection.settimeout(remaining)

    def send(self, message: Mapping[str, Any]) -> None:
        payload = json.dumps(
            dict(message), separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if len(payload) > MAX_MESSAGE_BYTES:
            raise UnityControllerProtocolError("controller message exceeds size limit")
        self._apply_remaining_timeout()
        try:
            self.connection.sendall(struct.pack("!I", len(payload)) + payload)
        except (TimeoutError, socket.timeout) as exc:
            raise UnityControllerProtocolError("controller channel timed out") from exc

    def receive(self) -> dict[str, Any]:
        try:
            self._apply_remaining_timeout()
            (size,) = struct.unpack("!I", _recv_exact(self.connection, 4))
            if size <= 0 or size > MAX_MESSAGE_BYTES:
                raise UnityControllerProtocolError("invalid controller message length")
            value = json.loads(_recv_exact(self.connection, size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, struct.error) as exc:
            raise UnityControllerProtocolError(f"invalid controller message: {exc}") from exc
        if not isinstance(value, dict) or value.get("type") not in MESSAGE_TYPES:
            raise UnityControllerProtocolError("message must be an object with a known type")
        return value


@dataclass
class UnityControllerSession:
    channel: LengthPrefixedJSONChannel
    run_id: str
    nonce: str
    build_digest: str
    next_command_sequence: int = 1
    last_observation_sequence: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def handshake(self) -> dict[str, Any]:
        message = self.channel.receive()
        expected = {
            "type": "hello",
            "protocol": PROTOCOL,
            "run_id": self.run_id,
            "nonce": self.nonce,
            "build_digest": self.build_digest,
        }
        for key, value in expected.items():
            if message.get(key) != value:
                raise UnityControllerProtocolError(f"hello {key} mismatch")
        return message

    def receive_ready(self) -> dict[str, Any]:
        message = self.channel.receive()
        if message.get("type") != "ready" or not isinstance(message.get("frame"), int):
            raise UnityControllerProtocolError("expected ready(scene, frame)")
        return message

    def send_action(
        self,
        canonical_action: str,
        value: Any,
        hold_frames: int,
        *,
        actions: tuple[str, ...] = (),
        axes: tuple[tuple[str, float], ...] = (),
    ) -> int:
        action_ids = actions or ((canonical_action,) if canonical_action else ())
        valid_id = re.compile(r"^[a-z][a-z0-9_]{1,31}$").fullmatch
        if (
            hold_frames <= 0
            or any(not valid_id(action) for action in action_ids)
            or any(not valid_id(axis) or not math.isfinite(float(axis_value)) for axis, axis_value in axes)
        ):
            raise UnityControllerProtocolError("invalid current action command")
        sequence = self.next_command_sequence
        self.next_command_sequence += 1
        message = {
            "type": "action",
            "sequence": sequence,
            "canonical_action": canonical_action,
            "value": value,
            "hold_frames": hold_frames,
        }
        if actions:
            message["actions"] = list(actions)
        if axes:
            message["axis_ids"] = [axis for axis, _ in axes]
            message["axis_values"] = [axis_value for _, axis_value in axes]
        self.channel.send(message)
        return sequence

    def send_action_batch(self, actions: Sequence[Mapping[str, Any]]) -> int:

        if not actions or len(actions) > 64:
            raise UnityControllerProtocolError("action batch size must be in [1, 64]")
        steps = []
        for item in actions:
            canonical = str(item.get("canonical_action") or "")
            chord = tuple(str(value) for value in item.get("actions") or ())
            axes = tuple((str(key), float(value)) for key, value in dict(item.get("axes") or {}).items())
            hold = int(item.get("hold_frames") or 0)
            action_ids = chord or ((canonical,) if canonical else ())
            valid_id = re.compile(r"^[a-z][a-z0-9_]{1,31}$").fullmatch
            if (
                hold <= 0 or hold > 600
                or any(not valid_id(action) for action in action_ids)
                or any(not valid_id(axis) or not math.isfinite(value) for axis, value in axes)
            ):
                raise UnityControllerProtocolError("invalid action in batch")
            step = {
                "canonical_action": canonical,
                "value": item.get("value", 1.0),
                "hold_frames": hold,
            }
            if chord:
                step["actions"] = list(chord)
            if axes:
                step["axis_ids"] = [axis for axis, _ in axes]
                step["axis_values"] = [value for _, value in axes]
            steps.append(step)
        sequence = self.next_command_sequence
        self.next_command_sequence += 1
        self.channel.send({"type": "action_batch", "sequence": sequence, "batch": steps})
        return sequence

    def send_capture(self, checkpoint_id: str) -> int:
        if not checkpoint_id:
            raise UnityControllerProtocolError("capture checkpoint id is required")
        sequence = self.next_command_sequence
        self.next_command_sequence += 1
        self.channel.send({
            "type": "capture",
            "sequence": sequence,
            "checkpoint_id": checkpoint_id,
        })
        return sequence

    def receive_ack(self, expected_type: str, sequence: int) -> dict[str, Any]:
        while True:
            message = self.channel.receive()
            if message.get("type") == "observation":
                self._record_observation(message)
                continue
            if message.get("type") == "fatal":
                raise UnityControllerProtocolError(str(message.get("error") or "Unity peer fatal"))
            if message.get("type") != expected_type or message.get("sequence") != sequence:
                raise UnityControllerProtocolError(
                    f"expected {expected_type} for sequence {sequence}"
                )
            return message

    def receive_observation(self) -> dict[str, Any]:
        message = self.channel.receive()
        if message.get("type") == "fatal":
            raise UnityControllerProtocolError(str(message.get("error") or "Unity peer fatal"))
        if message.get("type") != "observation":
            raise UnityControllerProtocolError("expected observation(row, events)")
        self._record_observation(message)
        return message

    def _record_observation(self, message: Mapping[str, Any]) -> None:
        sequence = message.get("sequence")
        row = message.get("row")
        events = message.get("events", [])
        if not isinstance(sequence, int) or sequence <= self.last_observation_sequence:
            raise UnityControllerProtocolError("observation sequence is duplicate or out of order")
        if not isinstance(row, dict) or not isinstance(row.get("f"), int):
            raise UnityControllerProtocolError("observation row requires an integer frame")
        if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
            raise UnityControllerProtocolError("observation events must be objects")
        self.last_observation_sequence = sequence
        self.rows.append(dict(row))
        self.events.extend(dict(item) for item in events)

    def receive_through_frame(self, frame: int) -> dict[str, Any]:

        while not self.rows or int(self.rows[-1].get("f", -1)) < frame:
            self.receive_observation()
        return self.rows[-1]

    def stop(self, reason: str, *, wait_for_ack: bool = False) -> None:
        self.channel.send({"type": "stop", "reason": reason})
        if wait_for_ack:
            while True:
                message = self.channel.receive()
                if message.get("type") == "observation":
                    self._record_observation(message)
                    continue
                if message.get("type") == "fatal":
                    raise UnityControllerProtocolError(
                        str(message.get("error") or "Unity peer fatal")
                    )
                if message.get("type") != "stop_ack" or message.get("reason") != reason:
                    raise UnityControllerProtocolError("expected stop_ack for requested reason")
                break


class LoopbackControllerServer:
    def __init__(self, *, timeout: float = 10.0):
        self.timeout = timeout
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(1)
        self.socket.settimeout(timeout)

    @property
    def port(self) -> int:
        return int(self.socket.getsockname()[1])

    def accept(self) -> LengthPrefixedJSONChannel:
        connection, address = self.socket.accept()
        if address[0] not in {"127.0.0.1", "::1"}:
            connection.close()
            raise UnityControllerProtocolError("controller accepted a non-loopback peer")
        return LengthPrefixedJSONChannel(connection, timeout=self.timeout)

    def close(self) -> None:
        self.socket.close()

    def __enter__(self) -> "LoopbackControllerServer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "LengthPrefixedJSONChannel",
    "LoopbackControllerServer",
    "MAX_MESSAGE_BYTES",
    "PROTOCOL",
    "UnityControllerProtocolError",
    "UnityControllerSession",
]
