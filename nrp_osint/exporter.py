from pathlib import Path
import json,pandas as pd

RESULT_SQL='''
SELECT p.account_id AS cuenta,p.nss_local AS NSS,p.entity AS Entidad,p.curp2_local AS Curp_2,p.age AS Edad,p.age_origin AS Edad_origen,
       p.nrp,p.company_input AS nombre_empresa_entrada,p.blindaje,p.first_names AS nombre,p.paternal AS a_paterno,p.maternal AS a_materno,p.sex AS sexo,p.institute AS instituto,
       e.company_canonical AS empresa_resuelta,e.domain,e.website,e.generic_email AS correo_generico_empresa,e.phone AS telefono_empresa,e.mx_valid,
       ROUND(e.company_confidence*100,1) AS confianza_empresa,ROUND(e.domain_confidence*100,1) AS confianza_dominio,
       ROUND(e.pattern_confidence*100,1) AS confianza_patron,e.match_route AS ruta_empresa,e.status AS estado_empresa,
       r.email AS correo_principal,r.email_type AS tipo_correo,r.email_confidence AS confianza_correo,r.email_alternatives_json,r.match_route AS ruta_contacto,r.status AS estado_contacto
FROM people p LEFT JOIN employers e ON p.employer_key=e.employer_key LEFT JOIN results r ON p.row_id=r.row_id ORDER BY p.row_id
'''

def _export_all(db,output_dir='data/exports',chunk_size=50000):
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True);csv_path=out/'resultados_contactos.csv';parq=out/'parquet';parq.mkdir(exist_ok=True)
    if csv_path.exists():csv_path.unlink()
    total=part=0
    for df in pd.read_sql_query(RESULT_SQL,db.conn,chunksize=chunk_size):
        df['correos_alternativos']=df['email_alternatives_json'].fillna('[]').map(lambda x:' | '.join(json.loads(x or '[]')) if isinstance(x,str) else '')
        df=df.drop(columns=['email_alternatives_json']);df.to_csv(csv_path,mode='a',index=False,header=part==0,encoding='utf-8-sig')
        try:df.to_parquet(parq/f'resultados_{part:04d}.parquet',index=False)
        except ImportError:pass
        total+=len(df);part+=1
    employers=pd.read_sql_query('SELECT * FROM employers ORDER BY updated_at DESC',db.conn);employers.to_csv(out/'empresas_resueltas.csv',index=False,encoding='utf-8-sig')
    evidence=pd.read_sql_query('SELECT * FROM evidence ORDER BY id',db.conn);evidence.to_csv(out/'evidencia.csv',index=False,encoding='utf-8-sig')
    hist=pd.read_sql_query('SELECT account_id,nrp,company_name,domain,email,pattern,source_file,verified FROM historical_contacts ORDER BY id',db.conn);hist.to_csv(out/'historico_consolidado.csv',index=False,encoding='utf-8-sig')
    with pd.ExcelWriter(out/'resumen_ejecutivo.xlsx',engine='openpyxl') as w:
        pd.read_sql_query('SELECT status,COUNT(*) registros FROM results GROUP BY status ORDER BY registros DESC',db.conn).to_excel(w,sheet_name='Contactos',index=False)
        pd.read_sql_query('SELECT match_route,COUNT(*) empleadores FROM employers GROUP BY match_route ORDER BY empleadores DESC',db.conn).to_excel(w,sheet_name='Rutas',index=False)
        pd.read_sql_query('SELECT state,COUNT(*) fuentes FROM source_health GROUP BY state',db.conn).to_excel(w,sheet_name='Fuentes',index=False)
        employers.head(100000).to_excel(w,sheet_name='Empresas',index=False)
    return {'rows':total,'csv':str(csv_path),'parquet_parts':part,'summary':str(out/'resumen_ejecutivo.xlsx')}

def export_all(db,output_dir='data/exports',chunk_size=50000):
    try:
        return _export_all(db,output_dir,chunk_size)
    except PermissionError as exc:
        raise PermissionError(
            'No se pudieron actualizar las exportaciones. Cierra los archivos CSV o Excel abiertos e intenta nuevamente.'
        ) from exc
