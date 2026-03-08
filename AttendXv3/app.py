from flask import Flask, request, jsonify, render_template
import sqlite3, os, json, hashlib, time, secrets, re
from datetime import datetime

app = Flask(__name__)

# DB PATH
if os.environ.get('RENDER') or os.environ.get('DATABASE_PATH'):
    DB = os.environ.get('DATABASE_PATH', '/tmp/attendx.db')
else:
    DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attendx.db')

# In-memory QR token store
QR_TOKENS = {}   # token -> {session_id, issued_at}

DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']

def new_id():   return secrets.token_hex(8)
def hp(p):      return hashlib.sha256(p.encode()).hexdigest()[:20]
def now_iso():  return datetime.now().isoformat(timespec='seconds')
def today():    return datetime.now().strftime('%Y-%m-%d')
def today_day():return datetime.now().strftime('%A')  # e.g. "Monday"

def get_db():
    d = os.path.dirname(DB)
    if d and not os.path.exists(d): os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    c.execute('PRAGMA cache_size=10000')
    c.execute('PRAGMA temp_store=MEMORY')
    c.execute('PRAGMA busy_timeout=15000')
    return c

def cosine(a, b):
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x*y; na += x*x; nb += y*y
    return dot / ((na*nb)**0.5 or 1.0)

# ── INIT ──────────────────────────────────────────────────────
def init_db():
    c = get_db()
    # Create all tables
    c.executescript('''
        CREATE TABLE IF NOT EXISTS depts(
            id TEXT PRIMARY KEY, name TEXT, code TEXT);

        CREATE TABLE IF NOT EXISTS divisions(
            id TEXT PRIMARY KEY, name TEXT,
            dept_id TEXT, sems INTEGER DEFAULT 8);

        CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY, name TEXT, roll TEXT,
            email TEXT UNIQUE, pw TEXT,
            role TEXT DEFAULT "student",
            div_id TEXT, sem INTEGER, dept_id TEXT,
            face TEXT, face_img TEXT, created_at TEXT);

        CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY,
            teacher_id TEXT, teacher_name TEXT,
            div_id TEXT, div_name TEXT,
            dept_id TEXT, sem INTEGER,
            subject TEXT, room TEXT,
            date TEXT, day TEXT,
            started_at TEXT, ended_at TEXT,
            active INTEGER DEFAULT 1,
            qr_open INTEGER DEFAULT 0,
            att_mode TEXT DEFAULT "both",
            qr_lifetime INTEGER DEFAULT 30);

        CREATE TABLE IF NOT EXISTS attendance(
            id TEXT PRIMARY KEY,
            session_id TEXT, student_id TEXT,
            name TEXT, roll TEXT,
            div_id TEXT, div_name TEXT,
            sem INTEGER, subject TEXT,
            date TEXT, day TEXT,
            status TEXT, method TEXT,
            conf INTEGER, marked_at TEXT,
            cancel_reason TEXT DEFAULT '');

        CREATE TABLE IF NOT EXISTS timetable(
            id TEXT PRIMARY KEY,
            div_id TEXT, sem INTEGER, dept_id TEXT,
            day TEXT, period INTEGER,
            start_time TEXT, end_time TEXT,
            subject TEXT, teacher_id TEXT,
            teacher_name TEXT, room TEXT);

        CREATE TABLE IF NOT EXISTS cfg(k TEXT PRIMARY KEY, v TEXT);

        CREATE TABLE IF NOT EXISTS qr_tokens(
            token TEXT PRIMARY KEY,
            session_id TEXT,
            issued_at REAL);

        CREATE INDEX IF NOT EXISTS idx_att_stu   ON attendance(student_id);
        CREATE INDEX IF NOT EXISTS idx_att_sess  ON attendance(session_id);
        CREATE INDEX IF NOT EXISTS idx_att_date  ON attendance(date);
        CREATE INDEX IF NOT EXISTS idx_att_div   ON attendance(div_id, sem, date);
        CREATE INDEX IF NOT EXISTS idx_usr_div   ON users(div_id, sem, role);
        CREATE INDEX IF NOT EXISTS idx_sess_tea  ON sessions(teacher_id, active);
        CREATE INDEX IF NOT EXISTS idx_sess_div  ON sessions(div_id, sem, active);
        CREATE INDEX IF NOT EXISTS idx_tt_div    ON timetable(div_id, sem, day);
    ''')

    # Safe migrations — add missing columns to existing DBs
    migrations = [
        'ALTER TABLE attendance ADD COLUMN cancel_reason TEXT DEFAULT ""',
        'ALTER TABLE sessions ADD COLUMN qr_lifetime INTEGER DEFAULT 30',
        'ALTER TABLE sessions ADD COLUMN att_mode TEXT DEFAULT "both"',
    ]
    for sql in migrations:
        try: c.execute(sql)
        except: pass  # column already exists — ignore
    c.commit()

    if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
        d1=new_id(); d2=new_id()
        v1=new_id(); v2=new_id(); v3=new_id(); v4=new_id()
        c.executemany('INSERT OR IGNORE INTO depts VALUES(?,?,?)',[
            (d1,'Computer Science','CS'),(d2,'BCA','BCA'),
        ])
        c.executemany('INSERT OR IGNORE INTO divisions VALUES(?,?,?,?)',[
            (v1,'CS Div-A',d1,8),(v2,'CS Div-B',d1,8),
            (v3,'BCA Div-A',d2,6),(v4,'BCA Div-B',d2,6),
        ])
        t1=new_id(); t2=new_id()
        c.executemany('INSERT OR IGNORE INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[
            (new_id(),'Admin HOD','ADM001','admin@college.edu',hp('admin@123'),'admin',None,None,None,None,None,now_iso()),
            (t1,'Prof. Sharma','TCH001','teacher@college.edu',hp('admin@123'),'teacher',None,None,d1,None,None,now_iso()),
            (t2,'Prof. Mehta','TCH002','mehta@college.edu',hp('admin@123'),'teacher',None,None,d2,None,None,now_iso()),
            (new_id(),'Demo Student','CS001','student@college.edu',hp('stud@123'),'student',v1,3,d1,None,None,now_iso()),
        ])
        c.executemany('INSERT OR IGNORE INTO cfg VALUES(?,?)',[
            ('college','My Engineering College'),
            ('dept','Academics'),
            ('min_att','75'),
            ('face_conf','55'),
            ('qr_lifetime','30'),
            ('periods','6'),
        ])
        # Seed some timetable for CS Div-A Sem 3
        times=[('09:00','09:55'),('10:00','10:55'),('11:00','11:55'),
               ('12:00','12:55'),('14:00','14:55'),('15:00','15:55')]
        subjs=['Data Structures','Algorithms','DBMS','OS','Maths','Web Tech']
        for di,day in enumerate(DAYS[:5]):
            for pi in range(6):
                c.execute('INSERT OR IGNORE INTO timetable VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
                    new_id(),v1,3,d1,day,pi+1,
                    times[pi][0],times[pi][1],
                    subjs[(di+pi)%6],t1,'Prof. Sharma','Room 101'))
    c.commit(); c.close()

