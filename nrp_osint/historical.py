from pathlib import Path
import csv,re,json
from collections import Counter,defaultdict
from itertools import groupby
from openpyxl import load_workbook
from rapidfuzz import process,fuzz
from .normalize import clean_text,normalize_nrp,normalize_company,canonical_person,employer_key,norm
from .domain import root_domain,mail_domain
from .emailintel import infer_patterns
from .tabular import csv_format,csv_incrustado

# Tuplas, no conjuntos: el orden define la prioridad y debe ser reproducible.
# Con conjuntos, la aleatorizacion del hash de Python hacia que el mapeo pudiera
# cambiar entre ejecuciones cuando varios alias coincidian.
ALIASES={
 'account_id':('IDCUENTA','ID_CUENTA','CUENTA','ID CUENTA','ACCOUNT_ID','NO_CUENTA','NUM_CUENTA'),
 'nrp':('NRP','REGISTRO_PATRONAL','REG_PATRONAL','REGISTRO PATRONAL'),
 'company':('NOMBRE_EMPRESA','RAZON_SOCIAL','RAZÓN_SOCIAL','NOMBRE_ORIGINAL','NOMBRE_ORIGINAL_PADRON',
            'NOMBRE_COMERCIAL','NOMBRE_COMERCIAL_PADRON','EMPRESA','COMPANY','EMPLEADOR','PATRON'),
 'domain':('DOMINIO','DOMAIN','WEBSITE','SITIO','PAGINA_WEB','PÁGINA_WEB'),
 'email':('CORREO_PRINCIPAL','CORREO','EMAIL','E_MAIL','MAIL','CORREO_ELECTRONICO'),
 'email_alt':('CORREO_ALTERNATIVO','EMAIL_ALTERNATIVO','EMAIL_ALT','CORREOS_ALTERNATIVOS'),
 'pattern':('PATRON_CORREO','EMAIL_PATTERN','PATRON','PATRÓN'),
 'full_name':('NOMBRE_COMPLETO','NOMBRE COMPLETO','FULL_NAME','NOMBRE_Y_APELLIDOS'),
 'first_names':('NOMBRE','NOMBRES','FIRST_NAME','FIRST_NAMES'),
 'paternal':('A_PATERNO','APELLIDO_PATERNO','PATERNO','LAST_NAME'),
 'maternal':('A_MATERNO','APELLIDO_MATERNO','MATERNO'),
 'verified':('VERIFICADO','VALIDADO','VERIFIED','ESTATUS_VALIDACION'),
 'domain_status':('DOMINIO_VALIDO','DOMAIN_VALID','MX','MX_VALIDO','ESTATUS_DOMINIO'),
}

# La validacion de dominio ya viene resuelta en el archivo de origen; 'mx'
# significa que el dominio tiene registros de correo comprobados.
MX_POSITIVO={'MX','SI','YES','TRUE','1','VALIDO','OK'}
MX_NEGATIVO={'NO','FALSE','0','INVALIDO','SIN MX'}

def mx_desde_estatus(valor):
    """Traduce el estatus del archivo a True/False/None."""
    v=norm(valor)
    if not v:return None
    if v in MX_POSITIVO:return True
    if v in MX_NEGATIVO:return False
    return None

# Vocabulario de patrones de otros generadores -> nombres internos del motor.
PATTERN_ALIASES={
 'NOMBRE_APELLIDO':'first.paternal',      # nombre.apellido
 'INICIAL_APELLIDO':'f_paternal',         # inicial+apellido -> mhernandez
 'NOMBRE.APELLIDO':'first.paternal',
}
def _patron_externo(valor):
    """Traduce 'nombre.apellido', 'inicial+apellido', etc. al vocabulario interno."""
    v=str(valor or '').strip().lower()
    if not v:return ''
    tabla={
      'nombre.apellido':'first.paternal',
      'nombre_apellido':'first_paternal',
      'nombre-apellido':'first-paternal',
      'nombre+apellido':'firstpaternal',
      'nombreapellido':'firstpaternal',
      'inicial.apellido':'f.paternal',
      'inicial+apellido':'f_paternal',
      'inicialapellido':'f_paternal',
    }
    return tabla.get(v,valor if isinstance(valor,str) else '')

# Columna que no contiene un correo, sino el NOMBRE de la columna de correo
# que aplica segun el patron detectado por el generador que produjo el archivo.
PUNTEROS_CORREO=('COLUMNA_CORREO_PATRON','COLUMNA_CORREO','CORREO_ELEGIDO')

