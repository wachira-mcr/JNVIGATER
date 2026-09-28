import re
import os

def parse_tns(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    entries = {}
    current_alias = None
    current_lines = []
    paren_depth = 0

    reserved_keywords = {'DESCRIPTION', 'DESCRIPTION_LIST', 'ADDRESS', 'ADDRESS_LIST', 'CONNECT_DATA', 'SERVER', 'SERVICE_NAME', 'SID', 'INSTANCE_NAME', 'FAILOVER_MODE', 'HS'}

    for line in content.splitlines():
        # Remove comments starting with #
        clean_line = re.sub(r'#.*$', '', line).strip()
        if not clean_line:
            continue

        # If at depth 0, check if this line starts a new alias
        if paren_depth == 0:
            m = re.match(r'^([A-Za-z0-9_\.\-\,]+)\s*=\s*(.*)', clean_line)
            if m:
                raw_alias = m.group(1).strip()
                if raw_alias.upper() not in reserved_keywords and not raw_alias.startswith('('):
                    if current_alias and current_lines:
                        entries[current_alias] = ' '.join(current_lines)
                    aliases = [a.strip() for a in raw_alias.split(',') if a.strip()]
                    current_alias = aliases[0] if aliases else raw_alias
                    current_lines = [m.group(2)] if m.group(2) else []
                    paren_depth += clean_line.count('(') - clean_line.count(')')
                    continue

        if current_alias:
            current_lines.append(clean_line)
            paren_depth += clean_line.count('(') - clean_line.count(')')
            if paren_depth < 0:
                paren_depth = 0

    if current_alias and current_lines:
        entries[current_alias] = ' '.join(current_lines)

    results = []
    for alias, conn_str in entries.items():
        host_m = re.search(r'HOST\s*=\s*([^\)\s]+)', conn_str, re.IGNORECASE)
        port_m = re.search(r'PORT\s*=\s*([^\)\s]+)', conn_str, re.IGNORECASE)
        svc_m = re.search(r'SERVICE_NAME\s*=\s*([^\)\s]+)', conn_str, re.IGNORECASE)
        sid_m = re.search(r'SID\s*=\s*([^\)\s]+)', conn_str, re.IGNORECASE)

        host = host_m.group(1) if host_m else ''
        port = port_m.group(1) if port_m else '1521'
        svc = svc_m.group(1) if svc_m else ''
        sid = sid_m.group(1) if sid_m else ''

        if host:
            results.append({
                'alias': alias,
                'host': host,
                'port': port,
                'service_name': svc,
                'sid': sid
            })
    return results

if __name__ == "__main__":
    tns_file = os.path.join(os.path.dirname(__file__), "db_config.env")
    res = parse_tns(tns_file)
    print(f"Found {len(res)} TNS aliases in file.")
    for r in res[:15]:
        print(r)

