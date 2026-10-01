import asyncio,time,random
from collections import defaultdict
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

class CircuitOpen(RuntimeError):pass

class NetworkGovernor:
    def __init__(self,db,settings):
        self.db=db;self.settings=settings;self.global_sem=asyncio.Semaphore(settings.global_concurrency)
        self.host_sem=defaultdict(lambda:asyncio.Semaphore(settings.per_host_concurrency));self.source_locks=defaultdict(asyncio.Lock)
        self.last_call=defaultdict(float)

    def _min_interval(self,source):
        policies={
          'DDGS':self.settings.ddgs_min_interval_seconds,
          'SEARXNG':1.2,'DENUE':0.7,'CLEARBIT_AUTOCOMPLETE':1.0,
          'WIKIDATA':0.7,'COMMON_CRAWL':1.0,'COMPANY_WEB':self.settings.web_min_interval_seconds,
          'SITEMAP':1.0,'ROBOTS':1.0
        }
        return policies.get(source,self.settings.web_min_interval_seconds)

    async def before(self,source,url=''):
        health=self.db.health_get(source) or {};now=time.time()
        if health.get('state')=='OPEN' and float(health.get('cooldown_until') or 0)>now:raise CircuitOpen(f'{source} pausado hasta {health["cooldown_until"]}')
        async with self.source_locks[source]:
            wait=self._min_interval(source)-(time.time()-self.last_call[source])
            if wait>0:await asyncio.sleep(wait+random.uniform(0,.15))
            self.last_call[source]=time.time()
        await self.global_sem.acquire();host=urlparse(url).netloc.lower() if url else source.lower();await self.host_sem[host].acquire();return host

    def after_success(self,source,host,status='200'):
        self.host_sem[host].release();self.global_sem.release();h=self.db.health_get(source) or {};self.db.health_upsert(source,state='CLOSED',consecutive_failures=0,success_count=int(h.get('success_count') or 0)+1,last_status=str(status),last_error='')

    def after_failure(self,source,host,event,error='',retry_after=None):
        self.host_sem[host].release();self.global_sem.release();h=self.db.health_get(source) or {};fail=int(h.get('consecutive_failures') or 0)+1;th=int(h.get('throttle_count') or 0)+(1 if event=='THROTTLED' else 0)
        cooldown=0;state='CLOSED'
        if retry_after:cooldown=time.time()+retry_after;state='OPEN'
        elif fail>=self.settings.circuit_failure_threshold:cooldown=time.time()+self.settings.circuit_cooldown_seconds;state='OPEN'
        self.db.health_upsert(source,state=state,consecutive_failures=fail,failure_count=int(h.get('failure_count') or 0)+1,throttle_count=th,cooldown_until=cooldown,last_status=event,last_error=str(error)[:800])
        self.db.log_network(source,event,str(error))

    @staticmethod
    def retry_after_seconds(value):
        if not value:return None
        try:return max(0,int(value))
        except:pass
        try:return max(0,int(parsedate_to_datetime(value).timestamp()-time.time()))
        except:return None