def nh(x):return re.sub(r'[^A-Z0-9]+','_',str(x).strip().upper()).strip('_')
def mapping(headers):
    """Cada columna se asigna a un solo campo. Sin exclusividad, una columna
    llamada PATRON terminaba siendo a la vez la empresa y el patron de correo."""
    h={nh(x):x for x in headers}; out={}; usadas=set()
    for field,names in ALIASES.items():
        for name in names:
            clave=nh(name)
            if clave in h and clave not in usadas:
                out[field]=h[clave];usadas.add(clave);break
    punteros={nh(x) for x in PUNTEROS_CORREO}
    puntero=next((x for x in headers if nh(x) in punteros),'')
    if puntero:out['columna_que_elige_correo']=puntero
    # Archivos que traen un correo por cada patron posible en columnas separadas.
    pool=[x for x in headers if nh(x).startswith('CORREO') and nh(x) not in punteros and x!=out.get('email')]
    if pool:out['correos_por_patron']=pool
    return out

def _truthy(v):return norm(v) in {'SI','S','TRUE','1','VERIFICADO','VALIDADO','OK'}

def _correos_de_fila(row,m,email_directo):
    """Correo principal y alternativos. Se toman todos los candidatos aunque el
    archivo no traiga patron: si hay coincidencia, el correo sirve igual."""
    candidatos=[]
    for col in m.get('correos_por_patron') or ():
        v=clean_text(row.get(col,'')).lower()
        if '@' in v and v not in [x[1] for x in candidatos]:candidatos.append((col,v))
    principal=email_directo if '@' in email_directo else ''
    puntero=m.get('columna_que_elige_correo')
    if not principal and puntero:
        objetivo=nh(clean_text(row.get(puntero,'')))
        if objetivo:
            principal=next((v for c,v in candidatos if nh(c)==objetivo),'')
    if not principal and candidatos:principal=candidatos[0][1]
    alternativos=[v for _,v in candidatos if v!=principal]
    return principal,alternativos

def normalize_hist_row(row,m,source):
    g=lambda f:clean_text(row.get(m.get(f,''),''))
    company=g('company'); domain=g('domain')
    email,extra=_correos_de_fila(row,m,g('email').lower())
    alt=g('email_alt')
    alternativos=[x for x in ([alt] if alt else [])+extra if x]
    # El dominio es exactamente lo que va despues de la @ (bancomer.com.mx), sin recortar.
    domain=mail_domain(email) if not domain and '@' in email else (mail_domain(domain) or root_domain(domain))
    first,pat,mat=g('first_names'),g('paternal'),g('maternal')
    # Archivos que traen el nombre en una sola columna en vez de tres.
    completo=g('full_name')
    if completo and not (first or pat or mat):
        partes=completo.split()
        first,pat,mat=(' '.join(partes[:-2]) or (partes[0] if partes else ''),
                       partes[-2] if len(partes)>=3 else (partes[-1] if len(partes)==2 else ''),
                       partes[-1] if len(partes)>=2 else '')
        nombre=completo
    else:
        nombre=canonical_person(first,pat,mat)
    return {
      'account_id':g('account_id'),'nrp':normalize_nrp(g('nrp')),'company_name':company,'company_norm':normalize_company(company),
      'domain':domain,'email':email if '@' in email else '','email_alt':' | '.join(dict.fromkeys(alternativos)),'pattern':_patron_externo(g('pattern')),
      'full_name':nombre,'first_names':first,'paternal':pat,'maternal':mat,
      'source_file':source,'verified':_truthy(g('verified')),'domain_status':g('domain_status')
    }

def iter_rows(path):
    p=Path(path)
    if p.suffix.lower()=='.csv':
        encoding,dialect=csv_format(p)
        with open(p,'r',encoding=encoding,newline='') as f:
            rd=csv.DictReader(f,dialect=dialect); m=mapping(rd.fieldnames or [])
            for row in rd:yield normalize_hist_row(row,m,p.name)
    else:
        wb=load_workbook(p,read_only=True,data_only=True)
        try:
            ws=wb.active;it=ws.iter_rows(values_only=True);headers=[clean_text(x) for x in next(it)]
            empacado=csv_incrustado(headers)
            if empacado:
                headers,dialecto=empacado;m=mapping(headers);n=len(headers)
                for vals in it:
                    cruda=clean_text(vals[0] if vals else '')
                    if not cruda:continue
                    campos=next(csv.reader([cruda],dialecto))
                    row={h:(campos[j] if j<n and j<len(campos) else '') for j,h in enumerate(headers)}
                    yield normalize_hist_row(row,m,p.name)
                return
            m=mapping(headers)
            for vals in it:
                row={headers[i]:(vals[i] if i<len(vals) else '') for i in range(len(headers))};yield normalize_hist_row(row,m,p.name)
        finally:
            wb.close()

