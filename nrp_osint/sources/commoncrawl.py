import json

class CommonCrawlSource:
    name="CommonCrawl"
    def __init__(self,net,enabled=True):
        self.net=net; self.enabled=enabled
        self.collection=None

    async def _latest(self):
        if self.collection: return self.collection
        r=await self.net.get("https://index.commoncrawl.org/collinfo.json",source="COMMON_CRAWL")
        r.raise_for_status()
        data=r.json()
        self.collection=data[0]["id"] if data else None
        return self.collection

    async def urls_for_domain(self,domain,limit=50):
        if not self.enabled or not domain: return []
        coll=await self._latest()
        if not coll: return []
        url=f"https://index.commoncrawl.org/{coll}-index"
        params={
            "url":f"{domain}/*","output":"json","filter":"status:200",
            "matchType":"domain","pageSize":str(limit)
        }
        r=await self.net.get(url,source="COMMON_CRAWL",params=params)
        if r.status_code!=200: return []
        rows=[]
        for line in r.text.splitlines():
            try:
                x=json.loads(line)
                u=x.get("url","")
                low=u.lower()
                score=sum(k in low for k in (
                    "team","people","leadership","management","contact","contacto",
                    "directorio","nosotros","about","staff","executive"
                ))
                rows.append((score,u))
            except Exception:
                continue
        rows.sort(reverse=True)
        return [u for _,u in rows[:limit]]
