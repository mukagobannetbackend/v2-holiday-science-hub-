from functools import wraps
from flask import session, redirect, url_for, flash, request, abort
from werkzeug.security import check_password_hash, generate_password_hash
from .db import get_db, row, audit, now

def current_user():
    uid=session.get('user_id'); return row('SELECT * FROM users WHERE id=? AND is_active=1',(uid,)) if uid else None
def login_user(user): session.clear(); session['user_id']=user['id']; session['role']=user['role']
def logout_user(): session.clear()
def login(email,password):
    user=row('SELECT * FROM users WHERE lower(email)=lower(?)',(email.strip(),))
    if not user or not check_password_hash(user['password_hash'],password): return None
    if not user['is_active']: return None
    if user['role']=='student':
        payment=row("SELECT status FROM payments WHERE student_id=? ORDER BY id DESC LIMIT 1",(user['id'],))
        if not payment or payment['status']!='Approved/Paid': return None
    return user
def role_required(*roles):
    def dec(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            user=current_user()
            if not user: return redirect(url_for('login_view', next=request.path))
            if user['role'] not in roles: abort(403)
            if user['must_change_password'] and request.endpoint not in {'change_password','logout'}: return redirect(url_for('change_password'))
            return fn(*args,**kwargs)
        return wrapped
    return dec
def student_required(fn): return role_required('student')(fn)
def staff_required(fn): return role_required('admin','teacher')(fn)
def admin_required(fn): return role_required('admin')(fn)
def assigned_student(student_id=None, class_id=None):
    u=current_user(); profile=row('SELECT * FROM student_profiles WHERE user_id=?',(student_id or u['id'],))
    if u['role']=='student' and (not profile or (class_id is not None and profile['class_id']!=int(class_id))): abort(403)
    return profile
def set_password(user_id,password,must_change=0):
    db=get_db(); db.execute('UPDATE users SET password_hash=?,must_change_password=? WHERE id=?',(generate_password_hash(password),must_change,user_id)); db.commit(); audit(user_id,'password_changed','user',str(user_id))
