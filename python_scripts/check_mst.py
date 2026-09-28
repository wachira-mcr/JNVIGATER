import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM apps.mst_discount_code_type WHERE discount_code = '01120'")
        print('Count:', cur.fetchone()[0])
