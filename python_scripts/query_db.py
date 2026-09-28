import oracledb

try:
    oracledb.init_oracle_client()
    conn = oracledb.connect(user="apps", password="pytapps", host="10.22.252.217", port=1531, service_name="TST")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT intflag, COUNT(*)
        FROM integration.HISPLC_ERPGL_ITF_OUT@lnkerp
        WHERE trunc(accounting_date) BETWEEN TO_DATE('2026-07-01', 'YYYY-MM-DD') AND TO_DATE('2026-07-31', 'YYYY-MM-DD')
        GROUP BY intflag
    """)
    for row in cursor:
        print("Intflag:", row[0], "Count:", row[1])
    cursor.close()
    conn.close()
except Exception as e:
    print("Error:", e)
