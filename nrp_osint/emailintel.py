import math, re, json
from collections import Counter
from .normalize import fold
from .domain import mail_domain

def token(x):
    return re.sub(r"[^a-z0-9]","",fold(x).lower())

def parts_from_display(name):
    return [token(x) for x in re.split(r"\s+",name or "") if token(x)]

def observed_forms(name):
    p=parts_from_display(name)
    if len(p)<2:return {}
    first=p[0]; last=p[-1]; paternal=p[-2] if len(p)>=3 else last
    given="".join(p[:-2]) if len(p)>=4 else first
    forms={
        "first.last":f"{first}.{last}",
        "f_last":f"{first[:1]}{last}",
        "f.last":f"{first[:1]}.{last}",
        "firstlast":f"{first}{last}",
        "last.first":f"{last}.{first}",
        "first.paternal":f"{first}.{paternal}",
        "f_paternal":f"{first[:1]}{paternal}",
        "f.paternal":f"{first[:1]}.{paternal}",
        "firstpaternal":f"{first}{paternal}",
        "first_paternal":f"{first}_{paternal}",
        "first-paternal":f"{first}-{paternal}",
        "first.paternalmaternal":f"{first}.{paternal}{last}" if paternal!=last else f"{first}.{last}",
        "given.paternal":f"{given}.{paternal}",
    }
    return forms

def infer_patterns(samples):
    votes=Counter(); total=0
    for s in samples:
        name=s.get("display_name","")
        email=s.get("email","")
        if not name or "@" not in email:continue
        local=email.split("@",1)[0].lower()
        forms=observed_forms(name)
        matches=[k for k,v in forms.items() if v==local]
        if matches:
            total+=1
            # Prefer semantically useful canonical patterns when several produce
            # the same local-part for a short name.
            priority=[
                "first.paternal","f_paternal","f.paternal","first_paternal",
                "first-paternal","first.paternalmaternal","given.paternal",
                "first.last","f_last","f.last","firstlast","last.first"
            ]
            chosen=next((k for k in priority if k in matches),matches[0])
            votes[chosen]+=1
    if not votes:return {},0.0
    raw=dict(votes)
    top=max(raw.values())
    dominance=top/max(1e-9,sum(raw.values()))
    # Two consistent samples ~= 76%, three ~= 84%, four+ approaches 92-96%.
    conf=0.45 + 0.15*dominance + 0.08*min(total,4)
    return raw,min(0.96,conf)

def generate(person,pattern,domain):
    first=token((person.get("first_names") or "").split()[0] if person.get("first_names") else "")
    given=token(person.get("first_names",""))
    pat=token(person.get("paternal",""))
    mat=token(person.get("maternal",""))
    last=mat or pat
    mapping={
        "first.last":f"{first}.{last}",
        "f_last":f"{first[:1]}{last}",
        "f.last":f"{first[:1]}.{last}",
        "firstlast":f"{first}{last}",
        "last.first":f"{last}.{first}",
        "first.paternal":f"{first}.{pat}",
        "f_paternal":f"{first[:1]}{pat}",
        "f.paternal":f"{first[:1]}.{pat}",
        "firstpaternal":f"{first}{pat}",
        "first_paternal":f"{first}_{pat}",
        "first-paternal":f"{first}-{pat}",
        "first.paternalmaternal":f"{first}.{pat}{mat}",
        "given.paternal":f"{given}.{pat}",
    }
    local=mapping.get(pattern,"")
    # El dominio va tal cual se observo despues de la @: no se recorta ni se inventa.
    dom=mail_domain(domain)
    return f"{local}@{dom}".lower() if local and dom else ""

def rank_emails(person,domain,patterns):
    total=sum(float(v) for v in patterns.values()) or 0
    out=[]
    for pat,count in sorted(patterns.items(),key=lambda x:x[1],reverse=True):
        email=generate(person,pat,domain)
        if email:
            out.append({"email":email,"pattern":pat,"pattern_share":float(count)/total if total else 0})
    # ensure sensible Mexican fallbacks exist, but with no implied confidence
    for pat in ("first.paternal","f_paternal","first.last","f_last","firstpaternal"):
        e=generate(person,pat,domain)
        if e and not any(x["email"]==e for x in out):
            out.append({"email":e,"pattern":pat,"pattern_share":0})
    return out[:8]

