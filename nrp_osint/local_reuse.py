import json,time
from .historical import LocalMatcher
from .normalize import normalize_company,employer_key
from .emailintel import rank_emails,composed_confidence
from .domain import mx_records

LOTE=20000


def _vaciar(db,people_upd,emp_ins,job_ins,resultados,jobs_done):
    """Escribe todo lo acumulado en una sola transaccion."""
    with db.tx():
        if people_upd:
            db.conn.executemany('UPDATE people SET nrp=?,company_input=?,company_norm=?,employer_key=? WHERE row_id=?',people_upd)
        if emp_ins:
            db.conn.executemany('INSERT OR IGNORE INTO employers(employer_key,nrp,company_input,company_norm,status,updated_at) VALUES(?,?,?,?,?,?)',emp_ins)
        if job_ins:
            db.conn.executemany('INSERT OR IGNORE INTO jobs(employer_key,status,priority,updated_at) VALUES(?,?,?,?)',job_ins)
        if resultados:
            db.conn.executemany('''INSERT OR REPLACE INTO results(row_id,employer_key,email,email_type,email_confidence,email_alternatives_json,company_confidence,domain_confidence,pattern_confidence,match_route,status,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',resultados)
        if jobs_done:
            db.conn.executemany("UPDATE jobs SET status='DONE_LOCAL',updated_at=? WHERE employer_key=?",jobs_done)
    people_upd.clear();emp_ins.clear();job_ins.clear();resultados.clear();jobs_done.clear()


def apply_local_reuse(db,settings):
    matcher=LocalMatcher(db,settings)
    procesadas=coincidencias=directos=completadas=0
    people_upd=[];emp_ins=[];job_ins=[];resultados=[];jobs_done=[]
    # Las escrituras a nivel empleador se deduplican: son por empresa, no por persona.
    empleadores={};nrps={};perfiles={}
    perfil_cache={};mx_cache={}
    ahora=time.time()

    cursor=db.conn.execute('SELECT * FROM people ORDER BY row_id')
    while True:
        bloque=cursor.fetchmany(LOTE)
        if not bloque:break
        for fila in bloque:
            p=dict(fila);procesadas+=1
            m=matcher.match_person(p)
            if not m:continue
            coincidencias+=1
            nrp=p['nrp'] or m.get('nrp','');company=p['company_input'] or m['company'];domain=m['domain']
            cn=m['company_norm'] or normalize_company(company);key=employer_key(nrp,company)
            if key!=p['employer_key'] or nrp!=p['nrp'] or company!=p['company_input']:
                people_upd.append((nrp,company,cn,key,p['row_id']))
                p['nrp']=nrp;p['company_input']=company;p['employer_key']=key;p['company_norm']=cn
                completadas+=1
                if key:
                    emp_ins.append((key,nrp,company,cn,'PENDING',ahora))
                    job_ins.append((key,'PENDING',5,ahora))

            if cn in perfil_cache:
                profile=perfil_cache[cn]
            else:
                profile=db.get_company_profile(cn) if cn else None
                perfil_cache[cn]=profile
            patterns={};pconf=0
            if profile:
                try:patterns=json.loads(profile.get('email_patterns_json') or '{}')
                except:patterns={}
                pconf=float(profile.get('pattern_confidence') or 0);domain=domain or profile.get('domain','')

            # 1) MX que ya venia validado en el archivo historico (sin red).
            mx=None
            if profile is not None and profile.get('mx_valid') is not None:
                mx=bool(profile['mx_valid'])
            # 2) Solo si no lo sabemos y hay DNS habilitado, se consulta la red.
            elif domain and settings.enable_dns:
                if domain not in mx_cache:mx_cache[domain]=mx_records(domain).get('valid')
                mx=mx_cache[domain]

            if key:
                empleadores[key]={'company_canonical':company,'company_norm':cn,'company_confidence':m['confidence'],
                    'domain':domain,'website':('https://'+domain if domain else ''),'domain_confidence':.98 if domain else 0,
                    'mx_valid':None if mx is None else int(bool(mx)),
                    'email_patterns_json':json.dumps(patterns,ensure_ascii=False),'pattern_confidence':pconf,
                    'match_route':m['route'],'source_summary':'HISTORICO_LOCAL','status':'LOCAL_MATCH'}
            if nrp and company:
                nrps[nrp]=(nrp,company,cn,domain,m['confidence'],'',m['route'])
            if cn and company:
                perfiles[cn]=(cn,company,{'domain':domain,'website':('https://'+domain if domain else ''),
                    'domain_confidence':.98 if domain else 0,
                    'email_patterns_json':json.dumps(patterns,ensure_ascii=False),
                    'pattern_confidence':pconf,'source_kind':m['route']})

            direct_email=m['direct_emails'][0] if m['direct_emails'] else ''
            if direct_email:
                directos+=1;alts=m['direct_emails'][1:6];etype='HISTORICO_ID_CUENTA';conf=99.0
            else:
                ranked=rank_emails(p,domain,patterns);primary=ranked[0] if ranked else None
                direct_email=primary['email'] if primary and domain else ''
                alts=[x['email'] for x in ranked[1:6]] if ranked else []
                etype='DEDUCIDO_HISTORICO_'+m['route'] if direct_email else ''
                effective=pconf if pconf else .35
                conf=composed_confidence(m['confidence'],.98 if domain else 0,effective,mx) if direct_email else 0
            if direct_email:
                resultados.append((p['row_id'],key,direct_email,etype,conf,json.dumps(alts,ensure_ascii=False),
                    round(m['confidence']*100,1),98.0 if domain else 0,round(pconf*100,1),m['route'],'RESUELTO_LOCAL',ahora))
                if key:jobs_done.append((ahora,key))

        _vaciar(db,people_upd,emp_ins,job_ins,resultados,jobs_done)

    _vaciar(db,people_upd,emp_ins,job_ins,resultados,jobs_done)
    db.update_employer_many(list(empleadores.items()))
    db.upsert_nrp_many(list(nrps.values()))
    db.upsert_company_profile_many(list(perfiles.values()))
    return {'processed':procesadas,'matched':coincidencias,'direct_account_emails':directos,'rows_completed_from_history':completadas}
