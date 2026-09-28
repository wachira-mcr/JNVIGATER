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

cursor.execute("SELECT count(*) FROM user_tables")
tbl_count = cursor.fetchone()[0]

cursor.execute("SELECT count(*) FROM user_views")
view_count = cursor.fetchone()[0]

cursor.execute("SELECT count(*) FROM user_objects WHERE object_type LIKE '%PACKAGE%'")
pkg_count = cursor.fetchone()[0]

cursor.execute("SELECT table_name FROM user_tables WHERE rownum <= 5")
sample_tables = [r[0] for r in cursor.fetchall()]

print(f"AI Direct DB Access Verification:")
print(f"Connected User: {user}")
print(f"Total User Tables: {tbl_count}")
print(f"Total User Views:  {view_count}")
print(f"Total Packages:    {pkg_count}")
print(f"Sample Tables:     {sample_tables}")

conn.close()
