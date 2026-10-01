from rapidfuzz import fuzz
from ..normalize import normalize_company
from ..domain import root_domain

class ClearbitAutocompleteSource:
    '''Fuente auxiliar gratuita y sin token. No se usa como prueba única del dominio.'''
    name='CLEARBIT_AUTOCOMPLETE'
    def __init__(self,net,enabled=True):self.net=net;self.enabled=enabled
    async def company(self,name,limit=8):
        if not self.enabled or not name:return []
        r=await self.net.get('https://autocomplete.clearbit.com/v1/companies/suggest',source=self.name,params={'query':name})
        if r.status_code!=200:return []
        try:data=r.json()
        except Exception:return []
        target=normalize_company(name);out=[]
        for x in data[:limit] if isinstance(data,list) else []:
            cand=x.get('name','');domain=root_domain(x.get('domain',''));score=fuzz.WRatio(target,normalize_company(cand))/100
            if domain:out.append({'company':cand,'domain':domain,'score':score,'source':self.name,'url':'https://autocomplete.clearbit.com/'})
        return sorted(out,key=lambda x:x['score'],reverse=True)
