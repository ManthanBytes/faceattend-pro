from flask import Flask, request, jsonify, render_template
import os, json, hashlib, time, secrets, re
from datetime import datetime

app = Flask(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL','')
if DATABASE_URL:
    import psycopg2, psycopg2.extras
    from psycopg2 import pool as pgpool
    if DATABASE_URL.startswith('postgres://'): DATABASE_URL=DATABASE_URL.replace('postgres://','postgresql://',1)
    USE_PG=True
    _pg_pool=None
else:
    import sqlite3
    USE_PG=False
    DB=os.path.join(os.path.dirname(os.path.abspath(__file__)),'attendx.db')

def get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        _pg_pool=pgpool.ThreadedConnectionPool(2,20,DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor)
    return _pg_pool

DAYS=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
def new_id():    return secrets.token_hex(8)
def hp(p):       return hashlib.sha256(p.encode()).hexdigest()[:20]
def now_iso():   return datetime.now().isoformat(timespec='seconds')
def today():     return datetime.now().strftime('%Y-%m-%d')
def today_day(): return datetime.now().strftime('%A')
def cosine(a,b):
    dot=na=nb=0.0
    for x,y in zip(a,b): dot+=x*y; na+=x*x; nb+=y*y
    return dot/((na*nb)**0.5 or 1.0)

class DB_Conn:
    def __init__(self):
        if USE_PG:
            self._pool=get_pg_pool()
            self.conn=self._pool.getconn()
            self.conn.autocommit=False; self.pg=True
        else:
            self.conn=sqlite3.connect(DB,timeout=30,check_same_thread=False)
            self.conn.row_factory=sqlite3.Row
            for p in ['PRAGMA journal_mode=WAL','PRAGMA synchronous=NORMAL','PRAGMA busy_timeout=15000']:
                self.conn.execute(p)
            self.pg=False
    def _fix(self,sql,params):
        if self.pg:
            sql=sql.replace('?','%s')
            sql=re.sub(r'"(present|absent|cancelled|student|teacher|admin|both|qr|face|auto|face\+qr)"',lambda m:f"'{m.group(1)}'",sql)
        return sql,list(params)
    def execute(self,sql,params=()):
        sql,params=self._fix(sql,params); cur=self.conn.cursor(); cur.execute(sql,params); return cur
    def executemany(self,sql,data):
        sql,_=self._fix(sql,()); cur=self.conn.cursor(); cur.executemany(sql,data); return cur
    def fetchone(self,sql,params=()):
        cur=self.execute(sql,params); r=cur.fetchone(); return dict(r) if r else None
    def fetchall(self,sql,params=()):
        cur=self.execute(sql,params); return [dict(r) for r in cur.fetchall()]
    def commit(self): self.conn.commit()
    def close(self):
        if USE_PG:
            try: self.conn.commit()
            except: pass
            try: self._pool.putconn(self.conn)
            except: pass
        else: self.conn.close()
    def executescript(self,sql):
        if not self.pg: self.conn.executescript(sql)

def get_db(): return DB_Conn()

def init_db():
    c=get_db()
    if USE_PG:
        for sql in [
            "CREATE TABLE IF NOT EXISTS depts(id TEXT PRIMARY KEY,name TEXT,code TEXT)",
            "CREATE TABLE IF NOT EXISTS divisions(id TEXT PRIMARY KEY,name TEXT,dept_id TEXT,sems INTEGER DEFAULT 8)",
            "CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT,roll TEXT,email TEXT UNIQUE,pw TEXT,role TEXT DEFAULT 'student',div_id TEXT,sem INTEGER,dept_id TEXT,face TEXT,face_img TEXT,created_at TEXT,status TEXT DEFAULT 'active')",
            "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,teacher_id TEXT,teacher_name TEXT,div_id TEXT,div_name TEXT,dept_id TEXT,sem INTEGER,subject TEXT,room TEXT,date TEXT,day TEXT,started_at TEXT,ended_at TEXT,active INTEGER DEFAULT 1,qr_open INTEGER DEFAULT 0,att_mode TEXT DEFAULT 'both',qr_lifetime INTEGER DEFAULT 30)",
            "CREATE TABLE IF NOT EXISTS attendance(id TEXT PRIMARY KEY,session_id TEXT,student_id TEXT,name TEXT,roll TEXT,div_id TEXT,div_name TEXT,sem INTEGER,subject TEXT,date TEXT,day TEXT,status TEXT,method TEXT,conf INTEGER,marked_at TEXT,cancel_reason TEXT DEFAULT '',att_img TEXT DEFAULT NULL)",
            "CREATE TABLE IF NOT EXISTS timetable(id TEXT PRIMARY KEY,div_id TEXT,sem INTEGER,dept_id TEXT,day TEXT,period INTEGER,start_time TEXT,end_time TEXT,subject TEXT,teacher_id TEXT,teacher_name TEXT,room TEXT)",
            "CREATE TABLE IF NOT EXISTS cfg(k TEXT PRIMARY KEY,v TEXT)",
            "CREATE TABLE IF NOT EXISTS qr_tokens(token TEXT PRIMARY KEY,session_id TEXT,issued_at DOUBLE PRECISION)",
            "CREATE INDEX IF NOT EXISTS idx_att_stu  ON attendance(student_id)",
            "CREATE INDEX IF NOT EXISTS idx_att_sess ON attendance(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(date)",
            "CREATE INDEX IF NOT EXISTS idx_att_div  ON attendance(div_id,sem,date)",
            "CREATE INDEX IF NOT EXISTS idx_usr_div  ON users(div_id,sem,role)",
            "CREATE INDEX IF NOT EXISTS idx_sess_tea ON sessions(teacher_id,active)",
            "CREATE INDEX IF NOT EXISTS idx_sess_div ON sessions(div_id,sem,active)",
            "CREATE INDEX IF NOT EXISTS idx_tt_div   ON timetable(div_id,sem,day)",
            "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS cancel_reason TEXT DEFAULT ''",
            "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS att_img TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'",
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS qr_lifetime INTEGER DEFAULT 30",
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS att_mode TEXT DEFAULT 'both'",
        ]:
            try: c.execute(sql)
            except Exception as e:
                if 'already exists' not in str(e).lower(): print('init:',e)
        c.commit()
    else:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS depts(id TEXT PRIMARY KEY,name TEXT,code TEXT);
            CREATE TABLE IF NOT EXISTS divisions(id TEXT PRIMARY KEY,name TEXT,dept_id TEXT,sems INTEGER DEFAULT 8);
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT,roll TEXT,email TEXT UNIQUE,pw TEXT,role TEXT DEFAULT "student",div_id TEXT,sem INTEGER,dept_id TEXT,face TEXT,face_img TEXT,created_at TEXT,status TEXT DEFAULT "active");
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,teacher_id TEXT,teacher_name TEXT,div_id TEXT,div_name TEXT,dept_id TEXT,sem INTEGER,subject TEXT,room TEXT,date TEXT,day TEXT,started_at TEXT,ended_at TEXT,active INTEGER DEFAULT 1,qr_open INTEGER DEFAULT 0,att_mode TEXT DEFAULT "both",qr_lifetime INTEGER DEFAULT 30);
            CREATE TABLE IF NOT EXISTS attendance(id TEXT PRIMARY KEY,session_id TEXT,student_id TEXT,name TEXT,roll TEXT,div_id TEXT,div_name TEXT,sem INTEGER,subject TEXT,date TEXT,day TEXT,status TEXT,method TEXT,conf INTEGER,marked_at TEXT,cancel_reason TEXT DEFAULT "",att_img TEXT DEFAULT NULL);
            CREATE TABLE IF NOT EXISTS timetable(id TEXT PRIMARY KEY,div_id TEXT,sem INTEGER,dept_id TEXT,day TEXT,period INTEGER,start_time TEXT,end_time TEXT,subject TEXT,teacher_id TEXT,teacher_name TEXT,room TEXT);
            CREATE TABLE IF NOT EXISTS cfg(k TEXT PRIMARY KEY,v TEXT);
            CREATE TABLE IF NOT EXISTS qr_tokens(token TEXT PRIMARY KEY,session_id TEXT,issued_at REAL);
            CREATE INDEX IF NOT EXISTS idx_att_stu  ON attendance(student_id);
            CREATE INDEX IF NOT EXISTS idx_att_sess ON attendance(session_id);
            CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(date);
            CREATE INDEX IF NOT EXISTS idx_att_div  ON attendance(div_id,sem,date);
            CREATE INDEX IF NOT EXISTS idx_usr_div  ON users(div_id,sem,role);
            CREATE INDEX IF NOT EXISTS idx_sess_tea ON sessions(teacher_id,active);
            CREATE INDEX IF NOT EXISTS idx_sess_div ON sessions(div_id,sem,active);
            CREATE INDEX IF NOT EXISTS idx_tt_div   ON timetable(div_id,sem,day);
        ''')
        def _col_exists(tbl,col):
            return any(r['name']==col for r in c.fetchall(f'PRAGMA table_info("{tbl}")'))
        for tbl,col,sql in [
            ('attendance','cancel_reason','ALTER TABLE attendance ADD COLUMN cancel_reason TEXT DEFAULT ""'),
            ('attendance','att_img',      'ALTER TABLE attendance ADD COLUMN att_img TEXT DEFAULT NULL'),
            ('sessions',  'qr_lifetime',  'ALTER TABLE sessions ADD COLUMN qr_lifetime INTEGER DEFAULT 30'),
            ('sessions',  'att_mode',     'ALTER TABLE sessions ADD COLUMN att_mode TEXT DEFAULT "both"'),
            ('users',     'status',       'ALTER TABLE users ADD COLUMN status TEXT DEFAULT "active"'),
        ]:
            if not _col_exists(tbl,col):
                try: c.execute(sql)
                except: pass
        try: c.execute('UPDATE users SET status="active" WHERE status IS NULL')
        except: pass
        c.commit()
    if not c.fetchone('SELECT 1 FROM users LIMIT 1'):
        d1=new_id();d2=new_id();v1=new_id();v2=new_id();v3=new_id();v4=new_id();t1=new_id();t2=new_id()
        c.executemany('INSERT INTO depts(id,name,code) VALUES(?,?,?)',[(d1,'Computer Science','CS'),(d2,'BCA','BCA')])
        c.executemany('INSERT INTO divisions(id,name,dept_id,sems) VALUES(?,?,?,?)',[(v1,'CS Div-A',d1,8),(v2,'CS Div-B',d1,8),(v3,'BCA Div-A',d2,6),(v4,'BCA Div-B',d2,6)])
        c.executemany('INSERT INTO users(id,name,roll,email,pw,role,div_id,sem,dept_id,face,face_img,created_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',[(new_id(),'Admin HOD','ADM001','admin@college.edu',hp('admin@123'),'admin',None,None,None,None,None,now_iso(),'active'),(t1,'Prof. Sharma','TCH001','teacher@college.edu',hp('admin@123'),'teacher',None,None,d1,None,None,now_iso(),'active'),(t2,'Prof. Mehta','TCH002','mehta@college.edu',hp('admin@123'),'teacher',None,None,d2,None,None,now_iso(),'active'),(new_id(),'Demo Student','CS001','student@college.edu',hp('stud@123'),'student',v1,3,d1,None,None,now_iso(),'active'),])
        c.executemany('INSERT INTO cfg VALUES(?,?)',[('college','My Engineering College'),('dept','Academics'),('min_att','75'),('face_conf','80'),('qr_lifetime','30'),('periods','6')])
        times=[('09:00','09:55'),('10:00','10:55'),('11:00','11:55'),('12:00','12:55'),('14:00','14:55'),('15:00','15:55')]
        subjs=['Data Structures','Algorithms','DBMS','OS','Maths','Web Tech']
        for di,day in enumerate(DAYS[:5]):
            for pi in range(6):
                c.execute('INSERT INTO timetable(id,div_id,sem,dept_id,day,period,start_time,end_time,subject,teacher_id,teacher_name,room) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(new_id(),v1,3,d1,day,pi+1,times[pi][0],times[pi][1],subjs[(di+pi)%6],t1,'Prof. Sharma','Room 101'))
        c.commit()
    c.close()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/ping')
def ping(): return jsonify({'ok':True,'ts':now_iso(),'db':'postgresql' if USE_PG else 'sqlite'})

@app.route('/api/cfg')
def get_cfg():
    c=get_db();r={x['k']:x['v'] for x in c.fetchall('SELECT * FROM cfg')};c.close();return jsonify(r)

@app.route('/api/cfg',methods=['POST'])
def save_cfg():
    c=get_db()
    for k,v in request.json.items():
        if USE_PG: c.execute('INSERT INTO cfg VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=EXCLUDED.v',(k,str(v)))
        else: c.execute('INSERT OR REPLACE INTO cfg VALUES(?,?)',(k,str(v)))
    c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/login',methods=['POST'])
def login():
    d=request.json;c=get_db()
    u=c.fetchone('SELECT id,name,roll,email,role,div_id,sem,dept_id,face_img,created_at,status,CASE WHEN face IS NOT NULL THEN 1 ELSE 0 END as has_face FROM users WHERE email=? AND pw=? AND role=?',(d['email'].lower().strip(),hp(d['password']),d['role']))
    c.close()
    if not u: return jsonify({'ok':False,'msg':'Wrong credentials'})
    st=u.get('status','active')
    if st=='pending': return jsonify({'ok':False,'msg':'⏳ Approval pending. Admin will approve your account soon.'})
    if st=='rejected': return jsonify({'ok':False,'msg':'❌ Your registration was rejected. Contact admin.'})
    return jsonify({'ok':True,'user':u,'msg':''})

@app.route('/api/register',methods=['POST'])
def register():
    d=request.json;name=d.get('name','').strip();roll=d.get('roll','').strip().upper()
    email=d.get('email','').strip().lower();pw=d.get('password','');role=d.get('role','student')
    if not all([name,roll,email,pw]): return jsonify({'ok':False,'msg':'All fields required'})
    if len(pw)<6: return jsonify({'ok':False,'msg':'Password min 6 chars'})
    c=get_db()
    ex=c.fetchone('SELECT id FROM users WHERE roll=? AND role=?',(roll,role))
    if ex: c.execute('DELETE FROM users WHERE id=?',(ex['id'],))
    if c.fetchone('SELECT 1 FROM users WHERE email=?',(email,)): c.close();return jsonify({'ok':False,'msg':'Email already registered'})
    uid=new_id()
    status='pending' if role=='teacher' else 'active'
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid,name,roll,email,hp(pw),role,d.get('div_id'),int(d['sem']) if d.get('sem') else None,d.get('dept_id'),None,None,now_iso(),status))
    c.commit();u=c.fetchone('SELECT id,name,roll,email,role,div_id,sem,dept_id,face_img,created_at,status,CASE WHEN face IS NOT NULL THEN 1 ELSE 0 END as has_face FROM users WHERE id=?',(uid,));c.close()
    if role=='teacher':
        return jsonify({'ok':True,'pending':True,'msg':'✅ Registration submitted! Wait for admin approval before logging in.','user':u})
    return jsonify({'ok':True,'user':u})

@app.route('/api/depts')
def get_depts():
    c=get_db();r=c.fetchall('SELECT * FROM depts ORDER BY name');c.close();return jsonify(r)
@app.route('/api/depts',methods=['POST'])
def add_dept():
    d=request.json;c=get_db()
    c.execute('INSERT INTO depts VALUES(?,?,?)',(new_id(),d['name'].strip(),d.get('code','').upper() or d['name'][:3].upper()))
    c.commit();c.close();return jsonify({'ok':True})
@app.route('/api/depts/<did>',methods=['DELETE'])
def del_dept(did):
    c=get_db();c.execute('DELETE FROM depts WHERE id=?',(did,));c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/divisions')
def get_divs():
    c=get_db();r=c.fetchall('SELECT * FROM divisions ORDER BY name');c.close();return jsonify(r)
@app.route('/api/divisions',methods=['POST'])
def add_div():
    d=request.json;c=get_db()
    c.execute('INSERT INTO divisions VALUES(?,?,?,?)',(new_id(),d['name'].strip(),d.get('dept_id'),int(d.get('sems',8))))
    c.commit();c.close();return jsonify({'ok':True})
@app.route('/api/divisions/<did>',methods=['DELETE'])
def del_div(did):
    c=get_db();c.execute('DELETE FROM divisions WHERE id=?',(did,));c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/users')
def get_users():
    c=get_db()
    q='SELECT id,name,roll,email,role,div_id,sem,dept_id,face_img,created_at,CASE WHEN face IS NOT NULL THEN 1 ELSE 0 END as has_face FROM users WHERE 1=1';p=[]
    for k in ['role','div_id','sem','dept_id']:
        if request.args.get(k): q+=f' AND {k}=?';p.append(request.args[k])
    q+=' ORDER BY name LIMIT 500';r=c.fetchall(q,p);c.close();return jsonify(r)

@app.route('/api/users',methods=['POST'])
def add_user():
    d=request.json;c=get_db();roll=d['roll'].strip().upper();role=d.get('role','student')
    ex=c.fetchone('SELECT id FROM users WHERE roll=? AND role=?',(roll,role))
    if ex: c.execute('DELETE FROM users WHERE id=?',(ex['id'],))
    if c.fetchone('SELECT 1 FROM users WHERE email=?',(d['email'].lower(),)): c.close();return jsonify({'ok':False,'msg':'Email already exists'})
    uid=new_id()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(uid,d['name'].strip(),roll,d['email'].lower(),hp(d.get('password','pass@123')),role,d.get('div_id'),int(d['sem']) if d.get('sem') else None,d.get('dept_id'),None,None,now_iso(),'active'))
    c.commit();c.close();return jsonify({'ok':True,'id':uid})

@app.route('/api/users/<uid>',methods=['GET'])
def get_user(uid):
    c=get_db();u=c.fetchone('SELECT id,name,roll,email,role,div_id,sem,dept_id,face_img,created_at,CASE WHEN face IS NOT NULL THEN 1 ELSE 0 END as has_face FROM users WHERE id=?',(uid,));c.close();return jsonify({'ok':bool(u),'user':u})

@app.route('/api/users/<uid>',methods=['DELETE'])
def del_user(uid):
    c=get_db();c.execute('DELETE FROM users WHERE id=?',(uid,));c.execute('DELETE FROM attendance WHERE student_id=?',(uid,));c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/users/<uid>/face',methods=['POST'])
def enroll_face(uid):
    d=request.json;c=get_db()
    c.execute('UPDATE users SET face=?,face_img=? WHERE id=?',(json.dumps(d['face']),d.get('img'),uid))
    c.commit();u=c.fetchone('SELECT id,name,roll,email,role,div_id,sem,dept_id,face_img,created_at,CASE WHEN face IS NOT NULL THEN 1 ELSE 0 END as has_face FROM users WHERE id=?',(uid,));c.close();return jsonify({'ok':True,'user':u})

@app.route('/api/users/<uid>/face',methods=['DELETE'])
def del_face(uid):
    c=get_db();c.execute('UPDATE users SET face=NULL,face_img=NULL WHERE id=?',(uid,));c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/teachers/pending')
def pending_teachers():
    c=get_db()
    r=c.fetchall("SELECT id,name,roll,email,dept_id,created_at,status FROM users WHERE role='teacher' AND status='pending' ORDER BY created_at DESC")
    c.close();return jsonify(r)

@app.route('/api/teachers/all')
def all_teachers():
    c=get_db()
    teachers=c.fetchall("SELECT id,name,roll,email,dept_id,created_at,status,CASE WHEN face IS NOT NULL THEN 1 ELSE 0 END as has_face FROM users WHERE role='teacher' ORDER BY name")
    result=[]
    for t in teachers:
        t=dict(t)
        sess=c.fetchone('SELECT COUNT(*) as cnt FROM sessions WHERE teacher_id=?',(t['id'],))
        t['session_count']=sess['cnt'] if sess else 0
        result.append(t)
    c.close();return jsonify(result)

@app.route('/api/teachers/<uid>/approve',methods=['POST'])
def approve_teacher(uid):
    c=get_db();c.execute("UPDATE users SET status='active' WHERE id=? AND role='teacher'",(uid,));c.commit();c.close();return jsonify({'ok':True,'msg':'Teacher approved'})

@app.route('/api/teachers/<uid>/reject',methods=['POST'])
def reject_teacher(uid):
    c=get_db();c.execute("UPDATE users SET status='rejected' WHERE id=? AND role='teacher'",(uid,));c.commit();c.close();return jsonify({'ok':True,'msg':'Teacher rejected'})

@app.route('/api/teachers/<uid>/revoke',methods=['POST'])
def revoke_teacher(uid):
    c=get_db();c.execute("UPDATE users SET status='pending' WHERE id=? AND role='teacher'",(uid,));c.commit();c.close();return jsonify({'ok':True,'msg':'Approval revoked'})

@app.route('/api/sessions')
def get_sessions():
    c=get_db();tid=request.args.get('teacher_id');div_id=request.args.get('div_id');active=request.args.get('active');limit=int(request.args.get('limit',50))
    q='SELECT * FROM sessions WHERE 1=1';p=[]
    if tid: q+=' AND teacher_id=?';p.append(tid)
    if div_id: q+=' AND div_id=?';p.append(div_id)
    if active: q+=' AND active=?';p.append(int(active))
    q+=' ORDER BY started_at DESC LIMIT ?';p.append(limit)
    r=c.fetchall(q,p);c.close();return jsonify(r)

@app.route('/api/sessions',methods=['POST'])
def start_session():
    d=request.json;c=get_db();tid=d['teacher_id']
    c.execute('UPDATE sessions SET active=0,qr_open=0,ended_at=? WHERE teacher_id=? AND active=1',(now_iso(),tid))
    sid=new_id();dt=today();dy=today_day();qrl=int(d.get('qr_lifetime',30))
    c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?)',(sid,tid,d['teacher_name'],d['div_id'],d['div_name'],d.get('dept_id'),int(d['sem']),d['subject'],d.get('room',''),dt,dy,now_iso(),None,d.get('att_mode','both'),qrl))
    c.commit();c.close();return jsonify({'ok':True,'id':sid})

@app.route('/api/sessions/<sid>/end',methods=['POST'])
def end_session(sid):
    c=get_db()
    sess=c.fetchone('SELECT * FROM sessions WHERE id=?',(sid,))
    if not sess: c.close();return jsonify({'ok':False,'msg':'Not found'})
    all_stds=c.fetchall("SELECT id,name,roll FROM users WHERE role='student' AND div_id=? AND sem=?",(sess['div_id'],sess['sem']))
    present_ids={r['student_id'] for r in c.fetchall("SELECT student_id FROM attendance WHERE session_id=? AND status='present'",(sid,))}
    nm=now_iso()
    for s in all_stds:
        if s['id'] not in present_ids:
            if USE_PG:
                c.execute("INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",(new_id(),sid,s['id'],s['name'],s['roll'],sess['div_id'],sess['div_name'],sess['sem'],sess['subject'],sess['date'],sess['day'],'absent','auto',None,nm,''))
            else:
                c.execute("INSERT OR IGNORE INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(new_id(),sid,s['id'],s['name'],s['roll'],sess['div_id'],sess['div_name'],sess['sem'],sess['subject'],sess['date'],sess['day'],'absent','auto',None,nm,''))
    present_list=c.fetchall("SELECT a.id as att_id,a.student_id,a.name,a.roll,a.conf,a.method,COALESCE(a.att_img,u.face_img) as face_img FROM attendance a LEFT JOIN users u ON u.id=a.student_id WHERE a.session_id=? AND a.status='present' ORDER BY a.name",(sid,))
    absent_list=c.fetchall("SELECT student_id,name,roll FROM attendance WHERE session_id=? AND status='absent' ORDER BY name",(sid,))
    c.execute('UPDATE sessions SET active=0,qr_open=0,ended_at=? WHERE id=?',(nm,sid))
    c.commit();c.close()
    return jsonify({'ok':True,'summary':{'session':sess,'total':len(all_stds),'present':len(present_list),'absent':len(absent_list),'present_list':present_list,'absent_list':absent_list}})

@app.route('/api/sessions/<sid>/open-qr',methods=['POST'])
def open_qr(sid):
    c=get_db();c.execute('UPDATE sessions SET qr_open=1 WHERE id=? AND active=1',(sid,));c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/sessions/<sid>/close-qr',methods=['POST'])
def close_qr(sid):
    c=get_db();c.execute('UPDATE sessions SET qr_open=0 WHERE id=?',(sid,));c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/sessions/<sid>/qr-token',methods=['POST'])
def gen_qr(sid):
    import random,string
    c=get_db()
    sess=c.fetchone('SELECT * FROM sessions WHERE id=? AND active=1 AND qr_open=1',(sid,))
    cfg_r={x['k']:x['v'] for x in c.fetchall('SELECT * FROM cfg')}
    if not sess: c.close();return jsonify({'ok':False,'msg':'Session not active'})
    lifetime=int(sess.get('qr_lifetime') or cfg_r.get('qr_lifetime',30))
    c.execute('DELETE FROM qr_tokens WHERE issued_at<?',(time.time()-600,))
    token=''.join(random.choices(string.ascii_uppercase+string.digits,k=10))
    if USE_PG:
        c.execute("INSERT INTO qr_tokens VALUES(?,?,?) ON CONFLICT(token) DO UPDATE SET session_id=EXCLUDED.session_id,issued_at=EXCLUDED.issued_at",(token,sid,time.time()))
    else:
        c.execute('INSERT OR REPLACE INTO qr_tokens VALUES(?,?,?)',(token,sid,time.time()))
    c.commit();c.close();return jsonify({'ok':True,'token':token,'lifetime':lifetime})

@app.route('/api/sessions/my-active')
def my_active():
    tid=request.args.get('teacher_id')
    if not tid: return jsonify({'session':None})
    c=get_db();s=c.fetchone('SELECT * FROM sessions WHERE teacher_id=? AND active=1 ORDER BY started_at DESC LIMIT 1',(tid,));c.close();return jsonify({'session':s})

@app.route('/api/sessions/for-student')
def sess_for_student():
    div_id=request.args.get('div_id');sem=request.args.get('sem');c=get_db()
    rows=c.fetchall('SELECT * FROM sessions WHERE div_id=? AND sem=? AND active=1 ORDER BY started_at DESC',(div_id,int(sem))) if div_id and sem else []
    c.close();return jsonify({'sessions':rows})

@app.route('/api/sessions/<sid>/summary')
def session_summary(sid):
    c=get_db()
    sess=c.fetchone('SELECT * FROM sessions WHERE id=?',(sid,))
    if not sess: c.close();return jsonify({'ok':False})
    total=c.fetchone("SELECT COUNT(*) as n FROM users WHERE role='student' AND div_id=? AND sem=?",(sess['div_id'],sess['sem']))['n']
    present_list=c.fetchall("SELECT a.id as att_id,a.student_id,a.name,a.roll,a.conf,a.method,COALESCE(a.att_img,u.face_img) as face_img FROM attendance a LEFT JOIN users u ON u.id=a.student_id WHERE a.session_id=? AND a.status='present' ORDER BY a.name",(sid,))
    absent_list=c.fetchall("SELECT student_id,name,roll FROM attendance WHERE session_id=? AND status='absent' ORDER BY name",(sid,))
    cancelled_list=c.fetchall("SELECT a.id as att_id,a.student_id,a.name,a.roll,a.method,a.cancel_reason,COALESCE(a.att_img,u.face_img) as face_img FROM attendance a LEFT JOIN users u ON u.id=a.student_id WHERE a.session_id=? AND a.status='cancelled' ORDER BY a.name",(sid,))
    c.close()
    return jsonify({'ok':True,'summary':{'session':sess,'total':total,'present':len(present_list),'absent':len(absent_list),'cancelled':len(cancelled_list),'present_list':present_list,'absent_list':absent_list,'cancelled_list':cancelled_list}})

@app.route('/api/verify-qr',methods=['POST'])
def verify_qr():
    d=request.json;raw=d.get('token','').strip();student_id=d.get('student_id')
    token=re.sub(r'[^A-Z0-9]','',raw.upper())
    if 'token=' in raw:
        try:
            from urllib.parse import urlparse,parse_qs
            qs=parse_qs(urlparse(raw).query)
            if qs.get('token'): token=re.sub(r'[^A-Z0-9]','',qs['token'][0].upper())
        except: pass
    if not token: return jsonify({'ok':False,'msg':'Enter token'})
    c=get_db()
    rec=c.fetchone('SELECT * FROM qr_tokens WHERE token=?',(token,))
    if not rec: c.close();return jsonify({'ok':False,'msg':'Invalid or expired token'})

    # Check QR token age against session's own lifetime
    sid=rec['session_id']
    sess=c.fetchone('SELECT * FROM sessions WHERE id=? AND active=1',(sid,))
    if not sess: c.close();return jsonify({'ok':False,'msg':'Session no longer active'})

    token_age=time.time()-float(rec['issued_at'])
    qr_lifetime=int(sess.get('qr_lifetime') or 30)
    if token_age > qr_lifetime:
        c.close();return jsonify({'ok':False,'msg':f'QR expired ({int(token_age)}s old, lifetime {qr_lifetime}s) — scan the new QR code','reason':'qr_expired'})

    student=c.fetchone("SELECT * FROM users WHERE id=? AND role='student'",(student_id,))
    if not student: c.close();return jsonify({'ok':False,'msg':'Student not found','reason':'no_student'})
    if student['div_id']!=sess['div_id'] or str(student['sem'])!=str(sess['sem']):
        c.close();return jsonify({'ok':False,'msg':f'Session is for {sess["div_name"]} Sem {sess["sem"]}','reason':'wrong_div'})
    if c.fetchone("SELECT 1 FROM attendance WHERE session_id=? AND student_id=? AND status='present'",(sid,student_id)):
        c.close();return jsonify({'ok':True,'already':True,'session':sess,'student':student,'msg':f'Already marked present for {sess["subject"]}'})

    mode=sess.get('att_mode','both')
    if mode=='qr':
        c.execute('INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(new_id(),sid,student_id,student['name'],student['roll'],sess['div_id'],sess['div_name'],sess['sem'],sess['subject'],sess['date'],sess.get('day',''),'present','qr',None,now_iso(),''))
        c.commit();c.close();return jsonify({'ok':True,'marked':True,'mode':'qr','session':sess,'student':student,'msg':f'Present marked — {sess["subject"]}'})

    # Issue face_ticket — valid for REMAINING QR lifetime only
    # If QR had 10s lifetime and student scanned at 7s, they have 3s left for face
    remaining=qr_lifetime-token_age
    if remaining<=0:
        c.close();return jsonify({'ok':False,'msg':'QR already expired — scan the new QR code','reason':'qr_expired'})
    face_ticket=new_id()
    face_ticket_exp=time.time()+remaining  # expires exactly when QR would have expired
    # Store in qr_tokens table reusing it: token=face_ticket, session_id=sid+"::face::"+student_id
    face_key=f'FACE_{face_ticket}'
    face_val=f'{sid}::{student_id}::{face_ticket_exp}'
    if USE_PG:
        c.execute("INSERT INTO qr_tokens VALUES(?,?,?) ON CONFLICT(token) DO UPDATE SET session_id=EXCLUDED.session_id,issued_at=EXCLUDED.issued_at",(face_key,face_val,time.time()))
    else:
        c.execute('INSERT OR REPLACE INTO qr_tokens VALUES(?,?,?)',(face_key,face_val,time.time()))
    c.commit();c.close()
    return jsonify({'ok':True,'verified':True,'mode':mode,'session':sess,'student':student,
                    'face_ticket':face_ticket,
                    'msg':f'Token verified! Now scan your face for {sess["subject"]}'})

@app.route('/api/mark-face',methods=['POST'])
def mark_face():
    d=request.json;face=d.get('face',[]);student_id=d.get('student_id');session_id=d.get('session_id')
    face_ticket=d.get('face_ticket','')

    c=get_db()

    # ── Check session mode — for 'both'/'face+qr' a valid face_ticket is REQUIRED ──
    sess_check=c.fetchone('SELECT att_mode FROM sessions WHERE id=?',(session_id,))
    sess_mode=(sess_check.get('att_mode','both') if sess_check else 'both')

    if sess_mode in ('both','face+qr') and not face_ticket:
        # No ticket means student bypassed QR scan — block it
        c.close();return jsonify({'ok':False,'msg':'QR expired or not scanned — please scan the QR code first','reason':'qr_expired'})

    # Validate face ticket — must be present and not expired
    if face_ticket:
        face_key=f'FACE_{face_ticket}'
        trec=c.fetchone('SELECT * FROM qr_tokens WHERE token=?',(face_key,))
        if not trec:
            c.close();return jsonify({'ok':False,'msg':'QR session expired — please scan QR again','reason':'ticket_invalid'})
        # Parse stored value: sid::student_id::expiry
        parts=str(trec['session_id']).split('::')
        if len(parts)>=3:
            stored_sid,stored_stud,exp_ts=parts[0],parts[1],float(parts[2])
            if time.time()>exp_ts:
                c.execute('DELETE FROM qr_tokens WHERE token=?',(face_key,));c.commit()
                c.close();return jsonify({'ok':False,'msg':'QR expired — please scan the new QR code','reason':'ticket_expired'})
            if stored_stud!=student_id or stored_sid!=session_id:
                c.close();return jsonify({'ok':False,'msg':'Invalid session — scan QR again','reason':'ticket_mismatch'})
        # Delete ticket after use — one-time use
        c.execute('DELETE FROM qr_tokens WHERE token=?',(face_key,));c.commit()
    # else: face-only mode — no ticket needed

    cfg_r={x['k']:x['v'] for x in c.fetchall('SELECT * FROM cfg')};thresh=float(cfg_r.get('face_conf',80))/100
    student=c.fetchone('SELECT id,name,roll,div_id,sem,face,face_img FROM users WHERE id=?',(student_id,))
    if not student: c.close();return jsonify({'ok':False,'msg':'Student not found'})
    if not student['face']: c.close();return jsonify({'ok':False,'msg':'Face not enrolled','reason':'no_face'})
    thumb=d.get('thumb')  # real-time captured thumbnail
    enrolled=json.loads(student['face']);sim=cosine(face,enrolled);score=round(sim*100);req=round(thresh*100)
    if sim<thresh:
        hint='Hold phone steady, improve lighting.' if score>40 else 'Try re-enrolling your face.'
        c.close();return jsonify({'ok':False,'reason':'low_match','msg':f'Face not matched ({score}% match, need {req}%)','score':score,'required':req,'hint':hint,'img':student.get('face_img')})
    if c.fetchone("SELECT 1 FROM attendance WHERE session_id=? AND student_id=? AND status='present'",(session_id,student_id)):
        c.close();return jsonify({'ok':True,'already':True,'name':student['name'],'msg':'Already marked present','img':student.get('face_img')})
    sess=c.fetchone('SELECT * FROM sessions WHERE id=?',(session_id,))
    if not sess: c.close();return jsonify({'ok':False,'msg':'Session not found'})
    att_img=thumb or student.get('face_img')  # prefer live capture, fall back to enrolled
    c.execute('INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(new_id(),session_id,student_id,student['name'],student['roll'],sess['div_id'],sess['div_name'],sess['sem'],sess['subject'],sess['date'],sess.get('day',''),'present','face+qr',score,now_iso(),'',att_img))
    c.commit();c.close();return jsonify({'ok':True,'marked':True,'name':student['name'],'conf':score,'img':att_img,'msg':f'Present marked — {sess["subject"]} ({score}% match)'})

@app.route('/api/attendance')
def get_attendance():
    c=get_db();q='SELECT * FROM attendance WHERE 1=1';p=[]
    for k in ['student_id','session_id','div_id','sem','date','status','day']:
        if request.args.get(k): q+=f' AND {k}=?';p.append(request.args[k])
    if request.args.get('from'): q+=' AND date>=?';p.append(request.args['from'])
    if request.args.get('to'):   q+=' AND date<=?';p.append(request.args['to'])
    q+=' ORDER BY date DESC,marked_at DESC LIMIT '+str(int(request.args.get('limit',500)))
    r=c.fetchall(q,p);c.close();return jsonify(r)

@app.route('/api/stats')
def get_stats():
    c=get_db();td=today()
    r={'students':c.fetchone("SELECT COUNT(*) as n FROM users WHERE role='student'")['n'],'teachers':c.fetchone("SELECT COUNT(*) as n FROM users WHERE role='teacher'")['n'],'sessions_today':c.fetchone('SELECT COUNT(*) as n FROM sessions WHERE date=?',(td,))['n'],'active_sessions':c.fetchone('SELECT COUNT(*) as n FROM sessions WHERE active=1')['n'],'total_present':c.fetchone("SELECT COUNT(*) as n FROM attendance WHERE status='present'")['n']}
    c.close();return jsonify(r)

@app.route('/api/timetable')
def get_timetable():
    div_id=request.args.get('div_id');sem=request.args.get('sem');c=get_db()
    if div_id and sem: rows=c.fetchall('SELECT * FROM timetable WHERE div_id=? AND sem=? ORDER BY day,period',(div_id,int(sem)))
    elif div_id: rows=c.fetchall('SELECT * FROM timetable WHERE div_id=? ORDER BY sem,day,period',(div_id,))
    else: rows=[]
    c.close();return jsonify(rows)

@app.route('/api/timetable',methods=['POST'])
def save_timetable():
    rows=request.json;c=get_db()
    for row in rows:
        ex=c.fetchone('SELECT id FROM timetable WHERE div_id=? AND sem=? AND day=? AND period=?',(row['div_id'],int(row['sem']),row['day'],int(row['period'])))
        if ex:
            c.execute('UPDATE timetable SET start_time=?,end_time=?,subject=?,teacher_id=?,teacher_name=?,room=?,dept_id=? WHERE id=?',(row.get('start_time',''),row.get('end_time',''),row.get('subject',''),row.get('teacher_id',''),row.get('teacher_name',''),row.get('room',''),row.get('dept_id',''),ex['id']))
        else:
            c.execute('INSERT INTO timetable VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(new_id(),row['div_id'],int(row['sem']),row.get('dept_id',''),row['day'],int(row['period']),row.get('start_time',''),row.get('end_time',''),row.get('subject',''),row.get('teacher_id',''),row.get('teacher_name',''),row.get('room','')))
    c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/timetable/<tid>',methods=['DELETE'])
def del_timetable(tid):
    c=get_db();c.execute('DELETE FROM timetable WHERE id=?',(tid,));c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/timetable-with-att')
def timetable_with_att():
    student_id=request.args.get('student_id');div_id=request.args.get('div_id');sem=request.args.get('sem');day=request.args.get('day',today_day());date=request.args.get('date',today())
    c=get_db()
    tt=c.fetchall('SELECT * FROM timetable WHERE div_id=? AND sem=? AND day=? ORDER BY period',(div_id,int(sem),day))
    att={r['subject']:r for r in c.fetchall('SELECT * FROM attendance WHERE student_id=? AND date=? AND div_id=? AND sem=?',(student_id,date,div_id,int(sem)))}
    active={r['subject']:r for r in c.fetchall('SELECT * FROM sessions WHERE div_id=? AND sem=? AND active=1',(div_id,int(sem)))}
    c.close()
    for row in tt:
        subj=row['subject'];row['att_status']=att[subj]['status'] if subj in att else None;row['att_method']=att[subj]['method'] if subj in att else None;row['session_active']=subj in active;row['session_id']=active[subj]['id'] if subj in active else None;row['qr_open']=active[subj]['qr_open'] if subj in active else False
    return jsonify(tt)

@app.route('/api/timetable-with-att-teacher')
def tt_with_att_teacher():
    teacher_id=request.args.get('teacher_id');day=request.args.get('day',today_day())
    c=get_db()
    tt=c.fetchall('SELECT * FROM timetable WHERE teacher_id=? AND day=? ORDER BY period',(teacher_id,day))
    active_by_div={}
    for r in c.fetchall('SELECT * FROM sessions WHERE teacher_id=? AND active=1',(teacher_id,)): active_by_div[(r['div_id'],str(r['sem']),r['subject'])]=r
    for row in tt:
        key=(row['div_id'],str(row['sem']),row['subject'])
        if key in active_by_div: row['session_active']=True;row['session_id']=active_by_div[key]['id'];row['session']=active_by_div[key]
        else: row['session_active']=False;row['session_id']=None;row['session']=None
    c.close();return jsonify(tt)

@app.route('/api/attendance/<att_id>',methods=['DELETE'])
def cancel_attendance(att_id):
    c=get_db();reason=request.args.get('reason','')
    c.execute("UPDATE attendance SET status='cancelled',cancel_reason=? WHERE id=?",(reason,att_id))
    c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/reset-attendance',methods=['POST'])
def reset_att():
    if request.json.get('confirm')!='RESET_CONFIRM': return jsonify({'ok':False,'msg':'Invalid key'})
    c=get_db();c.execute('DELETE FROM attendance');c.execute('DELETE FROM sessions');c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/reset-timetable',methods=['POST'])
def reset_tt():
    div_id=request.json.get('div_id');sem=request.json.get('sem');c=get_db()
    if div_id and sem: c.execute('DELETE FROM timetable WHERE div_id=? AND sem=?',(div_id,int(sem)))
    elif div_id: c.execute('DELETE FROM timetable WHERE div_id=?',(div_id,))
    c.commit();c.close();return jsonify({'ok':True})

init_db()

# Keep-alive — prevents Render free plan from sleeping
def _keep_alive():
    import threading, urllib.request
    def ping():
        while True:
            try:
                url=os.environ.get('RENDER_EXTERNAL_URL','')
                if url: urllib.request.urlopen(url+'/ping',timeout=10)
            except: pass
            time.sleep(14*60)  # ping every 14 minutes
    t=threading.Thread(target=ping,daemon=True); t.start()

if os.environ.get('RENDER'): _keep_alive()

if __name__=='__main__':
    import socket
    try: ip=socket.gethostbyname(socket.gethostname())
    except: ip='localhost'
    port=int(os.environ.get('PORT',5000))
    print(f'\n{"="*54}\n  AttendX Pro  |  DB: {"PostgreSQL" if USE_PG else "SQLite (local)"}\n{"="*54}')
    print(f'  Local:   http://localhost:{port}')
    print(f'  Network: http://{ip}:{port}\n{"="*54}\n')
    app.run(debug=False,host='0.0.0.0',port=port)
