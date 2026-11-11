
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from flask import current_app, g
from werkzeug.security import generate_password_hash


def now():
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class DatabaseConnection:
    """Small adapter that lets the application use the same SQL placeholders."""

    def __init__(self, connection, is_postgres=False):
        self.connection = connection
        self.is_postgres = is_postgres

    def execute(self, sql, args=()):
        if self.is_postgres:
            sql = sql.replace("?", "%s")
        return self.connection.execute(sql, args)

    def executemany(self, sql, args):
        if self.is_postgres:
            sql = sql.replace("?", "%s")
        return self.connection.executemany(sql, args)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def get_db():
    if "db" not in g:
        database_url = (
            current_app.config.get("DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )

        if database_url:
            import psycopg
            from psycopg.rows import dict_row

            if database_url.startswith("postgres://"):
                database_url = database_url.replace(
                    "postgres://", "postgresql://", 1
                )

            connection = psycopg.connect(
                database_url,
                row_factory=dict_row,
            )
            g.db = DatabaseConnection(connection, is_postgres=True)

        else:
            path = Path(current_app.config["DATABASE_PATH"])
            path.parent.mkdir(parents=True, exist_ok=True)

            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")

            g.db = DatabaseConnection(connection)

    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db:
        db.close()


def rows(sql, args=()):
    return [
        dict(r)
        for r in get_db().execute(sql, args).fetchall()
    ]


def row(sql, args=()):
    result = get_db().execute(sql, args).fetchone()
    return dict(result) if result else None


def audit(actor, action, target_type, target_id="", details=""):
    db = get_db()
    db.execute(
        """
        INSERT INTO audit_logs
        (actor_id, action, target_type, target_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (actor, action, target_type, target_id, details, now()),
    )
    db.commit()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','teacher','student')),
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classes(
    id INTEGER PRIMARY KEY,
    level TEXT NOT NULL,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS subjects(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS student_profiles(
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    class_id INTEGER REFERENCES classes(id),
    admission_no TEXT,
    guardian_contact TEXT
);

CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER REFERENCES users(id),
    amount REAL,
    currency TEXT DEFAULT 'UGX',
    reference TEXT,
    status TEXT DEFAULT 'Pending',
    verified_by INTEGER,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS resources(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    filename TEXT NOT NULL,
    level TEXT NOT NULL,
    class_id INTEGER REFERENCES classes(id),
    subject_id INTEGER REFERENCES subjects(id),
    topic TEXT,
    uploaded_by INTEGER,
    archived INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS download_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    resource_id INTEGER,
    reason TEXT,
    status TEXT DEFAULT 'Pending',
    decided_by INTEGER,
    decided_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    class_id INTEGER,
    subject_id INTEGER,
    teacher_id INTEGER,
    starts_at TEXT,
    ends_at TEXT,
    meet_url TEXT,
    status TEXT DEFAULT 'Scheduled',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    body TEXT,
    target_level TEXT,
    target_class_id INTEGER,
    subject_id INTEGER,
    publish_at TEXT,
    expires_at TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    announcement_id INTEGER,
    message TEXT,
    is_read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tests(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    instructions TEXT,
    class_id INTEGER,
    subject_id INTEGER,
    topic TEXT,
    opens_at TEXT,
    closes_at TEXT,
    max_marks REAL,
    created_by INTEGER,
    status TEXT DEFAULT 'Open',
    results_released INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER,
    question_number INTEGER,
    question_text TEXT,
    marks REAL,
    answer_type TEXT DEFAULT 'long'
);

CREATE TABLE IF NOT EXISTS submissions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER,
    student_id INTEGER,
    status TEXT DEFAULT 'Draft',
    submitted_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(test_id, student_id)
);

CREATE TABLE IF NOT EXISTS answers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER,
    question_id INTEGER,
    answer_text TEXT,
    uploaded_file TEXT,
    UNIQUE(submission_id, question_id)
);

CREATE TABLE IF NOT EXISTS marks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER,
    question_id INTEGER,
    marks_awarded REAL,
    teacher_comment TEXT,
    marked_by INTEGER,
    released_at TEXT,
    UNIQUE(submission_id, question_id)
);

CREATE TABLE IF NOT EXISTS test_marking_guides(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER UNIQUE,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    uploaded_by INTEGER,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_download_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    reason TEXT,
    status TEXT DEFAULT 'Pending',
    decided_by INTEGER,
    decided_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    description TEXT,
    youtube_url TEXT,
    class_id INTEGER,
    subject_id INTEGER,
    topic TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_profiles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    job_title TEXT NOT NULL DEFAULT '',
    qualifications TEXT NOT NULL DEFAULT '',
    institution TEXT NOT NULL DEFAULT '',
    experience TEXT NOT NULL DEFAULT '',
    bio TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    whatsapp TEXT NOT NULL DEFAULT '',
    photo_filename TEXT NOT NULL DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0,
    is_published INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    action TEXT,
    target_type TEXT,
    target_id TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);
"""


def init_db():
    db = get_db()
    is_postgres = db.is_postgres

    schema = SCHEMA

    if is_postgres:
        schema = schema.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT",
            "SERIAL PRIMARY KEY",
        )

        for statement in schema.split(";"):
            if statement.strip():
                db.execute(statement)

    else:
        db.connection.executescript(schema)

    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        uq_marks_submission_question
        ON marks(submission_id, question_id)
        """
    )
    db.commit()

    seed_reference_data(db)

    seed_enabled = current_app.config.get("SEED_DEMO_DATA", False)

    if seed_enabled:
        count = db.execute(
            "SELECT COUNT(*) AS user_count FROM users"
        ).fetchone()["user_count"]

        if count == 0:
            seed_demo(db)


def seed_reference_data(db):
    classes = [
        (1, "O-Level", "S1"),
        (2, "O-Level", "S2"),
        (3, "O-Level", "S3"),
        (4, "O-Level", "S4"),
        (5, "A-Level", "S5"),
        (6, "A-Level", "S6"),
    ]

    for class_id, level, name in classes:
        existing = db.execute(
            "SELECT id FROM classes WHERE name=?", (name,)
        ).fetchone()

        if not existing:
            db.execute(
                "INSERT INTO classes(id, level, name) VALUES(?,?,?)",
                (class_id, level, name),
            )

    for name in [
        "Biology",
        "Chemistry",
        "Physics",
        "Mathematics",
        "Agriculture",
    ]:
        existing = db.execute(
            "SELECT id FROM subjects WHERE name=?", (name,)
        ).fetchone()

        if not existing:
            db.execute("INSERT INTO subjects(name) VALUES(?)", (name,))

    db.commit()


def seed_demo(db):
    t = now()

    accounts = [
        ("Hub Administrator", "admin@holidayhub.local",
         "Admin@123", "admin", 0),
        ("Grace Teacher", "teacher@holidayhub.local",
         "Teacher@123", "teacher", 0),
        ("Approved Student", "student@holidayhub.local",
         "Student@123", "student", 0),
        ("Pending Student", "pending@holidayhub.local",
         "Pending@123", "student", 1),
    ]

    for name, email, password, role, must in accounts:
        db.execute(
            """
            INSERT INTO users
            (full_name, email, password_hash, role,
             must_change_password, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                email,
                generate_password_hash(password),
                role,
                must,
                t,
            ),
        )

    admin = db.execute(
        "SELECT id FROM users WHERE email=?",
        ("admin@holidayhub.local",),
    ).fetchone()["id"]

    teacher = db.execute(
        "SELECT id FROM users WHERE email=?",
        ("teacher@holidayhub.local",),
    ).fetchone()["id"]

    student = db.execute(
        "SELECT id FROM users WHERE email=?",
        ("student@holidayhub.local",),
    ).fetchone()["id"]

    pending = db.execute(
        "SELECT id FROM users WHERE email=?",
        ("pending@holidayhub.local",),
    ).fetchone()["id"]

    db.executemany(
        """
        INSERT INTO student_profiles
        (user_id, class_id, admission_no, guardian_contact)
        VALUES (?, ?, ?, ?)
        """,
        [
            (student, 2, "HCH-2026-001", "+256 700 000 001"),
            (pending, 4, "HCH-2026-002", "+256 700 000 002"),
        ],
    )

    db.executemany(
        """
        INSERT INTO payments
        (student_id, amount, reference, status, verified_by, verified_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (student, 150000, "PAY-APPROVED",
             "Approved/Paid", admin, t),
            (pending, 150000, "PAY-PENDING",
             "Pending", None, None),
        ],
    )

    subject = db.execute(
        "SELECT id FROM subjects WHERE name=?",
        ("Biology",),
    ).fetchone()["id"]

    db.execute(
        """
        INSERT INTO lessons
        (title, class_id, subject_id, teacher_id,
         starts_at, ends_at, meet_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Cell structure live clinic",
            2,
            subject,
            teacher,
            "2026-11-10T15:00",
            "2026-11-10T16:00",
            "https://meet.google.com/demo-cell",
            t,
        ),
    )

    db.execute(
        """
        INSERT INTO announcements
        (title, body, target_level, publish_at, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "Welcome to Holiday Coaching",
            "Review your class resources and join the next live lesson from the timetable.",
            "O-Level",
            t,
            admin,
            t,
        ),
    )

    db.execute(
        """
        INSERT INTO videos
        (title, description, youtube_url, class_id,
         subject_id, topic, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Introduction to cells",
            "A guided Biology starter lesson.",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            2,
            subject,
            "Cells",
            teacher,
            t,
        ),
    )

    test_id = db.execute(
        """
        INSERT INTO tests
        (title, instructions, class_id, subject_id, topic,
         opens_at, closes_at, max_marks, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            "Biology: Cell structure",
            "Answer all questions in your own words. Save a draft before submitting.",
            2,
            subject,
            "Cells",
            "2026-11-01T00:00",
            "2026-12-01T23:59",
            20,
            teacher,
            t,
        ),
    ).fetchone()["id"]

    db.executemany(
        """
        INSERT INTO questions
        (test_id, question_number, question_text, marks, answer_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (test_id, 1, "Explain two functions of the cell membrane.", 8, "long"),
            (test_id, 2, "Define osmosis in one clear paragraph.", 6, "short"),
            (test_id, 3, "Describe one difference between plant and animal cells.", 6, "long"),
        ],
    )

    db.execute(
        """
        INSERT INTO audit_logs
        (actor_id, action, target_type, target_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (admin, "seed", "system", "DEMO", "seeded demo dataset", t),
    )

    db.commit()
