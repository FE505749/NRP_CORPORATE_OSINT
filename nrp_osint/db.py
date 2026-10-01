import json, sqlite3, time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = r'''
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA foreign_keys=ON;
PRAGMA cache_size=-262144;
PRAGMA mmap_size=1073741824;
PRAGMA busy_timeout=60000;

CREATE TABLE IF NOT EXISTS people(
  row_id INTEGER PRIMARY KEY,
  account_id TEXT,
  person_key TEXT,
  full_name TEXT NOT NULL,
  first_names TEXT,
  paternal TEXT,
  maternal TEXT,
  entity TEXT,
  nrp TEXT,
  company_input TEXT,
  blindaje TEXT,
  sex TEXT,
  institute TEXT,
  age TEXT,
  age_origin TEXT,
  nss_local TEXT,
  curp2_local TEXT,
  employer_key TEXT,
  ingest_source TEXT,
  status TEXT DEFAULT 'PENDING',
  created_at REAL NOT NULL,
  company_norm TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_people_account ON people(account_id);
CREATE INDEX IF NOT EXISTS idx_people_nrp ON people(nrp);
CREATE INDEX IF NOT EXISTS idx_people_company ON people(company_input);
CREATE INDEX IF NOT EXISTS idx_people_employer ON people(employer_key);
-- El indice de company_norm se crea en _migrate(): en bases antiguas la columna
-- aun no existe cuando corre este script.

CREATE TABLE IF NOT EXISTS historical_contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT,
  nrp TEXT,
  company_name TEXT,
  company_norm TEXT,
  domain TEXT,
  email TEXT,
  email_alt TEXT,
  pattern TEXT,
  full_name TEXT,
  first_names TEXT,
  paternal TEXT,
  maternal TEXT,
  source_file TEXT,
  verified INTEGER DEFAULT 0,
  imported_at REAL NOT NULL,
  domain_status TEXT DEFAULT ''
);
-- Compuestos: la clave localiza el grupo y (verified DESC,id DESC) deja la fila
-- ganadora al frente, de modo que "la mejor por clave" es una sola busqueda.
CREATE INDEX IF NOT EXISTS idx_hist_account_best ON historical_contacts(account_id,verified DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_hist_nrp_best ON historical_contacts(nrp,verified DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_hist_company_best ON historical_contacts(company_norm,verified DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_hist_domain ON historical_contacts(domain);

CREATE TABLE IF NOT EXISTS employers(
  employer_key TEXT PRIMARY KEY,
  nrp TEXT,
  company_input TEXT,
  company_canonical TEXT,
  company_norm TEXT,
  company_confidence REAL DEFAULT 0,
  domain TEXT,
  website TEXT,
  domain_confidence REAL DEFAULT 0,
  generic_email TEXT,
  phone TEXT,
  mx_valid INTEGER,
  email_patterns_json TEXT DEFAULT '{}',
  pattern_confidence REAL DEFAULT 0,
  match_route TEXT,
  source_summary TEXT,
  status TEXT DEFAULT 'PENDING',
  updated_at REAL NOT NULL,
  tukan_email TEXT DEFAULT '',
  tukan_status TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_emp_nrp ON employers(nrp);
CREATE INDEX IF NOT EXISTS idx_emp_company ON employers(company_norm);
CREATE INDEX IF NOT EXISTS idx_emp_status ON employers(status);

CREATE TABLE IF NOT EXISTS company_profiles(
  company_norm TEXT PRIMARY KEY,
  company_canonical TEXT NOT NULL,
  domain TEXT DEFAULT '',
  website TEXT DEFAULT '',
  domain_confidence REAL DEFAULT 0,
  generic_email TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  mx_valid INTEGER,
  email_patterns_json TEXT DEFAULT '{}',
  pattern_confidence REAL DEFAULT 0,
  source_kind TEXT DEFAULT '',
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS nrp_map(
  nrp TEXT PRIMARY KEY,
  company_canonical TEXT NOT NULL,
  company_norm TEXT DEFAULT '',
  domain TEXT DEFAULT '',
  confidence REAL DEFAULT 0,
  source_url TEXT DEFAULT '',
  source_kind TEXT DEFAULT '',
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs(
  employer_key TEXT PRIMARY KEY,
  status TEXT DEFAULT 'PENDING',
  priority INTEGER DEFAULT 100,
  attempts INTEGER DEFAULT 0,
  last_error TEXT DEFAULT '',
  next_retry REAL DEFAULT 0,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status,next_retry,priority,updated_at);

CREATE TABLE IF NOT EXISTS results(
  row_id INTEGER PRIMARY KEY,
  employer_key TEXT,
  email TEXT,
  email_type TEXT,
  email_confidence REAL DEFAULT 0,
  email_alternatives_json TEXT DEFAULT '[]',
  company_confidence REAL DEFAULT 0,
  domain_confidence REAL DEFAULT 0,
  pattern_confidence REAL DEFAULT 0,
  match_route TEXT,
  status TEXT,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employer_key TEXT,
  row_id INTEGER,
  kind TEXT,
  source_name TEXT,
  url TEXT,
  text TEXT,
  score REAL DEFAULT 0,
  observed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_emp ON evidence(employer_key);
CREATE INDEX IF NOT EXISTS idx_ev_row ON evidence(row_id);

CREATE TABLE IF NOT EXISTS email_samples(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employer_key TEXT,
  company_norm TEXT,
  display_name TEXT,
  email TEXT,
  source_url TEXT,
  confidence REAL DEFAULT 0.8,
  UNIQUE(employer_key,email)
);

CREATE TABLE IF NOT EXISTS search_cache(
  cache_key TEXT PRIMARY KEY,
  provider TEXT,
  query TEXT,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  expires_at REAL NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS page_cache(
  url TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  expires_at REAL NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS robots_cache(
  host TEXT PRIMARY KEY,
  robots_text TEXT NOT NULL,
  expires_at REAL NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health(
  source_name TEXT PRIMARY KEY,
  state TEXT DEFAULT 'CLOSED',
  consecutive_failures INTEGER DEFAULT 0,
  success_count INTEGER DEFAULT 0,
  failure_count INTEGER DEFAULT 0,
  throttle_count INTEGER DEFAULT 0,
  cooldown_until REAL DEFAULT 0,
  last_status TEXT DEFAULT '',
  last_error TEXT DEFAULT '',
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS network_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name TEXT,
  event_type TEXT,
  detail TEXT,
  observed_at REAL NOT NULL
);
'''

