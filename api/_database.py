"""
Hybrid Database Engine for Gym Booking System.
- Locally (Python with _sqlite3): Uses relational SQLite (gym.db) with atomic transactions and .db backups.
- On Vercel Serverless (where _sqlite3 C-extension is omitted): Automatically switches to a
  pure-Python thread-safe JSON storage engine in /tmp with snapshot backups.
Both engines enforce identical business rules:
  - Week 4 FR-03: no overbooking (booked_count <= capacity)
  - Week 6: snapshot backups
  - Week 7: SES-### session codes
  - BR-04: a member may hold at most MAX_ACTIVE_BOOKINGS active (CONFIRMED) bookings at once
  - BR-05: a booking can be cancelled only within CANCEL_WINDOW_HOURS after it was made
"""

import os
import re
import json
import random
import tempfile
import threading
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash

try:
    import sqlite3
    HAS_SQLITE = True
except ImportError:
    sqlite3 = None
    HAS_SQLITE = False

_STORE_LOCK = threading.Lock()

# ---- Business rules ----
MAX_ACTIVE_BOOKINGS = 2
CANCEL_WINDOW_HOURS = 24

TS_FMT = "%Y-%m-%d %H:%M:%S"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Demo account for presentations (seeded automatically, password is public on purpose)
DEMO_USER = {
    "full_name": "Aidar Bekzhan",
    "email": "demo@gym.kz",
    "phone": "+7 701 555 1234",
    "password": "demo1234",
}


def _now_str():
    return datetime.now().strftime(TS_FMT)


def _detect_storage_paths():
    """Safely determines writable directory for database and backups."""
    is_serverless = bool(
        os.environ.get("VERCEL")
        or os.environ.get("VERCEL_ENV")
        or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
        or os.environ.get("LAMBDA_TASK_ROOT")
    )

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if not is_serverless:
        try:
            backups_dir = os.path.join(project_root, "backups")
            os.makedirs(backups_dir, exist_ok=True)
            probe = os.path.join(backups_dir, ".write_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return (
                os.path.join(project_root, "gym.db"),
                os.path.join(project_root, "gym_store.json"),
                backups_dir,
                False,
            )
        except OSError:
            pass

    tmp_root = tempfile.gettempdir()
    tmp_backups = os.path.join(tmp_root, "gym_backups")
    try:
        os.makedirs(tmp_backups, exist_ok=True)
    except OSError:
        pass

    return (
        os.path.join(tmp_root, "gym.db"),
        os.path.join(tmp_root, "gym_store.json"),
        tmp_backups,
        True,
    )


DB_PATH, JSON_DB_PATH, BACKUP_DIR, IS_SERVERLESS = _detect_storage_paths()

# Use SQLite only if _sqlite3 is available AND we are not forced to serverless JSON
USE_SQLITE = HAS_SQLITE and not IS_SERVERLESS


def get_storage_engine_name():
    """Returns active database engine description for diagnostics."""
    if USE_SQLITE:
        return "SQLite (gym.db)"
    return "Serverless Store (/tmp/gym_store.json)"


