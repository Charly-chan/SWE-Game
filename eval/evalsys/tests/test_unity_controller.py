from __future__ import annotations

import socket
import threading
import time
import unittest

from evalsys.taskgen.unity.unity_controller import (
    LengthPrefixedJSONChannel,
    LoopbackControllerServer,
    PROTOCOL,
    UnityControllerProtocolError,
    UnityControllerSession,
)


class UnityControllerTests(unittest.TestCase):
    def test_action_batch_preserves_order_and_frame_horizons(self) -> None:
        left, right = socket.socketpair()
        try:
            receiver = LengthPrefixedJSONChannel(left)
            session = UnityControllerSession(
                LengthPrefixedJSONChannel(right),
                run_id="run", nonce="nonce", build_digest="sha256:build",
            )
            session.send_action_batch([
                {"canonical_action": "gb_right", "hold_frames": 1},
                {"canonical_action": "gb_jump", "hold_frames": 2},
            ])
            message = receiver.receive()
            self.assertEqual("action_batch", message["type"])
            self.assertEqual(["gb_right", "gb_jump"], [row["canonical_action"] for row in message["batch"]])
            self.assertEqual([1, 2], [row["hold_frames"] for row in message["batch"]])
        finally:
            left.close()
            right.close()

    def test_action_state_can_dispatch_chords_and_analog_axes(self) -> None:
        left, right = socket.socketpair()
        try:
            receiver = LengthPrefixedJSONChannel(left)
            session = UnityControllerSession(
                LengthPrefixedJSONChannel(right),
                run_id="run", nonce="nonce", build_digest="sha256:build",
            )
            session.send_action(
                "", 0.0, 12,
                actions=("gb_attack", "reload"), axes=(("look_x", 0.75),),
            )
            message = receiver.receive()
            self.assertEqual(["gb_attack", "reload"], message["actions"])
            self.assertEqual(["look_x"], message["axis_ids"])
            self.assertEqual([0.75], message["axis_values"])
        finally:
            left.close()
            right.close()

    def test_loopback_handshake_action_observation_and_stop(self) -> None:
        received: list[dict] = []
        errors: list[BaseException] = []
        with LoopbackControllerServer(timeout=2.0) as server:
            def peer() -> None:
                try:
                    connection = socket.create_connection(("127.0.0.1", server.port), timeout=2)
                    with connection:
                        channel = LengthPrefixedJSONChannel(connection, timeout=2)
                        channel.send({
                            "type": "hello",
                            "protocol": PROTOCOL,
                            "run_id": "run-1",
                            "nonce": "nonce-1",
                            "build_digest": "sha256:build",
                        })
                        channel.send({"type": "ready", "scene": "Level1", "frame": 4})
                        action = channel.receive()
                        received.append(action)
                        channel.send({
                            "type": "action_ack",
                            "sequence": action["sequence"],
                            "start_frame": 5,
                            "end_frame": 10,
                        })
                        channel.send({
                            "type": "observation",
                            "sequence": 1,
                            "row": {"f": 10, "g": {"gb_player": 1}},
                            "events": [{"kind": "checkpoint", "id": "spawn"}],
                        })
                        received.append(channel.receive())
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=peer)
            thread.start()
            channel = server.accept()
            session = UnityControllerSession(
                channel, run_id="run-1", nonce="nonce-1", build_digest="sha256:build"
            )
            session.handshake()
            self.assertEqual("Level1", session.receive_ready()["scene"])
            sequence = session.send_action("gb_attack", 1.0, 6)
            session.receive_ack("action_ack", sequence)
            session.receive_observation()
            session.stop("goal_reached")
            channel.connection.close()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual("action", received[0]["type"])
        self.assertEqual("stop", received[1]["type"])
        serialized = str(received).lower()
        self.assertNotIn("policy", serialized)
        self.assertNotIn("predicate", serialized)
        self.assertNotIn("verdict", serialized)
        self.assertEqual(10, session.rows[0]["f"])
        self.assertEqual("spawn", session.events[0]["id"])

    def test_nonce_mismatch_is_rejected(self) -> None:
        left, right = socket.socketpair()
        try:
            sender = LengthPrefixedJSONChannel(left)
            receiver = LengthPrefixedJSONChannel(right)
            sender.send({
                "type": "hello",
                "protocol": PROTOCOL,
                "run_id": "run",
                "nonce": "wrong",
                "build_digest": "sha256:build",
            })
            session = UnityControllerSession(
                receiver, run_id="run", nonce="expected", build_digest="sha256:build"
            )
            with self.assertRaisesRegex(UnityControllerProtocolError, "nonce mismatch"):
                session.handshake()
        finally:
            left.close()
            right.close()

    def test_duplicate_observation_sequence_is_rejected(self) -> None:
        left, right = socket.socketpair()
        try:
            sender = LengthPrefixedJSONChannel(left)
            session = UnityControllerSession(
                LengthPrefixedJSONChannel(right),
                run_id="run",
                nonce="nonce",
                build_digest="sha256:build",
            )
            observation = {
                "type": "observation",
                "sequence": 1,
                "row": {"f": 1},
                "events": [],
            }
            sender.send(observation)
            sender.send(observation)
            session.receive_observation()
            with self.assertRaisesRegex(UnityControllerProtocolError, "duplicate or out of order"):
                session.receive_observation()
        finally:
            left.close()
            right.close()

    def test_receive_timeout_is_a_protocol_error(self) -> None:
        left, right = socket.socketpair()
        try:
            channel = LengthPrefixedJSONChannel(right, timeout=0.01)
            with self.assertRaisesRegex(UnityControllerProtocolError, "timed out"):
                channel.receive()
        finally:
            left.close()
            right.close()

    def test_periodic_messages_do_not_extend_channel_wall_deadline(self) -> None:
        left, right = socket.socketpair()
        sender = LengthPrefixedJSONChannel(left, timeout=2)
        receiver = LengthPrefixedJSONChannel(right, timeout=0.06)
        stopped = threading.Event()

        def peer() -> None:
            sequence = 0
            while not stopped.wait(0.01):
                sequence += 1
                try:
                    sender.send({
                        "type": "observation",
                        "sequence": sequence,
                        "row": {"f": 0},
                        "events": [],
                    })
                except (OSError, UnityControllerProtocolError):
                    return

        thread = threading.Thread(target=peer)
        thread.start()
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(UnityControllerProtocolError, "timed out"):
                while True:
                    receiver.receive()
        finally:
            stopped.set()
            left.close()
            right.close()
            thread.join(timeout=1)
        self.assertLess(time.monotonic() - started, 0.3)

    def test_action_ack_collects_interleaved_fixed_frame_observations(self) -> None:
        left, right = socket.socketpair()
        try:
            sender = LengthPrefixedJSONChannel(left)
            session = UnityControllerSession(
                LengthPrefixedJSONChannel(right),
                run_id="run", nonce="nonce", build_digest="sha256:build",
            )
            sender.send({
                "type": "observation", "sequence": 1,
                "row": {"f": 8, "g": {"gb_player": 1}}, "events": [],
            })
            sender.send({
                "type": "action_ack", "sequence": 3,
                "start_frame": 8, "end_frame": 9,
            })

            ack = session.receive_ack("action_ack", 3)

            self.assertEqual(9, ack["end_frame"])
            self.assertEqual(8, session.rows[-1]["f"])
        finally:
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