class DB:
    def __init__(self, path='corporate_osint.sqlite'):
        self.path=str(path)
        if self.path != ':memory:':
            Path(self.path).expanduser().resolve(strict=False).parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.path, timeout=60, check_same_thread=False)
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript(SCHEMA)
        from .normalize import normalize_company
        # Permite normalizar en SQL las filas antiguas sin escribir en la base.
        self.conn.create_function('norm_company',1,normalize_company,deterministic=True)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Agrega columnas nuevas a bases creadas por versiones anteriores."""
        hist={r[1] for r in self.conn.execute('PRAGMA table_info(historical_contacts)')}
        if 'domain_status' not in hist:
            # Validacion de dominio que ya venia hecha en el archivo de origen
            # (columna DOMINIO_VALIDO), para no repetir consultas DNS.
            self.conn.execute("ALTER TABLE historical_contacts ADD COLUMN domain_status TEXT DEFAULT ''")
        emp={r[1] for r in self.conn.execute('PRAGMA table_info(employers)')}
        for col in ('tukan_email','tukan_status'):
            # Contacto del patron en Tukan y su ESTATUS (ACTIVO, CADUCO, REVOCADO).
            if col not in emp:self.conn.execute(f"ALTER TABLE employers ADD COLUMN {col} TEXT DEFAULT ''")
        cols={r[1] for r in self.conn.execute('PRAGMA table_info(people)')}
        if 'company_norm' not in cols:
            self.conn.execute("ALTER TABLE people ADD COLUMN company_norm TEXT DEFAULT ''")
            from .normalize import normalize_company
            pendientes=[(normalize_company(r[1]),r[0]) for r in
                        self.conn.execute("SELECT row_id,company_input FROM people WHERE company_input<>''")]
            if pendientes:
                self.conn.executemany('UPDATE people SET company_norm=? WHERE row_id=?',pendientes)
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_people_company_norm ON people(company_norm)')
        # Los de una sola columna quedan cubiertos como prefijo de los compuestos.
        for viejo in ('idx_hist_account','idx_hist_nrp','idx_hist_company'):
            self.conn.execute(f'DROP INDEX IF EXISTS {viejo}')
        self.conn.commit()

    @contextmanager
    def tx(self):
        try:
            self.conn.execute('BEGIN'); yield; self.conn.commit()
        except Exception:
            self.conn.rollback(); raise

    def executemany(self, sql, rows):
        with self.tx(): self.conn.executemany(sql, rows)

    def upsert_people(self, rows):
        now=time.time(); payload=[]
        for r in rows:
            payload.append((r['row_id'],r['account_id'],r['person_key'],r['full_name'],r['first_names'],r['paternal'],r['maternal'],r['entity'],r['nrp'],r['company_input'],r['blindaje'],r['sex'],r['institute'],r['age'],r['age_origin'],r['nss_local'],r['curp2_local'],r['employer_key'],r['ingest_source'],'PENDING',now,r.get('company_norm','')))
        self.executemany('''INSERT OR REPLACE INTO people
        (row_id,account_id,person_key,full_name,first_names,paternal,maternal,entity,nrp,company_input,blindaje,sex,institute,age,age_origin,nss_local,curp2_local,employer_key,ingest_source,status,created_at,company_norm)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',payload)

    def ensure_employer_jobs(self):
        now=time.time()
        with self.tx():
            self.conn.execute('''INSERT OR IGNORE INTO employers
              (employer_key,nrp,company_input,company_norm,status,updated_at)
              SELECT DISTINCT employer_key,nrp,company_input,'','PENDING',? FROM people WHERE employer_key<>'' ''',(now,))
            self.conn.execute('''INSERT OR IGNORE INTO jobs(employer_key,status,priority,updated_at)
              SELECT employer_key,'PENDING',CASE WHEN nrp<>'' AND company_input<>'' THEN 10 WHEN nrp<>'' THEN 20 ELSE 30 END,? FROM employers''',(now,))

    def add_historical_many(self, rows):
        now=time.time(); payload=[]
        for r in rows:
            payload.append((r.get('account_id',''),r.get('nrp',''),r.get('company_name',''),r.get('company_norm',''),r.get('domain',''),r.get('email',''),r.get('email_alt',''),r.get('pattern',''),r.get('full_name',''),r.get('first_names',''),r.get('paternal',''),r.get('maternal',''),r.get('source_file',''),int(bool(r.get('verified',0))),now,r.get('domain_status','')))
        self.executemany('''INSERT INTO historical_contacts
          (account_id,nrp,company_name,company_norm,domain,email,email_alt,pattern,full_name,first_names,paternal,maternal,source_file,verified,imported_at,domain_status)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',payload)

    def hist_by_account(self, account_id):
        if not account_id:return []
        return [dict(r) for r in self.conn.execute('SELECT * FROM historical_contacts WHERE account_id=? ORDER BY verified DESC,id DESC',(account_id,))]
    def hist_by_nrp(self, nrp):
        if not nrp:return []
        return [dict(r) for r in self.conn.execute('SELECT * FROM historical_contacts WHERE nrp=? ORDER BY verified DESC,id DESC',(nrp,))]
    def hist_by_company(self, company_norm):
        if not company_norm:return []
        return [dict(r) for r in self.conn.execute('SELECT * FROM historical_contacts WHERE company_norm=? ORDER BY verified DESC,id DESC',(company_norm,))]
    def historical_company_names(self):
        return [r[0] for r in self.conn.execute("SELECT DISTINCT company_norm FROM historical_contacts WHERE company_norm<>''")]

    def get_nrp(self,nrp):
        r=self.conn.execute('SELECT * FROM nrp_map WHERE nrp=?',(nrp,)).fetchone(); return dict(r) if r else None
    def upsert_nrp(self,nrp,company,company_norm='',domain='',confidence=0,source_url='',source_kind=''):
        now=time.time(); self.conn.execute('''INSERT INTO nrp_map(nrp,company_canonical,company_norm,domain,confidence,source_url,source_kind,updated_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(nrp) DO UPDATE SET company_canonical=excluded.company_canonical,company_norm=excluded.company_norm,
        domain=CASE WHEN excluded.domain<>'' THEN excluded.domain ELSE nrp_map.domain END,confidence=MAX(nrp_map.confidence,excluded.confidence),
        source_url=CASE WHEN excluded.confidence>=nrp_map.confidence THEN excluded.source_url ELSE nrp_map.source_url END,
        source_kind=CASE WHEN excluded.confidence>=nrp_map.confidence THEN excluded.source_kind ELSE nrp_map.source_kind END,updated_at=excluded.updated_at''',(nrp,company,company_norm,domain,confidence,source_url,source_kind,now)); self.conn.commit()

    UPSERT_NRP_SQL='''INSERT INTO nrp_map(nrp,company_canonical,company_norm,domain,confidence,source_url,source_kind,updated_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(nrp) DO UPDATE SET company_canonical=excluded.company_canonical,company_norm=excluded.company_norm,
        domain=CASE WHEN excluded.domain<>'' THEN excluded.domain ELSE nrp_map.domain END,confidence=MAX(nrp_map.confidence,excluded.confidence),
        source_url=CASE WHEN excluded.confidence>=nrp_map.confidence THEN excluded.source_url ELSE nrp_map.source_url END,
        source_kind=CASE WHEN excluded.confidence>=nrp_map.confidence THEN excluded.source_kind ELSE nrp_map.source_kind END,updated_at=excluded.updated_at'''

    def upsert_nrp_many(self,rows):
        """rows: (nrp,company,company_norm,domain,confidence,source_url,source_kind). Una sola transaccion."""
        if not rows:return
        now=time.time()
        self.executemany(self.UPSERT_NRP_SQL,[(*r,now) for r in rows])

    def all_company_profiles(self):
        return {r['company_norm']:dict(r) for r in self.conn.execute('SELECT * FROM company_profiles')}

    def upsert_company_profile_many(self,items):
        """items: (company_norm, company_canonical, campos). Conserva la semantica de
        upsert_company_profile pero lee el estado previo de una sola vez."""
        if not items:return
        actuales=self.all_company_profiles();now=time.time();payload=[]
        vacio={'company_canonical':'','domain':'','website':'','domain_confidence':0,'generic_email':'','phone':'','mx_valid':None,'email_patterns_json':'{}','pattern_confidence':0,'source_kind':''}
        for company_norm,company_canonical,fields in items:
            cur=actuales.get(company_norm)
            cur=dict(cur) if cur else {**vacio,'company_canonical':company_canonical}
            cur.update({k:v for k,v in fields.items() if v is not None})
            actuales[company_norm]=cur
            payload.append((company_norm,cur['company_canonical'],cur['domain'],cur['website'],cur['domain_confidence'],cur['generic_email'],cur['phone'],cur['mx_valid'],cur['email_patterns_json'],cur['pattern_confidence'],cur['source_kind'],now))
        self.executemany('''INSERT OR REPLACE INTO company_profiles(company_norm,company_canonical,domain,website,domain_confidence,generic_email,phone,mx_valid,email_patterns_json,pattern_confidence,source_kind,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',payload)

    def update_employer_many(self,items):
        """items: (employer_key, {campo: valor}). Agrupa por juego de campos para
        emitir un executemany por forma en lugar de un UPDATE por fila."""
        allowed={'company_canonical','company_norm','company_confidence','domain','website','domain_confidence','generic_email','phone','mx_valid','email_patterns_json','pattern_confidence','match_route','source_summary','status','tukan_email','tukan_status'}
        formas={}
        for key,fields in items:
            f={k:v for k,v in fields.items() if k in allowed}
            if not f:continue
            firma=tuple(sorted(f))
            formas.setdefault(firma,[]).append(tuple(f[k] for k in firma)+(key,))
        if not formas:return
        now=time.time()
        with self.tx():
            for firma,filas in formas.items():
                sql='UPDATE employers SET '+','.join(f'{k}=?' for k in firma)+f',updated_at={now!r} WHERE employer_key=?'
                self.conn.executemany(sql,filas)

    def get_company_profile(self,company_norm):
        r=self.conn.execute('SELECT * FROM company_profiles WHERE company_norm=?',(company_norm,)).fetchone(); return dict(r) if r else None
    def upsert_company_profile(self,company_norm,company_canonical,**fields):
        cur=self.get_company_profile(company_norm) or {'company_canonical':company_canonical,'domain':'','website':'','domain_confidence':0,'generic_email':'','phone':'','mx_valid':None,'email_patterns_json':'{}','pattern_confidence':0,'source_kind':''}
        cur.update({k:v for k,v in fields.items() if v is not None}); now=time.time()
        self.conn.execute('''INSERT OR REPLACE INTO company_profiles(company_norm,company_canonical,domain,website,domain_confidence,generic_email,phone,mx_valid,email_patterns_json,pattern_confidence,source_kind,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(company_norm,cur['company_canonical'],cur['domain'],cur['website'],cur['domain_confidence'],cur['generic_email'],cur['phone'],cur['mx_valid'],cur['email_patterns_json'],cur['pattern_confidence'],cur['source_kind'],now)); self.conn.commit()

    def update_employer(self,key,**fields):
        allowed={'company_canonical','company_norm','company_confidence','domain','website','domain_confidence','generic_email','phone','mx_valid','email_patterns_json','pattern_confidence','match_route','source_summary','status','tukan_email','tukan_status'}
        f={k:v for k,v in fields.items() if k in allowed};
        if not f:return
        f['updated_at']=time.time(); sql='UPDATE employers SET '+','.join(f'{k}=?' for k in f)+' WHERE employer_key=?'; self.conn.execute(sql,list(f.values())+[key]); self.conn.commit()
    def get_employer(self,key):
        r=self.conn.execute('SELECT * FROM employers WHERE employer_key=?',(key,)).fetchone(); return dict(r) if r else None
    def people_for_employer(self,key):
        return [dict(r) for r in self.conn.execute('SELECT * FROM people WHERE employer_key=? ORDER BY row_id',(key,))]

    def add_email_sample(self,key,display_name,email,source_url='',confidence=.8,company_norm=''):
        if not email:return
        self.conn.execute('INSERT OR IGNORE INTO email_samples(employer_key,company_norm,display_name,email,source_url,confidence) VALUES(?,?,?,?,?,?)',(key,company_norm,display_name,email.lower(),source_url,confidence)); self.conn.commit()
    def email_samples(self,key):
        return [dict(r) for r in self.conn.execute('SELECT * FROM email_samples WHERE employer_key=?',(key,))]

    def add_evidence(self,key,kind,source,url,text,score,row_id=None):
        self.conn.execute('INSERT INTO evidence(employer_key,row_id,kind,source_name,url,text,score,observed_at) VALUES(?,?,?,?,?,?,?,?)',(key,row_id,kind,source,url,(text or '')[:4000],score,time.time())); self.conn.commit()

    def save_results_many(self,rows):
        now=time.time(); payload=[]
        for r in rows: payload.append((r['row_id'],r['employer_key'],r['email'],r['email_type'],r['email_confidence'],json.dumps(r['alternatives'],ensure_ascii=False),r['company_confidence'],r['domain_confidence'],r['pattern_confidence'],r.get('match_route',''),r['status'],now))
        self.executemany('''INSERT OR REPLACE INTO results(row_id,employer_key,email,email_type,email_confidence,email_alternatives_json,company_confidence,domain_confidence,pattern_confidence,match_route,status,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',payload)

    def next_jobs(self,limit=100,source=None):
        """source: limita la cola a los empleadores de un archivo cargado (people.ingest_source)."""
        filtro=' AND j.employer_key IN (SELECT employer_key FROM people WHERE ingest_source=?)' if source else ''
        params=(time.time(),source,limit) if source else (time.time(),limit)
        return [dict(r) for r in self.conn.execute(f'''SELECT j.*,e.nrp,e.company_input,e.company_canonical,e.company_norm,e.domain FROM jobs j JOIN employers e USING(employer_key)
          WHERE j.status IN ('PENDING','RETRY') AND j.next_retry<=?{filtro} ORDER BY j.priority,j.updated_at LIMIT ?''',params)]
    def resolved_employers_missing_results(self,source):
        """Empleadores ya resueltos en corridas previas con personas nuevas sin correo."""
        return [dict(r) for r in self.conn.execute('''SELECT DISTINCT e.* FROM people p JOIN employers e USING(employer_key)
          LEFT JOIN results r ON r.row_id=p.row_id
          WHERE p.ingest_source=? AND e.status='RESUELTO' AND (r.row_id IS NULL OR r.email='')''',(source,))]
    def mark_job(self,key,status,error='',retry_after=0):
        inc=1 if status=='RUNNING' else 0; now=time.time(); self.conn.execute('UPDATE jobs SET status=?,attempts=attempts+?,last_error=?,next_retry=?,updated_at=? WHERE employer_key=?',(status,inc,error,now+retry_after,now,key)); self.conn.commit()

    def cache_get(self,key):
        r=self.conn.execute('SELECT * FROM search_cache WHERE cache_key=? AND expires_at>?',(key,time.time())).fetchone(); return dict(r) if r else None
    def cache_set_ok(self,key,provider,query,payload,ttl_days):
        now=time.time(); self.conn.execute('INSERT OR REPLACE INTO search_cache VALUES(?,?,?,?,?,?,?)',(key,provider,query,json.dumps(payload,ensure_ascii=False),'OK',now+ttl_days*86400,now)); self.conn.commit()
    def page_cache_get(self,url):
        r=self.conn.execute('SELECT payload_json FROM page_cache WHERE url=? AND expires_at>?',(url,time.time())).fetchone(); return json.loads(r['payload_json']) if r else None
    def page_cache_set(self,url,payload,ttl_days=30):
        now=time.time(); self.conn.execute('INSERT OR REPLACE INTO page_cache VALUES(?,?,?,?)',(url,json.dumps(payload,ensure_ascii=False),now+ttl_days*86400,now)); self.conn.commit()
    def robots_get(self,host):
        r=self.conn.execute('SELECT robots_text FROM robots_cache WHERE host=? AND expires_at>?',(host,time.time())).fetchone(); return r['robots_text'] if r else None
    def robots_set(self,host,text,ttl_days=2):
        now=time.time(); self.conn.execute('INSERT OR REPLACE INTO robots_cache VALUES(?,?,?,?)',(host,text,now+ttl_days*86400,now)); self.conn.commit()

    def health_get(self,source):
        r=self.conn.execute('SELECT * FROM source_health WHERE source_name=?',(source,)).fetchone(); return dict(r) if r else None
    def health_upsert(self,source,**fields):
        cur=self.health_get(source) or {'state':'CLOSED','consecutive_failures':0,'success_count':0,'failure_count':0,'throttle_count':0,'cooldown_until':0,'last_status':'','last_error':''}; cur.update(fields); now=time.time()
        self.conn.execute('''INSERT OR REPLACE INTO source_health(source_name,state,consecutive_failures,success_count,failure_count,throttle_count,cooldown_until,last_status,last_error,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)''',(source,cur['state'],cur['consecutive_failures'],cur['success_count'],cur['failure_count'],cur['throttle_count'],cur['cooldown_until'],cur['last_status'],cur['last_error'],now)); self.conn.commit()
    def log_network(self,source,event,detail=''):
        self.conn.execute('INSERT INTO network_events(source_name,event_type,detail,observed_at) VALUES(?,?,?,?)',(source,event,detail[:1200],time.time())); self.conn.commit()
