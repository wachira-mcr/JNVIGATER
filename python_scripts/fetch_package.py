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

# Search for PTAR_GOLD_INVOICE_PKG in user_source or all_source
sql = """
    SELECT type, name, line, text
    FROM all_source
    WHERE UPPER(name) = 'PTAR_GOLD_INVOICE_PKG'
    ORDER BY type, line
"""
cursor.execute(sql)
rows = cursor.fetchall()

print(f"Total lines found for PTAR_GOLD_INVOICE_PKG: {len(rows)}")

pkg_spec = []
pkg_body = []
for type_, name_, line_, text_ in rows:
    if type_ == "PACKAGE":
        pkg_spec.append(text_)
    elif type_ == "PACKAGE BODY":
        pkg_body.append(text_)

print(f"Spec lines: {len(pkg_spec)}, Body lines: {len(pkg_body)}")

# Save to local file so we can view and analyze
with open("PTAR_GOLD_INVOICE_PKG_spec.pls", "w", encoding="utf-8") as f:
    f.writelines(pkg_spec)

with open("PTAR_GOLD_INVOICE_PKG_body.pls", "w", encoding="utf-8") as f:
    f.writelines(pkg_body)

print("Saved Package Spec and Body locally.")
conn.close()
