"""
Supabase / PostgreSQL storage engine for Gym Booking System.

Activated automatically when the DATABASE_URL environment variable is set
(see supabase/SUPABASE_SETUP.md). The schema is created once by supabase/schema.sql.

It implements the same public functions as the SQLite / JSON engines in _database.py
and returns data in the same shape (dates and times as strings), so the API and the
frontend do not need to know which database is used.
Business rules are checked here AND by triggers in Postgres (double protection).
"""

import json
import os
import threading
from datetime import date, datetime, time

import psycopg
from psycopg.rows import dict_row

H = None             # helpers & constants from _database.py, set by bind()
DATABASE_URL = None
_init_lock = threading.Lock()
_initialized = False


# =========================================================================
# Connection & value conversion
# =========================================================================

def _connect():
    # prepare_threshold=None: required by Supabase "Transaction pooler" (port 6543)
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=10,
    )


def _to_str(v):
    """Converts Postgres date/time values into the strings the frontend expects."""
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone().replace(tzinfo=None)   # server local time, like datetime.now()
        return v.strftime(H.TS_FMT)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, time):
        return v.strftime("%H:%M")
    return v


def _row(r):
    return {k: _to_str(v) for k, v in r.items()} if r else None


def _rows(rs):
    return [_row(r) for r in rs]


def _db_error_message(e):
    """Turns a trigger / constraint error into a readable message."""
    diag = getattr(e, "diag", None)
    msg = getattr(diag, "message_primary", None) if diag else None
    return msg or str(e).strip().splitlines()[0]


SESSION_COLS = """
    id, session_code, title, trainer, session_date, session_time,
    capacity, booked_count, (capacity - booked_count) AS free_places, category, created_at
"""


# =========================================================================
# Setup
# =========================================================================

# Columns added after the first version of supabase/schema.sql.
# They are created automatically, so an existing Supabase database needs no manual SQL.
USER_COLUMNS = {
    "role": "text not null default 'member' check (role in ('member', 'admin'))",
    "streak": "integer not null default 0",
    "best_streak": "integer not null default 0",
    "last_booking_at": "timestamptz",
}


def _migrate(cur):
    cur.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'users'"""
    )
    existing = {r["column_name"] for r in cur.fetchall()}
    for column, ddl in USER_COLUMNS.items():
        if column not in existing:
            cur.execute(f"ALTER TABLE public.users ADD COLUMN {column} {ddl}")


def _ensure_admin(cur):
    """Creates the admin account or syncs its role/password with ADMIN_PASSWORD."""
    pw = H._admin_password()
    if not pw:
        return   # no ADMIN_PASSWORD on a public deployment: never create a guessable admin
    cur.execute("SELECT id, password_hash, role FROM users WHERE email = %s", (H.ADMIN_EMAIL,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            """INSERT INTO users (full_name, email, phone, password_hash, role)
               VALUES (%s, %s, '-', %s, 'admin')""",
            (H.ADMIN_NAME, H.ADMIN_EMAIL, H.generate_password_hash(pw)),
        )
        return
    if row["role"] != "admin":
        cur.execute("UPDATE users SET role = 'admin' WHERE id = %s", (row["id"],))
    if not H.check_password_hash(row["password_hash"], pw):
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                    (H.generate_password_hash(pw), row["id"]))


def _ensure_demo_user(cur):
    cur.execute("SELECT id FROM users WHERE email = %s", (H.DEMO_USER["email"],))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        """INSERT INTO users (full_name, email, phone, password_hash)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (H.DEMO_USER["full_name"], H.DEMO_USER["email"], H.DEMO_USER["phone"],
         H.generate_password_hash(H.DEMO_USER["password"])),
    )
    return cur.fetchone()["id"]


def _seed_schedule(cur, demo_id):
    seed_sessions, _ = H._build_seed_data()
    for (code, title, trainer, sdate, stime, cap, booked, cat) in seed_sessions:
        cur.execute(
            """INSERT INTO sessions
               (session_code, title, trainer, session_date, session_time, capacity, booked_count, category)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (session_code) DO NOTHING""",
            (code, title, trainer, sdate, stime, cap, booked, cat),
        )
    cur.execute("SELECT id FROM sessions WHERE session_code = 'SES-101'")
    s101 = cur.fetchone()
    if s101:
        cur.execute(
            """INSERT INTO bookings (booking_code, session_id, user_id, member_name, member_phone, status)
               VALUES ('BK-10101', %s, %s, %s, %s, 'CONFIRMED')
               ON CONFLICT (booking_code) DO NOTHING""",
            (s101["id"], demo_id, H.DEMO_USER["full_name"], H.DEMO_USER["phone"]),
        )


