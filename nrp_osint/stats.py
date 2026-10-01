def counts(db):
    q=lambda s:db.conn.execute(s).fetchone()[0]
    return {
      'personas':q('SELECT COUNT(*) FROM people'),
      'cuentas_unicas':q("SELECT COUNT(DISTINCT account_id) FROM people WHERE account_id<>''"),
      'nrp_unicos':q("SELECT COUNT(DISTINCT nrp) FROM people WHERE nrp<>''"),
      'empresas_unicas':q('SELECT COUNT(*) FROM company_profiles'),
      'historicos':q('SELECT COUNT(*) FROM historical_contacts'),
      'contactos_resueltos':q("SELECT COUNT(*) FROM results WHERE email<>''"),
      'resueltos_id_cuenta':q("SELECT COUNT(*) FROM results WHERE email_type='HISTORICO_ID_CUENTA'"),
      'jobs_pendientes':q("SELECT COUNT(*) FROM jobs WHERE status IN ('PENDING','RETRY')"),
      'jobs_done':q("SELECT COUNT(*) FROM jobs WHERE status IN ('DONE','DONE_LOCAL')"),
      'conflictos':q("SELECT COUNT(*) FROM employers WHERE status='CONFLICTO_NRP_EMPRESA'"),
    }