def import_history(db,path,batch_size=5000,rebuild=True):
    p=Path(path);batch=[];count=0;usable=0
    for row in iter_rows(p):
        if not any([row['account_id'],row['nrp'],row['company_name'],row['email']]):continue
        # Una fila solo sirve para cruzar si trae alguna llave de union.
        if any([row['account_id'],row['nrp'],row['company_norm']]):usable+=1
        batch.append(row);count+=1
        if len(batch)>=batch_size:db.add_historical_many(batch);batch=[]
    if batch:db.add_historical_many(batch)
    if rebuild:rebuild_knowledge(db)
    return {'rows_imported':count,'rows_usable':usable,'columns':mapping(headers_of(p)),'source_file':p.name,'source':str(p)}

def headers_of(path):
    """Encabezados del archivo, para diagnosticar el mapeo de columnas."""
    p=Path(path)
    if p.suffix.lower()=='.csv':
        encoding,dialect=csv_format(p)
        with open(p,'r',encoding=encoding,newline='') as f:
            return csv.DictReader(f,dialect=dialect).fieldnames or []
    wb=load_workbook(p,read_only=True,data_only=True)
    try:
        it=wb.active.iter_rows(values_only=True);return [clean_text(x) for x in next(it)]
    except StopIteration:
        return []
    finally:
        wb.close()

def _argmax_by_key(cursor):
    """De filas (clave,valor,conteo) ordenadas por primera aparicion, devuelve
    {clave: valor mas frecuente}. Empate -> el que aparecio primero, igual que Counter."""
    mejor={}
    for clave,valor,n in cursor:
        actual=mejor.get(clave)
        if actual is None or n>actual[1]:mejor[clave]=(valor,n)
    return {k:v for k,(v,_) in mejor.items()}

def rebuild_knowledge(db):
    """Reconstruye nrp_map y company_profiles agregando en SQL y escribiendo por lote."""
    c=db.conn

    # --- conocimiento por NRP ---
    cn_por_nrp=_argmax_by_key(c.execute(
        "SELECT nrp,company_norm,COUNT(*) FROM historical_contacts WHERE nrp<>'' AND company_norm<>''"
        " GROUP BY nrp,company_norm ORDER BY nrp,MIN(id)"))
    dom_por_nrp=_argmax_by_key(c.execute(
        "SELECT nrp,domain,COUNT(*) FROM historical_contacts WHERE nrp<>'' AND domain<>''"
        " GROUP BY nrp,domain ORDER BY nrp,MIN(id)"))
    totales={r[0]:(r[1],r[2]) for r in c.execute(
        "SELECT nrp,COUNT(*),MAX(verified) FROM historical_contacts WHERE nrp<>'' GROUP BY nrp")}
    # fila representativa: la primera de cada (nrp, company_norm), como hacia el next() original
    muestra={(r[0],r[1]):(r[2],r[3]) for r in c.execute(
        "SELECT h.nrp,h.company_norm,h.company_name,h.source_file FROM historical_contacts h"
        " JOIN (SELECT nrp,company_norm,MIN(id) mid FROM historical_contacts"
        "       WHERE nrp<>'' AND company_norm<>'' GROUP BY nrp,company_norm) t ON h.id=t.mid")}

    filas_nrp=[]
    for nrp,cn in cn_por_nrp.items():
        n_items,algun_verificado=totales.get(nrp,(0,0))
        company_name,source_file=muestra.get((nrp,cn),('',''))
        confianza=.99 if algun_verificado else min(.96,.78+.04*n_items)
        filas_nrp.append((nrp,company_name or cn,cn,dom_por_nrp.get(nrp,''),confianza,source_file,'HISTORICO'))
    db.upsert_nrp_many(filas_nrp)

    # --- conocimiento por empresa ---
    dom_por_emp=_argmax_by_key(c.execute(
        "SELECT company_norm,domain,COUNT(*) FROM historical_contacts WHERE company_norm<>'' AND domain<>''"
        " GROUP BY company_norm,domain ORDER BY company_norm,MIN(id)"))
    nombre_por_emp=_argmax_by_key(c.execute(
        "SELECT company_norm,company_name,COUNT(*) FROM historical_contacts WHERE company_norm<>'' AND company_name<>''"
        " GROUP BY company_norm,company_name ORDER BY company_norm,MIN(id)"))
    patrones={}
    for cn,grupo in groupby(c.execute(
            "SELECT company_norm,full_name,email FROM historical_contacts"
            " WHERE company_norm<>'' AND full_name<>'' AND email<>'' ORDER BY company_norm,id"),
            key=lambda r:r[0]):
        patrones[cn]=infer_patterns([{'display_name':r[1],'email':r[2]} for r in grupo])

    # Estatus de dominio que ya venia validado en el archivo de origen.
    estatus_por_emp=_argmax_by_key(c.execute(
        "SELECT company_norm,domain_status,COUNT(*) FROM historical_contacts"
        " WHERE company_norm<>'' AND domain_status<>''"
        " GROUP BY company_norm,domain_status ORDER BY company_norm,MIN(id)"))

    empresas=[r[0] for r in c.execute("SELECT DISTINCT company_norm FROM historical_contacts WHERE company_norm<>''")]
    lote=[]
    for cn in empresas:
        dom=dom_por_emp.get(cn,'')
        pats,pconf=patrones.get(cn,({},0.0))
        mx=mx_desde_estatus(estatus_por_emp.get(cn,''))
        lote.append((cn,nombre_por_emp.get(cn,cn),{
            'domain':dom,'website':('https://'+dom if dom else ''),'domain_confidence':.98 if dom else 0,
            'mx_valid':None if mx is None else int(mx),
            'email_patterns_json':json.dumps(pats,ensure_ascii=False),'pattern_confidence':pconf,
            'source_kind':'HISTORICO'}))
    db.upsert_company_profile_many(lote)

