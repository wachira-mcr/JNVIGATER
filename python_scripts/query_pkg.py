import oracledb

try:
    oracledb.init_oracle_client()
    conn = oracledb.connect(user="apps", password="pytapps", host="10.22.252.217", port=1531, service_name="TST")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT text
        FROM all_source
        WHERE name = 'PYT_GL_ACCOUNT_MAPPING_PKG'
          AND type = 'PACKAGE BODY'
        ORDER BY line
    """)
    with open("pkg_body.txt", "w") as f:
        for row in cursor:
            f.write(row[0])
    cursor.close()
    conn.close()
except Exception as e:
    print("Error:", e)