# ── ROUTES ────────────────────────────────────────────────────
@app.route('/')
def index(): return render_template('index.html')

@app.route('/ping')
def ping(): return jsonify({'ok':True,'ts':now_iso()})

# CONFIG
@app.route('/api/cfg')
def get_cfg():
    c=get_db(); r={x['k']:x['v'] for x in c.execute('SELECT * FROM cfg').fetchall()}; c.close(); return jsonify(r)

@app.route('/api/cfg',methods=['POST'])
def save_cfg():
    c=get_db()
    for k,v in request.json.items(): c.execute('INSERT OR REPLACE INTO cfg VALUES(?,?)',(k,str(v)))
    c.commit(); c.close(); return jsonify({'ok':True})

# AUTH
@app.route('/api/login',methods=['POST'])
def login():
    d=request.json; c=get_db()
    u=c.execute('SELECT * FROM users WHERE email=? AND pw=? AND role=?',
        (d['email'].lower().strip(),hp(d['password']),d['role'])).fetchone()
    c.close()
    return jsonify({'ok':bool(u),'user':dict(u) if u else None,'msg':'Wrong credentials' if not u else ''})

@app.route('/api/register',methods=['POST'])
def register():
    d=request.json
    name=d.get('name','').strip(); roll=d.get('roll','').strip().upper()
    email=d.get('email','').strip().lower(); pw=d.get('password','')
    role=d.get('role','student')
    if not all([name,roll,email,pw]): return jsonify({'ok':False,'msg':'All fields required'})
    if len(pw)<6: return jsonify({'ok':False,'msg':'Password min 6 chars'})
    c=get_db()
    ex=c.execute('SELECT id FROM users WHERE roll=? AND role=?',(roll,role)).fetchone()
    if ex: c.execute('DELETE FROM users WHERE id=?',(ex['id'],))
    if c.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():
        c.close(); return jsonify({'ok':False,'msg':'Email already registered'})
    uid=new_id()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (uid,name,roll,email,hp(pw),role,
         d.get('div_id'),int(d['sem']) if d.get('sem') else None,
         d.get('dept_id'),None,None,now_iso()))
    c.commit()
    u=dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    c.close(); return jsonify({'ok':True,'user':u})

