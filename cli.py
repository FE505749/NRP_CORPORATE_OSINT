import asyncio,typer
from pathlib import Path
from rich import print
from nrp_osint.config import load_tokens,Settings,tokens
from nrp_osint.db import DB
from nrp_osint.io_stream import ingest
from nrp_osint.historical import import_history,rebuild_knowledge
from nrp_osint.local_reuse import apply_local_reuse
from nrp_osint.pipeline import Pipeline
from nrp_osint.exporter import export_all
from nrp_osint.stats import counts
from nrp_osint import denue_local

app=typer.Typer(help='NRP Corporate OSINT V4')
def ctx():load_tokens();s=Settings.from_env();return s,DB(s.db_path)
def settings_only():load_tokens();return Settings.from_env()
@app.command()
def init():s,db=ctx();print('[green]Base preparada[/green]',s.db_path)
@app.command('ingest-main')
def ingest_main(path:str):s,db=ctx();print(ingest(db,path))
@app.command('ingest-history')
def ingest_history(path:str):s,db=ctx();print(import_history(db,path))
@app.command('local-match')
def local_match():s,db=ctx();print(apply_local_reuse(db,s))
@app.command()
def status():s,db=ctx();print(counts(db))
@app.command()
def run(max_jobs:int=0,batch:int=25,denue_local_only:bool=typer.Option(False,'--denue-local','-l',help='Solo la copia local de DENUE, sin Internet')):
    s,db=ctx();s.denue_local_only=s.denue_local_only or denue_local_only;pipe=Pipeline(db,s,tokens());print('[cyan]Iniciando enriquecimiento externo[/cyan]');n=asyncio.run(pipe.run(max_jobs=max_jobs,batch=batch,progress=lambda n,j,r:print(f'Empleadores procesados: {n}')));print('[green]Terminados[/green]',n)
@app.command('denue-update')
def denue_update(solo_carga:bool=typer.Option(False,help='No descargar; solo reconstruir desde los ZIP existentes')):
    """Descarga la descarga masiva de DENUE y construye data/denue/denue.sqlite."""
    s=settings_only();base=Path(s.denue_local_path).parent
    if solo_carga:print(denue_local.build(base,lambda i,n,name,total:print(f'carga {i}/{n} {name} - {total:,}')))
    else:print(denue_local.update(base,lambda fase,i,n,name:print(f'{fase} {i}/{n} {name}')))
@app.command('denue-buscar')
def denue_buscar(nombre:str,limite:int=5):
    """Prueba rapida contra la copia local de DENUE."""
    s=settings_only()
    for h in denue_local.DenueLocalSource(s.denue_local_path).search(nombre,limite):
        print(f"{h['score']:.2f} | {h['company']} | {h['trade_name']} | {h['domain'] or '-'} | {h['email'] or '-'} | {h['location']}")
@app.command()
def export(output_dir:str|None=None):s,db=ctx();print(export_all(db,output_dir or s.output_dir))
if __name__=='__main__':app()
