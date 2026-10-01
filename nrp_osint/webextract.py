import json, re, urllib.robotparser
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from .domain import root_domain

EMAIL_RE=re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b",re.I)
PHONE_RE=re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s().\-]*)?(?:\(?\d{2,4}\)?[\s.\-]*)"
    r"\d{3,4}[\s.\-]*\d{3,4}(?:\s*(?:ext\.?|x)\s*\d{1,6})?(?!\d)",re.I
)

def deobfuscate(text):
    x=text or ""
    x=re.sub(r"\s*(?:\[|\()?\s*(?:arroba|at)\s*(?:\]|\))?\s*","@",x,flags=re.I)
    x=re.sub(r"\s*(?:\[|\()?\s*(?:punto|dot)\s*(?:\]|\))?\s*",".",x,flags=re.I)
    return x

def _jsonld(soup):
    people=[]; orgs=[]
    for node in soup.select('script[type="application/ld+json"]'):
        raw=node.string or node.get_text()
        try:data=json.loads(raw)
        except Exception:continue
        stack=data if isinstance(data,list) else [data]
        flat=[]
        for x in stack:
            if isinstance(x,dict) and isinstance(x.get("@graph"),list):flat+=x["@graph"]
            else:flat.append(x)
        for x in flat:
            if not isinstance(x,dict):continue
            typ=x.get("@type",[])
            types=typ if isinstance(typ,list) else [typ]
            if "Person" in types:
                works=x.get("worksFor") or {}
                if isinstance(works,list):works=works[0] if works else {}
                people.append({
                    "name":x.get("name",""),"jobTitle":x.get("jobTitle",""),
                    "company":works.get("name","") if isinstance(works,dict) else "",
                    "email":str(x.get("email","")).replace("mailto:",""),
                    "telephone":x.get("telephone","")
                })
            if "Organization" in types or "Corporation" in types:
                orgs.append({
                    "name":x.get("name",""),"url":x.get("url",""),
                    "email":str(x.get("email","")).replace("mailto:",""),
                    "telephone":x.get("telephone","")
                })
    return people,orgs

def _context_for_anchor(a):
    node=a
    for _ in range(5):
        node=node.parent if node else None
        if not node:break
        txt=re.sub(r"\s+"," ",node.get_text(" ",strip=True))
        if 25<=len(txt)<=800:
            headings=node.select("h1,h2,h3,h4,h5,strong,b")
            label=headings[0].get_text(" ",strip=True) if headings else ""
            return txt,label
    return "", ""

def extract_html(url,html):
    soup=BeautifulSoup(html,"lxml")
    for t in soup(["script","style","noscript","svg"]):
        if t.get("type")!="application/ld+json":t.decompose()
    title=soup.title.get_text(" ",strip=True) if soup.title else ""
    text=re.sub(r"\s+"," ",soup.get_text(" ",strip=True))
    emails={}
    for e in EMAIL_RE.findall(deobfuscate(text)):
        emails[e.lower()]={"email":e.lower(),"context":"","display_name":""}
    # mailto plus richer ancestor context
    for a in soup.select('a[href^="mailto:"]'):
        e=a.get("href","")[7:].split("?")[0].strip().lower()
        if EMAIL_RE.fullmatch(e):
            ctx,label=_context_for_anchor(a)
            emails[e]={"email":e,"context":ctx,"display_name":label}
    people,orgs=_jsonld(BeautifulSoup(html,"lxml"))
    for p in people:
        e=(p.get("email") or "").lower()
        if EMAIL_RE.fullmatch(e):
            emails[e]={"email":e,"context":json.dumps(p,ensure_ascii=False),"display_name":p.get("name","")}
    phones=[]
    for p in PHONE_RE.findall(text):
        digits=re.sub(r"\D","",p)
        if 8<=len(digits)<=15 and p not in phones:phones.append(p)
    links=[]
    for a in soup.find_all("a",href=True):
        href=urljoin(url,a["href"])
        if root_domain(href)==root_domain(url):links.append(href)
    return {
        "url":url,"title":title[:500],"text":text[:150000],
        "emails":list(emails.values()),"phones":phones[:20],
        "people":people[:50],"organizations":orgs[:20],
        "links":list(dict.fromkeys(links))[:500]
    }

class WebCrawler:
    def __init__(self,db,net,settings):
        self.db=db; self.net=net; self.settings=settings

    async def _robots_allowed(self,url):
        p=urlparse(url); host=p.netloc.lower()
        txt=self.db.robots_get(host)
        if txt is None:
            robots=f"{p.scheme}://{p.netloc}/robots.txt"
            try:
                r=await self.net.get(robots,source="ROBOTS")
                txt=r.text if r.status_code==200 else ""
            except Exception:
                txt=""
            self.db.robots_set(host,txt,2)
        rp=urllib.robotparser.RobotFileParser()
        rp.set_url(f"{p.scheme}://{p.netloc}/robots.txt")
        rp.parse((txt or "").splitlines())
        try:return rp.can_fetch(self.settings.user_agent,url)
        except Exception:return True

    async def fetch(self,url):
        cached=self.db.page_cache_get(url)
        if cached is not None:return cached
        if not await self._robots_allowed(url):
            return {"url":url,"blocked":True,"emails":[],"phones":[],"people":[],"organizations":[],"links":[]}
        try:
            r=await self.net.get(url,source="COMPANY_WEB")
            if r.status_code!=200:
                return {"url":url,"status":r.status_code,"emails":[],"phones":[],"people":[],"organizations":[],"links":[]}
            ctype=(r.headers.get("content-type") or "").lower()
            if "text/html" not in ctype:
                return {"url":url,"status":200,"non_html":True,"emails":[],"phones":[],"people":[],"organizations":[],"links":[]}
            payload=extract_html(str(r.url),r.text[:2_000_000])
            self.db.page_cache_set(url,payload,self.settings.cache_days_page)
            return payload
        except Exception as e:
            return {"url":url,"error":str(e),"emails":[],"phones":[],"people":[],"organizations":[],"links":[]}

    async def sitemap_urls(self,website,limit=200):
        base=f"{urlparse(website).scheme or 'https'}://{urlparse(website).netloc or urlparse('https://'+website).netloc}"
        candidates=[urljoin(base,"/sitemap.xml"),urljoin(base,"/sitemap_index.xml")]
        out=[]
        for u in candidates:
            try:
                r=await self.net.get(u,source="SITEMAP")
                if r.status_code!=200:continue
                locs=re.findall(r"<loc>\s*(.*?)\s*</loc>",r.text,re.I)
                for x in locs:
                    low=x.lower()
                    if any(k in low for k in ("team","people","leadership","management","contact","contacto","directorio","nosotros","about","staff","executive")):
                        out.append(x)
            except Exception:continue
        return list(dict.fromkeys(out))[:limit]
