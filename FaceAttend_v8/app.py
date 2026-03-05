from flask import Flask, request, jsonify, render_template
import sqlite3, os, json, hashlib, time, secrets
from datetime import datetime

app = Flask(__name__)

# On Render/cloud: use /tmp for writable storage, locally use database/ folder
if os.environ.get('RENDER') or os.environ.get('DATABASE_PATH'):
    DB = os.environ.get('DATABASE_PATH', '/tmp/fa.db')
else:
    DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'fa.db')

# In-memory store for QR tokens: {token_id: {'session_id':..,'issued_at':..,'used':False}}
QR_TOKENS = {}
QR_LIFETIME = 45  # seconds a token is valid (give student enough time to scan + face)

def make_qr_token(session_id):
    """Generate a unique token id and store metadata server-side."""
    token_id = secrets.token_urlsafe(24)   # random, unguessable, no colons
    ts = int(time.time())
    QR_TOKENS[token_id] = {'session_id': session_id, 'issued_at': ts, 'used': False}
    print(f'[QR] Token created: {token_id[:8]}... for session {session_id[:12]}')
    # Purge tokens older than 10 minutes
    expired = [t for t, v in list(QR_TOKENS.items()) if time.time() - v['issued_at'] > 600]
    for t in expired:
        QR_TOKENS.pop(t, None)
    return token_id

def verify_qr_token(token):
    """Returns (ok, session_id, error_msg)"""
    # Strip ALL whitespace — Google Lens and clipboard often add newlines/spaces
    import re as _re
    token = _re.sub(r'\s+', '', str(token or ''))
    if not token or token not in QR_TOKENS:
        print(f'[QR] Token not found: {token[:12] if token else "empty"}... Available: {len(QR_TOKENS)}')
        return False, None, 'QR code not recognised — scan the latest QR on the projector'
    rec = QR_TOKENS[token]
    if rec['used']:
        return False, None, 'This QR token was already used'
    age = time.time() - rec['issued_at']
    if age > QR_LIFETIME:
        # Remove expired token
        QR_TOKENS.pop(token, None)
        return False, None, f'QR expired ({int(age)}s old, limit {QR_LIFETIME}s) — scan the new QR'
    return True, rec['session_id'], None

def db():
    db_dir = os.path.dirname(DB)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def hp(p):
    h = 5381
    for ch in p: h = ((h<<5)+h)+ord(ch)
    return hex(h & 0xFFFFFFFF)[2:]

def ts():
    return 'x' + datetime.now().strftime('%Y%m%d%H%M%S%f')