def init_db(force_reseed=False):
    """Creates the demo account; on reset, re-seeds schedule and bookings (accounts are kept)."""
    global _initialized
    if _initialized and not force_reseed:
        return
    with _init_lock:
        with _connect() as conn, conn.cursor() as cur:
            _migrate(cur)
            demo_id = _ensure_demo_user(cur)
            _ensure_admin(cur)
            if force_reseed:
                cur.execute("DELETE FROM bookings")
                cur.execute("DELETE FROM sessions")
                _seed_schedule(cur, demo_id)
            else:
                cur.execute("SELECT COUNT(*) AS cnt FROM sessions")
                if cur.fetchone()["cnt"] == 0:
                    _seed_schedule(cur, demo_id)
        _initialized = True


def get_storage_engine_name():
    return "Supabase PostgreSQL"


# =========================================================================
# Accounts
# =========================================================================

def register_user(full_name, email, phone, password):
    full_name = (full_name or "").strip()
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    password = password or ""

    err = H._validate_registration(full_name, email, phone, password)
    if err:
        return {"success": False, "error": err}

    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users (full_name, email, phone, password_hash)
                   VALUES (%s, %s, %s, %s)
                   RETURNING *""",
                (full_name, email, phone, H.generate_password_hash(password)),
            )
            return {"success": True, "user": H._public_user(_row(cur.fetchone()))}
    except psycopg.errors.UniqueViolation:
        return {"success": False, "error": "An account with this email already exists."}


def _find_user(field, value):
    if field not in ("id", "email"):
        raise ValueError("unsupported lookup field")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM users WHERE {field} = %s", (value,))
        return _row(cur.fetchone())


def authenticate_user(email, password):
    user = _find_user("email", (email or "").strip().lower())
    if not user or not H.check_password_hash(user["password_hash"], password or ""):
        return {"success": False, "error": "Invalid email or password."}
    return {"success": True, "user": H._public_user(user)}


def get_user_by_id(user_id):
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    return H._public_user(_find_user("id", user_id))


# =========================================================================
# Sessions
# =========================================================================

def get_all_sessions():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {SESSION_COLS} FROM sessions ORDER BY session_date, session_time")
        return _rows(cur.fetchall())


def get_session_by_id(session_id):
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {SESSION_COLS} FROM sessions WHERE id = %s", (session_id,))
        return _row(cur.fetchone())


def add_session(session_code, title, trainer, session_date, session_time, capacity, category="Fitness"):
    session_code = (session_code or "").strip().upper()
    if not H.validate_session_code(session_code):
        return {
            "success": False,
            "error": "Invalid Session Code format. Must be 'SES-###' (e.g. SES-117). Enforces Week 7 Data Governance rule.",
        }
    try:
        cap = int(capacity)
        if cap <= 0:
            return {"success": False, "error": "Capacity must be a positive integer."}
    except (TypeError, ValueError):
        return {"success": False, "error": "Capacity must be a number."}

    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sessions
                   (session_code, title, trainer, session_date, session_time, capacity, booked_count, category)
                   VALUES (%s, %s, %s, %s, %s, %s, 0, %s)""",
                (session_code, title.strip(), trainer.strip(), session_date, session_time, cap, category),
            )
        return {"success": True, "message": f"Session {session_code} successfully created."}
    except psycopg.errors.UniqueViolation:
        return {"success": False, "error": f"Session with code {session_code} already exists (Uniqueness rule)."}
    except psycopg.errors.DataError as e:
        return {"success": False, "error": f"Invalid date or time: {_db_error_message(e)}"}


# =========================================================================
# Bookings
# =========================================================================

