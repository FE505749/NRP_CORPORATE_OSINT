class WikidataSource:
    name="Wikidata"
    def __init__(self,net,enabled=True):
        self.net=net; self.enabled=enabled

    async def search_company(self,name,limit=5):
        if not self.enabled or not name:return []
        params={
            "action":"wbsearchentities","search":name,"language":"es",
            "format":"json","type":"item","limit":limit,"origin":"*"
        }
        r=await self.net.get("https://www.wikidata.org/w/api.php",source="WIKIDATA",params=params)
        if r.status_code!=200:return []
        return [{
            "id":x.get("id",""),"label":x.get("label",""),
            "description":x.get("description",""),"url":x.get("concepturi",""),
            "source":"Wikidata"
        } for x in r.json().get("search",[])]

    async def company_websites(self,name,limit=5):
        hits=await self.search_company(name,limit)
        ids=[x["id"] for x in hits if x.get("id")]
        if not ids:return []
        params={
            "action":"wbgetentities","ids":"|".join(ids),"props":"claims|labels",
            "languages":"es|en","format":"json","origin":"*"
        }
        r=await self.net.get("https://www.wikidata.org/w/api.php",source="WIKIDATA",params=params)
        if r.status_code!=200:return []
        entities=r.json().get("entities",{})
        out=[]
        for h in hits:
            ent=entities.get(h["id"],{})
            claims=ent.get("claims",{})
            websites=[]
            for c in claims.get("P856",[]):
                try:
                    websites.append(c["mainsnak"]["datavalue"]["value"])
                except Exception:pass
            if websites:
                out.append({**h,"websites":websites})
        return out
