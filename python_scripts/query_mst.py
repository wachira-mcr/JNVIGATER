import oracledb

try:
    oracledb.init_oracle_client()
    conn = oracledb.connect(user="apps", password="pytapps", host="10.22.252.217", port=1531, service_name="TST")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT *
        FROM apps.mst_discount_code_type
        WHERE discount_code = '01120'
    """)
    for row in cursor:
        print(row)
    cursor.close()
    conn.close()
except Exception as e:
    print("Error:", e)
