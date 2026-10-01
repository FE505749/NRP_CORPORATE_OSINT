import json,re,asyncio
from collections import defaultdict
from rapidfuzz import fuzz
from .normalize import normalize_company,norm
from .domain import root_domain,normalize_website,domain_company_score,is_blocked_domain,mx_records
from .emailintel import infer_patterns,weak_pattern_confidence
from .search import SearchError

COMPANY_PATTERNS=[
 re.compile(r'(?:raz[oó]n social|nombre del patr[oó]n|patr[oó]n|empresa|empleador)\s*[:\-]\s*([A-ZÁÉÍÓÚÑ0-9&.,\' -]{4,140})',re.I),
 re.compile(r'(?:registro patronal|NRP).{0,80}?(?:de|pertenece a|corresponde a)\s+([A-ZÁÉÍÓÚÑ0-9&.,\' -]{4,140})',re.I),
]
BAD={'LINKEDIN','FACEBOOK','EBAY','PREZI','PINTEREST','INSTAGRAM','ROCKETREACH','CONTACTOUT','SIGNALHIRE','WIZA','ZOOMINFO'}

def clean_candidate(x):
    x=re.split(r'[|•;\n]',x or '')[0];x=re.sub(r'\s+',' ',x).strip(' ,.-')
    if len(x)>120 or any(b in norm(x) for b in BAD):return ''
    return x

def company_candidates_from_hit(hit,nrp=''):
    text=f"{hit.get('title','')} {hit.get('snippet','')}";out=[]
    for pat in COMPANY_PATTERNS:
        for m in pat.findall(text):
            c=clean_candidate(m)
            if c:out.append(c)
    if nrp and nrp in re.sub(r'[^A-Za-z0-9]','',text).upper():
        for p in [clean_candidate(x) for x in re.split(r'\s+[|\-–—]\s+',hit.get('title',''))]:
            if p and 2<=len(p.split())<=12 and nrp not in p:out.append(p)
    return list(dict.fromkeys(out))

