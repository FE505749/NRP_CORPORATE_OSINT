import httpx
from .network_governor import NetworkGovernor,CircuitOpen

class NetClient:
    def __init__(self,settings,db):
        self.settings=settings;self.gov=NetworkGovernor(db,settings);self.client=httpx.AsyncClient(timeout=settings.request_timeout,follow_redirects=True,headers={'User-Agent':settings.user_agent,'Accept-Language':'es-MX,es;q=0.9,en;q=0.8'})
    async def get(self,url,source='WEB',**kwargs):
        host=await self.gov.before(source,url)
        try:
            r=await self.client.get(url,**kwargs)
            if r.status_code==429:
                self.gov.after_failure(source,host,'THROTTLED','HTTP 429',self.gov.retry_after_seconds(r.headers.get('Retry-After')));return r
            if r.status_code in (403,503):
                self.gov.after_failure(source,host,'HTTP_'+str(r.status_code),'status '+str(r.status_code));return r
            self.gov.after_success(source,host,r.status_code);return r
        except Exception as e:
            self.gov.after_failure(source,host,'NETWORK_ERROR',e);raise
    async def close(self):await self.client.aclose()
