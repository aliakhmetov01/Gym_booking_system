"""
Compatibility shim re-exporting database functions from api/_database.py.
"""

from api._database import (
    DB_PATH,
    BACKUP_DIR,
    init_db,
    validate_session_code,
    get_all_sessions,
    get_session_by_id,
    create_booking,
    get_all_bookings,
    checkin_booking,
    add_session,
    create_backup,
    get_backups,
    get_storage_engine_name,
)
