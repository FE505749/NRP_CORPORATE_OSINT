import asyncio,json
from .config import tokens as get_tokens
from .search import SearchManager,SearchError
from .net import NetClient
from .webextract import WebCrawler
from .sources.denue import DenueSource
from .denue_local import DenueLocalSource
from .tukan_local import TukanLocalSource
from .sources.commoncrawl import CommonCrawlSource
from .sources.wikidata import WikidataSource
from .sources.clearbit import ClearbitAutocompleteSource
from .employer import EmployerResolver
from .emailintel import rank_emails,composed_confidence
from .normalize import norm

class Pipeline:
    def __init__(self,db,settings,tokens=None):
        self.db=db;self.settings=settings;self.tokens=tokens or get_tokens();self.net=NetClient(settings,db);self.search=SearchManager(db,settings,self.tokens,self.net);self.crawler=WebCrawler(db,self.net,settings)
        local_path=getattr(settings,'denue_local_path','')
        local_only=bool(getattr(settings,'denue_local_only',False))
        # Con la copia local disponible no se usa la API: es mas rapida y no consume token.
        tukan_path=getattr(settings,'tukan_local_path','')
        # Tukan precargado: segunda fuente local, despues de DENUE.
        self.tukan=TukanLocalSource(tukan_path) if tukan_path and TukanLocalSource.available(tukan_path) else None
        if local_path and DenueLocalSource.available(local_path):self.denue=DenueLocalSource(local_path)
        elif local_only and self.tukan:self.denue=None
        elif local_only:raise RuntimeError('No hay copia local de DENUE ni de Tukan. Descarga DENUE en Fuentes > DENUE local.')
        else:self.denue=DenueSource(self.net,self.tokens.get('denue',''))
        self.clearbit=None if local_only else ClearbitAutocompleteSource(self.net,settings.enable_clearbit_autocomplete)
        self.commoncrawl=CommonCrawlSource(self.net,settings.enable_common_crawl) if settings.enable_common_crawl and not local_only else None
        self.wikidata=WikidataSource(self.net,settings.enable_wikidata) if settings.enable_wikidata and not local_only else None
        self.resolver=EmployerResolver(db,settings,self.search,self.crawler,self.denue,self.clearbit,self.commoncrawl,self.wikidata,self.tukan);self.worker_sem=asyncio.Semaphore(max(1,min(8,settings.global_concurrency//2)))
    async def close(self):await self.net.close()
    async def run(self,max_jobs=0,batch=30,progress=None,source=None):
        processed=0
        try:
            while True:
                remaining=max_jobs-processed if max_jobs else batch
                if max_jobs and remaining<=0:break
                jobs=self.db.next_jobs(min(batch,remaining),source)
                if not jobs:break
                results=await asyncio.gather(*[self._guarded(j) for j in jobs],return_exceptions=True);processed+=len(jobs)
                if progress:progress(processed,jobs,results)
        finally:await self.close()
        return processed
    async def _guarded(self,job):
        async with self.worker_sem:
            key=job['employer_key'];self.db.mark_job(key,'RUNNING')
            try:
                employer=await self.resolver.resolve(job);self._materialize(employer);status=employer.get('status','ERROR') if employer else 'ERROR'
                self.db.mark_job(key,'REVIEW' if status=='CONFLICTO_NRP_EMPRESA' else ('UNRESOLVED' if status in {'SIN_EMPRESA','EMPRESA_SIN_DOMINIO','SIN_DOMINIO_DENUE','SIN_DOMINIO_TUKAN','SIN_COINCIDENCIA_DENUE'} else 'DONE'));return {'key':key,'status':status}
            except SearchError as e:
                self.db.mark_job(key,'RETRY',f'{e.kind}: {e}',300 if e.kind in {'THROTTLED','CIRCUIT_OPEN'} else 60);return {'key':key,'status':'RETRY','error':str(e)}
            except Exception as e:
                self.db.mark_job(key,'RETRY',str(e),90);return {'key':key,'status':'RETRY','error':str(e)}
    def materialize_known(self,source):
        """Genera correos para personas nuevas de empleadores ya resueltos antes,
        sin volver a buscarlos."""
        emps=self.db.resolved_employers_missing_results(source)
        for emp in emps:self._materialize(emp)
        return len(emps)
    def _materialize(self,emp):
        if not emp:return
        key=emp['employer_key'];people=self.db.people_for_employer(key)
        try:patterns=json.loads(emp.get('email_patterns_json') or '{}')
        except:patterns={}
        samples=self.db.email_samples(key);public={norm(x['display_name']):x['email'] for x in samples if x.get('display_name') and x.get('email')};rows=[]
        # Correos reales del dominio en Tukan: si un candidato de la persona esta ahi, queda confirmado.
        en_tukan=self.tukan.emails_for_domain(emp.get('domain','')) if self.tukan and emp.get('domain') else set()
        route=emp.get('match_route') or ''
        for p in people:
            # Preserve direct historical account result if already present.
            existing=self.db.conn.execute("SELECT * FROM results WHERE row_id=? AND email_type='HISTORICO_ID_CUENTA'",(p['row_id'],)).fetchone()
            if existing:continue
            ranked=rank_emails(p,emp.get('domain',''),patterns);primary=ranked[0] if ranked else None;published=public.get(norm(p['full_name']))
            confirmed=next((x for x in ranked if x['email'] in en_tukan),None) if en_tukan else None
            if published:email=published;etype='PUBLICADO';eff=1.0
            # Con inicial ('jmartinez') puede ser otra persona del mismo apellido: vale menos.
            elif confirmed:email=confirmed['email'];etype='CONFIRMADO_TUKAN';eff=.75 if confirmed['pattern'] in {'f_paternal','f.paternal','f_last','f.last'} else .90
            elif primary and float(emp.get('pattern_confidence') or 0)>=self.settings.pattern_accept:email=primary['email'];etype='DEDUCIDO_PATRON';eff=float(emp.get('pattern_confidence') or 0)
            elif primary and float(emp.get('pattern_confidence') or 0)>=.45:email=primary['email'];etype=('DEDUCIDO_PATRON_DENUE_TUKAN' if 'TUKAN_PATRON' in route and 'DENUE_PATRON' in route else 'DEDUCIDO_PATRON_TUKAN' if 'TUKAN_PATRON' in route else 'DEDUCIDO_PATRON_DENUE' if 'DENUE' in route else 'DEDUCIDO_PATRON_DEBIL');eff=float(emp.get('pattern_confidence') or 0)
            elif primary and self.settings.generate_weak_email_candidates and float(emp.get('domain_confidence') or 0)>=self.settings.company_domain_accept:email=primary['email'];etype='DEDUCIDO_SIN_PATRON';eff=.35
            else:email='';etype='';eff=.01
            conf=composed_confidence(float(emp.get('company_confidence') or 0),float(emp.get('domain_confidence') or 0),eff,None if emp.get('mx_valid') is None else bool(emp.get('mx_valid'))) if email else 0
            alts=[x['email'] for x in ranked if x['email']!=email][:5] if ranked else []
            rows.append({'row_id':p['row_id'],'employer_key':key,'email':email,'email_type':etype,'email_confidence':conf,'alternatives':alts,'company_confidence':round(float(emp.get('company_confidence') or 0)*100,1),'domain_confidence':round(float(emp.get('domain_confidence') or 0)*100,1),'pattern_confidence':round(float(emp.get('pattern_confidence') or 0)*100,1),'match_route':emp.get('match_route',''),'status':'OK' if email else ('SIN_PATRON' if emp.get('domain') else emp.get('status','SIN_DOMINIO'))})
        if rows:self.db.save_results_many(rows)
