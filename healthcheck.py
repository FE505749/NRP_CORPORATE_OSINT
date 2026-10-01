from nrp_osint.db import DB
from nrp_osint.normalize import normalize_nrp,normalize_company
from nrp_osint.emailintel import infer_patterns,generate,composed_confidence
from nrp_osint.config import Settings
import tempfile,os
fd,path=tempfile.mkstemp(suffix='.sqlite');os.close(fd)
try:
    db=DB(path)
    assert normalize_nrp('A12-345')=='A12345'
    assert normalize_company('PE!A SA DE CV')=='PENA'
    pats,conf=infer_patterns([{'display_name':'Carlos Ramirez Soto','email':'carlos.ramirez@demo.com'},{'display_name':'Laura Torres Mena','email':'laura.torres@demo.com'}])
    assert pats.get('first.paternal',0)==2
    assert generate({'first_names':'Maria Guadalupe','paternal':'Hernandez','maternal':'Garcia'},'first.paternal','demo.com')=='maria.hernandez@demo.com'
    assert composed_confidence(.98,.95,.85,True)>80
    print('NRP Corporate OSINT V4 - Healthcheck OK')
finally:
    try:db.conn.close()
    except:pass
    try:os.remove(path)
    except:pass
