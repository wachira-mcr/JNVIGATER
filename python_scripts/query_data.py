import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT activity_code, account_code, package_discount_account, discount_code, right_code FROM apps.PYT_ACTIVITY_MAPPING_DTL WHERE hospital_id = 102 AND discount_code = '01120'")
        for row in cur:
            print(row)
