"""
Flask application for Gym Booking System.
Acts as the central server in Client-Server architecture (Week 10).
Connects web clients (browsers) with SQLite database and provides REST API.
"""

import os
import time
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
import database

# Initialize Flask app
# Static folder is set to 'public' for easy Vercel & local compatibility
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

app = Flask(__name__, static_folder=PUBLIC_DIR)
START_TIME = time.time()

# Ensure DB is initialized on app startup
database.init_db()


# -------------------------------------------------------------
# Static Frontend Routes
# -------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    if os.path.exists(os.path.join(PUBLIC_DIR, filename)):
        return send_from_directory(PUBLIC_DIR, filename)
    return send_from_directory(PUBLIC_DIR, "index.html")


# -------------------------------------------------------------
# REST API Endpoints (Client / Server Contract)
# -------------------------------------------------------------

@app.route("/api/sessions", methods=["GET"])
def api_get_sessions():
    """
    FR-01: Show available training sessions with date, time, trainer,
    capacity and free places.
    """
    sessions = database.get_all_sessions()
    return jsonify({
        "status": "success",
        "count": len(sessions),
        "data": sessions
    })


@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    """
    Enforces Week 7 Data Governance:
    Only creates session if Session Code matches 'SES-###' and data is valid.
    """
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
            "message": "All fields are required (Completeness rule - Week 7)."
        }), 400

    result = database.add_session(
        session_code=session_code,
        title=title,
        trainer=trainer,
        session_date=session_date,
        session_time=session_time,
        capacity=capacity,
        category=category
    )

    if not result["success"]:
        return jsonify({"status": "error", "message": result["error"]}), 400

    return jsonify({"status": "success", "message": result["message"]}), 201


@app.route("/api/book", methods=["POST"])
def api_book_session():
    """
    FR-02 & FR-03:
    Books training session, avoids overbooking, decreases free seats.
    """
    data = request.get_json() or {}
    session_id = data.get("session_id")
    member_name = data.get("member_name", "")
    member_phone = data.get("member_phone", "")

    if not session_id or not member_name or not member_phone:
        return jsonify({
            "status": "error",
            "message": "Please provide session_id, member_name, and member_phone."
        }), 400

    try:
        session_id = int(session_id)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid session_id."}), 400

    result = database.create_booking(session_id, member_name, member_phone)

    if not result["success"]:
        return jsonify({
            "status": "error",
            "message": result["error"]
        }), 400

    return jsonify({
        "status": "success",
        "message": "Booking confirmed!",
        "data": result
    }), 200


@app.route("/api/bookings", methods=["GET"])
def api_get_bookings():
    """
    Staff / Reception desk endpoint: retrieves all registered bookings.
    """
    bookings = database.get_all_bookings()
    return jsonify({
        "status": "success",
        "count": len(bookings),
        "data": bookings
    })


@app.route("/api/checkin", methods=["POST"])
def api_checkin():
    """
    Week 5 Hardware integration: Receptionist scans QR code or enters booking number.
    Marks member as CHECKED_IN.
    """
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
        "data": result
    }), 200


@app.route("/api/backup", methods=["POST"])
def api_trigger_backup():
    """
    Week 6 Team Security Choice: Automatic Data Backup.
    Creates a physical snapshot of the database.
    """
    data = request.get_json() or {}
    trigger = data.get("trigger", "MANUAL_UI")
    result = database.create_backup(trigger_type=trigger)

    if not result["success"]:
        return jsonify({"status": "error", "message": result["error"]}), 500

    return jsonify({
        "status": "success",
        "message": "Backup created successfully!",
        "data": result
    }), 200


@app.route("/api/backups", methods=["GET"])
def api_list_backups():
    """Returns list of backups and audit log."""
    logs = database.get_backups()
    return jsonify({
        "status": "success",
        "count": len(logs),
        "data": logs
    })


@app.route("/api/reset", methods=["POST"])
def api_reset_db():
    """
    Convenience endpoint for live classroom demonstrations.
    Resets database back to default seed state.
    """
    database.init_db(force_reseed=True)
    return jsonify({
        "status": "success",
        "message": "Database reset to initial presentation seed."
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    """
    Week 10 Telecommunications and Networks diagnostic endpoint.
    Reports server health, uptime, client remote IP, and database latency.
    """
    t0 = time.time()
    # Test DB roundtrip
    _ = database.get_all_sessions()
    db_latency_ms = round((time.time() - t0) * 1000, 2)

    uptime_sec = round(time.time() - START_TIME, 1)

    return jsonify({
        "status": "ONLINE",
        "system": "Gym Booking System - Client/Server Core",
        "architecture": "3-Tier (Client Browser -> Application Server -> SQLite DB)",
        "protocol": request.environ.get("SERVER_PROTOCOL", "HTTP/1.1"),
        "client_ip": request.remote_addr,
        "db_latency_ms": db_latency_ms,
        "uptime_seconds": uptime_sec,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Gym Booking System server running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
