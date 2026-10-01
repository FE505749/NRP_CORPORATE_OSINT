# Compatibilidad con versiones anteriores: un "seed" ahora se importa como histórico.
from .historical import import_history

def import_seed(db,path):
    return import_history(db,path)
