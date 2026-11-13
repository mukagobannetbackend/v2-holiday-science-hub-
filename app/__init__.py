from __future__ import annotations
import csv, io, os, re, secrets, mimetypes
import html, zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_file, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash
from .db import init_db, close_db, get_db, row, rows, now, audit
from .auth import current_user, login_user, logout_user, login, role_required, student_required, staff_required, admin_required, assigned_student, set_password

ALLOWED_UPLOADS={'pdf','png','jpg','jpeg'}
def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    database_url = os.getenv('DATABASE_URL')

    if database_url and database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)

    app.config.from_mapping(
        SECRET_KEY=os.getenv('FLASK_SECRET_KEY', 'dev-only-change-me'),
        DATABASE_URL=database_url,
        DATABASE_PATH=os.getenv(
            'HUB_DATABASE_PATH',
            str(Path(app.instance_path) / 'hub.db')
        ),
        UPLOAD_DIR=os.getenv(
            'HUB_UPLOAD_DIR',
            str(Path(app.instance_path) / 'uploads')
        ),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        SEED_DEMO_DATA=os.getenv(
            'SEED_DEMO_DATA',
            'false'
        ).lower() == 'true'
    )

    if test_config:
        app.config.update(test_config)

    Path(app.config['UPLOAD_DIR']).mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    @app.context_processor
    def inject():
        return {
            'user': current_user(),
            'classes': rows('SELECT * FROM classes ORDER BY id'),
            'subjects': rows('SELECT * FROM subjects ORDER BY name'),
            'current_path': request.path
        }

    @app.get('/')
    def home():
        return redirect(url_for('dashboard')) if current_user() else render_template('landing.html')

    @app.get('/health')
    def health():
        return jsonify({
            'ok': True,
            'service': 'holiday-coaching-hub',
            'database': 'postgresql' if app.config.get('DATABASE_URL') else 'sqlite'
          })
    @app.get('/manus-routes.json')
    def route_manifest(): return jsonify({'routes':[{'path':'/','title':'Holiday Coaching Hub'},{'path':'/login','title':'Sign in'},{'path':'/dashboard','title':'Dashboard'},{'path':'/library','title':'Library'},{'path':'/timetable','title':'Timetable'},{'path':'/tests','title':'Assessments'},{'path':'/videos','title':'Video lessons'}]})
    @app.route('/login',methods=['GET','POST'])
    def login_view():
        if request.method=='POST':
            user=login(request.form.get('email',''),request.form.get('password',''))
            if not user: flash('Sign-in failed. Check your credentials or ask an administrator to activate your account.','error')
            else:
                login_user(user); audit(user['id'],'login','user',str(user['id']))
                return redirect(url_for('change_password') if user['must_change_password'] else request.args.get('next') or url_for('dashboard'))
        return render_template('login.html')
    @app.get('/logout')
    def logout():
        logout_user()
        return redirect(url_for('home'))
    @app.route('/change-password',methods=['GET','POST'])
    @role_required('admin','teacher','student')
    def change_password():
        if request.method=='POST':
            p=request.form.get('password',''); confirm=request.form.get('confirm','')
            if len(p)<8 or p!=confirm: flash('Use at least 8 characters and make both password fields match.','error')
            else: set_password(current_user()['id'],p); flash('Password updated.','success'); return redirect(url_for('dashboard'))
        return render_template('change_password.html')
    @app.get('/dashboard')
    @role_required('admin','teacher','student')
    def dashboard():
        u = current_user()
        profile = row(
            'SELECT sp.*,c.name class_name,c.level '
            'FROM student_profiles sp JOIN classes c ON c.id=sp.class_id '
            'WHERE sp.user_id=?',
            (u['id'],)
        )

        if u['role'] == 'student':
            upcoming = rows(
                "SELECT l.*,c.name class_name,s.name subject_name "
                "FROM lessons l JOIN classes c ON c.id=l.class_id "
                "JOIN subjects s ON s.id=l.subject_id "
                "WHERE l.class_id=? AND l.status<>'Cancelled' "
                "ORDER BY l.starts_at LIMIT 5",
                (profile['class_id'],)
            )
            notices = rows(
                "SELECT * FROM announcements "
                "WHERE (target_level IS NULL OR target_level=? OR target_class_id=?) "
                "AND (expires_at IS NULL OR expires_at>=?) "
                "ORDER BY publish_at DESC LIMIT 5",
                (profile['level'], profile['class_id'], now())
            )
            tests = rows(
                "SELECT t.*,c.name class_name,s.name subject_name "
                "FROM tests t JOIN classes c ON c.id=t.class_id "
                "JOIN subjects s ON s.id=t.subject_id "
                "WHERE t.class_id=? ORDER BY t.closes_at LIMIT 5",
                (profile['class_id'],)
            )
            resources = rows(
                "SELECT r.*,c.name class_name,s.name subject_name "
                "FROM resources r JOIN classes c ON c.id=r.class_id "
                "JOIN subjects s ON s.id=r.subject_id "
                "WHERE r.class_id=? AND r.archived=0 "
                "ORDER BY r.created_at DESC LIMIT 6",
                (profile['class_id'],)
            )
            return render_template(
                'student_dashboard.html',
                profile=profile,
                upcoming=upcoming,
                notices=notices,
                tests=tests,
                resources=resources
            )

        lessons = rows(
            'SELECT l.*,c.name class_name,s.name subject_name '
            'FROM lessons l JOIN classes c ON c.id=l.class_id '
            'JOIN subjects s ON s.id=l.subject_id '
            'ORDER BY l.starts_at LIMIT 6'
        )

        stats = {
            'marking': row(
                "SELECT COUNT(*) n FROM submissions WHERE status='Submitted'"
            )['n']
        }
        pending = []

        if u['role'] == 'admin':
            stats.update({
                'students': row(
                    "SELECT COUNT(*) n FROM users WHERE role='student'"
                )['n'],
                'pending': row(
                    "SELECT COUNT(*) n FROM payments WHERE status='Pending'"
                )['n'],
                'approved': row(
                    "SELECT COUNT(*) n FROM payments WHERE status='Approved/Paid'"
                )['n']
            })
            pending = rows(
                "SELECT p.*,u.full_name,c.name class_name "
                "FROM payments p JOIN users u ON u.id=p.student_id "
                "LEFT JOIN student_profiles sp ON sp.user_id=u.id "
                "LEFT JOIN classes c ON c.id=sp.class_id "
                "WHERE p.status='Pending' ORDER BY p.id DESC LIMIT 8"
            )

        return render_template(
            'staff_dashboard.html',
            stats=stats,
            pending=pending,
            lessons=lessons
        )

    @app.get('/students')
    @admin_required
    def students():
        viewing_hidden = request.args.get('view') == 'hidden'
        hidden_filter = 1 if viewing_hidden else 0

        student_rows = rows("""
            SELECT u.*, sp.admission_no, c.name AS class_name,
                   c.level, p.status AS payment_status,
                   p.reference
            FROM users u
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            LEFT JOIN classes c ON c.id = sp.class_id
            LEFT JOIN payments p ON p.id = (
                SELECT id FROM payments
                WHERE student_id = u.id
                ORDER BY id DESC LIMIT 1
            )
            WHERE u.role = 'student'
              AND u.is_hidden = ?
            ORDER BY u.full_name
        """, (hidden_filter,))

        hidden_count = row("""
            SELECT COUNT(*) AS total
            FROM users
            WHERE role = 'student' AND is_hidden = 1
        """)['total']

        return render_template(
            'students.html',
            students=student_rows,
            classes=rows("SELECT id, level, name FROM classes ORDER BY id"),
            viewing_hidden=viewing_hidden,
            hidden_count=hidden_count
        )

    @app.post('/students/register')
    @admin_required
    def register_student():
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        guardian_contact = request.form.get('guardian_contact', '').strip()
        admission_no = request.form.get('admission_no', '').strip()
        reference = request.form.get('reference', '').strip()
        password = request.form.get('temporary_password', '')
        amount_text = request.form.get('amount', '').strip()

        try:
            class_id = int(request.form.get('class_id', ''))
        except (TypeError, ValueError):
            class_id = None

        if not full_name or len(full_name) > 120:
            flash('Enter the student?s full name (maximum 120 characters).', 'error')
            return redirect(url_for('students'))

        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
            flash('Enter a valid student email address.', 'error')
            return redirect(url_for('students'))

        if not phone or len(phone) > 40:
            flash('Enter a valid student phone number.', 'error')
            return redirect(url_for('students'))

        if not admission_no or len(admission_no) > 50:
            flash('Enter a valid admission number.', 'error')
            return redirect(url_for('students'))

        if len(guardian_contact) > 40:
            flash('Guardian contact is too long.', 'error')
            return redirect(url_for('students'))

        if not reference or len(reference) > 100:
            flash('Enter the payment reference.', 'error')
            return redirect(url_for('students'))

        if not re.fullmatch(r'\d+(?:\.\d{1,2})?', amount_text):
            flash('Enter a valid payment amount in UGX.', 'error')
            return redirect(url_for('students'))

        amount = float(amount_text)
        if amount <= 0:
            flash('Payment amount must be greater than zero.', 'error')
            return redirect(url_for('students'))

        if len(password) < 8 or len(password) > 128:
            flash('Temporary password must contain 8?128 characters.', 'error')
            return redirect(url_for('students'))

        db = get_db()

        if not row("SELECT id FROM classes WHERE id = ?", (class_id,)):
            flash('Select a valid class.', 'error')
            return redirect(url_for('students'))

        if row("SELECT id FROM users WHERE lower(email) = lower(?)", (email,)):
            flash('That email address is already registered.', 'error')
            return redirect(url_for('students'))

        if row("SELECT user_id FROM student_profiles WHERE lower(admission_no) = lower(?)", (admission_no,)):
            flash('That admission number is already in use.', 'error')
            return redirect(url_for('students'))

        if row("SELECT id FROM payments WHERE lower(reference) = lower(?)", (reference,)):
            flash('That payment reference is already recorded.', 'error')
            return redirect(url_for('students'))

        try:
            db.execute("""
                INSERT INTO users
                (full_name, email, phone, password_hash, role,
                 is_active, must_change_password, created_at)
                VALUES (?, ?, ?, ?, 'student', 0, 0, ?)
            """, (
                full_name,
                email,
                phone,
                generate_password_hash(password),
                now()
            ))

            student = row("SELECT id FROM users WHERE lower(email) = lower(?)", (email,))
            student_id = student['id']

            db.execute("""
                INSERT INTO student_profiles
                (user_id, class_id, admission_no, guardian_contact)
                VALUES (?, ?, ?, ?)
            """, (student_id, class_id, admission_no, guardian_contact or None))

            db.execute("""
                INSERT INTO payments
                (student_id, amount, currency, reference, status,
                 verified_by, verified_at)
                VALUES (?, ?, 'UGX', ?, 'Pending', NULL, NULL)
            """, (student_id, amount, reference))

            db.commit()

        except Exception:
            db.rollback()
            raise

        audit(
            current_user()['id'],
            'student_registered',
            'student',
            str(student_id),
            'Student account and pending payment created.'
        )

        flash('Student registered. Account remains inactive until payment approval.', 'success')
        return redirect(url_for('students'))

    @app.post('/students/<int:student_id>/approve')
    @admin_required
    def approve_student(student_id):
        db = get_db()

        student = row(
            "SELECT id FROM users WHERE id = ? AND role = 'student'",
            (student_id,)
        )

        if not student:
            flash('Student account was not found.', 'error')
            return redirect(url_for('students'))

        payment = row("""
            SELECT id, status FROM payments
            WHERE student_id = ?
            ORDER BY id DESC LIMIT 1
        """, (student_id,))

        if not payment or payment['status'] != 'Pending':
            flash('This student has no pending payment to approve.', 'error')
            return redirect(url_for('students'))

        db.execute("""
            UPDATE payments
            SET status = 'Approved/Paid', verified_by = ?, verified_at = ?
            WHERE id = ? AND status = 'Pending'
        """, (current_user()['id'], now(), payment['id']))

        db.execute("""
            UPDATE users
            SET is_active = 1, must_change_password = 1
            WHERE id = ? AND role = 'student'
        """, (student_id,))

        db.commit()

        audit(
            current_user()['id'],
            'payment_approved',
            'student',
            str(student_id)
        )

        flash('Payment approved. Student can now log in and change the temporary password.', 'success')
        return redirect(url_for('students'))

    @app.post('/students/<int:student_id>/suspend')
    @admin_required
    def suspend_student(student_id):
        db = get_db()
        student = row(
            "SELECT id FROM users WHERE id = ? AND role = 'student'",
            (student_id,)
        )

        if not student:
            flash('Student account was not found.', 'error')
            return redirect(url_for('students'))

        db.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?",
            (student_id,)
        )
        db.commit()

        audit(
            current_user()['id'],
            'account_suspended',
            'student',
            str(student_id)
        )

        flash('Student account suspended.', 'success')
        return redirect(url_for('students'))

    @app.post('/students/bulk-action')
    @admin_required
    def bulk_student_action():
        db = get_db()
        action = (request.form.get('action') or '').strip().lower()
        raw_ids = request.form.getlist('student_ids')

        allowed_actions = {'suspend', 'restore', 'hide', 'show'}
        if action not in allowed_actions:
            flash('Invalid bulk action.', 'error')
            return redirect(url_for('students'))

        if not raw_ids:
            flash('Select at least one student first.', 'error')
            return redirect(url_for('students'))

        try:
            student_ids = list(dict.fromkeys(int(value) for value in raw_ids))
            if any(student_id <= 0 for student_id in student_ids):
                raise ValueError
        except (TypeError, ValueError):
            flash('The student selection contains an invalid ID.', 'error')
            return redirect(url_for('students'))

        # Validate every selected account before changing any account.
        valid_ids = []
        for student_id in student_ids:
            student = row(
                "SELECT id FROM users WHERE id = ? AND role = 'student'",
                (student_id,)
            )
            if not student:
                flash(
                    'No changes were made. One or more selected student accounts were not found.',
                    'error'
                )
                return redirect(url_for('students'))
            valid_ids.append(student_id)

        actor_id = current_user()['id']
        changed_ids = []
        skipped = 0

        try:
            for student_id in valid_ids:
                if action == 'suspend':
                    db.execute(
                        "UPDATE users SET is_active = 0 "
                        "WHERE id = ? AND role = 'student'",
                        (student_id,)
                    )
                    changed_ids.append(student_id)

                elif action == 'restore':
                    payment = row(
                        """
                        SELECT status FROM payments
                        WHERE student_id = ?
                        ORDER BY id DESC LIMIT 1
                        """,
                        (student_id,)
                    )
                    if payment and payment['status'] in ('Approved/Paid', 'Approved'):
                        db.execute(
                            "UPDATE users SET is_active = 1 "
                            "WHERE id = ? AND role = 'student'",
                            (student_id,)
                        )
                        changed_ids.append(student_id)
                    else:
                        skipped += 1

                elif action == 'hide':
                    db.execute(
                        "UPDATE users SET is_hidden = 1 "
                        "WHERE id = ? AND role = 'student'",
                        (student_id,)
                    )
                    changed_ids.append(student_id)

                elif action == 'show':
                    db.execute(
                        "UPDATE users SET is_hidden = 0 "
                        "WHERE id = ? AND role = 'student'",
                        (student_id,)
                    )
                    changed_ids.append(student_id)

            db.commit()

        except Exception:
            db.rollback()
            raise

        audit_actions = {
            'suspend': 'account_suspended',
            'restore': 'account_restored',
            'hide': 'student_hidden',
            'show': 'student_shown'
        }

        for student_id in changed_ids:
            audit(
                actor_id,
                audit_actions[action],
                'student',
                str(student_id),
                'Performed through bulk student management.'
            )

        if action == 'restore' and skipped:
            flash(
                f'{len(changed_ids)} student account(s) restored. '
                f'{skipped} account(s) were not restored because payment is not approved.',
                'success' if changed_ids else 'error'
            )
        elif changed_ids:
            flash(
                f'{len(changed_ids)} student account(s) updated successfully.',
                'success'
            )
        else:
            flash('No student accounts were changed.', 'error')

        return redirect(url_for('students'))

    @app.post('/announcements')
    @staff_required
    def create_announcement():
        db = get_db()

        aid = db.execute(
            """
            INSERT INTO announcements
            (title, body, target_level, target_class_id,
             publish_at, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                request.form['title'],
                request.form['body'],
                request.form.get('target_level') or None,
                request.form.get('target_class_id') or None,
                now(),
                current_user()['id'],
                now()
            )
        ).fetchone()['id']

        targets = rows(
            "SELECT id FROM users WHERE role='student' AND is_active=1"
        )

        for target in targets:
            db.execute(
                """
                INSERT INTO notifications
                (user_id, announcement_id, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (target['id'], aid, request.form['title'], now())
            )

        db.commit()

        audit(
            current_user()['id'],
            'announcement_published',
            'announcement',
            str(aid)
        )

        flash('Announcement published to the in-app notice stream.', 'success')
        return redirect(url_for('dashboard'))
    @app.get('/admin/resource-permissions')
    @admin_required
    def resource_permissions():
        classes = rows("SELECT id, name, level FROM classes ORDER BY id")
        if not classes:
            abort(404)

        try:
            class_id = int(request.args.get('class_id') or classes[0]['id'])
        except (TypeError, ValueError):
            class_id = classes[0]['id']

        selected_class = row(
            "SELECT id, name, level FROM classes WHERE id=?",
            (class_id,)
        )
        if not selected_class:
            class_id = classes[0]['id']
            selected_class = classes[0]

        students = rows(
            """
            SELECT u.id, u.full_name, u.email, u.is_active
            FROM users u
            JOIN student_profiles sp ON sp.user_id = u.id
            WHERE u.role='student' AND sp.class_id=?
            ORDER BY u.full_name
            """,
            (class_id,)
        )

        resources = rows(
            """
            SELECT id, title, subject_id, topic
            FROM resources
            WHERE class_id=? AND archived=0
            ORDER BY title
            """,
            (class_id,)
        )

        active_grants = rows(
            """
            SELECT g.id, g.granted_at, u.full_name AS student_name,
                   r.title AS resource_title
            FROM resource_access_grants g
            JOIN users u ON u.id=g.student_id
            JOIN resources r ON r.id=g.resource_id
            JOIN student_profiles sp ON sp.user_id=u.id
            WHERE sp.class_id=? AND r.class_id=? AND g.revoked_at IS NULL
            ORDER BY g.granted_at DESC, u.full_name
            """,
            (class_id, class_id)
        )

        return render_template(
            'resource_permissions.html',
            classes=classes,
            selected_class=selected_class,
            students=students,
            resources=resources,
            active_grants=active_grants
        )

    @app.post('/admin/resource-permissions/grant')
    @admin_required
    def grant_resource_permissions():
        db = get_db()
        actor_id = current_user()['id']

        try:
            class_id = int(request.form.get('class_id', ''))
            raw_student_ids = request.form.getlist('student_ids')
            raw_resource_ids = request.form.getlist('resource_ids')

            student_ids = list(dict.fromkeys(int(value) for value in raw_student_ids))
            resource_ids = list(dict.fromkeys(int(value) for value in raw_resource_ids))

            if class_id <= 0 or not student_ids or not resource_ids:
                raise ValueError

            if any(value <= 0 for value in student_ids + resource_ids):
                raise ValueError
        except (TypeError, ValueError):
            flash('Select a valid class, at least one student and at least one resource.', 'error')
            return redirect(url_for('resource_permissions'))

        if not row("SELECT id FROM classes WHERE id=?", (class_id,)):
            flash('The selected class was not found. No permissions were changed.', 'error')
            return redirect(url_for('resource_permissions'))

        for student_id in student_ids:
            valid_student = row(
                """
                SELECT u.id FROM users u
                JOIN student_profiles sp ON sp.user_id=u.id
                WHERE u.id=? AND u.role='student' AND sp.class_id=?
                """,
                (student_id, class_id)
            )
            if not valid_student:
                flash('No permissions were changed. A selected student is not in this class.', 'error')
                return redirect(url_for('resource_permissions', class_id=class_id))

        for resource_id in resource_ids:
            valid_resource = row(
                """
                SELECT id FROM resources
                WHERE id=? AND class_id=? AND archived=0
                """,
                (resource_id, class_id)
            )
            if not valid_resource:
                flash('No permissions were changed. A selected resource is unavailable or belongs to another class.', 'error')
                return redirect(url_for('resource_permissions', class_id=class_id))

        created = 0
        skipped = 0

        try:
            for student_id in student_ids:
                for resource_id in resource_ids:
                    existing = row(
                        """
                        SELECT id FROM resource_access_grants
                        WHERE student_id=? AND resource_id=? AND revoked_at IS NULL
                        LIMIT 1
                        """,
                        (student_id, resource_id)
                    )

                    if existing:
                        skipped += 1
                        continue

                    stamp = now()
                    db.execute(
                        """
                        INSERT INTO resource_access_grants
                        (student_id, resource_id, granted_by, granted_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (student_id, resource_id, actor_id, stamp)
                    )

                    db.execute(
                        """
                        INSERT INTO audit_logs
                        (actor_id, action, target_type, target_id, details, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            actor_id,
                            'resource_access_granted',
                            'resource',
                            str(resource_id),
                            f'Bulk download access granted to student ID {student_id}',
                            stamp
                        )
                    )
                    created += 1

            db.commit()
        except Exception:
            db.rollback()
            raise

        flash(
            f'Bulk access granted for {created} student-resource combination(s). '
            f'{skipped} existing active permission(s) skipped.',
            'success'
        )
        return redirect(url_for('resource_permissions', class_id=class_id))

    @app.post('/admin/resource-permissions/revoke')
    @admin_required
    def revoke_resource_permissions():
        db = get_db()
        actor_id = current_user()['id']

        try:
            class_id = int(request.form.get('class_id', ''))
            raw_grant_ids = request.form.getlist('grant_ids')
            grant_ids = list(dict.fromkeys(int(value) for value in raw_grant_ids))

            if class_id <= 0 or not grant_ids:
                raise ValueError

            if any(value <= 0 for value in grant_ids):
                raise ValueError
        except (TypeError, ValueError):
            flash('Select a valid class and at least one active permission to revoke.', 'error')
            return redirect(url_for('resource_permissions'))

        if not row("SELECT id FROM classes WHERE id=?", (class_id,)):
            flash('The selected class was not found. No permissions were changed.', 'error')
            return redirect(url_for('resource_permissions'))

        for grant_id in grant_ids:
            valid_grant = row(
                """
                SELECT g.id
                FROM resource_access_grants g
                JOIN resources r ON r.id=g.resource_id
                JOIN student_profiles sp ON sp.user_id=g.student_id
                WHERE g.id=? AND g.revoked_at IS NULL
                  AND r.class_id=? AND sp.class_id=?
                """,
                (grant_id, class_id, class_id)
            )
            if not valid_grant:
                flash('No permissions were revoked. One or more selected permissions are invalid or no longer active.', 'error')
                return redirect(url_for('resource_permissions', class_id=class_id))

        try:
            for grant_id in grant_ids:
                stamp = now()
                db.execute(
                    """
                    UPDATE resource_access_grants
                    SET revoked_by=?, revoked_at=?
                    WHERE id=? AND revoked_at IS NULL
                    """,
                    (actor_id, stamp, grant_id)
                )

                db.execute(
                    """
                    INSERT INTO audit_logs
                    (actor_id, action, target_type, target_id, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        actor_id,
                        'resource_access_revoked',
                        'resource_access_grant',
                        str(grant_id),
                        'Bulk resource download permission revoked',
                        stamp
                    )
                )

            db.commit()
        except Exception:
            db.rollback()
            raise

        flash(f'{len(grant_ids)} bulk permission(s) revoked.', 'success')
        return redirect(url_for('resource_permissions', class_id=class_id))

    @app.route('/library',methods=['GET','POST'])
    @role_required('admin','teacher','student')
    def library():
        u=current_user(); profile=assigned_student() if u['role']=='student' else None
        if request.method=='POST':
            if u['role']=='student':
                rid=int(request.form['resource_id']); assigned_student(class_id=row('SELECT class_id FROM resources WHERE id=?',(rid,))['class_id']); get_db().execute('INSERT INTO download_requests(student_id,resource_id,reason,created_at) VALUES(?,?,?,?)',(u['id'],rid,request.form.get('reason',''),now())); get_db().commit(); audit(u['id'],'download_requested','resource',str(rid)); flash('Download request sent to an administrator.','success')
            else:
                f=request.files.get('file'); ext=(f.filename.rsplit('.',1)[-1].lower() if f and '.' in f.filename else '')
                if not f or ext not in ALLOWED_UPLOADS: flash('Upload a PDF or image file.','error')
                else:
                    token=secrets.token_hex(8)+'_'+secure_filename(f.filename); f.save(Path(app.config['UPLOAD_DIR'])/token); db=get_db(); db.execute('INSERT INTO resources(title,description,filename,level,class_id,subject_id,topic,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(request.form['title'],request.form.get('description',''),token,request.form['level'],request.form['class_id'],request.form['subject_id'],request.form.get('topic',''),u['id'],now())); db.commit(); audit(u['id'],'resource_uploaded','resource',token); flash('Resource uploaded.','success')
        if u['role']=='student':
            resources=rows(
                '''
                SELECT r.*, c.name AS class_name, s.name AS subject_name,
                    CASE WHEN
                        EXISTS (
                            SELECT 1 FROM download_requests dr
                            WHERE dr.student_id=? AND dr.resource_id=r.id
                              AND dr.status='Approved' AND dr.expires_at>=?
                        )
                        OR EXISTS (
                            SELECT 1 FROM resource_access_grants g
                            WHERE g.student_id=? AND g.resource_id=r.id
                              AND g.revoked_at IS NULL
                        )
                    THEN 1 ELSE 0 END AS can_download
                FROM resources r
                JOIN classes c ON c.id=r.class_id
                JOIN subjects s ON s.id=r.subject_id
                WHERE r.class_id=? AND r.archived=0
                ORDER BY r.created_at DESC
                ''',
                (u['id'], now(), u['id'], profile['class_id'])
            )
        else: resources=rows('SELECT r.*,c.name class_name,s.name subject_name,u.full_name uploader FROM resources r JOIN classes c ON c.id=r.class_id JOIN subjects s ON s.id=r.subject_id LEFT JOIN users u ON u.id=r.uploaded_by WHERE r.archived=0 ORDER BY r.created_at DESC')
        return render_template('library.html',resources=resources,profile=profile,requests=rows('SELECT dr.*,u.full_name,r.title FROM download_requests dr JOIN users u ON u.id=dr.student_id JOIN resources r ON r.id=dr.resource_id ORDER BY dr.id DESC') if u['role']=='admin' else [])
    def resource_file_details(resource):
        path = Path(app.config['UPLOAD_DIR']) / resource['filename']
        if not path.is_file():
            abort(404)

        extension = path.suffix.lower()
        title = (resource['title'] or '').strip() or path.stem

        if Path(title).suffix.lower() != extension:
            title = f'{title}{extension}'

        mimetype = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        return path, title, mimetype

    @app.get('/resource/<int:resource_id>/reader')
    @role_required('admin','teacher','student')
    def resource_reader(resource_id):
        resource = row(
            "SELECT r.*, c.name AS class_name, s.name AS subject_name "
            "FROM resources r "
            "JOIN classes c ON c.id = r.class_id "
            "JOIN subjects s ON s.id = r.subject_id "
            "WHERE r.id = ? AND r.archived = 0",
            (resource_id,)
        )

        if not resource:
            abort(404)

        if current_user()['role'] == 'student':
            assigned_student(class_id=resource['class_id'])

        _, _, mimetype = resource_file_details(resource)

        return render_template(
            'resource_reader.html',
            resource=resource,
            mimetype=mimetype
        )

    @app.get('/resource/<int:resource_id>/view')
    @role_required('admin','teacher','student')
    def view_resource(resource_id):
        r = row('SELECT * FROM resources WHERE id=? AND archived=0', (resource_id,))
        if not r:
            abort(404)
        if current_user()['role'] == 'student':
            assigned_student(class_id=r['class_id'])

        path, display_name, mimetype = resource_file_details(r)
        return send_file(
            path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=display_name,
            conditional=True
        )

    @app.post('/downloads/<int:req_id>/<decision>')
    @admin_required
    def decide_download(req_id,decision):
        if decision not in {'approve','reject'}: abort(400)
        status='Approved' if decision=='approve' else 'Rejected'; expiry=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat() if status=='Approved' else None; db=get_db(); db.execute('UPDATE download_requests SET status=?,decided_by=?,decided_at=?,expires_at=? WHERE id=?',(status,current_user()['id'],now(),expiry,req_id)); db.commit(); audit(current_user()['id'],f'download_{decision}','download_request',str(req_id)); flash(f'Download request {status.lower()}.','success'); return redirect(url_for('library'))

    @app.get('/resource/<int:resource_id>/download')
    @role_required('admin', 'teacher', 'student')
    def download_resource(resource_id):
        user = current_user()
        r = row('SELECT * FROM resources WHERE id=?', (resource_id,))

        if not r:
            abort(404)

        if user['role'] == 'student':
            assigned_student(class_id=r['class_id'])
            individual_approval = row(
                "SELECT id FROM download_requests "
                "WHERE student_id=? AND resource_id=? "
                "AND status='Approved' AND expires_at>=? "
                "ORDER BY id DESC LIMIT 1",
                (user['id'], resource_id, now())
            )

            bulk_grant = row(
                "SELECT id FROM resource_access_grants "
                "WHERE student_id=? AND resource_id=? "
                "AND revoked_at IS NULL LIMIT 1",
                (user['id'], resource_id)
            )

            if not individual_approval and not bulk_grant:
                abort(403)

        path, display_name, mimetype = resource_file_details(r)
        return send_file(
            path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=secure_filename(display_name),
            conditional=True
        )
    @app.route('/timetable',methods=['GET','POST'])
    @role_required('admin','teacher','student')
    def timetable():
        u = current_user()
        profile = assigned_student() if u['role'] == 'student' else None

        if request.method == 'POST':
            if u['role'] == 'student':
                abort(403)

            db = get_db()
            meet = request.form['meet_url']

            if not meet.startswith('https://meet.google.com/'):
                flash('Use a valid Google Meet URL.', 'error')
            else:
                lesson_id = db.execute(
                    """
                    INSERT INTO lessons
                    (title, class_id, subject_id, teacher_id,
                     starts_at, ends_at, meet_url, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        request.form['title'],
                        request.form['class_id'],
                        request.form['subject_id'],
                        u['id'],
                        request.form['starts_at'],
                        request.form['ends_at'],
                        meet,
                        now()
                    )
                ).fetchone()['id']

                db.commit()
                audit(u['id'], 'lesson_created', 'lesson', str(lesson_id))
                flash('Lesson scheduled.', 'success')

        if profile:
            lesson_rows = rows(
                """
                SELECT l.*, c.name class_name, s.name subject_name,
                       u.full_name teacher
                FROM lessons l
                JOIN classes c ON c.id = l.class_id
                JOIN subjects s ON s.id = l.subject_id
                LEFT JOIN users u ON u.id = l.teacher_id
                WHERE l.class_id = ?
                ORDER BY l.starts_at
                """,
                (profile['class_id'],)
            )
        else:
            lesson_rows = rows(
                """
                SELECT l.*, c.name class_name, s.name subject_name,
                       u.full_name teacher
                FROM lessons l
                JOIN classes c ON c.id = l.class_id
                JOIN subjects s ON s.id = l.subject_id
                LEFT JOIN users u ON u.id = l.teacher_id
                ORDER BY l.starts_at
                """
            )

        return render_template(
            'timetable.html',
            lessons=lesson_rows,
            profile=profile
        )
    @app.route('/tests',methods=['GET','POST'])
    @role_required('admin','teacher','student')
    def tests():
        u = current_user()
        profile = assigned_student() if u['role'] == 'student' else None

        if request.method == 'POST':
            if u['role'] == 'student':
                abort(403)

            db = get_db()

            tid = db.execute(
                """
                INSERT INTO tests
                (title, instructions, class_id, subject_id, topic,
                 opens_at, closes_at, max_marks, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    request.form['title'],
                    request.form['instructions'],
                    request.form['class_id'],
                    request.form['subject_id'],
                    request.form['topic'],
                    request.form['opens_at'],
                    request.form['closes_at'],
                    request.form['max_marks'],
                    u['id'],
                    now()
                )
            ).fetchone()['id']

            questions = [
                request.form.get('question_1', ''),
                request.form.get('question_2', ''),
                request.form.get('question_3', '')
            ]

            for i, question_text in enumerate(questions, 1):
                if question_text.strip():
                    db.execute(
                        """
                        INSERT INTO questions
                        (test_id, question_number, question_text, marks, answer_type)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (tid, i, question_text, request.form.get(f'marks_{i}', 1), 'long')
                    )

            db.commit()
            audit(u['id'], 'test_created', 'test', str(tid))
            flash('Written test created.', 'success')
            return redirect(url_for('tests'))

        ts = rows(
            """
            SELECT t.*, c.name class_name, s.name subject_name
            FROM tests t
            JOIN classes c ON c.id = t.class_id
            JOIN subjects s ON s.id = t.subject_id
            """ + ('WHERE t.class_id=? ' if profile else '') + 'ORDER BY t.closes_at',
            (profile['class_id'],) if profile else ()
        )

        return render_template('tests.html', tests=ts, profile=profile)
    @app.route('/test/<int:test_id>',methods=['GET','POST'])
    @student_required
    def take_test(test_id):
        t = row(
            """
            SELECT t.*, c.name class_name, s.name subject_name
            FROM tests t
            JOIN classes c ON c.id = t.class_id
            JOIN subjects s ON s.id = t.subject_id
            WHERE t.id = ?
            """,
            (test_id,)
        )

        if not t:
            abort(404)

        assigned_student(class_id=t['class_id'])

        qs = rows(
            'SELECT * FROM questions WHERE test_id=? ORDER BY question_number',
            (test_id,)
        )

        db = get_db()
        sub = row(
            'SELECT * FROM submissions WHERE test_id=? AND student_id=?',
            (test_id, current_user()['id'])
        )

        if request.method == 'POST':
            if sub and sub['status'] == 'Submitted':
                flash('This script is already submitted and locked.', 'error')
            else:
                upload = request.files.get('script_file')
                uploaded_name = None
                upload_error = None

                if upload and upload.filename:
                    original_name = secure_filename(upload.filename)
                    extension = (
                        original_name.rsplit('.', 1)[-1].lower()
                        if '.' in original_name else ''
                    )

                    content = upload.read(25 * 1024 * 1024 + 1)

                    if len(content) > 25 * 1024 * 1024:
                        upload_error = 'The script is too large. Maximum size is 25 MB.'
                    elif extension not in ALLOWED_UPLOADS:
                        upload_error = 'Upload a PDF, PNG or JPG/JPEG image.'
                    elif extension == 'pdf' and not content.startswith(b'%PDF-'):
                        upload_error = 'The uploaded PDF file is invalid.'
                    elif extension == 'png' and not content.startswith(bytes.fromhex('89504e470d0a1a0a')):
                        upload_error = 'The uploaded PNG image is invalid.'
                    elif extension in {'jpg', 'jpeg'} and not content.startswith(bytes.fromhex('ffd8ff')):
                        upload_error = 'The uploaded JPG image is invalid.'

                    if not upload_error:
                        uploaded_name = (
                            secrets.token_hex(12) + '_' + original_name
                        )
                        upload_path = Path(app.config['UPLOAD_DIR']) / uploaded_name
                        upload_path.write_bytes(content)

                if upload_error:
                    flash(upload_error, 'error')
                else:
                    if not sub:
                        sid = db.execute(
                            """
                            INSERT INTO submissions
                            (test_id, student_id, status, updated_at)
                            VALUES (?, ?, ?, ?)
                            RETURNING id
                            """,
                            (test_id, current_user()['id'], 'Draft', now())
                        ).fetchone()['id']
                    else:
                        sid = sub['id']

                    for q in qs:
                        db.execute(
                            """
                            INSERT INTO answers
                            (submission_id, question_id, answer_text, uploaded_file)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(submission_id, question_id)
                            DO UPDATE SET
                                answer_text=excluded.answer_text,
                                uploaded_file=COALESCE(
                                    excluded.uploaded_file,
                                    answers.uploaded_file
                                )
                            """,
                            (
                                sid,
                                q['id'],
                                request.form.get(f"answer_{q['id']}", ''),
                                uploaded_name
                            )
                        )

                    status = (
                        'Submitted'
                        if request.form.get('action') == 'submit'
                        else 'Draft'
                    )

                    db.execute(
                        """
                        UPDATE submissions
                        SET status=?, submitted_at=?, updated_at=?
                        WHERE id=?
                        """,
                        (status, now() if status == 'Submitted' else None, now(), sid)
                    )

                    db.commit()
                    audit(
                        current_user()['id'],
                        f'test_{status.lower()}',
                        'submission',
                        str(sid)
                    )

                    flash(
                        'Script submitted and locked.'
                        if status == 'Submitted'
                        else 'Draft saved.',
                        'success'
                    )

                    sub = row('SELECT * FROM submissions WHERE id=?', (sid,))

        answers = {
            x['question_id']: x['answer_text']
            for x in rows('SELECT * FROM answers WHERE submission_id=?', (sub['id'],))
        } if sub else {}

        script_file = None
        if sub:
            script_row = row(
                '''
                SELECT uploaded_file
                FROM answers
                WHERE submission_id=? AND uploaded_file IS NOT NULL
                LIMIT 1
                ''',
                (sub['id'],)
            )
            script_file = script_row['uploaded_file'] if script_row else None

        test_download_ready = False

        release_record = row(
            'SELECT submission_id FROM submission_releases WHERE submission_id=?',
            (sub['id'],)
        ) if sub else None

        if sub and sub['status'] == 'Submitted' and release_record:
            guide = row(
                'SELECT id FROM test_marking_guides WHERE test_id=?',
                (test_id,)
            )
            marked_count = row(
                'SELECT COUNT(*) AS total FROM marks WHERE submission_id=?',
                (sub['id'],)
            )['total']

            test_download_ready = bool(guide) and marked_count == len(qs) and len(qs) > 0

        return render_template(
            'take_test.html',
            test=t,
            questions=qs,
            submission=sub,
            answers=answers,
            script_file=script_file,
            test_download_ready=test_download_ready
        )

    @app.post('/test-submission/<int:submission_id>/download-request')
    @student_required
    def request_test_download(submission_id):
        sub = row(
            'SELECT test_id FROM submissions WHERE id=? AND student_id=?',
            (submission_id, current_user()['id'])
        )
        if not sub:
            abort(404)

        flash('Download requests are no longer required for released marked tests. Use the download button on your test results page.', 'info')
        return redirect(url_for('take_test', test_id=sub['test_id']))

    @app.get('/mark/<int:submission_id>/script')
    @staff_required
    def view_submission_script(submission_id):
        submission = row(
            'SELECT id FROM submissions WHERE id=? AND status=?',
            (submission_id, 'Submitted')
        )
        if not submission:
            abort(404)

        script = row(
            """
            SELECT uploaded_file
            FROM answers
            WHERE submission_id=? AND uploaded_file IS NOT NULL
            LIMIT 1
            """,
            (submission_id,)
        )

        if not script or not script['uploaded_file']:
            abort(404)

        stored_name = script['uploaded_file']

        if Path(stored_name).name != stored_name:
            abort(404)

        extension = Path(stored_name).suffix.lower()
        mime_types = {
            '.pdf': 'application/pdf',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg'
        }

        if extension not in mime_types:
            abort(404)

        upload_root = Path(app.config['UPLOAD_DIR']).resolve()
        file_path = (upload_root / stored_name).resolve()

        if file_path.parent != upload_root or not file_path.is_file():
            abort(404)

        return send_file(
            str(file_path),
            mimetype=mime_types[extension],
            as_attachment=False,
            conditional=True
        )

    @app.get('/marking')
    @staff_required
    def marking(): return render_template('marking.html',submissions=rows("SELECT s.*,t.title test_title,u.full_name student,c.name class_name FROM submissions s JOIN tests t ON t.id=s.test_id JOIN users u ON u.id=s.student_id JOIN student_profiles sp ON sp.user_id=u.id JOIN classes c ON c.id=sp.class_id WHERE s.status='Submitted' ORDER BY s.submitted_at"))
    @app.route('/mark/<int:submission_id>',methods=['GET','POST'])
    @staff_required
    def mark(submission_id):
        sub = row(
            """
            SELECT s.*, t.title test_title, t.max_marks, u.full_name student
            FROM submissions s
            JOIN tests t ON t.id = s.test_id
            JOIN users u ON u.id = s.student_id
            WHERE s.id = ?
            """,
            (submission_id,)
        )

        if not sub:
            abort(404)

        qs = rows(
            """
            SELECT q.*, a.answer_text
            FROM questions q
            LEFT JOIN answers a
                ON a.question_id = q.id AND a.submission_id = ?
            WHERE q.test_id = ?
            ORDER BY q.question_number
            """,
            (submission_id, sub['test_id'])
        )

        script_row = row(
            '''
            SELECT uploaded_file
            FROM answers
            WHERE submission_id=? AND uploaded_file IS NOT NULL
            LIMIT 1
            ''',
            (submission_id,)
        )
        script_file = script_row['uploaded_file'] if script_row else None
        script_url = (
            url_for('view_submission_script', submission_id=submission_id)
            if script_file else None
        )

        guide = row(
            'SELECT * FROM test_marking_guides WHERE test_id=?',
            (sub['test_id'],)
        )

        if request.method == 'POST':
            uploaded_guide = request.files.get('marking_guide')

            if uploaded_guide and uploaded_guide.filename:
                original_name = secure_filename(uploaded_guide.filename)

                if not original_name or not original_name.lower().endswith('.pdf'):
                    flash('The marking guide must be a PDF file. Marks were not changed.', 'error')
                    return render_template(
                        'mark.html', submission=sub, questions=qs, guide=guide,
                        script_file=script_file, script_url=script_url
                    )

                signature = uploaded_guide.read(5)
                uploaded_guide.seek(0)

                if signature != b'%PDF-':
                    flash('The uploaded file is not a valid PDF. Marks were not changed.', 'error')
                    return render_template(
                        'mark.html', submission=sub, questions=qs, guide=guide,
                        script_file=script_file, script_url=script_url
                    )

                stored_name = secrets.token_hex(8) + '_' + original_name
                guide_path = Path(app.config['UPLOAD_DIR']) / stored_name
                uploaded_guide.save(str(guide_path))

                db = get_db()
                db.execute(
                    """
                    INSERT INTO test_marking_guides
                    (test_id, filename, original_name, uploaded_by, uploaded_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(test_id)
                    DO UPDATE SET
                        filename=excluded.filename,
                        original_name=excluded.original_name,
                        uploaded_by=excluded.uploaded_by,
                        uploaded_at=excluded.uploaded_at
                    """,
                    (
                        sub['test_id'],
                        stored_name,
                        original_name,
                        current_user()['id'],
                        now()
                    )
                )
            else:
                db = get_db()

            for q in qs:
                db.execute(
                    """
                    INSERT INTO marks
                    (submission_id, question_id, marks_awarded,
                     teacher_comment, marked_by, released_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(submission_id, question_id)
                    DO UPDATE SET
                        marks_awarded = excluded.marks_awarded,
                        teacher_comment = excluded.teacher_comment,
                        marked_by = excluded.marked_by,
                        released_at = excluded.released_at
                    """,
                    (
                        submission_id,
                        q['id'],
                        float(request.form.get(f"mark_{q['id']}", 0)),
                        request.form.get(f"comment_{q['id']}", ''),
                        current_user()['id'],
                        now()
                    )
                )

            # Keep the legacy test-wide flag for compatibility.
            db.execute(
                'UPDATE tests SET results_released=1 WHERE id=?',
                (sub['test_id'],)
            )

            # Release results for this submission only.
            db.execute(
                '''
                INSERT INTO submission_releases
                    (submission_id, released_by, released_at)
                VALUES (?, ?, ?)
                ON CONFLICT(submission_id) DO UPDATE SET
                    released_by=excluded.released_by,
                    released_at=excluded.released_at
                ''',
                (submission_id, current_user()['id'], now())
            )

            db.commit()
            audit(
                current_user()['id'],
                'marks_released',
                'submission',
                str(submission_id)
            )
            flash('Marks saved and released.', 'success')
            return redirect(url_for('marking'))

        return render_template(
            'mark.html', submission=sub, questions=qs, guide=guide,
            script_file=script_file, script_url=script_url
        )
    @app.route('/videos',methods=['GET','POST'])
    @role_required('admin','teacher','student')
    def videos():
        u = current_user()
        profile = assigned_student() if u['role'] == 'student' else None

        if request.method == 'POST':
            if u['role'] == 'student':
                abort(403)

            url = request.form['youtube_url']

            if not re.match(r'https://(www\.)?youtube\.com/watch\?v=|https://youtu\.be/', url):
                flash('Use an official YouTube watch or youtu.be URL.', 'error')
            else:
                db = get_db()

                video_id = db.execute(
                    """
                    INSERT INTO videos
                    (title, description, youtube_url, class_id,
                     subject_id, topic, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        request.form['title'],
                        request.form.get('description', ''),
                        url,
                        request.form['class_id'],
                        request.form['subject_id'],
                        request.form['topic'],
                        u['id'],
                        now()
                    )
                ).fetchone()['id']

                db.commit()
                audit(u['id'], 'video_added', 'video', str(video_id))
                flash('YouTube lesson added.', 'success')
                return redirect(url_for('videos'))

        if profile:
            vs = rows(
                """
                SELECT v.*, c.name class_name, s.name subject_name
                FROM videos v
                JOIN classes c ON c.id = v.class_id
                JOIN subjects s ON s.id = v.subject_id
                WHERE v.class_id = ?
                ORDER BY v.id DESC
                """,
                (profile['class_id'],)
            )
        else:
            vs = rows(
                """
                SELECT v.*, c.name class_name, s.name subject_name
                FROM videos v
                JOIN classes c ON c.id = v.class_id
                JOIN subjects s ON s.id = v.subject_id
                ORDER BY v.id DESC
                """
            )

        return render_template('videos.html', videos=vs)

    def staff_photo_folder():
        folder = Path(app.config['UPLOAD_DIR']) / 'staff'
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_staff_photo(upload):
        if not upload or not upload.filename:
            return None, None
        original = secure_filename(upload.filename)
        if '.' not in original:
            return None, 'Choose a PNG, JPG, JPEG or WEBP image.'
        extension = original.rsplit('.', 1)[-1].lower()
        if extension not in {'png', 'jpg', 'jpeg', 'webp'}:
            return None, 'Staff photos must be PNG, JPG, JPEG or WEBP files.'
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > 5 * 1024 * 1024:
            return None, 'The staff photo must be 5 MB or smaller.'
        filename = secrets.token_hex(12) + '.' + extension
        upload.save(staff_photo_folder() / filename)
        return filename, None

    def remove_staff_photo(filename):
        if filename:
            safe_name = secure_filename(filename)
            if safe_name == filename:
                folder = staff_photo_folder().resolve()
                photo_path = (folder / safe_name).resolve()
                if photo_path.parent == folder and photo_path.is_file():
                    photo_path.unlink()

    @app.get('/about')
    def about():
        profiles = rows("""
            SELECT * FROM staff_profiles
            WHERE is_published = 1
            ORDER BY display_order, full_name
        """)
        return render_template('about.html', profiles=profiles)

    @app.get('/staff-photo/<filename>')
    def staff_photo(filename):
        safe_name = secure_filename(filename)
        if not safe_name or safe_name != filename:
            abort(404)
        folder = staff_photo_folder().resolve()
        photo_path = (folder / safe_name).resolve()
        if photo_path.parent != folder or not photo_path.is_file():
            abort(404)
        content_type = mimetypes.guess_type(str(photo_path))[0] or 'application/octet-stream'
        return send_file(photo_path, mimetype=content_type, conditional=True)

    @app.get('/admin/staff')
    @admin_required
    def manage_staff():
        profiles = rows("""
            SELECT * FROM staff_profiles
            ORDER BY display_order, full_name
        """)
        return render_template('manage_staff.html', profiles=profiles)

    @app.route('/admin/staff/new', methods=['GET', 'POST'])
    @admin_required
    def add_staff():
        if request.method == 'POST':
            full_name = request.form.get('full_name', '').strip()
            if not full_name or len(full_name) > 120:
                flash('Enter a staff member name (maximum 120 characters).', 'error')
                return redirect(url_for('add_staff'))

            photo_filename, photo_error = save_staff_photo(request.files.get('photo'))
            if photo_error:
                flash(photo_error, 'error')
                return redirect(url_for('add_staff'))

            try:
                display_order = int(request.form.get('display_order') or 0)
            except ValueError:
                display_order = 0

            timestamp = now()
            db = get_db()
            db.execute("""
                INSERT INTO staff_profiles
                (full_name, job_title, qualifications, institution, experience, bio,
                 email, phone, whatsapp, photo_filename, display_order,
                 is_published, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                full_name,
                request.form.get('job_title', '').strip()[:120],
                request.form.get('qualifications', '').strip()[:1000],
                request.form.get('institution', '').strip()[:180],
                request.form.get('experience', '').strip()[:2000],
                request.form.get('bio', '').strip()[:3000],
                request.form.get('email', '').strip()[:180],
                request.form.get('phone', '').strip()[:40],
                request.form.get('whatsapp', '').strip()[:40],
                photo_filename or '',
                display_order,
                1 if request.form.get('is_published') == 'on' else 0,
                timestamp,
                timestamp
            ))
            db.commit()
            audit(current_user()['id'], 'create', 'staff_profile', '', full_name)
            flash('Staff profile added successfully.', 'success')
            return redirect(url_for('manage_staff'))

        return render_template('staff_form.html', profile=None, page_heading='Add staff member')

    @app.route('/admin/staff/<int:profile_id>/edit', methods=['GET', 'POST'])
    @admin_required
    def edit_staff(profile_id):
        profile = row('SELECT * FROM staff_profiles WHERE id = ?', (profile_id,))
        if not profile:
            abort(404)

        if request.method == 'POST':
            full_name = request.form.get('full_name', '').strip()
            if not full_name or len(full_name) > 120:
                flash('Enter a staff member name (maximum 120 characters).', 'error')
                return redirect(url_for('edit_staff', profile_id=profile_id))

            photo_filename = profile['photo_filename'] or ''
            new_photo, photo_error = save_staff_photo(request.files.get('photo'))
            if photo_error:
                flash(photo_error, 'error')
                return redirect(url_for('edit_staff', profile_id=profile_id))
            if new_photo:
                photo_filename = new_photo

            try:
                display_order = int(request.form.get('display_order') or 0)
            except ValueError:
                display_order = 0

            db = get_db()
            db.execute("""
                UPDATE staff_profiles SET
                    full_name = ?, job_title = ?, qualifications = ?, institution = ?,
                    experience = ?, bio = ?, email = ?, phone = ?, whatsapp = ?,
                    photo_filename = ?, display_order = ?, is_published = ?, updated_at = ?
                WHERE id = ?
            """, (
                full_name,
                request.form.get('job_title', '').strip()[:120],
                request.form.get('qualifications', '').strip()[:1000],
                request.form.get('institution', '').strip()[:180],
                request.form.get('experience', '').strip()[:2000],
                request.form.get('bio', '').strip()[:3000],
                request.form.get('email', '').strip()[:180],
                request.form.get('phone', '').strip()[:40],
                request.form.get('whatsapp', '').strip()[:40],
                photo_filename,
                display_order,
                1 if request.form.get('is_published') == 'on' else 0,
                now(),
                profile_id
            ))
            db.commit()
            if new_photo and profile['photo_filename']:
                remove_staff_photo(profile['photo_filename'])
            audit(current_user()['id'], 'update', 'staff_profile', str(profile_id), full_name)
            flash('Staff profile updated successfully.', 'success')
            return redirect(url_for('manage_staff'))

        return render_template('staff_form.html', profile=profile, page_heading='Edit staff member')

    @app.post('/admin/staff/<int:profile_id>/delete')
    @admin_required
    def delete_staff(profile_id):
        profile = row('SELECT * FROM staff_profiles WHERE id = ?', (profile_id,))
        if not profile:
            abort(404)
        db = get_db()
        db.execute('DELETE FROM staff_profiles WHERE id = ?', (profile_id,))
        db.commit()
        remove_staff_photo(profile['photo_filename'])
        audit(current_user()['id'], 'delete', 'staff_profile', str(profile_id), profile['full_name'])
        flash('Staff profile deleted.', 'success')
        return redirect(url_for('manage_staff'))


    @app.get('/test-submission/<int:submission_id>/download')
    @role_required('admin', 'teacher', 'student')
    def download_marked_test(submission_id):
        user = current_user()

        sub = row(
            """
            SELECT s.*, t.title AS test_title,
                   t.results_released, u.full_name AS student
            FROM submissions s
            JOIN tests t ON t.id = s.test_id
            JOIN users u ON u.id = s.student_id
            WHERE s.id = ?
            """,
            (submission_id,)
        )

        if not sub:
            abort(404)

        if sub['status'] != 'Submitted':
            abort(403)

        # Students may only download their own individually released submission.
        if user['role'] == 'student':
            if sub['student_id'] != user['id']:
                abort(403)

            release_record = row(
                'SELECT submission_id FROM submission_releases WHERE submission_id=?',
                (submission_id,)
            )
            if not release_record:
                abort(403)

        # Confirm that every question has a saved mark.
        qs = rows(
            """
            SELECT q.question_number, q.question_text, q.marks,
                   a.answer_text, m.marks_awarded, m.teacher_comment
            FROM questions q
            LEFT JOIN answers a
                ON a.question_id = q.id
               AND a.submission_id = ?
            LEFT JOIN marks m
                ON m.question_id = q.id
               AND m.submission_id = ?
            WHERE q.test_id = ?
            ORDER BY q.question_number
            """,
            (submission_id, submission_id, sub['test_id'])
        )

        if not qs or any(q['marks_awarded'] is None for q in qs):
            abort(403)

        guide = row(
            'SELECT * FROM test_marking_guides WHERE test_id = ?',
            (sub['test_id'],)
        )

        if not guide or not guide['filename']:
            abort(403)

        # Prevent path traversal or access outside the uploads directory.
        upload_root = Path(app.config['UPLOAD_DIR']).resolve()
        stored_name = guide['filename']

        if Path(stored_name).name != stored_name:
            abort(404)

        try:
            guide_path = (upload_root / stored_name).resolve(strict=True)
        except (OSError, RuntimeError):
            abort(404)

        if guide_path.parent != upload_root or not guide_path.is_file():
            abort(404)

        # Build a printable report. Escape all user-provided content.
        esc = lambda value: html.escape(
            '' if value is None else str(value)
        )

        total_awarded = sum(float(q['marks_awarded'] or 0) for q in qs)
        total_possible = sum(float(q['marks'] or 0) for q in qs)

        question_sections = []

        for q in qs:
            question_sections.append(
                '<section class="question">'
                f'<h3>Question {esc(q["question_number"])}</h3>'
                f'<p>{esc(q["question_text"])}</p>'
                f'<p><b>Maximum marks:</b> {esc(q["marks"])}</p>'
                f'<p><b>Student answer:</b><br>{esc(q["answer_text"]).replace(chr(10), "<br>")}</p>'
                f'<p><b>Marks awarded:</b> {esc(q["marks_awarded"])}</p>'
                f'<p><b>Teacher comments:</b><br>{esc(q["teacher_comment"]).replace(chr(10), "<br>")}</p>'
                '</section>'
            )

        report = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marked test report</title>
<style>
body {{font-family:Arial,sans-serif;max-width:850px;margin:35px auto;padding:20px;line-height:1.6;color:#222}}
h1,h2 {{color:#9a742e}}
.question {{border:1px solid #ddd;padding:16px;margin:18px 0;page-break-inside:avoid}}
.summary {{background:#f5f2eb;padding:16px;margin:20px 0}}
button {{padding:10px 18px;cursor:pointer}}
@media print {{button {{display:none}} body {{margin:0}}}}
</style>
</head>
<body>
<button onclick="window.print()">Print report</button>
<h1>Holiday Science Hub</h1>
<h2>Marked Test Report</h2>
<div class="summary">
<p><b>Test:</b> {esc(sub['test_title'])}</p>
<p><b>Student:</b> {esc(sub['student'])}</p>
<p><b>Marks awarded:</b> {esc(total_awarded)} / {esc(total_possible)}</p>
</div>
<h2>Questions, submitted answers and marking</h2>
{''.join(question_sections)}
</body>
</html>
"""

        # Create the ZIP in memory; no temporary file is needed.
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(
            zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr('marked_test_report.html', report)
            archive.write(guide_path, arcname='marking_guide.pdf')

        zip_buffer.seek(0)

        audit(
            user['id'],
            'marked_test_downloaded',
            'submission',
            str(submission_id)
        )

        download_name = secure_filename(
            f"{sub['test_title']}_{sub['student']}_marked_test.zip"
        ) or f"marked_test_{submission_id}.zip"

        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=download_name
        )

    @app.get('/admin/test-download-requests')
    @admin_required
    def test_download_requests():
        requests_list = rows(
            """
            SELECT
                r.*,
                s.test_id,
                t.title AS test_title,
                u.full_name AS student_name,
                u.email AS student_email
            FROM test_download_requests r
            JOIN submissions s ON s.id = r.submission_id
            JOIN tests t ON t.id = s.test_id
            JOIN users u ON u.id = r.student_id
            ORDER BY
                CASE WHEN r.status = 'Pending' THEN 0 ELSE 1 END,
                r.id DESC
            """
        )
        return render_template(
            'test_download_requests.html',
            requests_list=requests_list
        )

    @app.post('/admin/test-download-requests/<int:req_id>/<decision>')
    @admin_required
    def decide_test_download(req_id, decision):
        if decision not in {'approve', 'reject'}:
            abort(400)

        req = row(
            'SELECT * FROM test_download_requests WHERE id=?',
            (req_id,)
        )
        if not req:
            abort(404)

        if req['status'] != 'Pending':
            flash('This request has already been decided.', 'info')
            return redirect(url_for('test_download_requests'))

        status = 'Approved' if decision == 'approve' else 'Rejected'
        expiry = (
            (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
            if status == 'Approved' else None
        )

        db = get_db()
        db.execute(
            """
            UPDATE test_download_requests
            SET status=?, decided_by=?, decided_at=?, expires_at=?
            WHERE id=? AND status='Pending'
            """,
            (status, current_user()['id'], now(), expiry, req_id)
        )
        db.commit()

        audit(
            current_user()['id'],
            f'test_download_{decision}',
            'test_download_request',
            str(req_id)
        )

        flash(
            'Test download request approved.' if decision == 'approve'
            else 'Test download request rejected.',
            'success'
        )
        return redirect(url_for('test_download_requests'))

    @app.get('/audit')
    @admin_required
    def audit_page(): return render_template('audit.html',logs=rows('SELECT a.*,u.full_name actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 100'))
    @app.get('/export/students.csv')
    @admin_required
    def export_students():
        out=io.StringIO(); w=csv.writer(out); w.writerow(['name','email','class','level','payment','active']);
        for s in rows("SELECT u.full_name,u.email,c.name class_name,c.level,p.status,u.is_active FROM users u LEFT JOIN student_profiles sp ON sp.user_id=u.id LEFT JOIN classes c ON c.id=sp.class_id LEFT JOIN payments p ON p.student_id=u.id WHERE u.role='student'"): w.writerow([s['full_name'],s['email'],s['class_name'],s['level'],s['status'],s['is_active']])
        return app.response_class(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=students.csv'})
    return app