# DEPTS
@app.route('/api/depts')
def get_depts():
    c=get_db(); r=[dict(x) for x in c.execute('SELECT * FROM depts ORDER BY name').fetchall()]; c.close(); return jsonify(r)

@app.route('/api/depts',methods=['POST'])
def add_dept():
    d=request.json; c=get_db()
    c.execute('INSERT INTO depts VALUES(?,?,?)',(new_id(),d['name'].strip(),d.get('code','').upper() or d['name'][:3].upper()))
    c.commit(); c.close(); return jsonify({'ok':True})

@app.route('/api/depts/<did>',methods=['DELETE'])
def del_dept(did):
    c=get_db(); c.execute('DELETE FROM depts WHERE id=?',(did,)); c.commit(); c.close(); return jsonify({'ok':True})

# DIVISIONS
@app.route('/api/divisions')
def get_divs():
    c=get_db(); r=[dict(x) for x in c.execute('SELECT * FROM divisions ORDER BY name').fetchall()]; c.close(); return jsonify(r)

@app.route('/api/divisions',methods=['POST'])
def add_div():
    d=request.json; c=get_db()
    c.execute('INSERT INTO divisions VALUES(?,?,?,?)',(new_id(),d['name'].strip(),d.get('dept_id'),int(d.get('sems',8))))
    c.commit(); c.close(); return jsonify({'ok':True})

@app.route('/api/divisions/<did>',methods=['DELETE'])
def del_div(did):
    c=get_db(); c.execute('DELETE FROM divisions WHERE id=?',(did,)); c.commit(); c.close(); return jsonify({'ok':True})

# USERS
@app.route('/api/users')
def get_users():
    c=get_db()
    q='SELECT id,name,roll,email,role,div_id,sem,dept_id,face_img,created_at,CASE WHEN face IS NOT NULL THEN 1 ELSE 0 END as has_face FROM users WHERE 1=1'
    p=[]
    for k in ['role','div_id','sem','dept_id']:
        if request.args.get(k): q+=f' AND {k}=?'; p.append(request.args[k])
    q+=' ORDER BY name'
    r=[dict(x) for x in c.execute(q,p).fetchall()]; c.close(); return jsonify(r)

@app.route('/api/users',methods=['POST'])
def add_user():
    d=request.json; c=get_db()
    roll=d['roll'].strip().upper(); role=d.get('role','student')
    ex=c.execute('SELECT id FROM users WHERE roll=? AND role=?',(roll,role)).fetchone()
    if ex: c.execute('DELETE FROM users WHERE id=?',(ex['id'],))
    if c.execute('SELECT 1 FROM users WHERE email=?',(d['email'].lower(),)).fetchone():
        c.close(); return jsonify({'ok':False,'msg':'Email already exists'})
    uid=new_id()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (uid,d['name'].strip(),roll,d['email'].lower(),
         hp(d.get('password','pass@123')),role,
         d.get('div_id'),int(d['sem']) if d.get('sem') else None,
         d.get('dept_id'),None,None,now_iso()))
    c.commit(); c.close(); return jsonify({'ok':True,'id':uid})

@app.route('/api/users/<uid>',methods=['GET'])
def get_user(uid):
    c=get_db(); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close()
    return jsonify({'ok':bool(u),'user':dict(u) if u else None})

@app.route('/api/users/<uid>',methods=['DELETE'])
def del_user(uid):
    c=get_db()
    c.execute('DELETE FROM users WHERE id=?',(uid,))
    c.execute('DELETE FROM attendance WHERE student_id=?',(uid,))
    c.commit(); c.close(); return jsonify({'ok':True})

@app.route('/api/users/<uid>/face',methods=['POST'])
def enroll_face(uid):
    d=request.json; c=get_db()
    c.execute('UPDATE users SET face=?,face_img=? WHERE id=?',(json.dumps(d['face']),d.get('img'),uid))
    c.commit()
    u=dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    c.close(); return jsonify({'ok':True,'user':u})