def create_booking(session_id, user_id):
    """FR-02/FR-03 + BR-04, inside one transaction with the session row locked."""
    user = _find_user("id", user_id)
    if not user:
        return {"success": False, "error": "Account not found. Please log in again."}

    try:
        with _connect() as conn, conn.cursor() as cur:
            # Lock the session row: concurrent bookings of the same session wait here
            cur.execute(f"SELECT {SESSION_COLS} FROM sessions WHERE id = %s FOR UPDATE", (session_id,))
            session = _row(cur.fetchone())
            if not session:
                return {"success": False, "error": "Training session not found."}

            if session["booked_count"] >= session["capacity"]:
                return {
                    "success": False,
                    "error": f"Session {session['session_code']} is fully booked ({session['capacity']}/{session['capacity']} places taken). Acceptance criteria FR-03 enforced.",
                }

            cur.execute(
                "SELECT 1 FROM bookings WHERE session_id = %s AND user_id = %s AND status <> 'CANCELLED'",
                (session_id, user_id),
            )
            if cur.fetchone():
                return {"success": False, "error": "You already have a booking for this session."}

            cur.execute(
                "SELECT COUNT(*) AS cnt FROM bookings WHERE user_id = %s AND status = 'CONFIRMED'",
                (user_id,),
            )
            active = cur.fetchone()["cnt"]
            if active >= H.MAX_ACTIVE_BOOKINGS:
                return {"success": False, "error": H._limit_error(active)}

            cur.execute("SELECT booking_code FROM bookings")
            booking_code = H._new_booking_code({r["booking_code"] for r in cur.fetchall()})

            cur.execute(
                """INSERT INTO bookings (booking_code, session_id, user_id, member_name, member_phone, status)
                   VALUES (%s, %s, %s, %s, %s, 'CONFIRMED')
                   RETURNING created_at""",
                (booking_code, session_id, user_id, user["full_name"], user["phone"]),
            )
            created_at = _to_str(cur.fetchone()["created_at"])
            cur.execute("UPDATE sessions SET booked_count = booked_count + 1 WHERE id = %s", (session_id,))

            # Gamification: +1 flame, in the same transaction
            cur.execute("SELECT streak, best_streak, last_booking_at FROM users WHERE id = %s FOR UPDATE",
                        (user_id,))
            streak, best = H._streak_after_booking(_row(cur.fetchone()))
            cur.execute("UPDATE users SET streak = %s, best_streak = %s, last_booking_at = now() WHERE id = %s",
                        (streak, best, user_id))

        remaining = session["capacity"] - (session["booked_count"] + 1)
        return H._booking_result(booking_code, session, user, remaining, created_at, streak, best)
    except psycopg.errors.CheckViolation as e:       # raised by DB triggers / constraints
        return {"success": False, "error": _db_error_message(e)}
    except psycopg.errors.UniqueViolation:
        return {"success": False, "error": "You already have a booking for this session."}


def get_user_bookings(user_id):
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT b.id, b.booking_code, b.status, b.created_at, b.cancelled_at,
                      s.id AS session_id, s.session_code, s.title AS session_title, s.trainer,
                      s.category, s.session_date, s.session_time
               FROM bookings b JOIN sessions s ON b.session_id = s.id
               WHERE b.user_id = %s
               ORDER BY b.id DESC""",
            (user_id,),
        )
        return [H._decorate_user_booking(r) for r in _rows(cur.fetchall())]


def cancel_booking(booking_code, user_id):
    code = (booking_code or "").strip().upper()
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT b.id, b.session_id, b.user_id, b.status, b.created_at, s.session_code, s.title
                   FROM bookings b JOIN sessions s ON b.session_id = s.id
                   WHERE b.booking_code = %s
                   FOR UPDATE OF b""",
                (code,),
            )
            b = _row(cur.fetchone())
            if not b:
                return {"success": False, "error": f"Booking '{code}' not found."}
            if b["user_id"] != user_id:
                return {"success": False, "error": "You can only cancel your own bookings."}
            _, can_cancel, reason = H._cancel_info(b["status"], b["created_at"])
            if not can_cancel:
                return {"success": False, "error": reason}

            cur.execute("UPDATE bookings SET status = 'CANCELLED', cancelled_at = now() WHERE id = %s", (b["id"],))
            cur.execute(
                "UPDATE sessions SET booked_count = booked_count - 1 WHERE id = %s AND booked_count > 0",
                (b["session_id"],),
            )
            # Gamification: the cancelled booking takes its flame back
            cur.execute("SELECT streak, best_streak, last_booking_at FROM users WHERE id = %s FOR UPDATE",
                        (user_id,))
            cur.execute("UPDATE users SET streak = %s WHERE id = %s",
                        (H._streak_after_cancel(_row(cur.fetchone())), user_id))
        return {"success": True, "message": f"Booking {code} for {b['session_code']} ({b['title']}) cancelled. The spot is free again."}
    except psycopg.errors.CheckViolation as e:
        return {"success": False, "error": _db_error_message(e)}


