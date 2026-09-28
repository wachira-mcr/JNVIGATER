import oracledb
import os

oracledb.init_oracle_client()
creds = {}
with open("credentials.env", "r", encoding="utf-8") as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            creds[k] = v.strip()

user = creds.get("ORACLE_USER")
password = creds.get("ORACLE_PASSWORD")
host = creds.get("ORACLE_HOST")
port = int(creds.get("ORACLE_PORT", 1521))
sid = creds.get("ORACLE_SID")
svc = creds.get("ORACLE_SERVICE_NAME")

if svc:
    conn = oracledb.connect(user=user, password=password, host=host, port=port, service_name=svc)
else:
    conn = oracledb.connect(user=user, password=password, host=host, port=port, sid=sid)

cursor = conn.cursor()

# Query invoices matching PGY-180-010-07-26 to PGY-180-011-07-26 for ORG_ID = 141
sql = """
    SELECT 
        CT.TRX_NUMBER,
        CT.TRX_DATE,
        CT.ORG_ID,
        CT.ATTRIBUTE4 AS H_ATTRIBUTE4,
        CT.ATTRIBUTE6 AS H_ATTRIBUTE6,
        CT.ATTRIBUTE7 AS H_ATTRIBUTE7
    FROM RA_CUSTOMER_TRX_ALL CT
    WHERE CT.ORG_ID = 141
      AND CT.TRX_NUMBER BETWEEN 'PGY-180-010-07-26' AND 'PGY-180-011-07-26'
    ORDER BY CT.TRX_NUMBER
"""
cursor.execute(sql)
rows = cursor.fetchall()

print(f"Found {len(rows)} invoices for Org 141:")
for r in rows:
    print(r)

conn.close()