@app.route('/api/users/<uid>/face',methods=['DELETE'])
def del_face(uid):
    c=get_db(); c.execute('UPDATE users SET face=NULL,face_img=NULL WHERE id=?',(uid,)); c.commit(); c.close()
    return jsonify({'ok':True})

# SESSIONS — unlimited simultaneous
@app.route('/api/sessions')
def get_sessions():
    c=get_db()
    tid=request.args.get('teacher_id')
    div_id=request.args.get('div_id')
    active=request.args.get('active')
    limit=int(request.args.get('limit',50))
    q='SELECT * FROM sessions WHERE 1=1'; p=[]
    if tid:    q+=' AND teacher_id=?'; p.append(tid)
    if div_id: q+=' AND div_id=?';     p.append(div_id)
    if active: q+=' AND active=?';     p.append(int(active))
    q+=' ORDER BY rowid DESC LIMIT ?'; p.append(limit)
    r=[dict(x) for x in c.execute(q,p).fetchall()]; c.close(); return jsonify(r)

@app.route('/api/sessions',methods=['POST'])
def start_session():
    d=request.json; c=get_db()
    tid=d['teacher_id']
    # End ONLY this teacher's OWN previous sessions — other teachers untouched
    c.execute('UPDATE sessions SET active=0,qr_open=0,ended_at=? WHERE teacher_id=? AND active=1',
              (now_iso(),tid))
    sid=new_id()
    dt=today(); dy=today_day()
    qrl=int(d.get('qr_lifetime',30))
    c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?)',
        (sid,tid,d['teacher_name'],d['div_id'],d['div_name'],
         d.get('dept_id'),int(d['sem']),d['subject'],
         d.get('room',''),dt,dy,now_iso(),None,
         d.get('att_mode','both'),qrl))
    c.commit(); c.close()
    return jsonify({'ok':True,'id':sid})

@app.route('/api/sessions/<sid>/end',methods=['POST'])
def end_session(sid):
    c=get_db()
    sess=c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
    if not sess: c.close(); return jsonify({'ok':False,'msg':'Not found'})
    sess=dict(sess)
    all_stds=[dict(s) for s in c.execute(
        'SELECT id,name,roll FROM users WHERE role="student" AND div_id=? AND sem=?',
        (sess['div_id'],sess['sem'])).fetchall()]
    present_ids={r['student_id'] for r in c.execute(
        'SELECT student_id FROM attendance WHERE session_id=? AND status="present"',(sid,)).fetchall()}
    nm=now_iso()
    for s in all_stds:
        if s['id'] not in present_ids:
            c.execute('INSERT OR IGNORE INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (new_id(),sid,s['id'],s['name'],s['roll'],
                 sess['div_id'],sess['div_name'],sess['sem'],
                 sess['subject'],sess['date'],sess['day'],'absent','auto',None,nm,''))
    present_list=[dict(r) for r in c.execute(
        '''SELECT a.id as att_id, a.student_id, a.name, a.roll, a.conf, a.method,
           u.face_img FROM attendance a
           LEFT JOIN users u ON u.id=a.student_id
           WHERE a.session_id=? AND a.status="present" ORDER BY a.name''',(sid,)).fetchall()]
    absent_list=[dict(r) for r in c.execute(
        'SELECT student_id,name,roll FROM attendance WHERE session_id=? AND status="absent" ORDER BY name',(sid,)).fetchall()]
    c.execute('UPDATE sessions SET active=0,qr_open=0,ended_at=? WHERE id=?',(nm,sid))
    c.commit(); c.close()
    return jsonify({'ok':True,'summary':{
        'session':sess,'total':len(all_stds),
        'present':len(present_list),'absent':len(absent_list),
        'present_list':present_list,'absent_list':absent_list}})

@app.route('/api/sessions/<sid>/open-qr',methods=['POST'])
def open_qr(sid):
    c=get_db(); c.execute('UPDATE sessions SET qr_open=1 WHERE id=? AND active=1',(sid,)); c.commit(); c.close()
    return jsonify({'ok':True})

@app.route('/api/sessions/<sid>/close-qr',methods=['POST'])
def close_qr(sid):
    c=get_db(); c.execute('UPDATE sessions SET qr_open=0 WHERE id=?',(sid,)); c.commit(); c.close()
    return jsonify({'ok':True})