GENERIC_LOCALS = {
    "info", "informes", "informacion", "contacto", "contact", "ventas", "venta", "sales", "admin",
    "administracion", "facturacion", "factura", "facturas", "compras", "rh", "rrhh", "recursoshumanos",
    "recepcion", "gerencia", "direccion", "director", "atencion", "atencionaclientes", "clientes",
    "servicio", "soporte", "hola", "hello", "office", "oficina", "contabilidad", "cobranza", "mail",
    "correo", "noreply", "no-reply", "webmaster", "marketing", "pedidos", "reservaciones", "empleo",
}
_LOCAL_SHAPES = (
    # El guion admite apellidos compuestos: 'perla.uvon-borsted'.
    (re.compile(r"^[a-z]{2,}\.[a-z]{2,}(?:-[a-z]{2,})?$"), "first.paternal"),
    (re.compile(r"^[a-z]{2,}_[a-z]{2,}$"), "first_paternal"),
    (re.compile(r"^[a-z]{2,}-[a-z]{2,}$"), "first-paternal"),
    (re.compile(r"^[a-z]\.[a-z]{3,}$"), "f.paternal"),
)

# Apellidos y nombres frecuentes en Mexico. Solo sirven para reconocer correos de
# una sola palabra sin separador ('jmartinez', 'juanperez'); si no aparecen aqui,
# esos correos simplemente no se usan como muestra.
COMMON_SURNAMES = frozenset("""
hernandez garcia martinez lopez gonzalez perez rodriguez sanchez ramirez cruz flores gomez morales vazquez
reyes jimenez torres diaz gutierrez ruiz mendoza aguilar ortiz moreno castillo romero alvarez mendez chavez
rivera juarez ramos dominguez herrera medina castro vargas guzman velazquez munoz rojas contreras salazar
luna ortega santiago guerrero bautista estrada cortes delgado sandoval soto rios alvarado carrillo espinoza
silva navarro mejia salas leon cervantes campos vega cabrera nunez rosas marquez ibarra fuentes pacheco
avila figueroa molina valdez camacho trejo zamora acosta miranda escobar arellano galvan lara solis rangel
duran rosales villa robles cardenas orozco serrano maldonado beltran pena osorio calderon padilla montes
velasco meza valencia rubio enriquez gallegos franco cano andrade becerra cisneros zuniga barrera de leon
espinosa ayala ochoa macias villanueva arias benitez carmona olvera sosa bravo marin ponce montoya cuevas
paredes ventura lozano esquivel cordova tapia cuellar salinas vera guerra quintero vallejo sierra lugo
nava aguirre correa santos pineda corona bernal alonso hidalgo arroyo palacios reyna villarreal garza
trevino cavazos elizondo saldana tamez zapata guajardo alanis barragan carrasco cantu leal martin
""".split())
COMMON_FIRST_NAMES = frozenset("""
jose juan maria luis carlos jorge miguel francisco antonio jesus alejandro manuel pedro ricardo roberto
fernando javier david daniel eduardo rafael raul sergio arturo oscar mario hector victor enrique alberto
alfredo jaime gerardo ramon guillermo salvador ruben martin hugo ivan adrian pablo andres diego gabriel
rodrigo marco cesar armando ernesto ana laura patricia guadalupe rosa elena leticia martha claudia monica
veronica adriana alejandra gabriela silvia sandra lucia teresa carmen beatriz norma araceli diana karla
paola daniela andrea mariana fernanda sofia valeria jessica erika liliana susana yolanda angelica brenda
cristina gloria irma lorena maribel nancy rocio angel abraham emilio octavio rogelio saul felipe julio
evelyn isabel elizabeth josefina eugenia alicia margarita esther luz dolores concepcion pilar ines
jonathan cristian christian kevin omar alan ulises israel samuel joaquin agustin tomas nicolas lorenzo
aaron edgar oswaldo osvaldo alfonso alvaro arnulfo benjamin bernardo braulio bruno camilo cristobal
damian dario efrain eliseo elias erick erik esteban eugenio ezequiel fabian federico fidel gilberto
gonzalo gregorio gustavo heriberto horacio humberto ignacio isaac jacobo jairo jeronimo joel jonas
jordi josue julian leonardo leopoldo lucas marcos mauricio maximiliano moises nestor noe orlando
pascual patricio rene reynaldo reinaldo rigoberto santiago sebastian silvestre teodoro valentin
vicente wilfrido xavier abel adolfo alonso amado anselmo apolinar aurelio baltazar candelario ciro
crispin edmundo eloy emanuel emmanuel ernesto everardo faustino fermin filiberto florencio genaro
german gerson hilario hiram isidro jacinto jaciel juventino lazaro leobardo lino macario marcelo
margarito melquiades misael nazario nemesio pablo porfirio quirino ramiro raymundo refugio rolando
romeo rosendo rosalio rufino samuel severiano simon tadeo tiburcio tomas ubaldo uriel urbano
abigail adela adelina alba alejandrina alma amalia amparo angela angeles antonia araceli aurora
azucena barbara blanca carina carla carolina catalina cecilia celia citlali clara consuelo
cynthia dalia debora delia denisse dulce edith elsa elvira emma estela estefania eva fabiola
flor francisca gisela graciela griselda hilda iris itzel ivonne jacqueline janet jazmin joana
johana josefa juana judith julia julieta karen karina leonor lidia lilia lizbeth lourdes luisa
magdalena maira marcela maricela marisol marlene matilde mayra mercedes miriam mireya myriam
natalia nayeli nelly noemi olga olivia pamela perla priscila raquel rebeca regina reyna rosalba
rosario ruth sara selene sonia tania tatiana virginia viviana ximena yadira yesenia zaira
""".split()) - {"alba", "flor", "luz"}  # tambien forman nombres de una pieza: rosalba, marisol...

