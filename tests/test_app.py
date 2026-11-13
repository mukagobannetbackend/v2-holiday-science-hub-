import io
from pathlib import Path
import pytest
from app import create_app
from app.db import get_db, now
from werkzeug.security import generate_password_hash

@pytest.fixture()
def app(tmp_path):
    return create_app({'TESTING':True,'SECRET_KEY':'test','DATABASE_PATH':str(tmp_path/'hub.db'),'UPLOAD_DIR':str(tmp_path/'uploads'),'SEED_DEMO_DATA':True})
@pytest.fixture()
def client(app): return app.test_client()
def login(client,email,password): return client.post('/login',data={'email':email,'password':password},follow_redirects=True)

def test_landing_and_student_login(client):
    assert client.get('/').status_code==200
    response=login(client,'student@holidayhub.local','Student@123')
    assert response.status_code==200
    assert b'Your learning desk' in response.data
    assert b'S2' in response.data

def test_pending_student_cannot_login(client):
    response=client.post('/login',data={'email':'pending@holidayhub.local','password':'Pending@123'},follow_redirects=True)
    assert b'Sign-in failed' in response.data

def test_student_cannot_open_admin_and_class_scope_is_server_enforced(client):
    login(client,'student@holidayhub.local','Student@123')
    assert client.get('/students').status_code==403
    assert client.get('/timetable').status_code==200
    # S4 lesson/resource IDs are denied even if manually requested.
    with client.application.app_context():
        db=get_db(); db.execute("INSERT INTO resources(title,description,filename,level,class_id,subject_id,topic,uploaded_by,created_at) VALUES('S4 note','','nope.pdf','O-Level',4,1,'Cells',1,'2026-11-07T00:00Z')"); db.commit(); rid=db.execute('SELECT last_insert_rowid()').fetchone()[0]
    assert client.get(f'/resource/{rid}/view').status_code==403
    assert client.get(f'/resource/{rid}/reader').status_code==403

def test_admin_approves_payment_and_audit_exists(client):
    login(client,'admin@holidayhub.local','Admin@123')
    assert b'PENDING PAYMENTS' in client.get('/dashboard').data
    response=client.post('/students/4/approve',follow_redirects=True)
    assert response.status_code==200
    with client.application.app_context():
        assert get_db().execute("SELECT status FROM payments WHERE student_id=4").fetchone()[0]=='Approved/Paid'
        assert get_db().execute("SELECT COUNT(*) FROM audit_logs WHERE action='payment_approved'").fetchone()[0]==1

def test_download_request_requires_admin_approval(client):
    login(client,'student@holidayhub.local','Student@123')
    with client.application.app_context():
        db=get_db(); Path(client.application.config['UPLOAD_DIR']).mkdir(parents=True, exist_ok=True); (Path(client.application.config['UPLOAD_DIR'])/'nope.pdf').write_bytes(b'%PDF-1.4\\n% test PDF\\n'); db.execute("INSERT INTO resources(title,description,filename,level,class_id,subject_id,topic,uploaded_by,created_at) VALUES('S2 note','','nope.pdf','O-Level',2,1,'Cells',1,'2026-11-07T00:00Z')"); db.commit(); rid=db.execute('SELECT last_insert_rowid()').fetchone()[0]
    view_response=client.get(f'/resource/{rid}/view')
    assert view_response.status_code==200
    assert view_response.mimetype=='application/pdf'
    assert 'inline' in view_response.headers.get('Content-Disposition','').lower()
    assert 'S2 note.pdf' in view_response.headers.get('Content-Disposition','')
    reader_response=client.get(f'/resource/{rid}/reader')
    assert reader_response.status_code==200
    assert b'Reading mode' in reader_response.data
    assert b'id="pdf-canvas"' in reader_response.data
    assert b'pdfjsLib.getDocument' in reader_response.data
    assert client.get('/static/pdfjs/pdf.min.js').status_code == 200
    assert client.get('/static/pdfjs/pdf.worker.min.js').status_code == 200
    assert b'S2 note' in reader_response.data
    response=client.post('/library',data={'resource_id':rid,'reason':'revision'},follow_redirects=True)
    assert b'Download request sent' in response.data
    assert client.get(f'/resource/{rid}/download').status_code==403
    client.get('/logout'); login(client,'admin@holidayhub.local','Admin@123')
    req=None
    with client.application.app_context(): req=get_db().execute('SELECT id FROM download_requests ORDER BY id DESC LIMIT 1').fetchone()[0]
    client.post(f'/downloads/{req}/approve')
    client.get('/logout'); login(client,'student@holidayhub.local','Student@123')
    download_response=client.get(f'/resource/{rid}/download')
    assert download_response.status_code==200
    assert download_response.mimetype=='application/pdf'
    disposition=download_response.headers.get('Content-Disposition','')
    assert 'attachment' in disposition.lower()
    assert 'S2_note.pdf' in disposition

