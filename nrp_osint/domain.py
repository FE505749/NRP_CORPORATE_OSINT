import re
from urllib.parse import urlparse
from .paths import PROJECT_ROOT
try:
    import tldextract
    _TLD_CACHE = PROJECT_ROOT / 'data' / 'cache' / 'tldextract'
    _TLD_CACHE.mkdir(parents=True, exist_ok=True)
    _TLD=tldextract.TLDExtract(cache_dir=str(_TLD_CACHE),suffix_list_urls=None)
except ImportError:
    _TLD=None
try:
    import dns.resolver,dns.exception
except ImportError:
    dns=None
from rapidfuzz import fuzz
from .normalize import normalize_company,norm

BLOCKED={'linkedin.com','facebook.com','instagram.com','x.com','twitter.com','rocketreach.co','contactout.com','signalhire.com','wiza.co','zoominfo.com','theorg.com','marketscreener.com','crunchbase.com','indeed.com','glassdoor.com','wikipedia.org','ebay.com','prezi.com','pinterest.com'}

def root_domain(value):
    if not value:return ''
    v=value if '://' in value else 'https://'+value
    if _TLD is not None:
        ext=_TLD(v);return '.'.join(x for x in [ext.domain,ext.suffix] if x).lower()
    host=urlparse(v).netloc.lower().split(':')[0].removeprefix('www.');parts=host.split('.')
    if len(parts)<=2:return host
    two_level={'com.mx','org.mx','gob.mx','edu.mx','net.mx','co.uk','org.uk','com.br','com.au'}
    tail2='.'.join(parts[-2:])
    if tail2 in two_level and len(parts)>=3:return '.'.join(parts[-3:])
    return tail2

def normalize_website(v):
    v=(v or '').strip()
    if not v:return ''
    return v if re.match(r'^https?://',v,re.I) else 'https://'+v

def is_blocked_domain(domain):
    rd=root_domain(domain);return rd in BLOCKED or any(rd.endswith('.'+x) for x in BLOCKED)

def domain_company_score(domain,company,title='',snippet=''):
    rd=root_domain(domain)
    if not rd or is_blocked_domain(rd):return 0.
    stem=rd.split('.')[0].replace('-',' ');comp=normalize_company(company);text=normalize_company(f'{title} {snippet}')
    a=fuzz.WRatio(comp,norm(stem))/100 if comp else 0;b=fuzz.partial_ratio(comp,text)/100 if comp and text else 0
    return min(1.,.65*a+.35*b)

def mx_records(domain):
    rd=root_domain(domain)
    if not rd:return {'valid':False,'records':[],'reason':'empty_domain'}
    if dns is None:return {'valid':None,'records':[],'reason':'dnspython_not_installed'}
    try:
        ans=dns.resolver.resolve(rd,'MX',lifetime=4);rec=[str(x.exchange).rstrip('.') for x in ans];return {'valid':bool(rec),'records':rec,'reason':'ok'}
    except (dns.resolver.NXDOMAIN,dns.resolver.NoAnswer):return {'valid':False,'records':[],'reason':'no_mx'}
    except Exception as e:return {'valid':None,'records':[],'reason':'dns_error','error':str(e)[:250]}
