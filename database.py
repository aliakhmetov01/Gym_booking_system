"""
Database module for Gym Booking System.
Supports SQLite locally and in serverless environments (Vercel).
Includes data validation (Week 7), overbooking prevention (Week 4),
and automated backup functionality (Week 6).
"""

import os
import re
import sqlite3
import shutil
from datetime import datetime

# Determine DB path (use /tmp on Vercel if running serverless, otherwise local gym.db)
IS_VERCEL = bool(os.environ.get("VERCEL"))
if IS_VERCEL:
    DB_PATH = "/tmp/gym.db"
    BACKUP_DIR = "/tmp/backups"
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "gym.db")
    BACKUP_DIR = os.path.join(BASE_DIR, "backups")

os.makedirs(BACKUP_DIR, exist_ok=True)


def get_db_connection():
    """Returns a connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(force_reseed=False):
    """
    Initializes database schema and populates seed data if empty.
    Adheres to Week 4 (Requirements) and Week 7 (Data Resource Management).
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Table 1: Training Sessions (UpcomingTrainingSessions from Week 7)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_code TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        trainer TEXT NOT NULL,
        session_date TEXT NOT NULL,
        session_time TEXT NOT NULL,
        capacity INTEGER NOT NULL CHECK (capacity > 0),
        booked_count INTEGER NOT NULL DEFAULT 0 CHECK (booked_count >= 0 AND booked_count <= capacity),
        category TEXT NOT NULL DEFAULT 'Fitness',
        created_at TEXT NOT NULL
    );
    """)

    # Table 2: Bookings (Individual member reservations)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_code TEXT UNIQUE NOT NULL,
        session_id INTEGER NOT NULL,
        member_name TEXT NOT NULL,
        member_phone TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'CONFIRMED',
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
    );
    """)

    # Table 3: Backups log (Audit log for Week 6 Automatic Data Backup)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS backup_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        trigger_type TEXT NOT NULL DEFAULT 'MANUAL'
    );
    """)

    conn.commit()

    # Check if sessions table is empty or forced reseed
    cursor.execute("SELECT COUNT(*) as cnt FROM sessions")
    count = cursor.fetchone()["cnt"]

    if count == 0 or force_reseed:
        if force_reseed:
            cursor.execute("DELETE FROM bookings")
            cursor.execute("DELETE FROM sessions")

        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Test data matching Week 4 & Week 7 specifications
        seed_sessions = [
            (
                "SES-101",
                "CrossFit Conditioning",
                "Daniyar Bolat",
                today_str,
                "09:00",
                12,
                8,
                "CrossFit"
            ),
            (
                "SES-102",
                "Morning Yoga & Mobility",
                "Elena Kim",
                today_str,
                "11:00",
                16,
                14,
                "Yoga"
            ),
            (
                "SES-103",
                "Boxing & Core Technique",
                "Alexey Smirnov",
                today_str,
                "15:30",
                10,
                10,  # 10/10 -> FULL! For testing TC-01 overbooking rejection
                "Boxing"
            ),
            (
                "SES-104",
                "Strength & Hypertrophy",
                "Daniyar Bolat",
                today_str,
                "18:00",
                8,
                2,
                "Strength"
            ),
            (
                "SES-105",
                "Pilates & Spine Health",
                "Aruzhan Sadykova",
                today_str,
                "19:30",
                15,
                7,
                "Pilates"
            ),
        ]

        now_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for sess in seed_sessions:
            cursor.execute("""
                INSERT OR IGNORE INTO sessions 
                (session_code, title, trainer, session_date, session_time, capacity, booked_count, category, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (*sess, now_timestamp))

        # Add sample confirmed booking for SES-101
        cursor.execute("SELECT id FROM sessions WHERE session_code = 'SES-101'")
        s101 = cursor.fetchone()
        if s101:
            cursor.execute("""
                INSERT OR IGNORE INTO bookings 
                (booking_code, session_id, member_name, member_phone, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("BK-10101", s101["id"], "Aidar Bekzhan", "+7 701 555 1234", "CONFIRMED", now_timestamp))

        conn.commit()

    conn.close()


def validate_session_code(code: str) -> bool:
    """
    Validates SessionID format according to Week 7 Data Dictionary:
    Pattern: SES- followed by exactly 3 digits (e.g. SES-101).
    """
    if not code:
        return False
    return bool(re.match(r"^SES-\d{3}$", code.strip().upper()))


def get_all_sessions():
    """Returns all training sessions with calculated remaining places."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            id, session_code, title, trainer, session_date, session_time, 
            capacity, booked_count, (capacity - booked_count) AS free_places,
            category, created_at
        FROM sessions
        ORDER BY session_date ASC, session_time ASC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_session_by_id(session_id: int):
    """Returns single session by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, session_code, title, trainer, session_date, session_time, 
               capacity, booked_count, (capacity - booked_count) AS free_places, category
        FROM sessions WHERE id = ?
    """, (session_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def create_booking(session_id: int, member_name: str, member_phone: str):
    """
    Processes booking transaction.
    Enforces FR-02 and FR-03 (Avoid overbooking: capacity check).
    Uses database transaction lock to avoid race conditions.
    """
    member_name = member_name.strip()
    member_phone = member_phone.strip()

    if not member_name or len(member_name) < 2:
        return {"success": False, "error": "Member name must be at least 2 characters."}
    if not member_phone or len(member_phone) < 6:
        return {"success": False, "error": "Please provide a valid phone number."}

    conn = get_db_connection()
    try:
        # Atomic transaction
        conn.execute("BEGIN IMMEDIATE;")
        cursor = conn.cursor()

        # Fetch session with write lock
        cursor.execute("""
            SELECT id, session_code, title, trainer, session_date, session_time, capacity, booked_count
            FROM sessions WHERE id = ?
        """, (session_id,))
        session = cursor.fetchone()

        if not session:
            conn.rollback()
            conn.close()
            return {"success": False, "error": "Training session not found."}

        # Check Business Rule: FR-03 - No booking if session is full
        if session["booked_count"] >= session["capacity"]:
            conn.rollback()
            conn.close()
            return {
                "success": False, 
                "error": f"Session {session['session_code']} is fully booked ({session['capacity']}/{session['capacity']} places taken). Acceptance criteria FR-03 enforced."
            }

        # Check duplicate booking by the same phone number for this session
        cursor.execute("""
            SELECT id FROM bookings 
            WHERE session_id = ? AND member_phone = ? AND status != 'CANCELLED'
        """, (session_id, member_phone))
        existing = cursor.fetchone()
        if existing:
            conn.rollback()
            conn.close()
            return {"success": False, "error": "You already have an active booking for this session."}

        # Generate unique booking code e.g. BK-74921
        import random
        rand_suffix = random.randint(10000, 99999)
        booking_code = f"BK-{rand_suffix}"

        now_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Insert booking
        cursor.execute("""
            INSERT INTO bookings (booking_code, session_id, member_name, member_phone, status, created_at)
            VALUES (?, ?, ?, ?, 'CONFIRMED', ?)
        """, (booking_code, session_id, member_name, member_phone, now_timestamp))

        # 2. Update booked_count
        cursor.execute("""
            UPDATE sessions 
            SET booked_count = booked_count + 1 
            WHERE id = ?
        """, (session_id,))

        conn.commit()

        # Fetch updated session info
        updated_free = session["capacity"] - (session["booked_count"] + 1)

        result = {
            "success": True,
            "booking_code": booking_code,
            "session": {
                "session_code": session["session_code"],
                "title": session["title"],
                "trainer": session["trainer"],
                "date": session["session_date"],
                "time": session["session_time"],
                "remaining_free": updated_free
            },
            "member_name": member_name,
            "member_phone": member_phone,
            "created_at": now_timestamp
        }
        conn.close()
        return result

    except Exception as e:
        conn.rollback()
        conn.close()
        return {"success": False, "error": f"Booking transaction failed: {str(e)}"}


def get_all_bookings():
    """Returns all member bookings for Receptionist / Staff desk."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            b.id, b.booking_code, b.member_name, b.member_phone, b.status, b.created_at,
            s.session_code, s.title AS session_title, s.trainer, s.session_date, s.session_time
        FROM bookings b
        JOIN sessions s ON b.session_id = s.id
        ORDER BY b.id DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def checkin_booking(code: str):
    """
    Marks a member booking as CHECKED_IN.
    Used by receptionist scanning QR code or entering booking number.
    """
    code = code.strip().upper()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.id, b.booking_code, b.status, b.member_name, s.session_code, s.title
        FROM bookings b
        JOIN sessions s ON b.session_id = s.id
        WHERE b.booking_code = ?
    """, (code,))
    booking = cursor.fetchone()

    if not booking:
        conn.close()
        return {"success": False, "error": f"Booking '{code}' not found."}

    if booking["status"] == "CHECKED_IN":
        conn.close()
        return {"success": False, "error": f"Member {booking['member_name']} is ALREADY checked in."}

    cursor.execute("UPDATE bookings SET status = 'CHECKED_IN' WHERE id = ?", (booking["id"],))
    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": f"Successfully checked in {booking['member_name']} for {booking['session_code']} ({booking['title']}).",
        "booking_code": booking["booking_code"],
        "member_name": booking["member_name"]
    }


