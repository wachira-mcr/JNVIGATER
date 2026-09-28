import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), activity_code FROM apps.PYT_ACTIVITY_MAPPING_DTL GROUP BY activity_code")
        for row in cur:
            print(row)
