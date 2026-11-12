
from functools import wraps
import secrets
from datetime import datetime, timedelta, timezone

from flask import session, redirect, url_for, request, abort
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, row, audit, now


SESSION_TIMEOUT_HOURS = 12


def _session_expired(created_at):
    if not created_at:
        return True

    try:
        expiry_time = datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        )
        if expiry_time.tzinfo is None:
            expiry_time = expiry_time.replace(tzinfo=timezone.utc)

        return datetime.now(timezone.utc) - expiry_time > timedelta(
            hours=SESSION_TIMEOUT_HOURS
        )
    except (ValueError, TypeError):
        return True


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None

    user = row(
        "SELECT * FROM users WHERE id=? AND is_active=1",
        (uid,)
    )

    if not user:
        session.clear()
        return None

    if user["role"] == "student":
        token = session.get("session_token")

        if not token:
            session.clear()
            return None

        active = row(
            "SELECT session_token, created_at FROM active_sessions WHERE user_id=?",
            (uid,)
        )

        if (
            not active
            or not secrets.compare_digest(active["session_token"], token)
            or _session_expired(active["created_at"])
        ):
            session.clear()
            return None

        # Refresh the inactivity timeout on each authenticated request.
        db = get_db()
        db.execute(
            "UPDATE active_sessions SET created_at=? WHERE user_id=? AND session_token=?",
            (now(), uid, token)
        )
        db.commit()

    return user


def login_user(user):
    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]

    if user["role"] == "student":
        token = secrets.token_urlsafe(32)
        db = get_db()

        db.execute(
            """
            INSERT INTO active_sessions (user_id, session_token, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                session_token=excluded.session_token,
                created_at=excluded.created_at
            """,
            (user["id"], token, now())
        )
        db.commit()

        session["session_token"] = token


def logout_user():
    uid = session.get("user_id")
    token = session.get("session_token")

    if uid and token:
        db = get_db()
        db.execute(
            "DELETE FROM active_sessions WHERE user_id=? AND session_token=?",
            (uid, token)
        )
        db.commit()

    session.clear()


def login(email, password):
    user = row(
        "SELECT * FROM users WHERE lower(email)=lower(?)",
        (email.strip(),)
    )

    if not user or not check_password_hash(
        user["password_hash"], password
    ):
        return None

    if not user["is_active"]:
        return None

    if user["role"] == "student":
        payment = row(
            """
            SELECT status FROM payments
            WHERE student_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (user["id"],)
        )

        if not payment or payment["status"] != "Approved/Paid":
            return None

    return user


def role_required(*roles):
    def dec(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user()

            if not user:
                return redirect(
                    url_for("login_view", next=request.path)
                )

            if user["role"] not in roles:
                abort(403)

            if (
                user["must_change_password"]
                and request.endpoint not in {"change_password", "logout"}
            ):
                return redirect(url_for("change_password"))

            return fn(*args, **kwargs)

        return wrapped

    return dec


def student_required(fn):
    return role_required("student")(fn)


def staff_required(fn):
    return role_required("admin", "teacher")(fn)


def admin_required(fn):
    return role_required("admin")(fn)


def assigned_student(student_id=None, class_id=None):
    u = current_user()

    profile = row(
        "SELECT * FROM student_profiles WHERE user_id=?",
        (student_id or u["id"],)
    )

    if u["role"] == "student" and (
        not profile
        or (
            class_id is not None
            and profile["class_id"] != int(class_id)
        )
    ):
        abort(403)

    return profile


def set_password(user_id, password, must_change=0):
    db = get_db()

    db.execute(
        """
        UPDATE users
        SET password_hash=?, must_change_password=?
        WHERE id=?
        """,
        (generate_password_hash(password), must_change, user_id)
    )
    db.commit()

    audit(user_id, "password_changed", "user", str(user_id))
