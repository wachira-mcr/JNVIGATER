import os
import sys
import json
import oracledb
from parse_tns import parse_tns

try:
    oracledb.init_oracle_client()
except Exception:
    pass

def load_env(filepath):
    env_vars = {}
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
    return env_vars

def get_connection():
    scratch_dir = os.path.dirname(__file__)
    cred_file = os.path.join(scratch_dir, "credentials.env")
    tns_file = os.path.join(scratch_dir, "db_config.env")

    creds = load_env(cred_file)

    user = creds.get("ORACLE_USER", "")
    password = creds.get("ORACLE_PASSWORD", "")
    tns_name = creds.get("TNS_NAME", "")

    host = creds.get("ORACLE_HOST", "")
    port = int(creds.get("ORACLE_PORT", "1521")) if creds.get("ORACLE_PORT") else 1521
    service_name = creds.get("ORACLE_SERVICE_NAME", "")
    sid = creds.get("ORACLE_SID", "")

    if tns_name:
        # Resolve via parsed tnsnames
        tns_list = parse_tns(tns_file)
        matched = [t for t in tns_list if t["alias"].lower() == tns_name.lower()]
        if not matched:
            raise ValueError(f"Could not find TNS Alias '{tns_name}' in db_config.env (tnsnames.ora)")
        info = matched[0]
        host = info["host"]
        port = int(info["port"]) if info["port"] else 1521
        service_name = info["service_name"]
        sid = info["sid"]
        print(f"Connecting via TNS '{tns_name}' -> Host: {host}, Port: {port}, Service: {service_name}, SID: {sid}")

    if not user or not password:
        raise ValueError("ORACLE_USER and ORACLE_PASSWORD must be provided in credentials.env")

    if service_name:
        return oracledb.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            service_name=service_name
        )
    elif sid:
        return oracledb.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            sid=sid
        )
    else:
        raise ValueError("No Service Name or SID found for connection.")

def test_connection():
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT instance_name, host_name, version FROM v$instance")
            row = cursor.fetchone()
            print("\n==========================================")
            print(" SUCCESS: Connected to Oracle Database!")
            print(f" Instance Name: {row[0]}")
            print(f" Host Name:     {row[1]}")
            print(f" Oracle Ver:    {row[2]}")
            print("==========================================\n")
        conn.close()
    except Exception as e:
        print(f"\nERROR: {e}\n")

if __name__ == "__main__":
    test_connection()