def _build_seed_data():
    """Builds the 16 default training sessions (No Yoga) and sample booking."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    now_timestamp = _now_str()

    raw_sessions = [
        # --- TODAY: MORNING SLOTS (08:30 - 11:30) ---
        ("SES-101", "CrossFit Morning Blast", "Daniyar Bolat", today_str, "08:30", 12, 4, "CrossFit"),
        ("SES-102", "Strength: Upper Body Focus", "Timur Aliyev", today_str, "09:30", 10, 3, "Strength"),
        ("SES-103", "Boxing Fundamentals & Bagwork", "Alexey Smirnov", today_str, "10:30", 10, 2, "Boxing"),
        ("SES-104", "Pilates Core & Flexibility", "Aruzhan Sadykova", today_str, "11:30", 15, 5, "Pilates"),

        # --- TODAY: AFTERNOON SLOTS (13:00 - 17:00) ---
        ("SES-105", "HIIT Cardio Extreme", "Daniyar Bolat", today_str, "13:00", 16, 6, "HIIT Cardio"),
        ("SES-106", "Boxing Sparring & Technique", "Alexey Smirnov", today_str, "15:00", 8, 8, "Boxing"),  # FULL 8/8 for TC-01!
        ("SES-107", "CrossFit Conditioning Pro", "Daniyar Bolat", today_str, "16:30", 12, 5, "CrossFit"),

        # --- TODAY: EVENING SLOTS (18:00 - 20:30) ---
        ("SES-108", "Strength & Heavy Hypertrophy", "Timur Aliyev", today_str, "18:00", 10, 4, "Strength"),
        ("SES-109", "Evening Boxing Fight Club", "Alexey Smirnov", today_str, "19:00", 12, 7, "Boxing"),
        ("SES-110", "Pilates Spine Recovery", "Aruzhan Sadykova", today_str, "19:30", 15, 6, "Pilates"),
        ("SES-111", "Night Owl CrossFit", "Daniyar Bolat", today_str, "20:30", 10, 1, "CrossFit"),

        # --- TOMORROW: SLOTS ---
        ("SES-112", "Morning HIIT Cardio", "Aruzhan Sadykova", tomorrow_str, "09:00", 16, 2, "HIIT Cardio"),
        ("SES-113", "Strength: Deadlift & Squat", "Timur Aliyev", tomorrow_str, "11:00", 8, 1, "Strength"),
        ("SES-114", "Boxing Tactical Drill", "Alexey Smirnov", tomorrow_str, "16:00", 10, 3, "Boxing"),
        ("SES-115", "CrossFit Team Challenge", "Daniyar Bolat", tomorrow_str, "18:30", 14, 4, "CrossFit"),
        ("SES-116", "Pilates Balance & Posture", "Aruzhan Sadykova", tomorrow_str, "20:00", 15, 3, "Pilates"),
    ]

    return raw_sessions, now_timestamp


def validate_session_code(code: str) -> bool:
    """Validates SessionID format according to Week 7 Data Dictionary: SES-###."""
    if not code:
        return False
    return bool(re.match(r"^SES-\d{3}$", code.strip().upper()))


def _public_user(u):
    """Strips password hash before returning a user to the API layer."""
    if not u:
        return None
    return {
        "id": u["id"],
        "full_name": u["full_name"],
        "email": u["email"],
        "phone": u["phone"],
        "created_at": u["created_at"],
    }


def _cancel_info(status: str, created_at: str):
    """BR-05: returns (deadline_str, can_cancel, reason) for a booking."""
    try:
        created = datetime.strptime(created_at, TS_FMT)
    except (TypeError, ValueError):
        created = datetime.now()
    deadline = created + timedelta(hours=CANCEL_WINDOW_HOURS)
    deadline_str = deadline.strftime(TS_FMT)

    if status == "CANCELLED":
        return deadline_str, False, "Booking is already cancelled."
    if status == "CHECKED_IN":
        return deadline_str, False, "Member has already checked in."
    if datetime.now() > deadline:
        return deadline_str, False, (
            f"Cancellation window closed. Bookings can be cancelled only within "
            f"{CANCEL_WINDOW_HOURS} hours after booking (deadline was {deadline_str})."
        )
    return deadline_str, True, ""


def _validate_registration(full_name, email, phone, password):
    if len(full_name) < 2:
        return "Full name must be at least 2 characters."
    if not EMAIL_RE.match(email):
        return "Please provide a valid email address."
    if len(re.sub(r"\D", "", phone)) < 6:
        return "Please provide a valid phone number."
    if len(password) < 6:
        return "Password must be at least 6 characters."
    return None


def _new_booking_code(existing_codes):
    code = f"BK-{random.randint(10000, 99999)}"
    while code in existing_codes:
        code = f"BK-{random.randint(10000, 99999)}"
    return code


def _limit_error(active_count):
    return (
        f"Booking limit reached: you already have {active_count} active bookings. "
        f"A member can hold at most {MAX_ACTIVE_BOOKINGS} active bookings at the same time. "
        f"Cancel one or attend a session first."
    )


# =========================================================================
# ENGINE 1: SQLITE IMPLEMENTATION (LOCAL / PERSISTENT DISK)
# =========================================================================

def _sqlite_get_conn():
    needs_init = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    if needs_init:
        _sqlite_init_schema(conn)
    else:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cur.fetchone():
            _sqlite_init_schema(conn)
    return conn