@app.route('/api/sessions/<sid>/qr-token',methods=['POST'])
def gen_qr(sid):
    c=get_db()
    sess=c.execute('SELECT * FROM sessions WHERE id=? AND active=1 AND qr_open=1',(sid,)).fetchone()
    cfg_r={x['k']:x['v'] for x in c.execute('SELECT * FROM cfg').fetchall()}
    if not sess: c.close(); return jsonify({'ok':False,'msg':'Session not active'})
    sess=dict(sess)
    # Session-specific lifetime overrides global cfg
    lifetime=int(sess.get('qr_lifetime') or cfg_r.get('qr_lifetime',30))
    # Clean old tokens
    c.execute('DELETE FROM qr_tokens WHERE issued_at<?',(time.time()-600,))
    # Simple uppercase alphanumeric token (easy to type, easy to scan)
    import random,string
    token=''.join(random.choices(string.ascii_uppercase+string.digits,k=10))
    c.execute('INSERT OR REPLACE INTO qr_tokens VALUES(?,?,?)',(token,sid,time.time()))
    c.commit(); c.close()
    return jsonify({'ok':True,'token':token,'lifetime':lifetime})

@app.route('/api/sessions/my-active')
def my_active():
    tid=request.args.get('teacher_id')
    if not tid: return jsonify({'session':None})
    c=get_db()
    s=c.execute('SELECT * FROM sessions WHERE teacher_id=? AND active=1 ORDER BY rowid DESC LIMIT 1',(tid,)).fetchone()
    c.close(); return jsonify({'session':dict(s) if s else None})

@app.route('/api/sessions/for-student')
def sess_for_student():
    """Return ALL active sessions for a student's division+sem"""
    div_id=request.args.get('div_id'); sem=request.args.get('sem')
    c=get_db()
    if div_id and sem:
        rows=[dict(r) for r in c.execute(
            'SELECT * FROM sessions WHERE div_id=? AND sem=? AND active=1 ORDER BY started_at DESC',
            (div_id,int(sem))).fetchall()]
    else: rows=[]
    c.close(); return jsonify({'sessions':rows})

@app.route('/api/sessions/<sid>/summary')
def session_summary(sid):
    c=get_db()
    sess=c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
    if not sess: c.close(); return jsonify({'ok':False})
    sess=dict(sess)
    total=c.execute('SELECT COUNT(*) FROM users WHERE role="student" AND div_id=? AND sem=?',
        (sess['div_id'],sess['sem'])).fetchone()[0]
    present_list=[dict(r) for r in c.execute(
        '''SELECT a.id as att_id, a.student_id, a.name, a.roll, a.conf, a.method,
           u.face_img FROM attendance a
           LEFT JOIN users u ON u.id=a.student_id
           WHERE a.session_id=? AND a.status="present" ORDER BY a.name''',(sid,)).fetchall()]
    absent_list=[dict(r) for r in c.execute(
        'SELECT student_id,name,roll FROM attendance WHERE session_id=? AND status="absent" ORDER BY name',(sid,)).fetchall()]
    cancelled_list=[dict(r) for r in c.execute(
        '''SELECT a.id as att_id, a.student_id, a.name, a.roll, a.method,
           a.cancel_reason, u.face_img FROM attendance a
           LEFT JOIN users u ON u.id=a.student_id
           WHERE a.session_id=? AND a.status="cancelled" ORDER BY a.name''',(sid,)).fetchall()]
    c.close()
    return jsonify({'ok':True,'summary':{
        'session':sess,'total':total,
        'present':len(present_list),'absent':len(absent_list),'cancelled':len(cancelled_list),
        'present_list':present_list,'absent_list':absent_list,'cancelled_list':cancelled_list}})

