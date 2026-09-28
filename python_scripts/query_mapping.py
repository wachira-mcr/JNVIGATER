import oracledb

try:
    oracledb.init_oracle_client()
    conn = oracledb.connect(user="apps", password="pytapps", host="10.22.252.217", port=1531, service_name="TST")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT hospital_id, activity_type, discount_code, right_code, start_date_active, end_date_active, activity_source
        FROM PYT_ACTIVITY_MAPPING_DTL
        WHERE discount_code = '01120'
          AND right_code = '999'
          AND hospital_id = 102
          AND activity_type = 'OPD'
    """)
    for row in cursor:
        print(row)
    cursor.close()
    conn.close()
except Exception as e:
    print("Error:", e)
