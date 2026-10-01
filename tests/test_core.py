import tempfile,os
from nrp_osint.db import DB
from nrp_osint.normalize import normalize_nrp,normalize_company
from nrp_osint.emailintel import infer_patterns,generate
from nrp_osint.historical import import_history,LocalMatcher
from nrp_osint.config import Settings

def test_normalization():
    assert normalize_nrp('A12-34/5')=='A12345'
    assert normalize_company('PE!A SA DE CV')=='PENA'

def test_mexican_pattern():
    pats,conf=infer_patterns([{'display_name':'Carlos Ramirez Soto','email':'carlos.ramirez@demo.com'},{'display_name':'Laura Torres Mena','email':'laura.torres@demo.com'}])
    assert pats['first.paternal']==2
    assert conf>=.75
    p={'first_names':'Maria Guadalupe','paternal':'Hernandez','maternal':'Garcia'}
    assert generate(p,'first.paternal','demo.com')=='maria.hernandez@demo.com'

def test_account_priority(tmp_path):
    db=DB(str(tmp_path/'x.sqlite'))
    f=tmp_path/'hist.csv'
    f.write_text('cuenta,nrp,nombre_empresa,nombre,a_paterno,a_materno,correo,dominio\nC1,A123,EMPRESA DEMO SA DE CV,CARLOS,RAMIREZ,SOTO,carlos.ramirez@demo.com,demo.com\n',encoding='utf-8')
    import_history(db,f)
    m=LocalMatcher(db,Settings()).match_person({'account_id':'C1','nrp':'','company_input':''})
    assert m['route']=='ID_CUENTA_EXACTO'
    assert m['direct_emails'][0]=='carlos.ramirez@demo.com'

def test_end_to_end_local_reuse(tmp_path):
    from nrp_osint.io_stream import ingest
    from nrp_osint.local_reuse import apply_local_reuse
    hist=tmp_path/'hist.csv'
    hist.write_text('cuenta,nrp,nombre_empresa,nombre,a_paterno,a_materno,correo,dominio,verificado\nX1,A999,ACME MEXICO SA DE CV,CARLOS,RAMIREZ,SOTO,carlos.ramirez@acme.com,acme.com,SI\nX2,A999,ACME MEXICO SA DE CV,LAURA,TORRES,MENA,laura.torres@acme.com,acme.com,SI\n',encoding='utf-8')
    main=tmp_path/'main.csv'
    main.write_text('cuenta,NSS,Entidad,Curp_2,Edad,Edad_origen,nrp,nombre_empresa,blindaje,nombre,a_paterno,a_materno,sexo,instituto\nC2,LOCAL,JALISCO,LOCAL,40,40,A999,,NO,JUAN,PEREZ,LOPEZ,M,IMSS\n',encoding='utf-8')
    db=DB(str(tmp_path/'e2e.sqlite'));import_history(db,hist);ingest(db,main);info=apply_local_reuse(db,Settings(enable_dns=False))
    row=db.conn.execute('SELECT p.company_input,r.email,r.match_route FROM people p JOIN results r ON p.row_id=r.row_id').fetchone()
    assert row['company_input']=='ACME MEXICO SA DE CV'
    assert row['email']=='juan.perez@acme.com'
    assert row['match_route']=='NRP_EXACTO'
    assert info['rows_completed_from_history']==1
