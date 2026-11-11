# Holiday Coaching Hub

A responsive Flask prototype for an Online Secondary School Holiday Coaching Hub serving Ugandan O-Level (S1–S4) and A-Level (S5–S6) learners.

## Included in this package

- Role-based Admin, Teacher, and Student sign-in.
- Password hashing with Werkzeug and temporary-password change flow.
- Student payment status and admin approval/suspension.
- Class-scoped O-Level/A-Level library with protected viewer and download-request approval.
- Timetable with class-scoped Google Meet URLs.
- In-app announcements on the student dashboard.
- Written assessments with typed answer sheets, draft save, final submission lock, teacher marking and released feedback seam.
- YouTube lesson links organised by class, subject, and topic.
- Audit log and student CSV export.
- Responsive light/dark theme with mobile navigation.
- SQLite for local prototype; production should use PostgreSQL, HTTPS, protected storage and server hardening.

## Run it today

```bash
cd holiday-coaching-hub
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5001`.

## Demo accounts

| Role | Email | Password |
|---|---|---|
| Administrator | `admin@holidayhub.local` | `Admin@123` |
| Teacher | `teacher@holidayhub.local` | `Teacher@123` |
| Approved student (S2) | `student@holidayhub.local` | `Student@123` |
| Pending student (S4) | `pending@holidayhub.local` | `Pending@123` |

These are local demo credentials only. Change them before any real deployment.

## Important boundaries

Payment approval is a recorded administrative decision, not a payment gateway. Google Meet is a stored URL, not automatic calendar provisioning. YouTube links are opened at YouTube and are not downloaded or re-hosted. The library viewer discourages casual downloading but cannot guarantee that viewed material will never be copied.

Before launching to real students, add production authentication hardening, CSRF protection through Flask-WTF, HTTPS, secure cookies, rate limiting, protected object storage, background jobs, backups, guardian/child-safeguarding compliance, Ugandan privacy compliance, and PostgreSQL migrations.

## Tests

```bash
python3 -m pytest -q
```
