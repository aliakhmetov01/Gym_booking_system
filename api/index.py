"""
Flask application for Gym Booking System (Vercel Serverless & Local WSGI).
Acts as the central server in 3-Tier Client-Server architecture (Week 10).
"""

import os
import sys
import time
from datetime import datetime
from urllib.parse import parse_qs
from flask import Flask, jsonify, request, send_from_directory

try:
    from . import _database as database
except ImportError:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
    import _database as database

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

app = Flask(__name__, static_folder=PUBLIC_DIR)
app.url_map.strict_slashes = False
START_TIME = time.time()


class VercelPathFixMiddleware:
    """
    Ensures Flask sees the true original API route even when Vercel rewrites
    /api/(.*) -> /api/index?__path=/api/$1.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        qs = environ.get("QUERY_STRING", "")
        if "__path=" in qs:
            parsed = parse_qs(qs, keep_blank_values=True)
            if "__path" in parsed and parsed["__path"]:
                target_path = parsed["__path"][0]
                if not target_path.startswith("/"):
                    target_path = "/" + target_path
                environ["PATH_INFO"] = target_path
        elif environ.get("PATH_INFO") in ("/api/index", "/api/index.py"):
            orig = (
                environ.get("HTTP_X_FORWARDED_URI")
                or environ.get("HTTP_X_ORIGINAL_URI")
                or environ.get("HTTP_X_REWRITE_URL")
                or environ.get("RAW_URI")
            )
            if orig and orig.startswith("/"):
                environ["PATH_INFO"] = orig.split("?")[0]
        return self.wsgi_app(environ, start_response)


app.wsgi_app = VercelPathFixMiddleware(app.wsgi_app)


@app.before_request
def ensure_db():
    """Ensures database schema and seed data exist before any request."""
    database.init_db()


@app.after_request
def add_cors(response):
    """Allow cross-origin requests and disable aggressive caching for API responses."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


# -------------------------------------------------------------
# Static Frontend Routes (Used locally; Vercel serves public/ via CDN)
# -------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    if filename.startswith("api/"):
        return jsonify({"status": "error", "message": "API endpoint not found"}), 404
    if os.path.exists(os.path.join(PUBLIC_DIR, filename)):
        return send_from_directory(PUBLIC_DIR, filename)
    return send_from_directory(PUBLIC_DIR, "index.html")


# -------------------------------------------------------------
# REST API Endpoints
# -------------------------------------------------------------

@app.route("/api/sessions", methods=["GET"])
def api_get_sessions():
    """FR-01: Show available training sessions with date, time, trainer, capacity, and free places."""
    sessions = database.get_all_sessions()
    return jsonify({
        "status": "success",
        "count": len(sessions),
        "data": sessions,
    })


@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    """Week 7 Data Governance: Enforce SES-### pattern and completeness."""
    data = request.get_json() or {}
    session_code = data.get("session_code", "")
    title = data.get("title", "")
    trainer = data.get("trainer", "")
    session_date = data.get("session_date", "")
    session_time = data.get("session_time", "")
    capacity = data.get("capacity", 0)
    category = data.get("category", "Fitness")

    if not all([session_code, title, trainer, session_date, session_time, capacity]):
        return jsonify({
            "status": "error",
            "message": "All fields are required (Completeness rule - Week 7).",
        }), 400

    result = database.add_session(
        session_code=session_code,
        title=title,
        trainer=trainer,
        session_date=session_date,
        session_time=session_time,
        capacity=capacity,
        category=category,
    )

    if not result["success"]:
        return jsonify({"status": "error", "message": result["error"]}), 400

    return jsonify({"status": "success", "message": result["message"]}), 201


@app.route("/api/book", methods=["POST"])
def api_book_session():
    """FR-02 & FR-03: Books training session, prevents overbooking, updates free places."""
    data = request.get_json() or {}
    session_id = data.get("session_id")
    member_name = data.get("member_name", "")
    member_phone = data.get("member_phone", "")

    if not session_id or not member_name or not member_phone:
        return jsonify({
            "status": "error",
            "message": "Please provide session_id, member_name, and member_phone.",
        }), 400

    try:
        session_id = int(session_id)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid session_id."}), 400

    result = database.create_booking(session_id, member_name, member_phone)
    if not result["success"]:
        return jsonify({"status": "error", "message": result["error"]}), 400

    return jsonify({
        "status": "success",
        "message": "Booking confirmed!",
        "data": result,
    }), 200


@app.route("/api/bookings", methods=["GET"])
def api_get_bookings():
    """Reception desk endpoint: retrieves all bookings."""
    bookings = database.get_all_bookings()
    return jsonify({
        "status": "success",
        "count": len(bookings),
        "data": bookings,
    })


@app.route("/api/checkin", methods=["POST"])
def api_checkin():
    """Week 5 Hardware workflow: check-in member by booking code / QR scan."""
    data = request.get_json() or {}
    booking_code = data.get("booking_code", "")

    if not booking_code:
        return jsonify({"status": "error", "message": "Booking code is required."}), 400

    result = database.checkin_booking(booking_code)
    if not result["success"]:
        return jsonify({"status": "error", "message": result["error"]}), 400

    return jsonify({
        "status": "success",
        "message": result["message"],
        "data": result,
    }), 200


@app.route("/api/backup", methods=["POST"])
def api_trigger_backup():
    """Week 6 Security Choice: Create physical snapshot backup."""
    data = request.get_json() or {}
    trigger = data.get("trigger", "MANUAL_UI")
    result = database.create_backup(trigger_type=trigger)

    if not result["success"]:
        return jsonify({"status": "error", "message": result["error"]}), 500

    return jsonify({
        "status": "success",
        "message": "Backup created successfully!",
        "data": result,
    }), 200


@app.route("/api/backups", methods=["GET"])
def api_list_backups():
    """Returns backup audit logs."""
    logs = database.get_backups()
    return jsonify({
        "status": "success",
        "count": len(logs),
        "data": logs,
    })


@app.route("/api/reset", methods=["POST"])
def api_reset_db():
    """Resets database to initial seed state for demo presentations."""
    database.init_db(force_reseed=True)
    return jsonify({
        "status": "success",
        "message": "Database reset to initial presentation seed.",
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    """Week 10 Telecommunications & Networks diagnostic endpoint."""
    t0 = time.time()
    _ = database.get_all_sessions()
    db_latency_ms = round((time.time() - t0) * 1000, 2)
    uptime_sec = round(time.time() - START_TIME, 1)

    return jsonify({
        "status": "ONLINE",
        "system": "Gym Booking System - Client/Server Core",
        "architecture": "3-Tier (Client Browser -> Application Server -> Database)",
        "storage_engine": database.get_storage_engine_name(),
        "protocol": request.environ.get("SERVER_PROTOCOL", "HTTP/1.1"),
        "client_ip": request.remote_addr,
        "db_latency_ms": db_latency_ms,
        "uptime_seconds": uptime_sec,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