def _sqlite_ensure_demo_user(cursor):
    cursor.execute("SELECT id FROM users WHERE email = ?", (DEMO_USER["email"],))
    row = cursor.fetchone()
    if row:
        return row["id"]
    cursor.execute("""
        INSERT INTO users (full_name, email, phone, password_hash, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        DEMO_USER["full_name"], DEMO_USER["email"], DEMO_USER["phone"],
        generate_password_hash(DEMO_USER["password"]), _now_str(),
    ))
    return cursor.lastrowid


def _sqlite_init_schema(conn, force_reseed=False):
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_code TEXT UNIQUE NOT NULL,
        session_id INTEGER NOT NULL,
        user_id INTEGER,
        member_name TEXT NOT NULL,
        member_phone TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'CONFIRMED',
        created_at TEXT NOT NULL,
        cancelled_at TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
    );
    """)
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

    # Migration for databases created before accounts existed
    cols = {r["name"] for r in cursor.execute("PRAGMA table_info(bookings)").fetchall()}
    if "user_id" not in cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN user_id INTEGER REFERENCES users(id)")
    if "cancelled_at" not in cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN cancelled_at TEXT")

    demo_id = _sqlite_ensure_demo_user(cursor)
    conn.commit()

    cursor.execute("SELECT COUNT(*) as cnt FROM sessions")
    if cursor.fetchone()["cnt"] == 0 or force_reseed:
        if force_reseed:
            cursor.execute("DELETE FROM bookings")
            cursor.execute("DELETE FROM sessions")

        seed_sessions, now_ts = _build_seed_data()
        for sess in seed_sessions:
            cursor.execute("""
                INSERT OR IGNORE INTO sessions
                (session_code, title, trainer, session_date, session_time, capacity, booked_count, category, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (*sess, now_ts))

        cursor.execute("SELECT id FROM sessions WHERE session_code = 'SES-101'")
        s101 = cursor.fetchone()
        if s101:
            cursor.execute("""
                INSERT OR IGNORE INTO bookings
                (booking_code, session_id, user_id, member_name, member_phone, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("BK-10101", s101["id"], demo_id, DEMO_USER["full_name"], DEMO_USER["phone"], "CONFIRMED", now_ts))
        conn.commit()

    # Attach the legacy seed booking to the demo account
    cursor.execute(
        "UPDATE bookings SET user_id = ? WHERE booking_code = 'BK-10101' AND user_id IS NULL",
        (demo_id,),
    )
    conn.commit()


# =========================================================================
# ENGINE 2: PURE-PYTHON SERVERLESS STORE (VERCEL / NO _sqlite3 REQUIRED)
# =========================================================================

_MEMORY_FALLBACK = None


def _json_demo_user_record(user_id=1):
    return {
        "id": user_id,
        "full_name": DEMO_USER["full_name"],
        "email": DEMO_USER["email"],
        "phone": DEMO_USER["phone"],
        "password_hash": generate_password_hash(DEMO_USER["password"]),
        "created_at": _now_str(),
    }


def _json_create_seed_store(users=None):
    seed_sessions, now_ts = _build_seed_data()
    sessions = []
    for idx, (code, title, trainer, sdate, stime, cap, booked, cat) in enumerate(seed_sessions, start=1):
        sessions.append({
            "id": idx,
            "session_code": code,
            "title": title,
            "trainer": trainer,
            "session_date": sdate,
            "session_time": stime,
            "capacity": cap,
            "booked_count": booked,
            "category": cat,
            "created_at": now_ts,
        })

    users = users if users else [_json_demo_user_record(1)]
    demo = next((u for u in users if u["email"] == DEMO_USER["email"]), None)
    if not demo:
        demo = _json_demo_user_record(max(u["id"] for u in users) + 1)
        users.append(demo)

    bookings = [
        {
            "id": 1,
            "booking_code": "BK-10101",
            "session_id": 1,
            "user_id": demo["id"],
            "member_name": DEMO_USER["full_name"],
            "member_phone": DEMO_USER["phone"],
            "status": "CONFIRMED",
            "created_at": now_ts,
            "cancelled_at": None,
        }
    ]
    return {"users": users, "sessions": sessions, "bookings": bookings, "backup_logs": []}


def _json_migrate(store):
    """Upgrades stores created before accounts existed."""
    changed = False
    if "users" not in store:
        store["users"] = []
        changed = True
    if not any(u["email"] == DEMO_USER["email"] for u in store["users"]):
        next_id = max((u["id"] for u in store["users"]), default=0) + 1
        store["users"].append(_json_demo_user_record(next_id))
        changed = True
    demo_id = next(u["id"] for u in store["users"] if u["email"] == DEMO_USER["email"])
    for b in store.get("bookings", []):
        if "user_id" not in b:
            b["user_id"] = demo_id if b["booking_code"] == "BK-10101" else None
            changed = True
        b.setdefault("cancelled_at", None)
    store.setdefault("backup_logs", [])
    return changed


def _json_load_store():
    global _MEMORY_FALLBACK
    data = None
    if os.path.exists(JSON_DB_PATH):
        try:
            with open(JSON_DB_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict) and "sessions" in loaded:
                    data = loaded
        except Exception:
            pass

    if data is None and _MEMORY_FALLBACK is not None:
        data = _MEMORY_FALLBACK

    if data is None:
        data = _json_create_seed_store()
        _json_save_store(data)
        return data

    if _json_migrate(data):
        _json_save_store(data)
    return data


def _json_save_store(store):
    global _MEMORY_FALLBACK
    _MEMORY_FALLBACK = store
    try:
        with open(JSON_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# =========================================================================
# UNIFIED PUBLIC API
# =========================================================================

def init_db(force_reseed=False):
    if USE_SQLITE:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        _sqlite_init_schema(conn, force_reseed=force_reseed)
        conn.close()
    else:
        with _STORE_LOCK:
            if force_reseed:
                # Keep registered accounts, reset schedule and bookings
                existing_users = None
                if os.path.exists(JSON_DB_PATH) or _MEMORY_FALLBACK is not None:
                    existing_users = _json_load_store().get("users")
                _json_save_store(_json_create_seed_store(existing_users))
            else:
                _json_load_store()


# ---------------------------- Accounts ----------------------------------

def register_user(full_name: str, email: str, phone: str, password: str):
    full_name = (full_name or "").strip()
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    password = password or ""

    err = _validate_registration(full_name, email, phone, password)
    if err:
        return {"success": False, "error": err}

    pw_hash = generate_password_hash(password)
    now_ts = _now_str()

    if USE_SQLITE:
        conn = _sqlite_get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO users (full_name, email, phone, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (full_name, email, phone, pw_hash, now_ts))
            conn.commit()
            user = {"id": cur.lastrowid, "full_name": full_name, "email": email,
                    "phone": phone, "created_at": now_ts}
            return {"success": True, "user": user}
        except sqlite3.IntegrityError:
            return {"success": False, "error": "An account with this email already exists."}
        finally:
            conn.close()

    with _STORE_LOCK:
        store = _json_load_store()
        if any(u["email"] == email for u in store["users"]):
            return {"success": False, "error": "An account with this email already exists."}
        user = {
            "id": max((u["id"] for u in store["users"]), default=0) + 1,
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "password_hash": pw_hash,
            "created_at": now_ts,
        }
        store["users"].append(user)
        _json_save_store(store)
        return {"success": True, "user": _public_user(user)}


def _find_user(field: str, value):
    if USE_SQLITE:
        conn = _sqlite_get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM users WHERE {field} = ?", (value,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    with _STORE_LOCK:
        store = _json_load_store()
        u = next((u for u in store["users"] if u[field] == value), None)
        return dict(u) if u else None


def authenticate_user(email: str, password: str):
    email = (email or "").strip().lower()
    user = _find_user("email", email)
    if not user or not check_password_hash(user["password_hash"], password or ""):
        return {"success": False, "error": "Invalid email or password."}
    return {"success": True, "user": _public_user(user)}


def get_user_by_id(user_id: int):
    return _public_user(_find_user("id", user_id))


# ---------------------------- Sessions ----------------------------------

def get_all_sessions():
    if USE_SQLITE:
        conn = _sqlite_get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, session_code, title, trainer, session_date, session_time,
                   capacity, booked_count, (capacity - booked_count) AS free_places,
                   category, created_at
            FROM sessions
            ORDER BY session_date ASC, session_time ASC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    with _STORE_LOCK:
        store = _json_load_store()
        rows = []
        for s in store["sessions"]:
            item = dict(s)
            item["free_places"] = s["capacity"] - s["booked_count"]
            rows.append(item)
        rows.sort(key=lambda x: (x["session_date"], x["session_time"]))
        return rows


def get_session_by_id(session_id: int):
    if USE_SQLITE:
        conn = _sqlite_get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, session_code, title, trainer, session_date, session_time,
                   capacity, booked_count, (capacity - booked_count) AS free_places, category
            FROM sessions WHERE id = ?
        """, (session_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    with _STORE_LOCK:
        store = _json_load_store()
        for s in store["sessions"]:
            if s["id"] == session_id:
                item = dict(s)
                item["free_places"] = s["capacity"] - s["booked_count"]
                return item
        return None


# ---------------------------- Bookings ----------------------------------

def _booking_result(booking_code, session, user, remaining_free, now_ts):
    return {
        "success": True,
        "booking_code": booking_code,
        "session": {
            "session_code": session["session_code"],
            "title": session["title"],
            "trainer": session["trainer"],
            "date": session["session_date"],
            "time": session["session_time"],
            "remaining_free": remaining_free,
        },
        "member_name": user["full_name"],
        "member_phone": user["phone"],
        "created_at": now_ts,
        "cancel_deadline": _cancel_info("CONFIRMED", now_ts)[0],
    }


def create_booking(session_id: int, user_id: int):
    """FR-02/FR-03 + BR-04: books a session for a logged-in member."""
    user = _find_user("id", user_id)
    if not user:
        return {"success": False, "error": "Account not found. Please log in again."}

    if USE_SQLITE:
        conn = _sqlite_get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.cursor()
            cur.execute("""
                SELECT id, session_code, title, trainer, session_date, session_time, capacity, booked_count
                FROM sessions WHERE id = ?
            """, (session_id,))
            session = cur.fetchone()

            if not session:
                conn.rollback()
                return {"success": False, "error": "Training session not found."}

            if session["booked_count"] >= session["capacity"]:
                conn.rollback()
                return {
                    "success": False,
                    "error": f"Session {session['session_code']} is fully booked ({session['capacity']}/{session['capacity']} places taken). Acceptance criteria FR-03 enforced.",
                }

            cur.execute("""
                SELECT id FROM bookings
                WHERE session_id = ? AND user_id = ? AND status != 'CANCELLED'
            """, (session_id, user_id))
            if cur.fetchone():
                conn.rollback()
                return {"success": False, "error": "You already have a booking for this session."}

            cur.execute(
                "SELECT COUNT(*) AS cnt FROM bookings WHERE user_id = ? AND status = 'CONFIRMED'",
                (user_id,),
            )
            active = cur.fetchone()["cnt"]
            if active >= MAX_ACTIVE_BOOKINGS:
                conn.rollback()
                return {"success": False, "error": _limit_error(active)}

            cur.execute("SELECT booking_code FROM bookings")
            booking_code = _new_booking_code({r["booking_code"] for r in cur.fetchall()})
            now_ts = _now_str()

            cur.execute("""
                INSERT INTO bookings (booking_code, session_id, user_id, member_name, member_phone, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'CONFIRMED', ?)
            """, (booking_code, session_id, user_id, user["full_name"], user["phone"], now_ts))

            cur.execute("UPDATE sessions SET booked_count = booked_count + 1 WHERE id = ?", (session_id,))
            conn.commit()
            remaining = session["capacity"] - (session["booked_count"] + 1)
            return _booking_result(booking_code, session, user, remaining, now_ts)
        except Exception as e:
            conn.rollback()
            return {"success": False, "error": f"Booking transaction failed: {str(e)}"}
        finally:
            conn.close()

    # Pure-Python Serverless Store
    with _STORE_LOCK:
        store = _json_load_store()
        session = next((s for s in store["sessions"] if s["id"] == session_id), None)
        if not session:
            return {"success": False, "error": "Training session not found."}

        if session["booked_count"] >= session["capacity"]:
            return {
                "success": False,
                "error": f"Session {session['session_code']} is fully booked ({session['capacity']}/{session['capacity']} places taken). Acceptance criteria FR-03 enforced.",
            }

        if any(b["session_id"] == session_id and b.get("user_id") == user_id and b["status"] != "CANCELLED"
               for b in store["bookings"]):
            return {"success": False, "error": "You already have a booking for this session."}

        active = sum(1 for b in store["bookings"] if b.get("user_id") == user_id and b["status"] == "CONFIRMED")
        if active >= MAX_ACTIVE_BOOKINGS:
            return {"success": False, "error": _limit_error(active)}

        booking_code = _new_booking_code({b["booking_code"] for b in store["bookings"]})
        now_ts = _now_str()
        next_id = max((b["id"] for b in store["bookings"]), default=0) + 1

        store["bookings"].append({
            "id": next_id,
            "booking_code": booking_code,
            "session_id": session_id,
            "user_id": user_id,
            "member_name": user["full_name"],
            "member_phone": user["phone"],
            "status": "CONFIRMED",
            "created_at": now_ts,
            "cancelled_at": None,
        })
        session["booked_count"] += 1
        _json_save_store(store)
        return _booking_result(booking_code, session, user, session["capacity"] - session["booked_count"], now_ts)


def _decorate_user_booking(b):
    deadline, can_cancel, reason = _cancel_info(b["status"], b["created_at"])
    b["cancel_deadline"] = deadline
    b["can_cancel"] = can_cancel
    b["cancel_block_reason"] = reason
    return b


def get_user_bookings(user_id: int):
    """Profile: all bookings of one member with cancellation eligibility (BR-05)."""
    if USE_SQLITE:
        conn = _sqlite_get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT b.id, b.booking_code, b.status, b.created_at, b.cancelled_at,
                   s.id AS session_id, s.session_code, s.title AS session_title, s.trainer,
                   s.category, s.session_date, s.session_time
            FROM bookings b
            JOIN sessions s ON b.session_id = s.id
            WHERE b.user_id = ?
            ORDER BY b.id DESC
        """, (user_id,))
        rows = [_decorate_user_booking(dict(r)) for r in cur.fetchall()]
        conn.close()
        return rows

    with _STORE_LOCK:
        store = _json_load_store()
        sessions_by_id = {s["id"]: s for s in store["sessions"]}
        rows = []
        for b in sorted(store["bookings"], key=lambda x: x["id"], reverse=True):
            if b.get("user_id") != user_id:
                continue
            s = sessions_by_id.get(b["session_id"], {})
            rows.append(_decorate_user_booking({
                "id": b["id"],
                "booking_code": b["booking_code"],
                "status": b["status"],
                "created_at": b["created_at"],
                "cancelled_at": b.get("cancelled_at"),
                "session_id": b["session_id"],
                "session_code": s.get("session_code", ""),
                "session_title": s.get("title", ""),
                "trainer": s.get("trainer", ""),
                "category": s.get("category", ""),
                "session_date": s.get("session_date", ""),
                "session_time": s.get("session_time", ""),
            }))
        return rows


def cancel_booking(booking_code: str, user_id: int):
    """BR-05: owner may cancel a CONFIRMED booking within 24h after booking; frees the seat."""
    code = (booking_code or "").strip().upper()

    if USE_SQLITE:
        conn = _sqlite_get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.cursor()
            cur.execute("""
                SELECT b.id, b.session_id, b.user_id, b.status, b.created_at, s.session_code, s.title
                FROM bookings b JOIN sessions s ON b.session_id = s.id
                WHERE b.booking_code = ?
            """, (code,))
            b = cur.fetchone()
            if not b:
                conn.rollback()
                return {"success": False, "error": f"Booking '{code}' not found."}
            if b["user_id"] != user_id:
                conn.rollback()
                return {"success": False, "error": "You can only cancel your own bookings."}
            _, can_cancel, reason = _cancel_info(b["status"], b["created_at"])
            if not can_cancel:
                conn.rollback()
                return {"success": False, "error": reason}

            cur.execute("UPDATE bookings SET status = 'CANCELLED', cancelled_at = ? WHERE id = ?",
                        (_now_str(), b["id"]))
            cur.execute("UPDATE sessions SET booked_count = booked_count - 1 WHERE id = ? AND booked_count > 0",
                        (b["session_id"],))
            conn.commit()
            return {"success": True, "message": f"Booking {code} for {b['session_code']} ({b['title']}) cancelled. The spot is free again."}
        except Exception as e:
            conn.rollback()
            return {"success": False, "error": f"Cancellation failed: {str(e)}"}
        finally:
            conn.close()

    with _STORE_LOCK:
        store = _json_load_store()
        b = next((x for x in store["bookings"] if x["booking_code"] == code), None)
        if not b:
            return {"success": False, "error": f"Booking '{code}' not found."}
        if b.get("user_id") != user_id:
            return {"success": False, "error": "You can only cancel your own bookings."}
        _, can_cancel, reason = _cancel_info(b["status"], b["created_at"])
        if not can_cancel:
            return {"success": False, "error": reason}

        b["status"] = "CANCELLED"
        b["cancelled_at"] = _now_str()
        session = next((s for s in store["sessions"] if s["id"] == b["session_id"]), None)
        if session and session["booked_count"] > 0:
            session["booked_count"] -= 1
        _json_save_store(store)
        label = f"{session['session_code']} ({session['title']})" if session else ""
        return {"success": True, "message": f"Booking {code} for {label} cancelled. The spot is free again."}


def get_all_bookings():
    if USE_SQLITE:
        conn = _sqlite_get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT b.id, b.booking_code, b.member_name, b.member_phone, b.status, b.created_at,
                   s.session_code, s.title AS session_title, s.trainer, s.session_date, s.session_time
            FROM bookings b
            JOIN sessions s ON b.session_id = s.id
            ORDER BY b.id DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    with _STORE_LOCK:
        store = _json_load_store()
        sessions_by_id = {s["id"]: s for s in store["sessions"]}
        rows = []
        for b in sorted(store["bookings"], key=lambda x: x["id"], reverse=True):
            s = sessions_by_id.get(b["session_id"], {})
            rows.append({
                "id": b["id"],
                "booking_code": b["booking_code"],
                "member_name": b["member_name"],
                "member_phone": b["member_phone"],
                "status": b["status"],
                "created_at": b["created_at"],
                "session_code": s.get("session_code", ""),
                "session_title": s.get("title", ""),
                "trainer": s.get("trainer", ""),
                "session_date": s.get("session_date", ""),
                "session_time": s.get("session_time", ""),
            })
        return rows


def checkin_booking(code: str):
    code = code.strip().upper()
    if USE_SQLITE:
        conn = _sqlite_get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT b.id, b.booking_code, b.status, b.member_name, s.session_code, s.title
            FROM bookings b
            JOIN sessions s ON b.session_id = s.id
            WHERE b.booking_code = ?
        """, (code,))
        booking = cur.fetchone()

        if not booking:
            conn.close()
            return {"success": False, "error": f"Booking '{code}' not found."}
        if booking["status"] == "CHECKED_IN":
            conn.close()
            return {"success": False, "error": f"Member {booking['member_name']} is ALREADY checked in."}
        if booking["status"] == "CANCELLED":
            conn.close()
            return {"success": False, "error": f"Booking {code} was cancelled and is no longer valid."}

        cur.execute("UPDATE bookings SET status = 'CHECKED_IN' WHERE id = ?", (booking["id"],))
        conn.commit()
        conn.close()
        return {
            "success": True,
            "message": f"Successfully checked in {booking['member_name']} for {booking['session_code']} ({booking['title']}).",
            "booking_code": booking["booking_code"],
            "member_name": booking["member_name"],
        }

    with _STORE_LOCK:
        store = _json_load_store()
        booking = next((b for b in store["bookings"] if b["booking_code"] == code), None)
        if not booking:
            return {"success": False, "error": f"Booking '{code}' not found."}
        if booking["status"] == "CHECKED_IN":
            return {"success": False, "error": f"Member {booking['member_name']} is ALREADY checked in."}
        if booking["status"] == "CANCELLED":
            return {"success": False, "error": f"Booking {code} was cancelled and is no longer valid."}

        booking["status"] = "CHECKED_IN"
        _json_save_store(store)
        session = next((s for s in store["sessions"] if s["id"] == booking["session_id"]), {})
        return {
            "success": True,
            "message": f"Successfully checked in {booking['member_name']} for {session.get('session_code', '')} ({session.get('title', '')}).",
            "booking_code": booking["booking_code"],
            "member_name": booking["member_name"],
        }


def add_session(session_code: str, title: str, trainer: str, session_date: str, session_time: str, capacity: int, category: str = "Fitness"):
    session_code = session_code.strip().upper()
    if not validate_session_code(session_code):
        return {
            "success": False,
            "error": "Invalid Session Code format. Must be 'SES-###' (e.g. SES-117). Enforces Week 7 Data Governance rule.",
        }

    try:
        cap = int(capacity)
        if cap <= 0:
            return {"success": False, "error": "Capacity must be a positive integer."}
    except ValueError:
        return {"success": False, "error": "Capacity must be a number."}

    if USE_SQLITE:
        conn = _sqlite_get_conn()
        cur = conn.cursor()
        try:
            now_ts = _now_str()
            cur.execute("""
                INSERT INTO sessions
                (session_code, title, trainer, session_date, session_time, capacity, booked_count, category, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """, (session_code, title.strip(), trainer.strip(), session_date, session_time, cap, category, now_ts))
            conn.commit()
            conn.close()
            return {"success": True, "message": f"Session {session_code} successfully created."}
        except sqlite3.IntegrityError:
            conn.close()
            return {"success": False, "error": f"Session with code {session_code} already exists (Uniqueness rule)."}

    with _STORE_LOCK:
        store = _json_load_store()
        if any(s["session_code"] == session_code for s in store["sessions"]):
            return {"success": False, "error": f"Session with code {session_code} already exists (Uniqueness rule)."}

        next_id = max((s["id"] for s in store["sessions"]), default=0) + 1
        now_ts = _now_str()
        store["sessions"].append({
            "id": next_id,
            "session_code": session_code,
            "title": title.strip(),
            "trainer": trainer.strip(),
            "session_date": session_date,
            "session_time": session_time,
            "capacity": cap,
            "booked_count": 0,
            "category": category,
            "created_at": now_ts,
        })
        _json_save_store(store)
        return {"success": True, "message": f"Session {session_code} successfully created."}


def create_backup(trigger_type: str = "MANUAL"):
    """Week 6 Team Security Choice: Physical DB Snapshot Backup."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    now_str = _now_str()

    if USE_SQLITE:
        if not os.path.exists(DB_PATH):
            init_db()
        backup_filename = f"gym_backup_{timestamp}.db"
        backup_dest = os.path.join(BACKUP_DIR, backup_filename)
        try:
            src_conn = sqlite3.connect(DB_PATH)
            dst_conn = sqlite3.connect(backup_dest)
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()

            file_size = os.path.getsize(backup_dest)
            conn = _sqlite_get_conn()
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
                "backup_dir": BACKUP_DIR,
            }
        except Exception as e:
            return {"success": False, "error": f"Backup failed: {str(e)}"}

    # Pure-Python Serverless Backup
    with _STORE_LOCK:
        store = _json_load_store()
        backup_filename = f"gym_backup_{timestamp}.json"
        backup_dest = os.path.join(BACKUP_DIR, backup_filename)
        snapshot_bytes = json.dumps(store, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            with open(backup_dest, "wb") as f:
                f.write(snapshot_bytes)
        except OSError:
            pass

        next_id = max((b["id"] for b in store["backup_logs"]), default=0) + 1
        store["backup_logs"].append({
            "id": next_id,
            "filename": backup_filename,
            "file_path": backup_dest,
            "size_bytes": len(snapshot_bytes),
            "created_at": now_str,
            "trigger_type": trigger_type,
        })
        _json_save_store(store)
        return {
            "success": True,
            "filename": backup_filename,
            "size_bytes": len(snapshot_bytes),
            "created_at": now_str,
            "backup_dir": BACKUP_DIR,
        }


def get_backups():
    if USE_SQLITE:
        conn = _sqlite_get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM backup_logs ORDER BY id DESC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    with _STORE_LOCK:
        store = _json_load_store()
        return list(sorted(store["backup_logs"], key=lambda x: x["id"], reverse=True))
