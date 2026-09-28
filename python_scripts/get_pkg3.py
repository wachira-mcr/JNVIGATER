import oracledb
oracledb.init_oracle_client()
with oracledb.connect(user='apps', password='pytapps', host='10.22.252.217', port=1531, service_name='TST') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM all_source WHERE name = 'PYT_GL_REP_INTF_SSB' AND type = 'PACKAGE BODY' ORDER BY line")
        with open('pkg3_body.txt', 'w', encoding='utf-8') as f:
            for row in cur:
                f.write(row[0])
