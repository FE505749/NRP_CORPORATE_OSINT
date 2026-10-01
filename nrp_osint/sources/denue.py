from urllib.parse import quote
from rapidfuzz import fuzz
from ..normalize import normalize_company, norm

ENTITY_CODES = {
    "AGUASCALIENTES":"01","BAJA CALIFORNIA":"02","BAJA CALIFORNIA SUR":"03",
    "CAMPECHE":"04","COAHUILA":"05","COLIMA":"06","CHIAPAS":"07","CHIHUAHUA":"08",
    "CIUDAD DE MEXICO":"09","CDMX":"09","DISTRITO FEDERAL":"09","DURANGO":"10",
    "GUANAJUATO":"11","GUERRERO":"12","HIDALGO":"13","JALISCO":"14","MEXICO":"15",
    "ESTADO DE MEXICO":"15","MICHOACAN":"16","MORELOS":"17","NAYARIT":"18",
    "NUEVO LEON":"19","OAXACA":"20","PUEBLA":"21","QUERETARO":"22",
    "QUINTANA ROO":"23","SAN LUIS POTOSI":"24","SINALOA":"25","SONORA":"26",
    "TABASCO":"27","TAMAULIPAS":"28","TLAXCALA":"29","VERACRUZ":"30",
    "YUCATAN":"31","ZACATECAS":"32"
}

class DenueSource:
    name="DENUE"
    def __init__(self,net,token):
        self.net=net; self.token=token

    async def company(self,name,entity="",limit=20):
        if not self.token or not name:
            return []
        ent=ENTITY_CODES.get(norm(entity),"00")
        url=(
            "https://www.inegi.org.mx/app/api/denue/v1/consulta/Nombre/"
            f"{quote(name,safe='')}/{ent}/1/{limit}/{self.token}"
        )
        r=await self.net.get(url,source="DENUE")
        if r.status_code==429:
            raise RuntimeError("DENUE_THROTTLED")
        r.raise_for_status()
        data=r.json()
        target=normalize_company(name)
        out=[]
        for x in data if isinstance(data,list) else []:
            candidate=x.get("Razon_social") or x.get("Nombre") or ""
            score=max(
                fuzz.WRatio(target,normalize_company(candidate)),
                fuzz.token_set_ratio(target,normalize_company(candidate))
            )/100
            out.append({
                "company":candidate,
                "trade_name":x.get("Nombre",""),
                "website":(x.get("Sitio_internet") or "").strip(),
                "email":(x.get("Correo_e") or "").strip(),
                "phone":(x.get("Telefono") or "").strip(),
                "location":x.get("Ubicacion","") or x.get("Entidad",""),
                "score":score,
                "url":url,
                "source":"DENUE",
            })
        return sorted(out,key=lambda x:x["score"],reverse=True)
