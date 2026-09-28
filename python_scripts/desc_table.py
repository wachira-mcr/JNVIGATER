import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM all_tab_columns WHERE table_name = 'PYT_ACTIVITY_MAPPING_DTL' ORDER BY column_id")
        for row in cur:
            print(row[0])
