import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT reference30, count(*) FROM apps.XXPL_SSBGL_ITF_OUT_V WHERE rownum <= 100 GROUP BY reference30")
        for row in cur:
            print(row)
