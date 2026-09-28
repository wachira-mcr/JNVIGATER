import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT account_code, package_discount_account, description FROM apps.PYT_ACTIVITY_MAPPING_DTL WHERE hospital_id = 102 AND discount_code = '02010' AND activity_type = 'OPD'")
        for row in cur:
            print(row)