# MARK — Step 1: Verify QR token
@app.route('/api/verify-qr',methods=['POST'])
def verify_qr():
    """
    Student sends token. If valid → returns session info + 'verified' flag.
    Does NOT mark attendance yet — face step comes next.
    For QR-only mode → marks immediately.
    """
    d=request.json
    raw_token=str(d.get('token',''))
    # Strip whitespace, extract if URL contains ?token=XXX
    import urllib.parse
    if '?' in raw_token or 'token=' in raw_token:
        try:
            parsed=urllib.parse.urlparse(raw_token)
            qs=urllib.parse.parse_qs(parsed.query)
            raw_token=qs.get('token',[''])[0] or raw_token
        except: pass
    token=re.sub(r'\s+','',raw_token).upper()
    student_id=d.get('student_id')

    c=get_db()
    cfg_r={x['k']:x['v'] for x in c.execute('SELECT * FROM cfg').fetchall()}
    lifetime=int(cfg_r.get('qr_lifetime',45))

    rec=c.execute('SELECT * FROM qr_tokens WHERE token=?',(token,)).fetchone()
    if not rec:
        c.close()
        return jsonify({'ok':False,'msg':'Invalid token — scan latest QR code','reason':'bad_token'})
    rec=dict(rec)

    if time.time()-rec['issued_at']>lifetime:
        c.execute('DELETE FROM qr_tokens WHERE token=?',(token,)); c.commit(); c.close()
        return jsonify({'ok':False,'msg':'QR expired — please scan new code','reason':'expired'})

    sid=rec['session_id']
    sess=c.execute('SELECT * FROM sessions WHERE id=? AND active=1 AND qr_open=1',(sid,)).fetchone()
    if not sess: c.close(); return jsonify({'ok':False,'msg':'Session ended or QR closed','reason':'no_session'})
    sess=dict(sess)

    student=c.execute('SELECT * FROM users WHERE id=? AND role="student"',(student_id,)).fetchone()
    if not student: c.close(); return jsonify({'ok':False,'msg':'Student not found','reason':'no_student'})
    student=dict(student)

    if student['div_id']!=sess['div_id'] or student['sem']!=sess['sem']:
        c.close(); return jsonify({'ok':False,'msg':f'Session is for {sess["div_name"]} Sem {sess["sem"]} only','reason':'wrong_div'})

    # Already marked?
    if c.execute('SELECT 1 FROM attendance WHERE session_id=? AND student_id=? AND status="present"',(sid,student_id)).fetchone():
        c.close()
        return jsonify({'ok':True,'already':True,'session':sess,'student':student,
                        'msg':f'Already marked present for {sess["subject"]}'})

    mode=sess.get('att_mode','both')
    if mode=='qr':
        # Mark immediately for QR-only sessions
        c.execute('INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (new_id(),sid,student_id,student['name'],student['roll'],
             sess['div_id'],sess['div_name'],sess['sem'],
             sess['subject'],sess['date'],sess.get('day',''),'present','qr',None,now_iso(),''))
        c.commit(); c.close()
        return jsonify({'ok':True,'marked':True,'mode':'qr',
                        'session':sess,'student':student,
                        'msg':f'Present marked — {sess["subject"]}'})

    # For both/face modes → token verified, need face next
    c.close()
    return jsonify({'ok':True,'verified':True,'mode':mode,
                    'session':sess,'student':student,
                    'msg':f'Token verified! Now scan your face for {sess["subject"]}'})

# MARK — Step 2: Face verification (after QR token verified)
@app.route('/api/mark-face',methods=['POST'])
def mark_face():
    d=request.json
    face=d.get('face',[])
    student_id=d.get('student_id')
    session_id=d.get('session_id')  # specific session from QR step

    c=get_db()
    cfg_r={x['k']:x['v'] for x in c.execute('SELECT * FROM cfg').fetchall()}
    thresh=float(cfg_r.get('face_conf',55))/100

    student=c.execute('SELECT * FROM users WHERE id=?',(student_id,)).fetchone()
    if not student: c.close(); return jsonify({'ok':False,'msg':'Student not found'})
    student=dict(student)

    if not student['face']:
        c.close(); return jsonify({'ok':False,'msg':'Face not enrolled. Please enroll first.','reason':'no_face'})

    # Match against enrolled face
    enrolled=json.loads(student['face'])
    sim=cosine(face,enrolled)
    score=round(sim*100)
    req=round(thresh*100)

    if sim<thresh:
        hint=('Hold phone steady, improve lighting.' if score>40 else 'Try re-enrolling your face in better lighting.')
        c.close()
        return jsonify({'ok':False,'reason':'low_match',
                        'msg':f'Face not matched ({score}% match, need {req}%)',
                        'score':score,'required':req,'hint':hint,
                        'img':student.get('face_img')})

    # Check already marked
    if c.execute('SELECT 1 FROM attendance WHERE session_id=? AND student_id=? AND status="present"',
                 (session_id,student_id)).fetchone():
        c.close(); return jsonify({'ok':True,'already':True,'name':student['name'],
                                   'msg':f'Already marked present','img':student.get('face_img')})

    sess=c.execute('SELECT * FROM sessions WHERE id=?',(session_id,)).fetchone()
    if not sess: c.close(); return jsonify({'ok':False,'msg':'Session not found'})
    sess=dict(sess)

    c.execute('INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (new_id(),session_id,student_id,student['name'],student['roll'],
         sess['div_id'],sess['div_name'],sess['sem'],
         sess['subject'],sess['date'],sess.get('day',''),'present','face+qr',score,now_iso(),''))
    c.commit(); c.close()
    return jsonify({'ok':True,'marked':True,'name':student['name'],
                    'conf':score,'img':student.get('face_img'),
                    'msg':f'Present marked — {sess["subject"]} ({score}% face match)'})

