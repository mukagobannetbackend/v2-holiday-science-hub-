import io
from pathlib import Path
import pytest
from app import create_app
from app.db import get_db

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

    # Student requests permission after marking and release.
    client.post('/logout')
    login(client, 'student@holidayhub.local', 'Student@123')

    response = client.post(
        f'/test-submission/{submission_id}/download-request',
        data={'reason': 'I want to keep my marked work.'},
        follow_redirects=True
    )
    assert response.status_code == 200

    with client.application.app_context():
        db = get_db()
        req = db.execute(
            "SELECT * FROM test_download_requests WHERE submission_id=? "
            "ORDER BY id DESC LIMIT 1",
            (submission_id,)
        ).fetchone()
        assert req is not None
        request_id = req['id']
        assert req['status'] == 'Pending'

    # Student cannot download before admin approval.
    response = client.get(
        f'/test-submission/{submission_id}/download'
    )
    assert response.status_code == 403

    # Teacher cannot open the admin queue or approve a request.
    assert teacher_client.get('/admin/test-download-requests').status_code == 403
    assert teacher_client.post(
        f'/admin/test-download-requests/{request_id}/approve'
    ).status_code == 403

    # Teacher can download directly once the marked materials are ready.
    teacher_download = teacher_client.get(
        f'/test-submission/{submission_id}/download'
    )
    assert teacher_download.status_code == 200
    assert teacher_download.mimetype == 'application/zip'

    with zipfile.ZipFile(io.BytesIO(teacher_download.data)) as archive:
        assert set(archive.namelist()) == {
            'marked_test_report.html',
            'marking_guide.pdf'
        }
        report = archive.read('marked_test_report.html').decode('utf-8')
        assert '&lt;script&gt;student answer&lt;/script&gt;' in report
        assert 'Good work from the student.' in report
        assert 'Marked Test Report' in report
        assert archive.read('marking_guide.pdf').startswith(b'%PDF-1.4')

    # Only the administrator approves the student's request.
    admin_client = client.application.test_client()
    login(admin_client, 'admin@holidayhub.local', 'Admin@123')

    assert admin_client.get('/admin/test-download-requests').status_code == 200

    response = admin_client.post(
        f'/admin/test-download-requests/{request_id}/approve',
        follow_redirects=True
    )
    assert response.status_code == 200

    with client.application.app_context():
        approved = get_db().execute(
            "SELECT * FROM test_download_requests WHERE id=?",
            (request_id,)
        ).fetchone()
        assert approved['status'] == 'Approved'
        assert approved['expires_at']

        expiry = datetime.fromisoformat(
            approved['expires_at'].replace('Z', '+00:00')
        )
        assert expiry > datetime.now(timezone.utc)

    # Student can now download the ZIP.
    student_download = client.get(
        f'/test-submission/{submission_id}/download'
    )
    assert student_download.status_code == 200
    assert student_download.mimetype == 'application/zip'

    # Admin can also download directly.
    admin_download = admin_client.get(
        f'/test-submission/{submission_id}/download'
    )
    assert admin_download.status_code == 200

    # Expired approval must block the student, but not the teacher.
    with client.application.app_context():
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        db = get_db()
        db.execute(
            "UPDATE test_download_requests SET expires_at=? WHERE id=?",
            (past, request_id)
        )
        db.commit()

    assert client.get(
        f'/test-submission/{submission_id}/download'
    ).status_code == 403

    assert teacher_client.get(
        f'/test-submission/{submission_id}/download'
    ).status_code == 200

