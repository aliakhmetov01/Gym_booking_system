import math
import shutil
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from api import index

database = index.database
app = index.app


class NetworkMonitoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resources = ExitStack()
        cls.addClassCleanup(resources.close)
        if database.USE_SQLITE and not database.USE_POSTGRES:
            folder = Path(resources.enter_context(tempfile.TemporaryDirectory()))
            db_path = folder / "gym.db"
            shutil.copyfile(Path(__file__).resolve().parents[1] / "gym.db", db_path)
            resources.enter_context(patch.object(database, "DB_PATH", str(db_path)))
            resources.enter_context(patch.object(database, "_ADMIN_SYNCED", False))
        database.init_db()

    def register(self):
        with app.test_client() as client:
            result = client.post("/api/auth/register", json={
                "full_name": "Rate Limit Tester",
                "email": f"{uuid4().hex}@example.test",
                "phone": "+77015551234",
                "password": "test-password",
            })
        self.assertEqual(result.status_code, 201)
        return {"Authorization": f"Bearer {result.json['data']['token']}"}

    @staticmethod
    def book(headers, path="/api/book"):
        with app.test_client() as client:
            return client.post(path, headers=headers, json={"session_id": -1})

    def test_atomic_limit_expiry_and_account_isolation(self):
        headers = self.register()
        other_headers = self.register()
        limit = index.BOOKING_REQUEST_LIMIT
        window = index.BOOKING_WINDOW_SECONDS
        now = math.floor(time.time() / window) * window + 1

        self.assertEqual(self.book({}).status_code, 401)
        with patch.object(index, "time", wraps=time) as clock:
            clock.time.return_value = now
            with ThreadPoolExecutor(max_workers=limit + 3) as workers:
                attempts = list(workers.map(lambda _: self.book(headers), range(limit + 3)))

            self.assertEqual([r.status_code for r in attempts].count(400), limit)
            blocked = [r for r in attempts if r.status_code == 429]
            self.assertEqual(len(blocked), 3)
            for response in blocked:
                self.assertEqual(response.headers["Retry-After"], str(window - 1))
                self.assertEqual(response.json["retry_after"], window - 1)
                self.assertEqual(response.json["code"], "BOOKING_RATE_LIMITED")
            self.assertEqual(self.book(headers, "/api/index?__path=/api/book").status_code, 429)
            self.assertEqual(self.book(other_headers).status_code, 400)

            clock.time.return_value = now + window
            self.assertEqual(self.book(headers).status_code, 400)

    def test_health_reports_failure_and_recovers(self):
        with app.test_client() as client:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            self.assertGreaterEqual(response.json["db_latency_ms"], 0)
            self.assertIn("no-store", response.headers["Cache-Control"])
            with (
                patch.object(database, "probe_storage", side_effect=OSError("Test probe failure")),
                self.assertLogs(app.logger, level="ERROR"),
            ):
                response = client.get("/api/health")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json["status"], "OFFLINE")
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["status"], "ONLINE")

    def test_limiter_unavailable_prevents_booking(self):
        headers = self.register()
        with (
            patch.object(database, "consume_booking_attempt", side_effect=OSError("Test store failure")),
            patch.object(database, "create_booking") as create_booking,
            self.assertLogs(app.logger, level="ERROR"),
        ):
            response = self.book(headers)
        self.assertEqual(response.status_code, 503)
        create_booking.assert_not_called()