# ATTENDANCE RECORDS
@app.route('/api/attendance')
def get_attendance():
    c=get_db()
    q='SELECT * FROM attendance WHERE 1=1'; p=[]
    for k in ['student_id','session_id','div_id','sem','date','status','day']:
        if request.args.get(k): q+=f' AND {k}=?'; p.append(request.args[k])
    if request.args.get('from'): q+=' AND date>=?'; p.append(request.args['from'])
    if request.args.get('to'):   q+=' AND date<=?'; p.append(request.args['to'])
    q+=' ORDER BY date DESC, rowid DESC LIMIT '+str(int(request.args.get('limit',500)))
    r=[dict(x) for x in c.execute(q,p).fetchall()]; c.close(); return jsonify(r)

# STATS
@app.route('/api/stats')
def get_stats():
    c=get_db()
    td=today()
    r={
        'students':       c.execute('SELECT COUNT(*) FROM users WHERE role="student"').fetchone()[0],
        'teachers':       c.execute('SELECT COUNT(*) FROM users WHERE role="teacher"').fetchone()[0],
        'sessions_today': c.execute('SELECT COUNT(*) FROM sessions WHERE date=?',(td,)).fetchone()[0],
        'active_sessions':c.execute('SELECT COUNT(*) FROM sessions WHERE active=1').fetchone()[0],
        'total_present':  c.execute('SELECT COUNT(*) FROM attendance WHERE status="present"').fetchone()[0],
    }
    c.close(); return jsonify(r)

# ── TIMETABLE ─────────────────────────────────────────────────
@app.route('/api/timetable')
def get_timetable():
    div_id=request.args.get('div_id'); sem=request.args.get('sem')
    c=get_db()
    if div_id and sem:
        rows=[dict(r) for r in c.execute(
            'SELECT * FROM timetable WHERE div_id=? AND sem=? ORDER BY day,period',
            (div_id,int(sem))).fetchall()]
    elif div_id:
        rows=[dict(r) for r in c.execute(
            'SELECT * FROM timetable WHERE div_id=? ORDER BY sem,day,period',(div_id,)).fetchall()]
    else: rows=[]
    c.close(); return jsonify(rows)

@app.route('/api/timetable',methods=['POST'])
def save_timetable():
    """Accepts array of period objects — upserts them all"""
    rows=request.json  # list of {div_id,sem,dept_id,day,period,start_time,end_time,subject,teacher_id,teacher_name,room}
    c=get_db()
    for row in rows:
        # Check if exists
        ex=c.execute('SELECT id FROM timetable WHERE div_id=? AND sem=? AND day=? AND period=?',
            (row['div_id'],int(row['sem']),row['day'],int(row['period']))).fetchone()
        if ex:
            c.execute('''UPDATE timetable SET start_time=?,end_time=?,subject=?,
                teacher_id=?,teacher_name=?,room=?,dept_id=? WHERE id=?''',
                (row.get('start_time',''),row.get('end_time',''),row.get('subject',''),
                 row.get('teacher_id',''),row.get('teacher_name',''),row.get('room',''),
                 row.get('dept_id',''),ex['id']))
        else:
            c.execute('INSERT INTO timetable VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
                new_id(),row['div_id'],int(row['sem']),row.get('dept_id',''),
                row['day'],int(row['period']),
                row.get('start_time',''),row.get('end_time',''),
                row.get('subject',''),row.get('teacher_id',''),
                row.get('teacher_name',''),row.get('room','')))
    c.commit(); c.close(); return jsonify({'ok':True})

