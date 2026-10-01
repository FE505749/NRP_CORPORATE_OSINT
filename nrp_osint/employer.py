import json,re,asyncio
from collections import Counter,defaultdict
from rapidfuzz import fuzz
from .normalize import normalize_company,norm
from .domain import root_domain,normalize_website,domain_company_score,is_blocked_domain,mx_records,mail_domain
from .emailintel import infer_patterns,weak_pattern_confidence,pattern_from_local_part,nombres_juntos
from .search import SearchError
from .tukan_local import corporate_domain

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
    def __init__(self,db,settings,search,crawler,denue,clearbit=None,commoncrawl=None,wikidata=None,tukan=None):
        self.db=db;self.settings=settings;self.search=search;self.crawler=crawler;self.denue=denue;self.clearbit=clearbit;self.commoncrawl=commoncrawl;self.wikidata=wikidata;self.tukan=tukan

    async def resolve(self,employer):
        key=employer['employer_key'];nrp=employer.get('nrp','') or '';supplied=(employer.get('company_input') or '').strip()
        row=self.db.conn.execute("SELECT entity FROM people WHERE employer_key=? AND entity<>'' LIMIT 1",(key,)).fetchone();entity=row['entity'] if row else ''
        company='';company_conf=0.;domain='';domain_conf=0.;website='';generic_email='';phone='';route=[];conflict=False
        local_only=bool(getattr(self.settings,'denue_local_only',False));denue_hit=None;tukan_hit=None;tukan_email=tukan_status=''

        # A. Local NRP knowledge
        known=self.db.get_nrp(nrp) if nrp else None
        if known:
            company=known['company_canonical'];company_conf=max(.93,float(known['confidence'] or 0));domain=known.get('domain','') or '';domain_conf=.96 if domain else 0;route.append('NRP_MAP_LOCAL')
            if supplied and fuzz.WRatio(normalize_company(supplied),normalize_company(company))<62:
                conflict=True;self.db.add_evidence(key,'CONFLICT','NRP_MAP_LOCAL',known.get('source_url',''),f'NRP={company} / entrada={supplied}',.95)

        # B. Company supplied by base
        if not company and supplied:
            company=supplied;company_conf=.76;route.append('EMPRESA_APORTADA')

        # B2. Tukan por NRP exacto: da la empresa cuando la base no la trae. Si el
        # nombre no se parece al de la base, el NRP no se usa y se busca por nombre.
        if self.tukan and nrp and not conflict:
            try:
                tukan_hit=self.tukan.by_nrp(nrp,company)
                if tukan_hit and company and tukan_hit['company'] and tukan_hit['score']<.62:
                    self.db.add_evidence(key,'CONFLICT','TUKAN_NRP',tukan_hit['url'],f"Tukan={tukan_hit['company']} / entrada={company}",tukan_hit['score']);tukan_hit=None
                if tukan_hit and not company and tukan_hit['company']:
                    company=tukan_hit['company'];company_conf=.90;route.append('TUKAN_NRP')
            except Exception as e:self.db.log_network('TUKAN','ERROR',str(e));tukan_hit=None

        # C. NRP-only discovery or NRP verification when knowledge is weak
        if nrp and not conflict and not local_only and not tukan_hit and (not known or float(known.get('confidence') or 0)<.90):
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

        # El dominio del correo sale de CORREOS reales y es exactamente lo que va
        # despues de la @ (bancomer.com.mx), nunca del sitio web si hay correos:
        # Profuturo tiene su web en profuturo.mx y sus correos en profuturo.com.mx.
        # DENUE y Tukan solo votan aqui; el paso D3 decide.
        denue_mail=Counter();tukan_mail=Counter();grupo={};web_domain='';buscado=company

        # D. DENUE structured source
        if not domain and self.denue:
            try:
                hits=await self.denue.company(company,entity,20)
                if hits and hits[0]['score']>=.72:
                    b=hits[0];denue_hit=b;company=b['company'] or company;cn=normalize_company(company);company_conf=max(company_conf,min(.98,.72+.26*b['score']));generic_email=b['email'];phone=b['phone']
                    # La copia local ya trae el dominio depurado; para la API se filtra aqui
                    # (un sitio de Facebook no es el dominio de la empresa).
                    cand=b['domain'] if 'domain' in b else root_domain(b['website'])
                    if cand and not is_blocked_domain(cand):web_domain=cand;website=normalize_website(b['website']) or 'https://'+cand
                    # Correos de todas las sucursales de esa razon social.
                    votes_fn=getattr(self.denue,'email_domain_votes',None)
                    if votes_fn:denue_mail.update(votes_fn(b.get('raz_norm','')))
                    elif corporate_domain(b.get('email','')):denue_mail[mail_domain(b['email'])]+=1
                    # Y de todo el grupo: cualquier establecimiento con el nombre buscado
                    # ('BBVA' junta BBVA BANCOMER, GRUPO FINANCIERO BBVA...).
                    grupo_fn=getattr(self.denue,'brand_email_hosts',None)
                    if grupo_fn:grupo=grupo_fn(buscado)
                    src=getattr(self.denue,'name','DENUE');route.append(src)
                    self.db.add_evidence(key,'COMPANY',src,b['url'],f"{b['company']} | {b['trade_name']} | web {b['website'] or '-'}",b['score'])
            except Exception as e:self.db.log_network('DENUE','ERROR',str(e))

        # D2. Tukan, segunda fuente local: el contacto del patron (ACTIVO primero)
        # y su dominio de correo, que vota junto con DENUE.
        tukan_correos=defaultdict(set)
        if self.tukan:
            try:
                if not tukan_hit:
                    tukan_hit=self.tukan.by_company(company)
                    if tukan_hit and tukan_hit['score']<.85:tukan_hit=None
                if tukan_hit:
                    tukan_email,tukan_status=tukan_hit['email'],tukan_hit['status'];td=tukan_hit['domain']
                    route.append('TUKAN_NRP' if tukan_hit['match']=='NRP' else 'TUKAN_NOMBRE')
                    # Solo el dominio que Tukan eligio (el de un contacto ACTIVO si lo hay); un correo activo vale doble.
                    for e,st in tukan_hit['emails']:
                        if td and corporate_domain(e)==td:
                            tukan_mail[mail_domain(e)]+=2 if st=='ACTIVO' else 1;tukan_correos[mail_domain(e)].add(e)
                    detalle=f"{tukan_hit['company']} | NRP {tukan_hit['nrp']} | {tukan_email} ({tukan_status}) | dominio {td or '-'}"
                    if tukan_hit['shared_domain']:detalle+=f" | dominio de tercero {tukan_hit['shared_domain']}"
                    self.db.add_evidence(key,'COMPANY','TUKAN_LOCAL',tukan_hit['url'],detalle,tukan_hit['score'])
            except Exception as e:self.db.log_network('TUKAN','ERROR',str(e))

        # D3. Dominio del correo por votos de correos reales. El sitio web solo entra
        # si ninguna fuente tiene un correo de empresa.
        if not domain:
            denue_votos={h:max(denue_mail.get(h,0),len(grupo.get(h,()))) for h in set(denue_mail)|set(grupo)}
            mail=Counter(denue_votos)+tukan_mail
            def valido(h):
                # Del grupo amplio solo cuenta un dominio que se parezca a la empresa;
                # de la razon social o de Tukan basta con dos correos.
                return domain_company_score(h,company)>=.5 or ((h in denue_mail or h in tukan_mail) and mail[h]>=2)
            cands=[h for h in mail if valido(h)]
            if cands:
                best=max(cands,key=lambda h:(mail[h],domain_company_score(h,company)))
                ambos=best in denue_votos and best in tukan_mail
                domain=best;domain_conf=min(.97,.82+.03*mail[best]+(.05 if ambos else 0))
                if best in denue_votos:route.append('DOMINIO_CORREOS_DENUE')
                if best in tukan_mail:route.append('DOMINIO_CORREOS_TUKAN')
                if ambos:route.append('TUKAN_CONFIRMA_DOMINIO')
                if not website:website='https://'+(web_domain or best)
                ejemplos=sorted(grupo.get(best,set())|tukan_correos.get(best,set()))
                if not ejemplos and hasattr(self.denue,'domain_emails'):ejemplos=sorted(self.denue.domain_emails(best))
                self.db.add_evidence(key,'DOMAIN','CORREOS',f'mailto:@{best}',f"Dominio {best}: {mail[best]} correos (DENUE {denue_votos.get(best,0)}, Tukan {tukan_mail[best]}) | ej. {', '.join(ejemplos[:6]) or '-'} | web {web_domain or '-'}",domain_conf)
            if not domain and web_domain:
                # Sin correos de la empresa: el sitio web, confirmado si hay correos con ese dominio exacto.
                confirman=set()
                if hasattr(self.denue,'domain_emails'):confirman|=self.denue.domain_emails(web_domain)
                if self.tukan:confirman|=self.tukan.emails_for_domain(web_domain)
                domain=web_domain
                if confirman:
                    domain_conf=.92;route.append('DOMINIO_SITIO_WEB_CONFIRMADO')
                    self.db.add_evidence(key,'DOMAIN','CORREOS',f'mailto:@{web_domain}',f"Dominio {web_domain} (sitio web) confirmado por {len(confirman)} correos | ej. {', '.join(sorted(confirman)[:6])}",domain_conf)
                else:domain_conf=.85;route.append('DOMINIO_SITIO_WEB')

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
            patterns,pconf=self._local_patterns(denue_hit,domain,route,key)
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
            if not patterns:patterns,pconf=self._local_patterns(denue_hit,domain,route,key)
        else:patterns={};pconf=0.

        if company and domain:status='RESUELTO'
        elif local_only:status='SIN_DOMINIO_DENUE' if denue_hit else ('SIN_DOMINIO_TUKAN' if tukan_hit else 'SIN_COINCIDENCIA_DENUE')
        else:status='EMPRESA_SIN_DOMINIO'
        self.db.update_employer(key,company_canonical=company,company_norm=cn,company_confidence=company_conf,domain=domain,website=website,domain_confidence=domain_conf,generic_email=generic_email,phone=phone,mx_valid=None if mx is None else int(bool(mx)),email_patterns_json=json.dumps(patterns,ensure_ascii=False),pattern_confidence=pconf,match_route='|'.join(dict.fromkeys(route)),source_summary='|'.join(dict.fromkeys(route)),status=status,tukan_email=tukan_email,tukan_status=tukan_status)
        self.db.upsert_company_profile(cn,company,domain=domain,website=website,domain_confidence=domain_conf,generic_email=generic_email,phone=phone,mx_valid=None if mx is None else int(bool(mx)),email_patterns_json=json.dumps(patterns,ensure_ascii=False),pattern_confidence=pconf,source_kind='|'.join(dict.fromkeys(route)))
        if nrp and company and company_conf>=.80:self.db.upsert_nrp(nrp,company,cn,domain,company_conf,source_kind='PIPELINE')
        return self.db.get_employer(key)

    def _local_patterns(self,denue_hit,domain,route,key=None):
        """Formato del correo a partir de correos reales del dominio exacto, sin
        nombre: los de DENUE (de cualquier sucursal) y los de Tukan se suman.
        Detecta si la empresa escribe todos los nombres juntos (victorhugo.olivares)."""
        fuentes={}
        if domain and hasattr(self.denue,'domain_emails'):fuentes['DENUE']=self.denue.domain_emails(domain)
        if domain and self.tukan:fuentes['TUKAN']=self.tukan.emails_for_domain(domain)
        votes=Counter();ejemplos=defaultdict(list);usaron=set()
        for e in sorted(set().union(*fuentes.values()) if fuentes else ()):
            pat=pattern_from_local_part(e.split('@',1)[0],allow_single=True)
            if pat:
                votes[pat]+=1;ejemplos[pat].append(e)
                usaron|={f for f,s in fuentes.items() if e in s}
        if not votes:return {},0.
        votes=nombres_juntos(votes)
        if 'DENUE' in usaron:route.append('DENUE_PATRON')
        if 'TUKAN' in usaron:route.append('TUKAN_PATRON')
        if 'given.paternal' in votes:
            # Primero los ejemplos con dos nombres juntos: son la prueba de la regla.
            route.append('FORMATO_NOMBRES_JUNTOS');ejemplos['given.paternal']=ejemplos.pop('given.paternal',[])+ejemplos.pop('first.paternal',[])
        elif 'first.paternal' in votes:ejemplos['first.paternal']+=ejemplos.pop('given.paternal',[])
        conf=weak_pattern_confidence(votes,cap=.70 if 'TUKAN' in usaron else .66)
        if key:
            detalle=' | '.join(f"{p} ({votes[p]}): {', '.join(ejemplos[p][:5])}" for p in sorted(votes,key=lambda p:-votes[p]))
            self.db.add_evidence(key,'PATTERN','FORMATO',f'mailto:@{domain}',detalle,conf)
        return dict(votes),conf

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
