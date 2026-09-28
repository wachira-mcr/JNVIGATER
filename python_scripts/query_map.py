import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT MAP_ITEM_CATE FROM apps.MAPPING_SOURCE_CATEGORY WHERE INTERFACE_SOURCE = 'GL' AND INTERFACE_CATEGORY = 'OPD Details'")
        row = cur.fetchone()
        if row:
            print("MAP_ITEM_CATE:", row[0])
        else:
            print("NO_DATA_FOUND")