def add_session(session_code: str, title: str, trainer: str, session_date: str, session_time: str, capacity: int, category: str = "Fitness"):
    """
    Creates a new session with strict validation (Week 7 Data Quality rules).
    """
    session_code = session_code.strip().upper()
    if not validate_session_code(session_code):
        return {
            "success": False, 
            "error": "Invalid Session Code format. Must be 'SES-###' (e.g. SES-106). Enforces Week 7 Data Governance rule."
        }

    try:
        cap = int(capacity)
        if cap <= 0:
            return {"success": False, "error": "Capacity must be a positive integer."}
    except ValueError:
        return {"success": False, "error": "Capacity must be a number."}

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        now_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO sessions 
            (session_code, title, trainer, session_date, session_time, capacity, booked_count, category, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (session_code, title.strip(), trainer.strip(), session_date, session_time, cap, category, now_timestamp))
        conn.commit()
        conn.close()
        return {"success": True, "message": f"Session {session_code} successfully created."}
    except sqlite3.IntegrityError:
        conn.close()
        return {"success": False, "error": f"Session with code {session_code} already exists (Uniqueness rule)."}


def create_backup(trigger_type: str = "MANUAL"):
    """
    Week 6 Team Security Choice: Automatic Data Backup.
    Creates a physical snapshot of the SQLite database file to safeguard against ransomware/data corruption.
    """
    if not os.path.exists(DB_PATH):
        init_db()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"gym_backup_{timestamp}.db"
    backup_dest = os.path.join(BACKUP_DIR, backup_filename)

    try:
        # Use SQLite backup API for a clean, consistent snapshot
        src_conn = sqlite3.connect(DB_PATH)
        dst_conn = sqlite3.connect(backup_dest)
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()

        file_size = os.path.getsize(backup_dest)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Record in backup log
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO backup_logs (filename, file_path, size_bytes, created_at, trigger_type)
            VALUES (?, ?, ?, ?, ?)
        """, (backup_filename, backup_dest, file_size, now_str, trigger_type))
        conn.commit()
        conn.close()

        return {
            "success": True,
            "filename": backup_filename,
            "size_bytes": file_size,
            "created_at": now_str,
            "backup_dir": BACKUP_DIR
        }
    except Exception as e:
        return {"success": False, "error": f"Backup failed: {str(e)}"}


def get_backups():
    """Returns list of all backups."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM backup_logs ORDER BY id DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
