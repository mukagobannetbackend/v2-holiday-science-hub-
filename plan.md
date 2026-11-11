# Holiday Coaching Hub — Implementation Plan

## Product
A Flask + SQLite prototype for Uganda O-Level (S1–S4) and A-Level (S5–S6) holiday coaching. Students see only approved, active, class-scoped content. Staff manage students, payments, resources, lessons, notices, tests, marking, videos and audits.

## Design
- **Design movement:** calm academic operations console with editorial card layouts and warm education accents.
- **Core principles:** obvious class context, permission-first surfaces, readable touch targets, and evidence-rich records.
- **Color philosophy:** navy ink and paper tones for trust, with amber/teal accents for learning and progress; dark mode keeps the same semantic colors.
- **Layout paradigm:** responsive sidebar plus asymmetric dashboard rails: “what needs attention” beside “what happens next”.
- **Signature elements:** level chips, next-lesson rail, and answer-sheet cards.
- **Interaction philosophy:** every action gives visible feedback and preserves an audit trail.
- **Typography:** system sans for clarity, compact mono metadata for status labels.
- **Brand essence:** a dependable holiday study cockpit for learners and the staff supporting them. Calm, clear, accountable.
- **Brand voice:** “Your next lesson is ready.” / “Access follows verified payment and your assigned class.”
- **Signature color:** teaching amber `#f1b84b`.

## Architecture
- Flask application factory in `app/__init__.py`.
- SQLite schema and seeded demo dataset in `app/db.py`.
- Session authentication and role/class guards in `app/auth.py`.
- Server-rendered Jinja templates with responsive CSS and small JavaScript helpers.
- Uploads live outside public static paths and are served only through permission-checked routes.
- Demo accounts are documented; all passwords are stored as Werkzeug hashes.
- `tests/test_app.py` covers login, role isolation, class scope, download permissions, tests and marking.

## Prototype delivery boundaries
- Google Meet is stored as a validated URL; calendar/Meet API is later work.
- YouTube videos are linked/embedded, never downloaded or re-hosted.
- Email/SMS/WhatsApp delivery is not included; notifications are in-app.
- Payments are recorded and admin-approved; no payment gateway is connected.
