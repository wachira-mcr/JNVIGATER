import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT discount_code, discount_type FROM apps.mst_discount_code_type WHERE discount_code = '02010' and hospital_code='102'")
        row = cur.fetchone()
        if row:
            print("TYPE:", row[1])
        else:
            print("NO_DATA_FOUND")