@app.route('/api/timetable/<tid>',methods=['DELETE'])
def del_timetable(tid):
    c=get_db(); c.execute('DELETE FROM timetable WHERE id=?',(tid,)); c.commit(); c.close()
    return jsonify({'ok':True})

@app.route('/api/timetable-with-att')
def timetable_with_att():
    """Timetable for a student with today's attendance status"""
    student_id=request.args.get('student_id')
    div_id=request.args.get('div_id'); sem=request.args.get('sem')
    day=request.args.get('day',today_day())
    date=request.args.get('date',today())
    c=get_db()
    tt=[dict(r) for r in c.execute(
        'SELECT * FROM timetable WHERE div_id=? AND sem=? AND day=? ORDER BY period',
        (div_id,int(sem),day)).fetchall()]
    # Get today's attendance for this student
    att={r['subject']:dict(r) for r in c.execute(
        'SELECT * FROM attendance WHERE student_id=? AND date=? AND div_id=? AND sem=?',
        (student_id,date,div_id,int(sem))).fetchall()}
    # Get active sessions
    active={r['subject']:dict(r) for r in c.execute(
        'SELECT * FROM sessions WHERE div_id=? AND sem=? AND active=1',
        (div_id,int(sem))).fetchall()}
    c.close()
    for row in tt:
        subj=row['subject']
        row['att_status']=att[subj]['status'] if subj in att else None
        row['att_method']=att[subj]['method'] if subj in att else None
        row['session_active']=subj in active
        row['session_id']=active[subj]['id'] if subj in active else None
        row['qr_open']=active[subj]['qr_open'] if subj in active else False
    return jsonify(tt)

@app.route('/api/timetable-with-att-teacher')
def tt_with_att_teacher():
    """Timetable for teacher + session status"""
    teacher_id=request.args.get('teacher_id')
    day=request.args.get('day',today_day())
    c=get_db()
    tt=[dict(r) for r in c.execute(
        'SELECT * FROM timetable WHERE teacher_id=? AND day=? ORDER BY period',(teacher_id,day)).fetchall()]
    active_by_div={}
    for r in c.execute('SELECT * FROM sessions WHERE teacher_id=? AND active=1',(teacher_id,)).fetchall():
        key=(r['div_id'],r['sem'],r['subject'])
        active_by_div[key]=dict(r)
    for row in tt:
        key=(row['div_id'],row['sem'],row['subject'])
        if key in active_by_div:
            row['session_active']=True
            row['session_id']=active_by_div[key]['id']
            row['session']=active_by_div[key]
        else:
            row['session_active']=False; row['session_id']=None; row['session']=None
    c.close(); return jsonify(tt)

# CANCEL single attendance record — mark as 'cancelled' (keeps record visible)
@app.route('/api/attendance/<att_id>', methods=['DELETE'])
def cancel_attendance(att_id):
    c = get_db()
    reason = request.args.get('reason','')
    # Mark as 'cancelled' instead of deleting — student can see it in their history
    c.execute("UPDATE attendance SET status='cancelled', cancel_reason=? WHERE id=?", (reason, att_id))
    c.commit(); c.close()
    return jsonify({'ok': True})

# ADMIN UTILS
@app.route('/api/reset-attendance',methods=['POST'])
def reset_att():
    if request.json.get('confirm')!='RESET_CONFIRM': return jsonify({'ok':False,'msg':'Invalid key'})
    c=get_db(); c.executescript('DELETE FROM attendance; DELETE FROM sessions;'); c.commit(); c.close()
    return jsonify({'ok':True})

@app.route('/api/reset-timetable',methods=['POST'])
def reset_tt():
    div_id=request.json.get('div_id'); sem=request.json.get('sem')
    c=get_db()
    if div_id and sem: c.execute('DELETE FROM timetable WHERE div_id=? AND sem=?',(div_id,int(sem)))
    elif div_id: c.execute('DELETE FROM timetable WHERE div_id=?',(div_id,))
    c.commit(); c.close(); return jsonify({'ok':True})

init_db()

if __name__=='__main__':
    import socket
    try: ip=socket.gethostbyname(socket.gethostname())
    except: ip='localhost'
    port=int(os.environ.get('PORT',5000))
    print(f'\n{"="*54}\n  AttendX Pro — Unlimited Sessions + Timetable\n{"="*54}')
    print(f'  Local:   http://localhost:{port}')
    print(f'  Network: http://{ip}:{port}  ← Share this!\n{"="*54}\n')
    app.run(debug=False,host='0.0.0.0',port=port)