LEAN_COLS="company_name,domain,pattern,nrp,email,email_alt,verified"

class LocalMatcher:
    """El resumen de un NRP o de una empresa no depende de la persona, solo de la
    clave. Se calcula una vez por clave y se reutiliza, en lugar de recorrer los
    históricos completos en cada una de las 500 mil personas."""

    def __init__(self,db,settings):
        self.db=db;self.settings=settings
        self._por_nrp={};self._por_empresa={};self._nombres=None

    def _lean(self,columna,valor):
        # Mismo orden que hist_by_*: define el desempate de los Counter del resumen.
        return self.db.conn.execute(
            f"SELECT {LEAN_COLS} FROM historical_contacts WHERE {columna}=?"
            " ORDER BY verified DESC,id DESC",(valor,)).fetchall()

    def _resumen_nrp(self,nrp):
        if nrp not in self._por_nrp:
            filas=self._lean('nrp',nrp)
            self._por_nrp[nrp]=self._core(filas) if filas else None
        return self._por_nrp[nrp]

    def _resumen_empresa(self,cn):
        if cn not in self._por_empresa:
            filas=self._lean('company_norm',cn)
            self._por_empresa[cn]=self._core(filas) if filas else None
        return self._por_empresa[cn]

    @property
    def nombres_empresa(self):
        """La lista de empresas históricas se consultaba una vez por persona."""
        if self._nombres is None:
            self._nombres=self.db.historical_company_names()
        return self._nombres

    def match_person(self,person):
        # 1 account exact
        by_acc=self.db.hist_by_account(person.get('account_id',''))
        if by_acc:
            return self._vestir(self._core(by_acc,direct_person=True),'ID_CUENTA_EXACTO',.99)
        # 2 NRP exact
        nrp=person.get('nrp','')
        if nrp:
            core=self._resumen_nrp(nrp)
            if core:return self._vestir(core,'NRP_EXACTO',.96)
        # 3 exact normalized company
        cn=person.get('company_norm') or normalize_company(person.get('company_input',''))
        if cn:
            core=self._resumen_empresa(cn)
            if core:return self._vestir(core,'EMPRESA_EXACTA',.93)
        # 4 fuzzy company, deliberately strict
        if cn:
            best=process.extractOne(cn,self.nombres_empresa,scorer=fuzz.WRatio,score_cutoff=self.settings.company_fuzzy_threshold)
            if best:
                candidate,score,_=best
                core=self._resumen_empresa(candidate)
                if core:return self._vestir(core,'EMPRESA_SIMILAR',min(.91,score/100))
        return None

    @staticmethod
    def _vestir(core,route,confidence):
        return {**core,'route':route,'confidence':confidence}

    @staticmethod
    def _core(items,direct_person=False):
        verified=[x for x in items if x['verified']];pool=verified or items
        companies=Counter(x['company_name'] for x in pool if x['company_name']);domains=Counter(x['domain'] for x in pool if x['domain']);patterns=Counter(x['pattern'] for x in pool if x['pattern']);nrps=Counter(x['nrp'] for x in pool if x['nrp'])
        company=companies.most_common(1)[0][0] if companies else ''
        domain=domains.most_common(1)[0][0] if domains else ''
        direct_emails=[]
        if direct_person:
            # El principal primero; luego los alternativos del mismo registro.
            for x in pool:
                if x['email']:direct_emails.append(x['email'])
                for extra in str(x['email_alt'] or '').split('|'):
                    extra=extra.strip().lower()
                    if '@' in extra:direct_emails.append(extra)
            direct_emails=list(dict.fromkeys(direct_emails))
        nrp=nrps.most_common(1)[0][0] if nrps else ''
        return {'company':company,'company_norm':normalize_company(company),'domain':domain,'nrp':nrp,
          'direct_emails':direct_emails,'patterns':dict(patterns),'items':[]}

    # compatibilidad con la version anterior
    def _summarize(self,items,route,confidence,direct_person=False):
        return self._vestir(self._core(items,direct_person),route,confidence)