def init():
    c = db()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS branches(id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE IF NOT EXISTS divisions(id TEXT PRIMARY KEY, name TEXT, sems INT, branch TEXT);
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, roll TEXT,
            email TEXT UNIQUE, ph TEXT, role TEXT, div TEXT, sem INT, face TEXT, img TEXT, branch TEXT);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, did TEXT, dname TEXT,
            sem INT, subj TEXT, date TEXT, tid TEXT, tname TEXT, active INT DEFAULT 0, branch TEXT,
            qr_open INT DEFAULT 0);
        CREATE TABLE IF NOT EXISTS attendance(id TEXT PRIMARY KEY, sid TEXT, uid TEXT,
            name TEXT, roll TEXT, did TEXT, dname TEXT, sem INT, subj TEXT, date TEXT,
            status TEXT, conf INT, method TEXT, at TEXT, branch TEXT);
        CREATE TABLE IF NOT EXISTS cfg(k TEXT PRIMARY KEY, v TEXT);
    ''')
    for tbl, col in [('divisions','branch'),('users','branch'),('sessions','branch'),('attendance','branch'),('sessions','qr_open')]:
        try: c.execute(f'ALTER TABLE {tbl} ADD COLUMN {col} {"INT DEFAULT 0" if col in ("qr_open",) else "TEXT"}')
        except: pass
    c.commit()

    if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
        c.executemany('INSERT OR IGNORE INTO branches VALUES(?,?)', [
            ('b1','BCA'), ('b2','MCA'), ('b3','BSc CS'), ('b4','BTech CS'), ('b5','BTech IT')
        ])
        c.executemany('INSERT OR IGNORE INTO divisions VALUES(?,?,?,?)', [
            ('d1','Division A',8,'b1'), ('d2','Division B',6,'b2')
        ])
        c.executemany('INSERT OR IGNORE INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?)', [
            ('u1','Admin HOD','ADM001','admin@college.edu',hp('admin123'),'admin',None,None,None,None,None),
            ('u2','Prof. Sharma','TCH001','teacher@college.edu',hp('teach123'),'teacher','d1',3,None,None,'b1'),
            ('u3','Rahul Verma','CS21001','student@college.edu',hp('stud123'),'student','d1',3,None,None,'b1'),
            ('u4','Priya Singh','CS21002','priya@college.edu',hp('stud123'),'student','d1',3,None,None,'b1'),
        ])
        c.executemany('INSERT OR IGNORE INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            ('a1','s0','u3','Rahul Verma','CS21001','d1','Division A',3,'Data Structures','2026-02-24','present',88,'face','','b1'),
            ('a2','s0','u3','Rahul Verma','CS21001','d1','Division A',3,'Algorithms','2026-02-25','present',91,'face','','b1'),
            ('a3','s0','u3','Rahul Verma','CS21001','d1','Division A',3,'Data Structures','2026-02-26','absent',None,'auto','','b1'),
            ('a4','s0','u4','Priya Singh','CS21002','d1','Division A',3,'Data Structures','2026-02-24','present',93,'face','','b1'),
        ])
        c.executemany('INSERT OR IGNORE INTO cfg VALUES(?,?)', [
            ('college','My Engineering College'),('dept','Computer Science'),('minAtt','75'),('faceConf','60')
        ])
    c.commit(); c.close()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/favicon.ico')
def favicon(): return '', 204

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json; c = db()
    u = c.execute('SELECT * FROM users WHERE email=? AND ph=? AND role=?',
        (d['email'].lower(), hp(d['password']), d['role'])).fetchone()
    c.close()
    if not u: return jsonify({'ok': False, 'msg': 'Wrong email, password or role'})
    return jsonify({'ok': True, 'user': dict(u)})

@app.route('/api/register', methods=['POST'])
def register():
    d = request.json
    name=d.get('name','').strip(); roll=d.get('roll','').strip().upper()
    email=d.get('email','').strip().lower(); pw=d.get('password','')
    role=d.get('role','student'); div=d.get('div') or None
    sem=d.get('sem') or None; branch=d.get('branch') or None
    if not name or not roll or not email or not pw:
        return jsonify({'ok': False, 'msg': 'All fields required'})
    if len(pw) < 6: return jsonify({'ok': False, 'msg': 'Password min 6 characters'})
    c = db()
    ex = c.execute('SELECT id FROM users WHERE roll=? AND role=?',(roll,role)).fetchone()
    if ex:
        c.execute('DELETE FROM attendance WHERE uid=?',(ex['id'],))
        c.execute('DELETE FROM users WHERE id=?',(ex['id'],))
    if c.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():
        c.close(); return jsonify({'ok': False, 'msg': 'Email already registered'})
    uid = ts()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (uid,name,roll,email,hp(pw),role,div,int(sem) if sem else None,None,None,branch))
    c.commit()
    u = dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    c.close()
    return jsonify({'ok': True, 'user': u})

@app.route('/api/branches')
def get_branches():
    c = db(); r = [dict(x) for x in c.execute('SELECT * FROM branches').fetchall()]
    c.close(); return jsonify(r)

@app.route('/api/branches', methods=['POST'])
def add_branch():
    d = request.json; c = db()
    if not d.get('name'): return jsonify({'ok': False, 'msg': 'Name required'})
    c.execute('INSERT INTO branches VALUES(?,?)',(ts(),d['name']))
    c.commit(); c.close(); return jsonify({'ok': True})

@app.route('/api/branches/<bid>', methods=['DELETE'])
def del_branch(bid):
    c = db(); c.execute('DELETE FROM branches WHERE id=?',(bid,)); c.commit(); c.close()
    return jsonify({'ok': True})

@app.route('/api/divisions')
def get_divs():
    c = db(); r = [dict(x) for x in c.execute('SELECT * FROM divisions').fetchall()]
    c.close(); return jsonify(r)

@app.route('/api/divisions', methods=['POST'])
def add_div():
    d = request.json; c = db()
    c.execute('INSERT INTO divisions VALUES(?,?,?,?)',
        (ts(),d['name'],int(d.get('sems',8)),d.get('branch') or None))
    c.commit(); c.close(); return jsonify({'ok': True})

@app.route('/api/divisions/<did>', methods=['DELETE'])
def del_div(did):
    c = db(); c.execute('DELETE FROM divisions WHERE id=?',(did,)); c.commit(); c.close()
    return jsonify({'ok': True})

@app.route('/api/users')
def get_users():
    c = db()
    r = [dict(x) for x in c.execute(
        'SELECT id,name,roll,email,role,div,sem,img,branch, CASE WHEN face IS NOT NULL THEN 1 ELSE 0 END as has_face FROM users'
    ).fetchall()]
    c.close(); return jsonify(r)

@app.route('/api/users', methods=['POST'])
def add_user():
    d = request.json; c = db()
    roll = d['roll'].strip().upper(); role = d.get('role','student')
    ex = c.execute('SELECT id FROM users WHERE roll=? AND role=?',(roll,role)).fetchone()
    if ex:
        c.execute('DELETE FROM attendance WHERE uid=?',(ex['id'],))
        c.execute('DELETE FROM users WHERE id=?',(ex['id'],))
    if c.execute('SELECT 1 FROM users WHERE email=?',(d['email'].lower(),)).fetchone():
        c.close(); return jsonify({'ok': False, 'msg': 'Email already exists'})
    uid = ts()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (uid,d['name'].strip(),roll,d['email'].lower(),hp(d.get('password','student@123')),
         role,d.get('div'),int(d['sem']) if d.get('sem') else None,None,None,d.get('branch') or None))
    c.commit(); c.close(); return jsonify({'ok': True})

@app.route('/api/users/<uid>')
def get_user(uid):
    c = db(); u = c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close()
    return jsonify({'ok': bool(u), 'user': dict(u) if u else None})

@app.route('/api/users/<uid>', methods=['DELETE'])
def del_user(uid):
    c = db()
    c.execute('DELETE FROM users WHERE id=?',(uid,))
    c.execute('DELETE FROM attendance WHERE uid=?',(uid,))
    c.commit(); c.close(); return jsonify({'ok': True})

@app.route('/api/users/<uid>/face', methods=['POST'])
def enroll_face(uid):
    d = request.json; c = db()
    c.execute('UPDATE users SET face=?,img=? WHERE id=?',(json.dumps(d['face']),d.get('img'),uid))
    c.commit()
    u = dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    c.close(); return jsonify({'ok': True, 'user': u})

@app.route('/api/sessions')
def get_sessions():
    c = db()
    r = [dict(x) for x in c.execute('SELECT * FROM sessions ORDER BY rowid DESC LIMIT 30').fetchall()]
    c.close(); return jsonify(r)

@app.route('/api/sessions', methods=['POST'])
def start_session():
    d = request.json; c = db()
    c.execute('UPDATE sessions SET active=0, qr_open=0')
    sid = ts()
    c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,1,?,1)',
        (sid,d['did'],d['dname'],int(d['sem']),d['subj'],d['date'],
         d['tid'],d['tname'],d.get('branch') or None))
    c.commit(); c.close(); return jsonify({'ok': True, 'id': sid})

@app.route('/api/sessions/<sid>/stop', methods=['POST'])
def stop_session(sid):
    c = db()
    sess = c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
    if not sess: c.close(); return jsonify({'ok': False, 'msg': 'Session not found'})
    sess = dict(sess)

    # Get all students in this division+sem
    all_stds = [dict(s) for s in c.execute(
        'SELECT id,name,roll,img FROM users WHERE role="student" AND div=? AND sem=?',
        (sess['did'], sess['sem'])).fetchall()]

    # Who already present
    present_ids = set(r['uid'] for r in
        c.execute('SELECT uid FROM attendance WHERE sid=? AND status="present"',(sid,)).fetchall())

    # Auto-mark absent
    now = datetime.now().isoformat()
    for s in all_stds:
        if s['id'] not in present_ids:
            c.execute('INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (ts(), sid, s['id'], s['name'], s['roll'],
                 sess['did'], sess['dname'], sess['sem'],
                 sess['subj'], sess['date'], 'absent', None, 'auto', now, sess.get('branch')))

    # Final lists for summary
    present_list = [dict(r) for r in c.execute(
        'SELECT a.uid,a.name,a.roll,a.conf,u.img FROM attendance a LEFT JOIN users u ON a.uid=u.id WHERE a.sid=? AND a.status="present" ORDER BY a.name',
        (sid,)).fetchall()]
    absent_list = [dict(r) for r in c.execute(
        'SELECT a.uid,a.name,a.roll,u.img FROM attendance a LEFT JOIN users u ON a.uid=u.id WHERE a.sid=? AND a.status="absent" ORDER BY a.name',
        (sid,)).fetchall()]

    c.execute('UPDATE sessions SET active=0 WHERE id=?',(sid,))
    c.commit(); c.close()

    return jsonify({'ok': True, 'summary': {
        'session': sess,
        'total': len(all_stds),
        'present': len(present_list),
        'absent': len(absent_list),
        'present_list': present_list,
        'absent_list': absent_list,
    }})

@app.route('/api/sessions/active')
def active_session():
    c = db(); s = c.execute('SELECT * FROM sessions WHERE active=1').fetchone(); c.close()
    return jsonify({'session': dict(s) if s else None})

@app.route('/api/sessions/<sid>/summary')
def session_summary(sid):
    c = db()
    sess = c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
    if not sess: c.close(); return jsonify({'ok': False})
    sess = dict(sess)
    total = c.execute('SELECT COUNT(*) FROM users WHERE role="student" AND div=? AND sem=?',
        (sess['did'],sess['sem'])).fetchone()[0]
    present_list = [dict(r) for r in c.execute(
        'SELECT a.uid,a.name,a.roll,a.conf,u.img FROM attendance a LEFT JOIN users u ON a.uid=u.id WHERE a.sid=? AND a.status="present" ORDER BY a.name',(sid,)).fetchall()]
    absent_list = [dict(r) for r in c.execute(
        'SELECT a.uid,a.name,a.roll,u.img FROM attendance a LEFT JOIN users u ON a.uid=u.id WHERE a.sid=? AND a.status="absent" ORDER BY a.name',(sid,)).fetchall()]
    c.close()
    return jsonify({'ok': True, 'summary': {
        'session': sess, 'total': total,
        'present': len(present_list), 'absent': len(absent_list),
        'present_list': present_list, 'absent_list': absent_list,
    }})

@app.route('/api/attendance')
def get_att():
    c = db(); q = 'SELECT * FROM attendance WHERE 1=1'; p = []
    for k in ['uid','did','sem','date','branch','sid','status']:
        if request.args.get(k): q += f' AND {k}=?'; p.append(request.args[k])
    if request.args.get('date_from'): q += ' AND date>=?'; p.append(request.args['date_from'])
    if request.args.get('date_to'):   q += ' AND date<=?'; p.append(request.args['date_to'])
    r = [dict(x) for x in c.execute(q+' ORDER BY date DESC, rowid DESC',p).fetchall()]
    c.close(); return jsonify(r)

@app.route('/api/sessions/<sid>/qr-token', methods=['POST'])
def get_qr_token(sid):
    """Teacher requests a fresh QR token for active session."""
    c = db()
    sess = c.execute('SELECT * FROM sessions WHERE id=? AND active=1 AND qr_open=1', (sid,)).fetchone()
    c.close()
    if not sess:
        return jsonify({'ok': False, 'msg': 'Session not active or QR not open'})
    token = make_qr_token(sid)
    return jsonify({'ok': True, 'token': token, 'lifetime': QR_LIFETIME, 'issued_at': int(time.time())})

@app.route('/api/sessions/<sid>/open-qr', methods=['POST'])
def open_qr(sid):
    """Teacher opens QR window (start or reopen)."""
    c = db()
    c.execute('UPDATE sessions SET qr_open=1 WHERE id=? AND active=1', (sid,))
    c.commit(); c.close()
    return jsonify({'ok': True})

@app.route('/api/sessions/<sid>/close-qr', methods=['POST'])
def close_qr(sid):
    """Teacher closes QR window without ending session."""
    c = db()
    c.execute('UPDATE sessions SET qr_open=0 WHERE id=?', (sid,))
    c.commit(); c.close()
    return jsonify({'ok': True})

@app.route('/api/attendance/mark-qr', methods=['POST'])
def mark_att_qr():
    """Student marks attendance via QR token + face."""
    d = request.json
    token = d.get('token', '')
    face = d.get('face', [])

    ok, session_id, err = verify_qr_token(token)
    if not ok:
        return jsonify({'ok': False, 'msg': err, 'reason': 'bad_token'})

    c = db()
    sess = c.execute('SELECT * FROM sessions WHERE id=? AND active=1 AND qr_open=1', (session_id,)).fetchone()
    if not sess:
        c.close(); return jsonify({'ok': False, 'msg': 'Session is not active or QR is closed', 'reason': 'no_session'})
    sess = dict(sess)

    stds = c.execute('SELECT * FROM users WHERE role="student" AND div=? AND sem=? AND face IS NOT NULL',
        (sess['did'], sess['sem'])).fetchall()
    if not stds:
        c.close(); return jsonify({'ok': False, 'msg': 'No enrolled faces in this class', 'reason': 'no_students'})

    def cs(a, b):
        dot = na = nb = 0
        for x, y in zip(a, b): dot += x*y; na += x*x; nb += y*y
        return dot / ((na*nb)**.5 or 1)

    best = None; bsim = 0
    for s in stds:
        sim = cs(face, json.loads(s['face']))
        if sim > bsim: bsim = sim; best = dict(s)

    cfg = c.execute('SELECT v FROM cfg WHERE k="faceConf"').fetchone()
    thresh = float(cfg['v'] if cfg else 60) / 100
    bs = round(bsim * 100); req = round(thresh * 100)

    if best and bsim >= thresh:
        already = c.execute('SELECT 1 FROM attendance WHERE uid=? AND sid=?', (best['id'], sess['id'])).fetchone()
        if already:
            c.close()
            # Mark token used so it can't be replayed
            if token in QR_TOKENS: QR_TOKENS[token]['used'] = True
            return jsonify({'ok': True, 'already': True, 'name': best['name'], 'img': best.get('img'),
                'msg': f'Already marked for {sess["subj"]}'})
        # Mark token used
        if token in QR_TOKENS: QR_TOKENS[token]['used'] = True
        c.execute('INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ts(), sess['id'], best['id'], best['name'], best['roll'], best['div'], sess['dname'],
             best['sem'], sess['subj'], sess['date'], 'present', bs, 'qr+face',
             datetime.now().isoformat(), sess.get('branch')))
        c.commit(); c.close()
        return jsonify({'ok': True, 'matched': True, 'name': best['name'], 'img': best.get('img'),
            'conf': bs, 'msg': f'Marked PRESENT — {sess["subj"]} ({bs}% match)'})

    c.close()
    hint = ('Very low match — wrong person or not enrolled.' if bs < 40
            else 'Poor lighting or angle. Try again.' if bs < 55
            else 'Close! Re-enroll face or improve lighting.')
    return jsonify({'ok': False,
        'msg': f'Not recognized. Best: {best["name"] if best else "none"} at {bs}%',
        'reason': 'low_match', 'bestName': best['name'] if best else 'Unknown',
        'bestRoll': best['roll'] if best else '—', 'bestScore': bs, 'required': req, 'hint': hint,
        'bestImg': best.get('img') if best else None})


def mark_att():
    face = request.json['face']; c = db()
    sess = c.execute('SELECT * FROM sessions WHERE active=1').fetchone()
    if not sess: c.close(); return jsonify({'ok':False,'msg':'No active session','reason':'no_session'})
    sess = dict(sess)
    stds = c.execute('SELECT * FROM users WHERE role="student" AND div=? AND sem=? AND face IS NOT NULL',
        (sess['did'],sess['sem'])).fetchall()
    if not stds: c.close(); return jsonify({'ok':False,'msg':'No enrolled faces','reason':'no_students'})
    def cs(a,b):
        dot=na=nb=0
        for x,y in zip(a,b): dot+=x*y; na+=x*x; nb+=y*y
        return dot/((na*nb)**.5 or 1)
    best=None; bsim=0
    for s in stds:
        sim = cs(face, json.loads(s['face']))
        if sim > bsim: bsim=sim; best=dict(s)
    cfg = c.execute('SELECT v FROM cfg WHERE k="faceConf"').fetchone()
    thresh = float(cfg['v'] if cfg else 60)/100
    bs = round(bsim*100); req = round(thresh*100)
    if best and bsim >= thresh:
        already = c.execute('SELECT 1 FROM attendance WHERE uid=? AND sid=?',(best['id'],sess['id'])).fetchone()
        if already:
            c.close()
            return jsonify({'ok':True,'already':True,'name':best['name'],'img':best.get('img'),
                'msg':f'Already marked for {sess["subj"]}'})
        c.execute('INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ts(),sess['id'],best['id'],best['name'],best['roll'],best['div'],sess['dname'],
             best['sem'],sess['subj'],sess['date'],'present',bs,'face',
             datetime.now().isoformat(), sess.get('branch')))
        c.commit(); c.close()
        return jsonify({'ok':True,'matched':True,'name':best['name'],'img':best.get('img'),
            'conf':bs,'msg':f'Marked PRESENT — {sess["subj"]} ({bs}% match)'})
    c.close()
    hint = ('Very low match — wrong person or not enrolled.' if bs<40
            else 'Poor lighting or angle. Try again.' if bs<55
            else 'Close! Re-enroll face or improve lighting.')
    return jsonify({'ok':False,'msg':f'Not recognized. Best: {best["name"] if best else "none"} at {bs}%',
        'reason':'low_match','bestName':best['name'] if best else 'Unknown',
        'bestRoll':best['roll'] if best else '—','bestScore':bs,'required':req,'hint':hint,
        'bestImg':best.get('img') if best else None})

@app.route('/api/cfg')
def get_cfg():
    c = db(); r = {x['k']:x['v'] for x in c.execute('SELECT * FROM cfg').fetchall()}; c.close()
    return jsonify(r)

@app.route('/api/cfg', methods=['POST'])
def save_cfg():
    c = db()
    for k,v in request.json.items():
        c.execute('INSERT OR REPLACE INTO cfg VALUES(?,?)',(k,str(v)))
    c.commit(); c.close(); return jsonify({'ok': True})

# Always init DB on startup (needed for gunicorn workers too)
init()

if __name__ == '__main__':
    import socket
    try: ip = socket.gethostbyname(socket.gethostname())
    except: ip = 'localhost'
    port = int(os.environ.get('PORT', 5000))
    print(f'\n{"="*52}\n  FaceAttend Pro V8\n{"="*52}')
    print(f'  Local:   http://localhost:{port}')
    print(f'  Network: http://{ip}:{port}  <- Share!\n{"="*52}\n')
    app.run(debug=False, host='0.0.0.0', port=port)