def get_all_bookings():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT b.id, b.booking_code, b.member_name, b.member_phone, b.status, b.created_at,
                      s.session_code, s.title AS session_title, s.trainer, s.session_date, s.session_time
               FROM bookings b JOIN sessions s ON b.session_id = s.id
               ORDER BY b.id DESC"""
        )
        return _rows(cur.fetchall())


def checkin_booking(code):
    code = (code or "").strip().upper()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT b.id, b.booking_code, b.status, b.member_name, s.session_code, s.title
               FROM bookings b JOIN sessions s ON b.session_id = s.id
               WHERE b.booking_code = %s FOR UPDATE OF b""",
            (code,),
        )
        b = cur.fetchone()
        if not b:
            return {"success": False, "error": f"Booking '{code}' not found."}
        if b["status"] == "CHECKED_IN":
            return {"success": False, "error": f"Member {b['member_name']} is ALREADY checked in."}
        if b["status"] == "CANCELLED":
            return {"success": False, "error": f"Booking {code} was cancelled and is no longer valid."}
        cur.execute("UPDATE bookings SET status = 'CHECKED_IN' WHERE id = %s", (b["id"],))
    return {
        "success": True,
        "message": f"Successfully checked in {b['member_name']} for {b['session_code']} ({b['title']}).",
        "booking_code": b["booking_code"],
        "member_name": b["member_name"],
    }


# =========================================================================
# Backups (Week 6): JSON export of all tables + audit log row
# =========================================================================

def create_backup(trigger_type="MANUAL"):
    now = datetime.now()
    filename = f"gym_backup_{now.strftime('%Y%m%d_%H%M%S')}.json"
    dest = os.path.join(H.BACKUP_DIR, filename)
    try:
        with _connect() as conn, conn.cursor() as cur:
            snapshot = {}
            for table in ("sessions", "bookings", "backup_logs"):
                cur.execute(f"SELECT * FROM {table} ORDER BY id")
                snapshot[table] = _rows(cur.fetchall())
            # Accounts without password hashes
            cur.execute("SELECT id, full_name, email, phone, created_at FROM users ORDER BY id")
            snapshot["users"] = _rows(cur.fetchall())

            data = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str).encode("utf-8")
            try:
                os.makedirs(H.BACKUP_DIR, exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(data)
            except OSError:
                pass   # read-only filesystem: the audit row still records the snapshot

            cur.execute(
                """INSERT INTO backup_logs (filename, file_path, size_bytes, trigger_type)
                   VALUES (%s, %s, %s, %s) RETURNING created_at""",
                (filename, dest, len(data), trigger_type),
            )
            created_at = _to_str(cur.fetchone()["created_at"])
        return {
            "success": True,
            "filename": filename,
            "size_bytes": len(data),
            "created_at": created_at,
            "backup_dir": H.BACKUP_DIR,
        }
    except Exception as e:
        return {"success": False, "error": f"Backup failed: {str(e)}"}


def get_backups():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM backup_logs ORDER BY id DESC")
        return _rows(cur.fetchall())


# =========================================================================
# Wiring
# =========================================================================

PUBLIC_FUNCTIONS = (
    "init_db", "get_storage_engine_name",
    "register_user", "authenticate_user", "get_user_by_id",
    "get_all_sessions", "get_session_by_id", "add_session",
    "create_booking", "get_user_bookings", "cancel_booking",
    "get_all_bookings", "checkin_booking",
    "create_backup", "get_backups",
)


def bind(namespace: dict, database_url: str):
    """Replaces the public functions of _database.py with the Postgres versions."""
    global H, DATABASE_URL

    class _Helpers:
        pass

    H = _Helpers()
    for name, value in namespace.items():
        setattr(H, name, value)
    DATABASE_URL = database_url

    g = globals()
    for name in PUBLIC_FUNCTIONS:
        namespace[name] = g[name]