def test_written_test_draft_and_submission_lock(client):
    login(client,'student@holidayhub.local','Student@123')
    response=client.get('/tests'); assert b'Cell structure' in response.data
    response=client.post('/test/1',data={'answer_1':'Membrane answer','answer_2':'Osmosis answer','answer_3':'Difference','action':'draft'},follow_redirects=True)
    assert b'Draft' in response.data
    response=client.post('/test/1',data={'answer_1':'Membrane answer','answer_2':'Osmosis answer','answer_3':'Difference','action':'submit'},follow_redirects=True)
    assert b'Submitted' in response.data
    response=client.post('/test/1',data={'answer_1':'Changed','action':'draft'},follow_redirects=True)
    assert b'already submitted' in response.data


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("handwritten.pdf", b"%PDF-1.4\n% test PDF\n"),
        ("handwritten.png", b"\x89PNG\r\n\x1a\n" + b"test image data"),
        ("handwritten.jpg", b"\xff\xd8\xff" + b"test image data"),
    ],
)
def test_student_can_upload_valid_script_files(client, filename, content):
    login(client, "student@holidayhub.local", "Student@123")

    response = client.post(
        "/test/1",
        data={
            "action": "draft",
            "script_file": (io.BytesIO(content), filename),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200

    with client.application.app_context():
        db = get_db()
        rows = db.execute(
            "SELECT uploaded_file FROM answers WHERE submission_id=("
            "SELECT id FROM submissions WHERE test_id=1 LIMIT 1)"
        ).fetchall()

        assert rows
        saved_names = {row["uploaded_file"] for row in rows}
        assert len(saved_names) == 1
        saved_name = saved_names.pop()
        assert saved_name
        saved_path = Path(client.application.config["UPLOAD_DIR"]) / saved_name
        assert saved_path.exists()
        assert saved_path.read_bytes() == content


def test_student_upload_rejects_invalid_pdf_signature(client):
    login(client, "student@holidayhub.local", "Student@123")

    response = client.post(
        "/test/1",
        data={
            "action": "draft",
            "script_file": (io.BytesIO(b"not a real PDF"), "fake.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert b"The uploaded PDF file is invalid." in response.data

    with client.application.app_context():
        db = get_db()
        assert db.execute(
            "SELECT COUNT(*) FROM submissions WHERE test_id=1"
        ).fetchone()[0] == 0


def test_student_draft_without_new_file_preserves_existing_upload(client):
    login(client, "student@holidayhub.local", "Student@123")

    client.post(
        "/test/1",
        data={
            "action": "draft",
            "script_file": (
                io.BytesIO(b"%PDF-1.4\n% original script\n"),
                "original.pdf",
            ),
        },
        content_type="multipart/form-data",
    )

    with client.application.app_context():
        db = get_db()
        original_name = db.execute(
            "SELECT uploaded_file FROM answers "
            "WHERE uploaded_file IS NOT NULL LIMIT 1"
        ).fetchone()["uploaded_file"]

    response = client.post(
        "/test/1",
        data={"action": "draft", "answer_1": "Updated typed answer"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        db = get_db()
        saved_name = db.execute(
            "SELECT uploaded_file FROM answers "
            "WHERE uploaded_file IS NOT NULL LIMIT 1"
        ).fetchone()["uploaded_file"]
        assert saved_name == original_name
        assert (
            Path(client.application.config["UPLOAD_DIR"]) / saved_name
        ).exists()


def test_submitted_script_upload_cannot_be_replaced(client):
    login(client, "student@holidayhub.local", "Student@123")

    client.post(
        "/test/1",
        data={
            "action": "submit",
            "script_file": (
                io.BytesIO(b"%PDF-1.4\n% submitted script\n"),
                "submitted.pdf",
            ),
        },
        content_type="multipart/form-data",
    )

    with client.application.app_context():
        db = get_db()
        original_name = db.execute(
            "SELECT uploaded_file FROM answers "
            "WHERE uploaded_file IS NOT NULL LIMIT 1"
        ).fetchone()["uploaded_file"]

    response = client.post(
        "/test/1",
        data={
            "action": "draft",
            "script_file": (
                io.BytesIO(b"%PDF-1.4\n% replacement script\n"),
                "replacement.pdf",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert b"already submitted and locked" in response.data

    with client.application.app_context():
        db = get_db()
        saved_name = db.execute(
            "SELECT uploaded_file FROM answers "
            "WHERE uploaded_file IS NOT NULL LIMIT 1"
        ).fetchone()["uploaded_file"]
        assert saved_name == original_name

def test_teacher_marks_and_releases(client):
    login(client,'student@holidayhub.local','Student@123')
    client.post('/test/1',data={'answer_1':'a','answer_2':'b','answer_3':'c','action':'submit'})
    client.get('/logout'); login(client,'teacher@holidayhub.local','Teacher@123')
    response=client.get('/marking'); assert b'Approved Student' in response.data
    with client.application.app_context(): sid=get_db().execute('SELECT id FROM submissions LIMIT 1').fetchone()[0]
    response=client.post(f'/mark/{sid}',data={'mark_1':'5','mark_2':'4','mark_3':'5','comment_1':'Good','comment_2':'Clear','comment_3':'Well done'},follow_redirects=True)
    assert b'Marks saved' in response.data


def registration_data(**overrides):
    data = {
        'full_name': 'Test Student',
        'email': 'newstudent@example.com',
        'phone': '0700000000',
        'class_id': '2',
        'admission_no': 'TEST-2026-001',
        'guardian_contact': '0711111111',
        'amount': '50000',
        'reference': 'TEST-PAY-001',
        'temporary_password': 'TempPass123!'
    }
    data.update(overrides)
    return data


def test_admin_registers_student_and_student_completes_first_login(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    response = client.post(
        '/students/register',
        data=registration_data(),
        follow_redirects=True
    )
    assert response.status_code == 200
    assert b'Student registered' in response.data

    with client.application.app_context():
        student = get_db().execute(
            "SELECT * FROM users WHERE email = ?",
            ('newstudent@example.com',)
        ).fetchone()

        assert student is not None
        student_id = student['id']
        assert student['role'] == 'student'
        assert student['is_active'] == 0
        assert student['must_change_password'] == 0

        payment = get_db().execute(
            "SELECT * FROM payments WHERE student_id = ?",
            (student_id,)
        ).fetchone()

        assert payment['status'] == 'Pending'
        assert payment['amount'] == 50000
        assert payment['reference'] == 'TEST-PAY-001'

    # A separate browser session cannot log in before payment approval.
    student_client = client.application.test_client()
    response = login(
        student_client,
        'newstudent@example.com',
        'TempPass123!'
    )
    assert b'Sign-in failed' in response.data

    # The administrator approves the student's pending payment.
    response = client.post(
        f'/students/{student_id}/approve',
        follow_redirects=True
    )
    assert response.status_code == 200
    assert b'Payment approved' in response.data

    with client.application.app_context():
        student = get_db().execute(
            "SELECT * FROM users WHERE id = ?",
            (student_id,)
        ).fetchone()

        payment = get_db().execute(
            "SELECT * FROM payments WHERE student_id = ?",
            (student_id,)
        ).fetchone()

        assert student['is_active'] == 1
        assert student['must_change_password'] == 1
        assert payment['status'] == 'Approved/Paid'

    # After approval, the student can log in but must change the password.
    response = login(
        student_client,
        'newstudent@example.com',
        'TempPass123!'
    )
    assert b'Choose a new password' in response.data

    response = student_client.post(
        '/change-password',
        data={
            'password': 'NewSecurePass456!',
            'confirm': 'NewSecurePass456!'
        },
        follow_redirects=True
    )
    assert response.status_code == 200
    assert b'Password updated' in response.data

    student_client.get('/logout')

    response = login(
        student_client,
        'newstudent@example.com',
        'NewSecurePass456!'
    )
    assert response.status_code == 200
    assert b'Your learning desk' in response.data

    with client.application.app_context():
        student = get_db().execute(
            "SELECT must_change_password FROM users WHERE id = ?",
            (student_id,)
        ).fetchone()
        assert student['must_change_password'] == 0


def test_registration_rejects_duplicate_details_and_invalid_class(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    response = client.post(
        '/students/register',
        data=registration_data(),
        follow_redirects=True
    )
    assert b'Student registered' in response.data

    # Duplicate email.
    response = client.post(
        '/students/register',
        data=registration_data(
            admission_no='TEST-2026-002',
            reference='TEST-PAY-002'
        ),
        follow_redirects=True
    )
    assert b'already registered' in response.data

    # Duplicate admission number.
    response = client.post(
        '/students/register',
        data=registration_data(
            email='anotherstudent@example.com',
            admission_no='TEST-2026-001',
            reference='TEST-PAY-003'
        ),
        follow_redirects=True
    )
    assert b'admission number is already in use' in response.data

    # Duplicate payment reference.
    response = client.post(
        '/students/register',
        data=registration_data(
            email='thirdstudent@example.com',
            admission_no='TEST-2026-003',
            reference='TEST-PAY-001'
        ),
        follow_redirects=True
    )
    assert b'payment reference is already recorded' in response.data

    # Invalid class.
    response = client.post(
        '/students/register',
        data=registration_data(
            email='fourthstudent@example.com',
            admission_no='TEST-2026-004',
            reference='TEST-PAY-004',
            class_id='99999'
        ),
        follow_redirects=True
    )
    assert b'Select a valid class' in response.data

    with client.application.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) FROM users WHERE role='student' AND email LIKE '%@example.com'"
        ).fetchone()[0]
        assert count == 1


def test_student_cannot_register_other_students(client):
    login(client, 'student@holidayhub.local', 'Student@123')

    response = client.post(
        '/students/register',
        data=registration_data(),
        follow_redirects=True
    )

    assert response.status_code == 403

    with client.application.app_context():
        student = get_db().execute(
            "SELECT id FROM users WHERE email = ?",
            ('newstudent@example.com',)
        ).fetchone()
        assert student is None



def test_teacher_cannot_access_admin_only_pages(client):
    login(client, 'teacher@holidayhub.local', 'Teacher@123')

    for path in ['/students', '/audit', '/admin/staff', '/export/students.csv']:
        response = client.get(path)
        assert response.status_code == 403, f"Teacher unexpectedly accessed {path}"


def test_teacher_can_access_teaching_pages(client):
    login(client, 'teacher@holidayhub.local', 'Teacher@123')

    for path in ['/library', '/timetable', '/tests', '/marking', '/videos']:
        response = client.get(path)
        assert response.status_code == 200, f"Teacher could not access {path}"


def test_admin_can_access_admin_and_teaching_pages(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    for path in [
        '/students', '/audit', '/admin/staff', '/export/students.csv',
        '/library', '/timetable', '/tests', '/marking', '/videos'
    ]:
        response = client.get(path)
        assert response.status_code == 200, f"Admin could not access {path}"


def test_teacher_dashboard_hides_admin_navigation(client):
    login(client, 'teacher@holidayhub.local', 'Teacher@123')
    response = client.get('/dashboard')

    assert response.status_code == 200
    assert b'href="/students"' not in response.data
    assert b'href="/admin/staff"' not in response.data
    assert b'href="/audit"' not in response.data


def test_marked_test_zip_download_permissions_and_contents(client):
    import zipfile
    from datetime import datetime, timezone, timedelta

    # Student submits the test normally.
    login(client, 'student@holidayhub.local', 'Student@123')

    with client.application.app_context():
        db = get_db()
        questions = db.execute(
            "SELECT id FROM questions WHERE test_id=1 ORDER BY question_number"
        ).fetchall()
        question_ids = [q['id'] for q in questions]

    assert len(question_ids) > 0

    answers_data = {'action': 'submit'}
    for qid in question_ids:
        answers_data[f'answer_{qid}'] = '<script>student answer</script>'

    response = client.post('/test/1', data=answers_data, follow_redirects=True)
    assert response.status_code == 200

    with client.application.app_context():
        db = get_db()
        submission = db.execute(
            "SELECT id FROM submissions WHERE test_id=1 AND student_id=("
            "SELECT id FROM users WHERE email='student@holidayhub.local')"
        ).fetchone()
        assert submission is not None
        submission_id = submission['id']

    # Teacher marks the script and uploads the original PDF guide.
    teacher_client = client.application.test_client()
    login(teacher_client, 'teacher@holidayhub.local', 'Teacher@123')

    marking_data = {}
    for qid in question_ids:
        marking_data[f'mark_{qid}'] = '5'
        marking_data[f'comment_{qid}'] = 'Good work from the student.'

    marking_data['marking_guide'] = (
        io.BytesIO(b'%PDF-1.4\n% test marking guide'),
        'marking_guide.pdf'
    )

    response = teacher_client.post(
        f'/mark/{submission_id}',
        data=marking_data,
        content_type='multipart/form-data',
        follow_redirects=True
    )
    assert response.status_code == 200

    with client.application.app_context():
        db = get_db()
        guide = db.execute(
            "SELECT * FROM test_marking_guides WHERE test_id=1"
        ).fetchone()
        assert guide is not None

        marked = db.execute(
            "SELECT COUNT(*) FROM marks WHERE submission_id=?",
            (submission_id,)
        ).fetchone()[0]
        assert marked == len(question_ids)

        released = db.execute(
            "SELECT results_released FROM tests WHERE id=1"
        ).fetchone()[0]
        assert released == 1

    # Student can download their own marked test immediately after release.
    client.post('/logout')
    login(client, 'student@holidayhub.local', 'Student@123')

    with client.application.app_context():
        db = get_db()
        request_count_before = db.execute(
            "SELECT COUNT(*) FROM test_download_requests"
        ).fetchone()[0]
        student_id = db.execute(
            "SELECT id FROM users WHERE email='student@holidayhub.local'"
        ).fetchone()['id']
        admin_id = db.execute(
            "SELECT id FROM users WHERE email='admin@holidayhub.local'"
        ).fetchone()['id']

    # The retired request endpoint must not create a new request.
    response = client.post(
        f'/test-submission/{submission_id}/download-request',
        data={'reason': 'I want to keep my marked work.'},
        follow_redirects=True
    )
    assert response.status_code == 200

    with client.application.app_context():
        request_count_after = get_db().execute(
            "SELECT COUNT(*) FROM test_download_requests"
        ).fetchone()[0]
        assert request_count_after == request_count_before

    # Student downloads the ZIP without admin approval.
    student_download = client.get(
        f'/test-submission/{submission_id}/download'
    )
    assert student_download.status_code == 200
    assert student_download.mimetype == 'application/zip'

    with zipfile.ZipFile(io.BytesIO(student_download.data)) as archive:
        assert set(archive.namelist()) == {
            'marked_test_report.html',
            'marking_guide.pdf'
        }
        report = archive.read('marked_test_report.html').decode('utf-8')
        assert '&lt;script&gt;student answer&lt;/script&gt;' in report
        assert 'Good work from the student.' in report
        assert 'Marked Test Report' in report
        assert archive.read('marking_guide.pdf').startswith(b'%PDF-1.4')

    # A student must not download another user's marked submission.
    with client.application.app_context():
        db = get_db()
        db.execute(
            "UPDATE submissions SET student_id=? WHERE id=?",
            (admin_id, submission_id)
        )
        db.commit()

    assert client.get(
        f'/test-submission/{submission_id}/download'
    ).status_code == 403

    # Restore the original submission owner.
    with client.application.app_context():
        db = get_db()
        db.execute(
            "UPDATE submissions SET student_id=? WHERE id=?",
            (student_id, submission_id)
        )
        db.commit()

    # Teacher and administrator can download directly.
    teacher_download = teacher_client.get(
        f'/test-submission/{submission_id}/download'
    )
    assert teacher_download.status_code == 200
    assert teacher_download.mimetype == 'application/zip'

    admin_client = client.application.test_client()
    login(admin_client, 'admin@holidayhub.local', 'Admin@123')

    admin_download = admin_client.get(
        f'/test-submission/{submission_id}/download'
    )
    assert admin_download.status_code == 200
    assert admin_download.mimetype == 'application/zip'



def test_marked_results_release_is_individual_per_student(app):
    student_a = app.test_client()
    student_b = app.test_client()
    teacher = app.test_client()

    # Create a second approved student in the same class.
    with app.app_context():
        db = get_db()
        existing_student = db.execute(
            "SELECT class_id FROM student_profiles "
            "WHERE user_id=(SELECT id FROM users "
            "WHERE email='student@holidayhub.local')"
        ).fetchone()
        admin_id = db.execute(
            "SELECT id FROM users WHERE email='admin@holidayhub.local'"
        ).fetchone()['id']

        cursor = db.execute(
            """
            INSERT INTO users (
                full_name, email, phone, password_hash, role,
                is_active, must_change_password, created_at
            )
            VALUES (?, ?, ?, ?, 'student', 1, 0, ?)
            """,
            (
                'Second Approved Student',
                'student2@holidayhub.local',
                '0700000099',
                generate_password_hash('Student2@123'),
                now()
            )
        )
        student_b_id = cursor.lastrowid

        db.execute(
            """
            INSERT INTO student_profiles (user_id, class_id)
            VALUES (?, ?)
            """,
            (student_b_id, existing_student['class_id'])
        )

        db.execute(
            """
            INSERT INTO payments (
                student_id, amount, currency, reference,
                status, verified_by, verified_at
            )
            VALUES (?, 50000, 'UGX', ?, 'Approved/Paid', ?, ?)
            """,
            (student_b_id, 'TEST-STUDENT-2', admin_id, now())
        )
        db.commit()

        questions = db.execute(
            "SELECT id FROM questions WHERE test_id=1 ORDER BY question_number"
        ).fetchall()
        question_ids = [q['id'] for q in questions]

    assert question_ids

    # Both students submit the same test through the normal student page.
    for client, email, password in (
        (student_a, 'student@holidayhub.local', 'Student@123'),
        (student_b, 'student2@holidayhub.local', 'Student2@123')
    ):
        login(client, email, password)
        answers = {'action': 'submit'}
        for qid in question_ids:
            answers[f'answer_{qid}'] = 'My submitted answer'

        response = client.post(
            '/test/1', data=answers, follow_redirects=True
        )
        assert response.status_code == 200

    with app.app_context():
        db = get_db()
        submission_a = db.execute(
            """
            SELECT id FROM submissions
            WHERE test_id=1 AND student_id=(
                SELECT id FROM users WHERE email='student@holidayhub.local'
            )
            """
        ).fetchone()['id']

        submission_b = db.execute(
            """
            SELECT id FROM submissions
            WHERE test_id=1 AND student_id=?
            """,
            (student_b_id,)
        ).fetchone()['id']

        teacher_id = db.execute(
            "SELECT id FROM users WHERE email='teacher@holidayhub.local'"
        ).fetchone()['id']

    # Teacher marks and releases Student A's result only.
    login(teacher, 'teacher@holidayhub.local', 'Teacher@123')

    marks_data = {}
    for qid in question_ids:
        marks_data[f'mark_{qid}'] = '5'
        marks_data[f'comment_{qid}'] = 'Good work.'

    marks_data['marking_guide'] = (
        io.BytesIO(b'%PDF-1.4\n% test guide'),
        'marking_guide.pdf'
    )

    response = teacher.post(
        f'/mark/{submission_a}',
        data=marks_data,
        content_type='multipart/form-data',
        follow_redirects=True
    )
    assert response.status_code == 200

    # Student B is also fully marked, but has not been individually released.
    with app.app_context():
        db = get_db()
        for qid in question_ids:
            db.execute(
                """
                INSERT INTO marks (
                    submission_id, question_id, marks_awarded,
                    teacher_comment, marked_by, released_at
                )
                VALUES (?, ?, 5, 'Awaiting release', ?, ?)
                """,
                (submission_b, qid, teacher_id, now())
            )
        db.commit()

        marked_count = db.execute(
            "SELECT COUNT(*) FROM marks WHERE submission_id=?",
            (submission_b,)
        ).fetchone()[0]
        release_b = db.execute(
            "SELECT submission_id FROM submission_releases "
            "WHERE submission_id=?",
            (submission_b,)
        ).fetchone()

        assert marked_count == len(question_ids)
        assert release_b is None

    # Student A can download; Student B cannot download A's released result
    # or their own fully marked but unreleased result.
    assert student_a.get(
        f'/test-submission/{submission_a}/download'
    ).status_code == 200

    assert student_b.get(
        f'/test-submission/{submission_b}/download'
    ).status_code == 403

    # Teacher releases Student B's own result using a fresh form.
    marks_data_b = {}
    for qid in question_ids:
        marks_data_b[f'mark_{qid}'] = '5'
        marks_data_b[f'comment_{qid}'] = 'Good work.'

    response = teacher.post(
        f'/mark/{submission_b}',
        data=marks_data_b,
        follow_redirects=True
    )
    assert response.status_code == 200

    with app.app_context():
        release_b = get_db().execute(
            "SELECT submission_id FROM submission_releases "
            "WHERE submission_id=?",
            (submission_b,)
        ).fetchone()
        assert release_b is not None

    # Student B can now download without admin approval.
    assert student_b.get(
        f'/test-submission/{submission_b}/download'
    ).status_code == 200


def test_second_student_device_takes_over_and_first_can_login_again(app):
    device_a = app.test_client()
    device_b = app.test_client()

    # Device A logs in first.
    response_a = login(
        device_a, 'student@holidayhub.local', 'Student@123'
    )
    assert response_a.status_code == 200
    assert device_a.get('/timetable').status_code == 200

    # Device B logs in using the same student account.
    response_b = login(
        device_b, 'student@holidayhub.local', 'Student@123'
    )
    assert response_b.status_code == 200
    assert device_b.get('/timetable').status_code == 200

    # Device A must now be asked to sign in again.
    response_a = device_a.get('/timetable')
    assert response_a.status_code == 302
    assert '/login' in response_a.headers['Location']

    # Logging out from Device A must not remove Device B's session.
    device_a.get('/logout')
    assert device_b.get('/timetable').status_code == 200

    # Device A can log in again and take over normally.
    response_a = login(
        device_a, 'student@holidayhub.local', 'Student@123'
    )
    assert response_a.status_code == 200
    assert device_a.get('/timetable').status_code == 200

    # Device B is now the old device.
    response_b = device_b.get('/timetable')
    assert response_b.status_code == 302
    assert '/login' in response_b.headers['Location']


def test_bulk_suspend_updates_selected_students_and_audits_each(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    with client.application.app_context():
        db = get_db()
        student_ids = [
            db.execute(
                "SELECT id FROM users WHERE email=?",
                (email,)
            ).fetchone()['id']
            for email in (
                'student@holidayhub.local',
                'pending@holidayhub.local'
            )
        ]

    response = client.post(
        '/students/bulk-action',
        data={
            'action': 'suspend',
            'student_ids': [str(value) for value in student_ids]
        },
        follow_redirects=True
    )
    assert response.status_code == 200

    with client.application.app_context():
        db = get_db()
        for student_id in student_ids:
            account = db.execute(
                "SELECT is_active FROM users WHERE id=?",
                (student_id,)
            ).fetchone()
            assert account['is_active'] == 0

        audit_count = db.execute(
            """
            SELECT COUNT(*) FROM audit_logs
            WHERE action='account_suspended'
              AND details='Performed through bulk student management.'
            """
        ).fetchone()[0]
        assert audit_count == 2


def test_bulk_restore_only_reactivates_students_with_approved_payment(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    with client.application.app_context():
        db = get_db()
        approved_id = db.execute(
            "SELECT id FROM users WHERE email='student@holidayhub.local'"
        ).fetchone()['id']
        pending_id = db.execute(
            "SELECT id FROM users WHERE email='pending@holidayhub.local'"
        ).fetchone()['id']

        db.execute(
            "UPDATE users SET is_active=0 WHERE id IN (?, ?)",
            (approved_id, pending_id)
        )
        db.commit()

    response = client.post(
        '/students/bulk-action',
        data={
            'action': 'restore',
            'student_ids': [str(approved_id), str(pending_id)]
        },
        follow_redirects=True
    )
    assert response.status_code == 200
    assert b'1 student account(s) restored' in response.data
    assert b'1 account(s) were not restored' in response.data

    with client.application.app_context():
        db = get_db()
        approved = db.execute(
            "SELECT is_active FROM users WHERE id=?",
            (approved_id,)
        ).fetchone()
        pending = db.execute(
            "SELECT is_active FROM users WHERE id=?",
            (pending_id,)
        ).fetchone()

        assert approved['is_active'] == 1
        assert pending['is_active'] == 0


def test_bulk_hide_and_show_do_not_change_account_status(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    with client.application.app_context():
        db = get_db()
        student_id = db.execute(
            "SELECT id FROM users WHERE email='student@holidayhub.local'"
        ).fetchone()['id']

        db.execute(
            "UPDATE users SET is_active=0 WHERE id=?",
            (student_id,)
        )
        db.commit()

    hidden_response = client.post(
        '/students/bulk-action',
        data={'action': 'hide', 'student_ids': str(student_id)},
        follow_redirects=True
    )
    assert hidden_response.status_code == 200

    with client.application.app_context():
        db = get_db()
        account = db.execute(
            "SELECT is_active, is_hidden FROM users WHERE id=?",
            (student_id,)
        ).fetchone()
        assert account['is_active'] == 0
        assert account['is_hidden'] == 1

    shown_response = client.post(
        '/students/bulk-action',
        data={'action': 'show', 'student_ids': str(student_id)},
        follow_redirects=True
    )
    assert shown_response.status_code == 200

    with client.application.app_context():
        account = get_db().execute(
            "SELECT is_active, is_hidden FROM users WHERE id=?",
            (student_id,)
        ).fetchone()
        assert account['is_active'] == 0
        assert account['is_hidden'] == 0


def test_bulk_action_invalid_selection_makes_no_partial_changes(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    with client.application.app_context():
        student_id = get_db().execute(
            "SELECT id FROM users WHERE email='student@holidayhub.local'"
        ).fetchone()['id']

    response = client.post(
        '/students/bulk-action',
        data={
            'action': 'suspend',
            'student_ids': [str(student_id), '999999']
        },
        follow_redirects=True
    )
    assert response.status_code == 200
    assert b'No changes were made' in response.data

    with client.application.app_context():
        account = get_db().execute(
            "SELECT is_active FROM users WHERE id=?",
            (student_id,)
        ).fetchone()
        assert account['is_active'] == 1


def test_non_admin_cannot_use_bulk_student_management(client):
    login(client, 'student@holidayhub.local', 'Student@123')

    response = client.post(
        '/students/bulk-action',
        data={'action': 'suspend', 'student_ids': '4'}
    )
    assert response.status_code == 403

# ---------------------------------------------------------------------
# Bulk learning-resource permission regression tests
# ---------------------------------------------------------------------

def _add_permission_test_resource(client, title, filename, class_id=2):
    upload_dir = Path(client.application.config['UPLOAD_DIR'])
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / filename).write_bytes(b'%PDF-1.4\n% permission test PDF\n')

    with client.application.app_context():
        db = get_db()
        db.execute(
            """
            INSERT INTO resources
            (title, description, filename, level, class_id,
             subject_id, topic, uploaded_by, created_at)
            VALUES (?, '', ?, 'O-Level', ?, 1, 'Cells', 1, ?)
            """,
            (title, filename, class_id, now())
        )
        db.commit()
        return db.execute(
            "SELECT id FROM resources WHERE filename=?",
            (filename,)
        ).fetchone()['id']


def _add_second_s2_permission_test_student(client):
    with client.application.app_context():
        db = get_db()
        db.execute(
            """
            INSERT INTO users
            (full_name, email, phone, password_hash, role,
             is_active, must_change_password, created_at)
            VALUES (?, ?, '', ?, 'student', 1, 0, ?)
            """,
            (
                'Second S2 Student',
                'second-s2-permission-test@holidayhub.local',
                generate_password_hash('SecondStudent@123'),
                now()
            )
        )
        student_id = db.execute(
            "SELECT id FROM users WHERE email=?",
            ('second-s2-permission-test@holidayhub.local',)
        ).fetchone()['id']

        db.execute(
            """
            INSERT INTO student_profiles
            (user_id, class_id, admission_no, guardian_contact)
            VALUES (?, 2, ?, '')
            """,
            (student_id, 'HCH-TEST-S2-002')
        )
        db.commit()
        return student_id


def test_bulk_resource_grant_supports_multiple_students_and_resources(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    second_student_id = _add_second_s2_permission_test_student(client)
    resource_one = _add_permission_test_resource(
        client, 'Bulk permission note one', 'bulk-permission-one.pdf'
    )
    resource_two = _add_permission_test_resource(
        client, 'Bulk permission note two', 'bulk-permission-two.pdf'
    )

    with client.application.app_context():
        db = get_db()
        first_student_id = db.execute(
            "SELECT id FROM users WHERE email='student@holidayhub.local'"
        ).fetchone()['id']
        admin_id = db.execute(
            "SELECT id FROM users WHERE email='admin@holidayhub.local'"
        ).fetchone()['id']

    grant_data = {
        'class_id': '2',
        'student_ids': [str(first_student_id), str(second_student_id)],
        'resource_ids': [str(resource_one), str(resource_two)]
    }

    response = client.post(
        '/admin/resource-permissions/grant',
        data=grant_data,
        follow_redirects=True
    )
    assert response.status_code == 200

    with client.application.app_context():
        db = get_db()
        grants = db.execute(
            """
            SELECT student_id, resource_id, granted_by, granted_at
            FROM resource_access_grants
            ORDER BY student_id, resource_id
            """
        ).fetchall()

        assert len(grants) == 4
        assert all(grant['granted_by'] == admin_id for grant in grants)
        assert all(grant['granted_at'] for grant in grants)
        assert {
            (grant['student_id'], grant['resource_id'])
            for grant in grants
        } == {
            (first_student_id, resource_one),
            (first_student_id, resource_two),
            (second_student_id, resource_one),
            (second_student_id, resource_two)
        }

        audit_count = db.execute(
            """
            SELECT COUNT(*) FROM audit_logs
            WHERE action='resource_access_granted'
            """
        ).fetchone()[0]
        assert audit_count == 4

    # Repeating the same grant must not create duplicate active permissions.
    response = client.post(
        '/admin/resource-permissions/grant',
        data=grant_data,
        follow_redirects=True
    )
    assert response.status_code == 200

    with client.application.app_context():
        active_count = get_db().execute(
            """
            SELECT COUNT(*) FROM resource_access_grants
            WHERE revoked_at IS NULL
            """
        ).fetchone()[0]
        assert active_count == 4


def test_revoking_bulk_grant_preserves_individual_download_approval(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')
    resource_id = _add_permission_test_resource(
        client, 'Independent approval note', 'individual-approval-note.pdf'
    )

    client.get('/logout')
    login(client, 'student@holidayhub.local', 'Student@123')

    request_response = client.post(
        '/library',
        data={
            'resource_id': str(resource_id),
            'reason': 'Revision'
        },
        follow_redirects=True
    )
    assert b'Download request sent' in request_response.data

    client.get('/logout')
    login(client, 'admin@holidayhub.local', 'Admin@123')

    with client.application.app_context():
        db = get_db()
        request_id = db.execute(
            """
            SELECT id FROM download_requests
            WHERE resource_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (resource_id,)
        ).fetchone()['id']

    approval_response = client.post(
        f'/downloads/{request_id}/approve',
        follow_redirects=True
    )
    assert approval_response.status_code == 200

    with client.application.app_context():
        student_id = get_db().execute(
            "SELECT id FROM users WHERE email='student@holidayhub.local'"
        ).fetchone()['id']

    grant_response = client.post(
        '/admin/resource-permissions/grant',
        data={
            'class_id': '2',
            'student_ids': str(student_id),
            'resource_ids': str(resource_id)
        },
        follow_redirects=True
    )
    assert grant_response.status_code == 200

    with client.application.app_context():
        grant_id = get_db().execute(
            """
            SELECT id FROM resource_access_grants
            WHERE resource_id=? AND revoked_at IS NULL
            """,
            (resource_id,)
        ).fetchone()['id']

    revoke_response = client.post(
        '/admin/resource-permissions/revoke',
        data={
            'class_id': '2',
            'grant_ids': str(grant_id)
        },
        follow_redirects=True
    )
    assert revoke_response.status_code == 200

    with client.application.app_context():
        revoked = get_db().execute(
            """
            SELECT revoked_by, revoked_at
            FROM resource_access_grants WHERE id=?
            """,
            (grant_id,)
        ).fetchone()
        assert revoked['revoked_by'] is not None
        assert revoked['revoked_at']

        individual_approval = get_db().execute(
            """
            SELECT status, expires_at FROM download_requests
            WHERE id=?
            """,
            (request_id,)
        ).fetchone()
        assert individual_approval['status'] == 'Approved'
        assert individual_approval['expires_at']

    client.get('/logout')
    login(client, 'student@holidayhub.local', 'Student@123')

    # The independent individual approval must still permit downloading.
    assert client.get(
        f'/resource/{resource_id}/download'
    ).status_code == 200

    # Online reading must remain available too.
    assert client.get(
        f'/resource/{resource_id}/reader'
    ).status_code == 200


def test_bulk_resource_grant_rejects_mixed_class_selection_without_partial_changes(client):
    login(client, 'admin@holidayhub.local', 'Admin@123')

    resource_id = _add_permission_test_resource(
        client, 'S2 class restriction note', 's2-class-restriction.pdf'
    )

    with client.application.app_context():
        db = get_db()
        s2_student_id = db.execute(
            "SELECT id FROM users WHERE email='student@holidayhub.local'"
        ).fetchone()['id']
        s4_student_id = db.execute(
            "SELECT id FROM users WHERE email='pending@holidayhub.local'"
        ).fetchone()['id']

    response = client.post(
        '/admin/resource-permissions/grant',
        data={
            'class_id': '2',
            'student_ids': [str(s2_student_id), str(s4_student_id)],
            'resource_ids': str(resource_id)
        },
        follow_redirects=True
    )
    assert response.status_code == 200
    assert b'No permissions were changed' in response.data

    with client.application.app_context():
        count = get_db().execute(
            """
            SELECT COUNT(*) FROM resource_access_grants
            WHERE resource_id=?
            """,
            (resource_id,)
        ).fetchone()[0]
        assert count == 0


def test_student_cannot_open_or_submit_bulk_resource_permissions(client):
    login(client, 'student@holidayhub.local', 'Student@123')

    assert client.get('/admin/resource-permissions').status_code == 403

    response = client.post(
        '/admin/resource-permissions/grant',
        data={
            'class_id': '2',
            'student_ids': '2',
            'resource_ids': '1'
        }
    )
    assert response.status_code == 403