def pattern_from_local_part(local, allow_single=False):
    """Patron probable de un correo cuando no se conoce el nombre de la persona.
    Formas con separador son inequivocas. Las de una sola palabra solo se aceptan
    con allow_single y si el resto es un apellido conocido ('jmartinez')."""
    local = (local or "").lower().strip()
    if not local or any(part in GENERIC_LOCALS for part in re.split(r"[._-]", local)):
        return ""
    for rx, pat in _LOCAL_SHAPES:
        if rx.match(local):
            # 'victorhugo.olivares': dos nombres juntos antes del punto.
            if pat == "first.paternal" and _dos_nombres(local.split(".", 1)[0]):
                return "given.paternal"
            return pat
    if allow_single and re.fullmatch(r"[a-z]{4,}", local):
        if local[1:] in COMMON_SURNAMES:
            return "f_paternal"
        for i in range(3, len(local) - 3):
            if local[:i] in COMMON_FIRST_NAMES and local[i:] in COMMON_SURNAMES:
                return "firstpaternal"
    return ""

_ENLACES = ("delos", "delas", "dela", "del", "de", "")  # mariadelcarmen, mariadelosangeles

def _dos_nombres(x):
    for i in range(3, len(x) - 2):
        if x[:i] in COMMON_FIRST_NAMES:
            resto = x[i:]
            if any(resto.startswith(e) and resto[len(e):] in COMMON_FIRST_NAMES for e in _ENLACES):
                return True
    return False

def nombres_juntos(votes, minimo=3, proporcion=0.2):
    """Decide si la empresa escribe TODOS los nombres juntos (victorhugo.olivares)
    o solo el primero (victor.olivares).

    Sin el nombre de la persona, 'juan.perez' puede ser de cualquiera de las dos
    reglas: una persona con un solo nombre se ve igual. Pero con la regla del
    primer nombre nunca aparecerian dos nombres juntos; si aparecen varios, la
    regla es la de todos los nombres y los 'juan.perez' son gente de un nombre.
    Con uno o dos casos sueltos se tratan como excepciones (alguien conocido por
    su nombre compuesto) y cuentan como nombre.apellido."""
    g, f = votes.get("given.paternal", 0), votes.get("first.paternal", 0)
    if not g:
        return dict(votes)
    v = {k: n for k, n in votes.items() if k not in ("given.paternal", "first.paternal")}
    if g >= minimo and g / (g + f) >= proporcion:
        v["given.paternal"] = g + f
    else:
        v["first.paternal"] = g + f
    return v

def weak_pattern_confidence(votes, cap=0.66):
    """Confianza para patrones vistos en correos sin nombre (DENUE, Tukan).
    Nunca alcanza el umbral de un patron aprendido con nombre y correo (0.72)."""
    total = sum(votes.values())
    if not total:
        return 0.0
    dominance = max(votes.values()) / total
    return round(min(cap, (0.48 + 0.05 * min(total, 4)) * dominance), 3)

def composed_confidence(company_conf,domain_conf,pattern_conf,mx_valid):
    # Weighted geometric composition. It avoids pretending the stages are independent,
    # while ensuring a weak company/domain stage limits the final confidence.
    vals=[
        (max(.01,company_conf),.40),
        (max(.01,domain_conf),.25),
        (max(.01,pattern_conf),.35),
    ]
    score=1.0
    for v,w in vals:score*=v**w
    if mx_valid is False:score*=0.65
    return round(max(0,min(1,score))*100,1)