class EmployerResolver:
    def __init__(self,db,settings,search,crawler,denue,clearbit=None,commoncrawl=None,wikidata=None):
        self.db=db;self.settings=settings;self.search=search;self.crawler=crawler;self.denue=denue;self.clearbit=clearbit;self.commoncrawl=commoncrawl;self.wikidata=wikidata

    async def resolve(self,employer):
        key=employer['employer_key'];nrp=employer.get('nrp','') or '';supplied=(employer.get('company_input') or '').strip()
        row=self.db.conn.execute("SELECT entity FROM people WHERE employer_key=? AND entity<>'' LIMIT 1",(key,)).fetchone();entity=row['entity'] if row else ''
        company='';company_conf=0.;domain='';domain_conf=0.;website='';generic_email='';phone='';route=[];conflict=False
        local_only=bool(getattr(self.settings,'denue_local_only',False));denue_hit=None

        # A. Local NRP knowledge
        known=self.db.get_nrp(nrp) if nrp else None
        if known:
            company=known['company_canonical'];company_conf=max(.93,float(known['confidence'] or 0));domain=known.get('domain','') or '';domain_conf=.96 if domain else 0;route.append('NRP_MAP_LOCAL')
            if supplied and fuzz.WRatio(normalize_company(supplied),normalize_company(company))<62:
                conflict=True;self.db.add_evidence(key,'CONFLICT','NRP_MAP_LOCAL',known.get('source_url',''),f'NRP={company} / entrada={supplied}',.95)

        # B. Company supplied by base
        if not company and supplied:
            company=supplied;company_conf=.76;route.append('EMPRESA_APORTADA')

        # C. NRP-only discovery or NRP verification when knowledge is weak
        if nrp and not conflict and not local_only and (not known or float(known.get('confidence') or 0)<.90):
            info=await self._search_nrp(nrp,supplied or company)
            if info['company']:
                found=info['company'];fconf=info['confidence']
                if company:
                    sim=fuzz.WRatio(normalize_company(company),normalize_company(found))/100
                    if sim>=.72:company_conf=max(company_conf,min(.98,(company_conf+fconf)/2+.08));route.append('NRP_WEB_VERIFICADO')
                    elif fconf>=.84:conflict=True
                else:company=found;company_conf=fconf;route.append('NRP_WEB')
                for ev in info['evidence']:self.db.add_evidence(key,'NRP',ev['source'],ev['url'],ev['text'],ev['score'])

        if conflict:
            self.db.update_employer(key,company_canonical=company or supplied,company_norm=normalize_company(company or supplied),company_confidence=company_conf,match_route='|'.join(route),status='CONFLICTO_NRP_EMPRESA');return self.db.get_employer(key)
        if not company:
            self.db.update_employer(key,match_route='|'.join(route),status='SIN_EMPRESA');return self.db.get_employer(key)

        cn=normalize_company(company);profile=self.db.get_company_profile(cn);cached_patterns={};cached_pconf=0.;cached_mx=None
        if profile and float(profile.get('domain_confidence') or 0)>=.80:
            domain=profile.get('domain','') or domain;website=profile.get('website','') or ('https://'+domain if domain else '');domain_conf=max(domain_conf,float(profile.get('domain_confidence') or 0));generic_email=profile.get('generic_email','') or '';phone=profile.get('phone','') or '';cached_mx=profile.get('mx_valid')
            try:cached_patterns=json.loads(profile.get('email_patterns_json') or '{}')
            except:cached_patterns={}
            cached_pconf=float(profile.get('pattern_confidence') or 0);route.append('COMPANY_PROFILE_LOCAL')

        # D. DENUE structured source
        if not domain and self.denue:
            try:
                hits=await self.denue.company(company,entity,20)
                if hits and hits[0]['score']>=.72:
                    b=hits[0];denue_hit=b;company=b['company'] or company;cn=normalize_company(company);company_conf=max(company_conf,min(.98,.72+.26*b['score']));generic_email=b['email'];phone=b['phone']
                    # La copia local ya trae el dominio depurado; para la API se filtra aqui
                    # (un sitio de Facebook no es el dominio de la empresa).
                    cand=b['domain'] if 'domain' in b else root_domain(b['website'])
                    if cand and not is_blocked_domain(cand):domain=cand;website=normalize_website(b['website']) or 'https://'+cand;domain_conf=.94
                    src=getattr(self.denue,'name','DENUE');route.append(src);self.db.add_evidence(key,'COMPANY',src,b['url'],f"{b['company']} | {b['trade_name']} | {b['website']} | {b['email']}",b['score'])
            except Exception as e:self.db.log_network('DENUE','ERROR',str(e))

        # E. Clearbit autocomplete - auxiliary only, requires independent validation
        if not domain and self.clearbit and not local_only:
            try:
                hits=await self.clearbit.company(company,8)
                if hits and hits[0]['score']>=.82:
                    cand=hits[0];candidate_domain=cand['domain'];verify=await self._validate_domain_candidate(company,candidate_domain)
                    if verify>=.75:
                        domain=candidate_domain;website='https://'+domain;domain_conf=min(.90,.58+.32*verify);route.append('CLEARBIT_AUTOCOMPLETE+VERIFY');self.db.add_evidence(key,'DOMAIN','ClearbitAutocomplete',cand['url'],f"{cand['company']} -> {domain}",domain_conf)
            except Exception as e:self.db.log_network('CLEARBIT_AUTOCOMPLETE','ERROR',str(e))

        # F. Wikidata official website
        if not domain and self.wikidata and not local_only:
            try:
                wd=await self.wikidata.company_websites(company,5);scored=[]
                for item in wd:
                    for site in item.get('websites',[]):
                        rd=root_domain(site);sc=domain_company_score(rd,company,item.get('label',''),item.get('description',''))
                        if rd:scored.append((sc,rd,site,item))
                if scored:
                    scored.sort(reverse=True,key=lambda x:x[0]);sc,domain,website,item=scored[0]
                    if sc>=.55:domain_conf=max(.75,min(.94,sc+.12));route.append('WIKIDATA');self.db.add_evidence(key,'DOMAIN','Wikidata',item.get('url',''),f"{item.get('label','')} -> {website}",domain_conf)
            except Exception as e:self.db.log_network('WIKIDATA','ERROR',str(e))

        # G. General public web search last
        if not domain and not local_only:
            d=await self._resolve_domain(company);domain=d['domain'];website=d['website'];domain_conf=d['confidence']
            if domain:route.append('WEB_DOMAIN')
            for ev in d['evidence']:self.db.add_evidence(key,'DOMAIN',ev['source'],ev['url'],ev['text'],ev['score'])

        mx=None
        if domain and self.settings.enable_dns and not local_only:
            mxinfo=await asyncio.to_thread(mx_records,domain);mx=mxinfo.get('valid');self.db.add_evidence(key,'DNS_MX','DNS',domain,json.dumps(mxinfo,ensure_ascii=False),1.0 if mx else .3)

        # H. Learn email pattern once per company/domain
        if domain and cached_patterns and cached_pconf>=self.settings.pattern_accept:
            patterns,pconf=cached_patterns,cached_pconf;mx=bool(cached_mx) if cached_mx is not None else mx
        elif domain and local_only:
            patterns,pconf=self._denue_patterns(denue_hit,domain)
        elif domain:
            pages=await self._company_pages(website or 'https://'+domain,domain)
            for page in pages:
                for e in page.get('emails',[]):
                    email=e.get('email','').lower()
                    if '@' not in email or root_domain(email.split('@',1)[1])!=domain:continue
                    disp=(e.get('display_name') or '').strip()
                    if disp:self.db.add_email_sample(key,disp,email,page.get('url',''),.90,cn)
                    if not generic_email and email.split('@')[0] in {'info','contact','contacto','ventas','administracion','hello'}:generic_email=email
                if not phone and page.get('phones'):phone=page['phones'][0]
            patterns,pconf=infer_patterns(self.db.email_samples(key))
            if not patterns:patterns,pconf=self._denue_patterns(denue_hit,domain)
        else:patterns={};pconf=0.
        if patterns and pconf and 'DENUE_PATRON' not in route and pconf<self.settings.pattern_accept and denue_hit:route.append('DENUE_PATRON')

        if company and domain:status='RESUELTO'
        elif local_only:status='SIN_DOMINIO_DENUE' if denue_hit else 'SIN_COINCIDENCIA_DENUE'
        else:status='EMPRESA_SIN_DOMINIO'
        self.db.update_employer(key,company_canonical=company,company_norm=cn,company_confidence=company_conf,domain=domain,website=website,domain_confidence=domain_conf,generic_email=generic_email,phone=phone,mx_valid=None if mx is None else int(bool(mx)),email_patterns_json=json.dumps(patterns,ensure_ascii=False),pattern_confidence=pconf,match_route='|'.join(dict.fromkeys(route)),source_summary='|'.join(dict.fromkeys(route)),status=status)
        self.db.upsert_company_profile(cn,company,domain=domain,website=website,domain_confidence=domain_conf,generic_email=generic_email,phone=phone,mx_valid=None if mx is None else int(bool(mx)),email_patterns_json=json.dumps(patterns,ensure_ascii=False),pattern_confidence=pconf,source_kind='|'.join(dict.fromkeys(route)))
        if nrp and company and company_conf>=.80:self.db.upsert_nrp(nrp,company,cn,domain,company_conf,source_kind='PIPELINE')
        return self.db.get_employer(key)

    def _denue_patterns(self,hit,domain):
        """Patron debil a partir de los correos que la empresa registro en DENUE."""
        votes_fn=getattr(self.denue,'email_pattern_votes',None)
        if not hit or not votes_fn:return {},0.
        votes,_=votes_fn(hit.get('raz_norm',''),domain)
        return (votes,weak_pattern_confidence(votes)) if votes else ({},0.)

    async def _search_nrp(self,nrp,supplied=''):
        queries=[f'"{nrp}"',f'"{nrp}" "razón social"',f'"{nrp}" empresa patrón empleador',f'"{nrp}" site:gob.mx'];votes=defaultdict(float);orig={};evidence=[]
        for q in queries:
            hits=await self.search.search(q)
            for h in hits:
                text=f"{h.get('title','')} {h.get('snippet','')}";compact=re.sub(r'[^A-Za-z0-9]','',text).upper()
                if nrp not in compact:continue
                for c in company_candidates_from_hit(h,nrp):
                    nc=normalize_company(c);score=.35+(.45*fuzz.WRatio(nc,normalize_company(supplied))/100 if supplied else 0)+(.15 if 'gob.mx' in h.get('url','') else 0)
                    if nc:votes[nc]+=score;orig[nc]=c;evidence.append({'source':'WEB_NRP','url':h.get('url',''),'text':text[:800],'score':min(1,score)})
        if not votes:return {'company':'','confidence':0,'url':'','evidence':evidence}
        ranked=sorted(votes.items(),key=lambda x:x[1],reverse=True);top,pts=ranked[0];margin=pts-(ranked[1][1] if len(ranked)>1 else 0);conf=min(.94,.58+.10*pts+.08*max(0,margin));return {'company':orig[top],'confidence':conf,'url':evidence[0]['url'] if evidence else '','evidence':evidence}

    async def _validate_domain_candidate(self,company,domain):
        if not domain or is_blocked_domain(domain):return 0
        page=await self.crawler.fetch('https://'+domain);return domain_company_score(domain,company,page.get('title',''),page.get('text','')[:1400])

    async def _resolve_domain(self,company):
        candidates=[];evidence=[]
        for q in (f'"{company}" sitio oficial',f'"{company}" official website',f'"{company}" contacto'):
            for h in await self.search.search(q):
                rd=root_domain(h.get('url',''))
                if not rd or is_blocked_domain(rd):continue
                sc=domain_company_score(rd,company,h.get('title',''),h.get('snippet',''))
                if sc>=.48:candidates.append((sc,rd,h))
        if not candidates:return {'domain':'','website':'','confidence':0,'evidence':[]}
        candidates.sort(reverse=True,key=lambda x:x[0]);score,rd,h=candidates[0];page=await self.crawler.fetch(h['url']);verify=domain_company_score(rd,company,page.get('title',''),page.get('text','')[:1200]);final=min(.97,.58*score+.42*verify+.08)
        if final<self.settings.company_domain_accept:return {'domain':'','website':'','confidence':final,'evidence':[{'source':'WEB_DOMAIN_REJECTED','url':h['url'],'text':h.get('title',''),'score':final}]}
        return {'domain':rd,'website':h['url'],'confidence':final,'evidence':[{'source':'WEB_DOMAIN','url':h['url'],'text':f"{h.get('title','')} {h.get('snippet','')}",'score':final}]}

    async def _company_pages(self,website,domain):
        urls=[website]+[f'https://{domain}{p}' for p in ('/contact','/contacto','/about','/nosotros','/team','/people','/leadership','/management','/directorio')]
        urls+=await self.crawler.sitemap_urls(website,100)
        if self.commoncrawl:
            try:urls+=await self.commoncrawl.urls_for_domain(domain,30)
            except Exception as e:self.db.log_network('COMMON_CRAWL','ERROR',str(e))
        keys=('contact','contacto','team','people','leadership','management','directorio','nosotros','about');urls=list(dict.fromkeys(urls));urls.sort(key=lambda u:sum(k in u.lower() for k in keys),reverse=True);seen=[]
        for u in urls:
            if len(seen)>=self.settings.max_company_pages:break
            p=await self.crawler.fetch(u)
            if p.get('emails') or p.get('people') or p.get('organizations') or p.get('text'):seen.append(p)
        return seen
