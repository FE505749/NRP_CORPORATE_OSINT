import asyncio,hashlib,json
from .network_governor import CircuitOpen

class SearchError(RuntimeError):
    def __init__(self,kind,message):super().__init__(message);self.kind=kind

class SearchManager:
    def __init__(self,db,settings,tokens,net):
        self.db=db;self.settings=settings;self.tokens=tokens;self.net=net
    def _key(self,provider,query):return hashlib.sha256(f'{provider}|{query}'.encode()).hexdigest()
    async def search(self,query,max_results=None):
        max_results=max_results or self.settings.max_search_results
        provider=self.settings.search_provider.lower()
        if provider=='auto':provider='searxng' if self.tokens.get('searxng') else 'ddgs'
        key=self._key(provider,query);cached=self.db.cache_get(key)
        if cached:return json.loads(cached['payload_json'])
        try:
            hits=await (self._searxng(query,max_results) if provider=='searxng' else self._ddgs(query,max_results))
        except SearchError:raise
        except CircuitOpen as e:raise SearchError('CIRCUIT_OPEN',str(e))
        except Exception as e:raise SearchError('NETWORK',str(e))
        self.db.cache_set_ok(key,provider,query,hits,self.settings.cache_days_search);return hits

    async def _ddgs(self,query,max_results):
        host=await self.net.gov.before('DDGS','https://duckduckgo.com/')
        try:
            def run():
                from ddgs import DDGS
                return list(DDGS(timeout=10).text(query,region='mx-es',safesearch='moderate',max_results=max_results,backend='auto') or [])
            raw=await asyncio.to_thread(run)
            self.net.gov.after_success('DDGS',host,'OK')
        except Exception as e:
            msg=str(e).lower();kind='THROTTLED' if any(x in msg for x in ('429','rate','captcha','ratelimit')) else 'NETWORK'
            self.net.gov.after_failure('DDGS',host,kind,e);raise SearchError(kind,str(e))
        return [{'title':str(x.get('title','')),'url':str(x.get('href','')),'snippet':str(x.get('body','')),'provider':'ddgs'} for x in raw if x.get('href')]

    async def _searxng(self,query,max_results):
        base=self.tokens['searxng'].rstrip('/');r=await self.net.get(base+'/search',source='SEARXNG',params={'q':query,'format':'json','language':'es-MX'})
        if r.status_code==429:raise SearchError('THROTTLED','SearXNG 429')
        if r.status_code>=400:raise SearchError('HTTP_'+str(r.status_code),r.text[:300])
        return [{'title':str(x.get('title','')),'url':str(x.get('url','')),'snippet':str(x.get('content','')),'provider':'searxng'} for x in (r.json().get('results') or [])[:max_results]]
