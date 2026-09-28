import os
import json
import re
import sys
import threading
import oracledb
from flask import Flask, jsonify, request, render_template_string, send_file, Response, stream_with_context
import zlib
import webview
if getattr(sys, 'frozen', False):
    sys.path.append(os.path.join(os.path.dirname(sys.executable), 'python_scripts'))
else:
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'python_scripts'))

from parse_tns import parse_tns

try:
    from google import genai
except ImportError as e:
    print(f"Warning: google.genai module not found. AI features may not work: {e}")

try:
    import paramiko
except ImportError as e:
    print(f"Warning: paramiko module not found. SFTP features may not work: {e}")

try:
    oracledb.init_oracle_client()
    print("Oracle Thick Mode initialized successfully.")
except Exception as e:
    print(f"Thick Mode notice: {e}")

oracledb.defaults.fetch_lobs = False

app = Flask(__name__)

from werkzeug.exceptions import HTTPException
import traceback

@app.errorhandler(Exception)
def handle_exception(e):
    if request.path.startswith('/api/'):
        if isinstance(e, HTTPException):
            return jsonify({"success": False, "error": f"HTTP Error {e.code}: {e.description}"}), e.code
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Internal Server Error: {str(e)}"}), 500
    if isinstance(e, HTTPException):
        return e
    return str(e), 500

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# Active DB connection state & Multi-DB session pool
active_session_key = None
db_sessions = {} # { "ALIAS_USER": conn_info }

if getattr(sys, 'frozen', False):
    SAVED_PROFILES_FILE = os.path.join(os.path.dirname(sys.executable), "saved_profiles.json")
else:
    SAVED_PROFILES_FILE = os.path.join(os.path.dirname(__file__), "saved_profiles.json")

def load_saved_profiles():
    if os.path.exists(SAVED_PROFILES_FILE):
        try:
            with open(SAVED_PROFILES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"profiles": {}, "ai_key": "", "last_used_alias": ""}

def save_profiles(data):
    try:
        with open(SAVED_PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving profiles: {e}")

if getattr(sys, 'frozen', False):
    SFTP_PROFILES_FILE = os.path.join(os.path.dirname(sys.executable), "sftp_profiles.json")
else:
    SFTP_PROFILES_FILE = os.path.join(os.path.dirname(__file__), "sftp_profiles.json")

def load_sftp_profiles():
    if os.path.exists(SFTP_PROFILES_FILE):
        try:
            with open(SFTP_PROFILES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def get_sftp_credentials(host, username_hint=None):
    global active_session_key, db_sessions
    env = ""
    tns_alias = db_sessions.get(active_session_key, {}).get('alias', '').lower() if active_session_key else ''
    if not tns_alias and active_session_key and '@' in active_session_key:
        tns_alias = active_session_key.split('@')[-1].lower()
    
    import re
    nums = re.findall(r'\d{4}', tns_alias) if tns_alias else []
    if tns_alias:
        for e in ['8060', 'uat1', 'tst', 'uat', 'dev', 'prod', 'pre', 'test', 'vis', 'bg', 'pgy']:
            if e in tns_alias:
                env = e
                break

    profiles = load_sftp_profiles()
    if host in profiles:
        cached_user = profiles[host].get("username", "").lower()
        if username_hint:
            if cached_user == username_hint.lower(): return profiles[host]
        elif env and (env in cached_user or (nums and any(num in cached_user for num in nums))):
            return profiles[host]
        elif not env and not nums:
            return profiles[host]
        
    xml_candidates = [
        r"C:\Users\MBx13\Downloads\FileZilla.xml",
        r"C:\Users\MBx13\Desktop\FileZilla.xml"
    ]
    
    import xml.etree.ElementTree as ET
    import base64
    
    servers_found = []
    for fz_xml in xml_candidates:
        if not os.path.exists(fz_xml):
            continue
        try:
            tree = ET.parse(fz_xml)
            root = tree.getroot()
            for server in root.findall(".//Server"):
                host_elem = server.find("Host")
                if host_elem is not None and host_elem.text and host_elem.text.strip() == host.strip():
                    user_elem = server.find("User")
                    pass_elem = server.find("Pass")
                    name_elem = server.find("Name")
                    
                    if user_elem is not None and pass_elem is not None and user_elem.text and pass_elem.text:
                        username = user_elem.text.strip()
                        password_encoded = pass_elem.text.strip()
                        if pass_elem.attrib.get("encoding") == "base64":
                            password = base64.b64decode(password_encoded).decode('utf-8', errors='ignore')
                        else:
                            password = password_encoded
                            
                        entry_name = name_elem.text.strip() if (name_elem is not None and name_elem.text) else ""
                        servers_found.append({"username": username, "password": password, "name": entry_name})
        except Exception as e:
            print(f"Error parsing FileZilla xml {fz_xml}: {e}")

    if not servers_found:
        return profiles.get(host)

    # 1. Exact username hint match
    if username_hint:
        for s in servers_found:
            if s["username"].lower() == username_hint.lower():
                creds = {"username": s["username"], "password": s["password"]}
                profiles[host] = creds
                save_sftp_profiles(profiles)
                return creds

    # 2. Match by 4-digit port/number in entry name
    if nums:
        for s in servers_found:
            if any(num in s["name"].lower() for num in nums):
                if env and (env in s["username"].lower() or env in s["name"].lower()):
                    creds = {"username": s["username"], "password": s["password"]}
                    profiles[host] = creds
                    save_sftp_profiles(profiles)
                    return creds
        for s in servers_found:
            if any(num in s["name"].lower() for num in nums):
                creds = {"username": s["username"], "password": s["password"]}
                profiles[host] = creds
                save_sftp_profiles(profiles)
                return creds

    # 3. Match by environment keyword
    if env:
        for s in servers_found:
            u_l = s["username"].lower()
            n_l = s["name"].lower()
            if (env == '8060' or env == 'uat1') and ('uat1' in u_l or '8060' in n_l):
                creds = {"username": s["username"], "password": s["password"]}
                profiles[host] = creds
                save_sftp_profiles(profiles)
                return creds
            if env in ['tst', 'test'] and ('tst' in u_l or 'test' in u_l or env in n_l):
                creds = {"username": s["username"], "password": s["password"]}
                profiles[host] = creds
                save_sftp_profiles(profiles)
                return creds
            if env == 'prod' and ('prod' in u_l or 'prod' in n_l):
                creds = {"username": s["username"], "password": s["password"]}
                profiles[host] = creds
                save_sftp_profiles(profiles)
                return creds
            if env == 'dev' and ('dev' in u_l or 'dev' in n_l):
                creds = {"username": s["username"], "password": s["password"]}
                profiles[host] = creds
                save_sftp_profiles(profiles)
                return creds
            if env == 'pre' and ('pre' in u_l or 'pre' in n_l):
                creds = {"username": s["username"], "password": s["password"]}
                profiles[host] = creds
                save_sftp_profiles(profiles)
                return creds
            if env == 'uat' and ('uat' in u_l and 'uat1' not in u_l):
                creds = {"username": s["username"], "password": s["password"]}
                profiles[host] = creds
                save_sftp_profiles(profiles)
                return creds

    # 4. Fallback to first found server for this host
    best = servers_found[0]
    creds = {"username": best["username"], "password": best["password"]}
    profiles[host] = creds
    save_sftp_profiles(profiles)
    return creds

def save_sftp_profiles(data):
    try:
        with open(SFTP_PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving SFTP profiles: {e}")

def get_tns_file():
    candidates = []

    try:
        prof = load_saved_profiles()
        if prof.get("custom_tns_path") and os.path.exists(prof["custom_tns_path"]):
            candidates.append(prof["custom_tns_path"])
    except Exception:
        pass

    if hasattr(sys, '_MEIPASS'):
        candidates.append(os.path.join(sys._MEIPASS, "db_config.env"))
        candidates.append(os.path.join(sys._MEIPASS, "tnsnames.ora"))

    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
    candidates.append(os.path.join(exe_dir, "db_config.env"))
    candidates.append(os.path.join(exe_dir, "tnsnames.ora"))
    candidates.append(os.path.join(os.path.dirname(exe_dir), "db_config.env"))
    candidates.append(os.path.join(os.path.dirname(exe_dir), "tnsnames.ora"))
    candidates.append(os.path.join(os.getcwd(), "db_config.env"))
    candidates.append(os.path.join(os.getcwd(), "tnsnames.ora"))
    candidates.append(os.path.join(os.path.dirname(__file__), "db_config.env"))
    candidates.append(os.path.join(os.path.dirname(__file__), "tnsnames.ora"))
    candidates.append(r"C:\Users\MBx13\.gemini\antigravity\scratch\db_config.env")
    candidates.append(r"C:\Users\MBx13\.gemini\antigravity\scratch\tnsnames.ora")

    tns_admin = os.environ.get("TNS_ADMIN")
    if tns_admin:
        candidates.append(os.path.join(tns_admin, "tnsnames.ora"))
        candidates.append(os.path.join(tns_admin, "db_config.env"))

    oracle_home = os.environ.get("ORACLE_HOME")
    if oracle_home:
        candidates.append(os.path.join(oracle_home, "network", "admin", "tnsnames.ora"))

    candidates.append(r"C:\DevSuiteHome_1\network\admin\tnsnames.ora")
    candidates.append(r"C:\oracle\product\11.2.0\client_1\network\admin\tnsnames.ora")
    candidates.append(r"C:\app\client\product\12.2.0\client_1\network\admin\tnsnames.ora")

    for path in candidates:
        if path and os.path.exists(path):
            print(f"[TNS Resolution] Found valid TNS configuration file at: {path}")
            return path

    fallback = os.path.join(exe_dir, "db_config.env")
    print(f"[TNS Resolution] Warning: No candidate file found. Defaulting to: {fallback}")
    return fallback


def _native_save_dialog(default_filename, initial_dir=None):
    """Open a native Windows Save As dialog using ctypes (GetSaveFileNameW). Instant, no flashing, thread-safe."""
    import subprocess
    
    ps_script = f"""
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.SaveFileDialog
    $dlg.Filter = 'All Files (*.*)|*.*'
    $dlg.FileName = '{default_filename}'
    """
    if initial_dir:
        ps_script += f"\n    $dlg.InitialDirectory = '{initial_dir}'"
        
    ps_script += """
    $dlg.Title = 'Save As'
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        Write-Output $dlg.FileName
    }
    """
    
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_script],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        out = result.stdout.strip()
        if out:
            return out
    except Exception as e:
        print("Save dialog error:", e)
    return None


class DesktopApi:
    """Desktop API exposed to PyWebView JS for spawning new native windows and accessing local FS"""
    def open_new_window(self, name, obj_type):
        import webview
        url = f"http://127.0.0.1:5055/standalone-editor?name={name}&type={obj_type}"
        webview.create_window(
            f"PL/SQL Editor - {name} ({obj_type})",
            url,
            width=1200,
            height=820,
            resizable=True
        )

    def export_to_file(self, sql, bind_vars_str, format_type, save_path, row_limit_str, table_name):
        """Export query results directly to file. Bypasses Flask entirely."""
        import csv, json
        from io import StringIO
        try:
            bind_vars = json.loads(bind_vars_str) if isinstance(bind_vars_str, str) else (bind_vars_str or {})
        except:
            bind_vars = {}
        
        row_limit = row_limit_str
        if row_limit != 'ALL':
            try: row_limit = int(row_limit)
            except: row_limit = 500

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.arraysize = 50000
            cursor.prefetchrows = 50000
            
            sql = sql.strip().rstrip(';')
            exec_sql = sql
            if exec_sql.upper().startswith('SELECT') and '/*+' not in exec_sql:
                exec_sql = re.sub(r'^(SELECT\s+)', r'\1/*+ PARALLEL */ ', exec_sql, flags=re.IGNORECASE)
            
            if bind_vars:
                cursor.execute(exec_sql, **bind_vars)
            else:
                cursor.execute(exec_sql)
            
            if not cursor.description:
                return json.dumps({'success': False, 'error': 'Not a SELECT statement'})
            
            columns = [desc[0] for desc in cursor.description]
            total_rows = 0
            fmt = format_type.upper()
            
            if fmt == 'EXCEL':
                import xlsxwriter, tempfile, os, shutil
                fd, temp_path = tempfile.mkstemp(suffix='.xlsx')
                os.close(fd)
                workbook = xlsxwriter.Workbook(temp_path, {'constant_memory': True})
                sheet_idx = 1
                ws = workbook.add_worksheet(f'Sheet {sheet_idx}')
                ws.write_row(0, 0, columns)
                row_idx = 1
                while True:
                    rows = cursor.fetchmany(10000)
                    if not rows: break
                    for row in rows:
                        if row_limit != 'ALL' and total_rows >= row_limit: break
                        if row_idx >= 1048500:
                            sheet_idx += 1
                            ws = workbook.add_worksheet(f'Sheet {sheet_idx}')
                            ws.write_row(0, 0, columns)
                            row_idx = 1
                        ws.write_row(row_idx, 0, [v if v is None or isinstance(v, (int, float, str)) else str(v) for v in row])
                        row_idx += 1
                        total_rows += 1
                    if row_limit != 'ALL' and total_rows >= row_limit: break
                workbook.close()
                shutil.move(temp_path, save_path)
            elif fmt == 'JSON':
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write('[\n')
                    first = True
                    while True:
                        rows = cursor.fetchmany(10000)
                        if not rows: break
                        for row in rows:
                            if row_limit != 'ALL' and total_rows >= row_limit: break
                            row_dict = dict(zip(columns, [v if v is None or isinstance(v, (int, float, str)) else str(v) for v in row]))
                            prefix = '' if first else ',\n'
                            f.write(prefix + json.dumps(row_dict, default=str))
                            first = False
                            total_rows += 1
                        if row_limit != 'ALL' and total_rows >= row_limit: break
                    f.write('\n]')
            else:
                # CSV, TXT, SQL_INSERT
                with open(save_path, 'w', encoding='utf-8', newline='') as f:
                    if fmt == 'SQL_INSERT':
                        while True:
                            rows = cursor.fetchmany(5000)
                            if not rows: break
                            for row in rows:
                                if row_limit != 'ALL' and total_rows >= row_limit: break
                                vals = []
                                for v in row:
                                    if v is None: vals.append('NULL')
                                    elif isinstance(v, (int, float)): vals.append(str(v))
                                    else: vals.append("'" + str(v).replace("'", "''") + "'")
                                f.write(f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(vals)});\n")
                                total_rows += 1
                            if row_limit != 'ALL' and total_rows >= row_limit: break
                    else:
                        delimiter = '\t' if fmt == 'TXT' else ','
                        writer = csv.writer(f, delimiter=delimiter)
                        if fmt == 'CSV': f.write('\ufeff')  # BOM
                        writer.writerow(columns)
                        while True:
                            rows = cursor.fetchmany(10000)
                            if not rows: break
                            for row in rows:
                                if row_limit != 'ALL' and total_rows >= row_limit: break
                                writer.writerow([str(v) if v is not None else '' for v in row])
                                total_rows += 1
                            if row_limit != 'ALL' and total_rows >= row_limit: break
            
            cursor.close()
            conn.close()
            return json.dumps({'success': True, 'rows': total_rows, 'path': save_path})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({'success': False, 'error': str(e)})

    def save_backup(self, default_filename, content):
        """Save backup file using native Save As dialog. Bypasses Flask entirely."""
        import json
        try:
            path = _native_save_dialog(default_filename)
            if not path:
                return json.dumps({'success': False, 'error': 'Cancelled'})
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return json.dumps({'success': True, 'path': path})
        except Exception as e:
            return json.dumps({'success': False, 'error': str(e)})


desktop_api = DesktopApi()

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/logo.jpg")
def serve_logo():
    candidates = []
    if hasattr(sys, '_MEIPASS'):
        candidates.append(os.path.join(sys._MEIPASS, "logo.jpg"))
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
    candidates.append(os.path.join(exe_dir, "logo.jpg"))
    candidates.append(os.path.join(os.path.dirname(__file__), "logo.jpg"))
    candidates.append(r"C:\Users\MBx13\.gemini\antigravity\brain\6e857d49-1059-4580-8e78-d08c27834f27\jnavigator_logo_1786432301986.jpg")
    for path in candidates:
        if os.path.exists(path):
            return send_file(path, mimetype='image/jpeg')
    return "", 404

@app.route("/standalone-editor")
def standalone_editor():
    name = request.args.get("name", "").strip().upper()
    obj_type = request.args.get("type", "PACKAGE BODY").strip().upper()
    return render_template_string(STANDALONE_EDITOR_TEMPLATE, name=name, obj_type=obj_type)

@app.route("/api/profiles", methods=["GET", "POST"])
def manage_profiles():
    if request.method == "POST":
        data = request.json or {}
        profiles_data = load_saved_profiles()
        
        alias = data.get("alias")
        user = data.get("user")
        password = data.get("password")
        ai_key = data.get("ai_key")

        if alias and user and password:
            key = f"{user}@{alias}".upper()
            profiles_data["profiles"][key] = {
                "alias": alias,
                "user": user,
                "password": password,
                "host": data.get("host"),
                "port": data.get("port"),
                "service_name": data.get("service_name"),
                "sid": data.get("sid")
            }
            profiles_data["last_used_alias"] = alias
            
        if ai_key is not None:
            profiles_data["ai_key"] = ai_key

        save_profiles(profiles_data)
        return jsonify({"success": True, "profiles": profiles_data["profiles"], "ai_key": profiles_data.get("ai_key")})
    else:
        profiles_data = load_saved_profiles()
        return jsonify({"success": True, "profiles": profiles_data["profiles"], "ai_key": profiles_data.get("ai_key"), "last_used_alias": profiles_data.get("last_used_alias")})

@app.route("/api/sessions", methods=["GET", "POST"])
def manage_sessions():
    global active_session_key, db_sessions
    if request.method == "POST":
        data = request.json or {}
        key = data.get("session_key")
        if key in db_sessions:
            active_session_key = key
            return jsonify({"success": True, "active_session": key, "session_info": db_sessions[key]})
        return jsonify({"success": False, "error": "Session key not found"}), 404
    else:
        sessions_list = []
        for k, v in db_sessions.items():
            sessions_list.append({
                "session_key": k,
                "user": v["user"],
                "alias": v["alias"],
                "is_active": (k == active_session_key)
            })
        return jsonify({"success": True, "active_session": active_session_key, "sessions": sessions_list})

@app.route("/api/tns-list", methods=["GET"])
def get_tns_list():
    tns_file = get_tns_file()
    if not os.path.exists(tns_file):
        return jsonify({"success": False, "error": f"TNS configuration file db_config.env not found at {tns_file}"}), 404
    
    entries = []
    try:
        entries = parse_tns(tns_file)
    except Exception as e:
        print(f"Error parsing TNS file: {e}")
        return jsonify({"success": False, "error": f"Parse error: {str(e)}"}), 500
        
    return jsonify({"success": True, "count": len(entries), "items": entries, "tns_file_path": tns_file})

@app.route("/api/connect", methods=["POST"])
def connect_db():
    global active_session_key, db_sessions
    data = request.json or {}
    
    host = data.get("host")
    port = int(data.get("port", 1521))
    service_name = data.get("service_name")
    sid = data.get("sid")
    user = data.get("user")
    password = data.get("password")
    tns_alias = data.get("alias", "")

    if not user or not password:
        return jsonify({"success": False, "error": "Username and Password are required"}), 400

    try:
        conn = create_connection(host, port, service_name, sid, user, password)

        cursor = conn.cursor()
        cursor.execute("SELECT instance_name, host_name, version FROM v$instance")
        row = cursor.fetchone()
        instance_info = {
            "instance_name": row[0] if row else "N/A",
            "host_name": row[1] if row else "N/A",
            "version": row[2] if row else "N/A"
        }
        cursor.close()
        conn.close()

        session_key = f"{user}@{tns_alias}".upper()
        save_pwd = data.get("save_password", True)
        
        session_data = {
            "host": host,
            "port": port,
            "service_name": service_name,
            "sid": sid,
            "user": user,
            "password": password if save_pwd else "",
            "alias": tns_alias,
            "instance": instance_info
        }
        
        # Keep real password in active db_sessions
        db_session_real = session_data.copy()
        db_session_real["password"] = password
        
        active_session_key = session_key
        db_sessions[session_key] = db_session_real

        profiles_data = load_saved_profiles()
        profiles_data["profiles"][session_key] = session_data
        profiles_data["last_used_alias"] = tns_alias
        save_profiles(profiles_data)

        return jsonify({
            "success": True, 
            "message": f"Connected to {session_key}!", 
            "instance": instance_info, 
            "session_key": session_key,
            "all_sessions": list(db_sessions.keys())
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/plsql/extract_query", methods=["POST"])
def extract_plsql_query():
    data = request.json or {}
    pkg_name = data.get("package", "")
    proc_name = data.get("procedure", "")
    code = data.get("code", "")
    api_key = data.get("api_key", "").strip()
    target_model = data.get("model", "gemini-flash-latest").strip()
    if target_model not in ["gemini-flash-latest", "gemini-pro-latest"]:
        target_model = "gemini-flash-latest"

    if not code or not proc_name:
        return jsonify({"success": False, "error": "Code and procedure name are required"}), 400

    profiles_data = load_saved_profiles()
    gemini_key = api_key or profiles_data.get("ai_key") or os.environ.get("GEMINI_API_KEY")

    if not gemini_key:
        return jsonify({"success": False, "error": "Google Gemini API Key is required for this AI feature. Please save your key in the AI Chat tab first."}), 400

    try:
        client = genai.Client(api_key=gemini_key)
        
        prompt = f"""
Analyze the following PL/SQL package body code. Focus on the procedure or function named '{proc_name}'.
Your task is to find the MAIN driving SELECT query (usually the primary cursor or the largest SELECT statement) used in this procedure to fetch data.
Extract ONLY that SELECT query. Do not include PL/SQL syntax like 'CURSOR ... IS', 'INTO ...', or 'BEGIN ... END'.
Replace any PL/SQL local variables or parameters (like p_org_id, l_date, or g_user_id) with standard SQL bind variables (e.g. :p_org_id).
Respond with ONLY the raw SQL query. Do not use markdown formatting (no ```sql or similar).

Package Code:
{code}
"""
        response = client.models.generate_content(
            model=target_model,
            contents=prompt,
        )
        
        query = response.text.replace("```sql", "").replace("```", "").strip()
        return jsonify({"success": True, "query": query})
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "Resource Exhausted" in err_msg or "quota" in err_msg.lower():
            err_msg = "🚨 Rate Limit Exceeded (โควต้าเต็มชั่วคราว)! Google API ของฟรีจำกัด 15 ครั้ง/นาที กรุณารอสักครู่แล้วลองใหม่ครับ หรือเปลี่ยนไปใช้โมเดล gemini-1.5-flash แทน"
        elif "400" in err_msg or "API key not valid" in err_msg:
            err_msg = "🔑 API Key ไม่ถูกต้อง กรุณาตรวจสอบ API Key ในหน้า Setting (AI Chat) อีกครั้ง"
            
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": err_msg}), 500

def create_connection(host, port, service_name, sid, user, password):
    if service_name and str(service_name).strip():
        return oracledb.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            service_name=str(service_name).strip()
        )
    elif sid and str(sid).strip():
        return oracledb.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            sid=str(sid).strip()
        )
    else:
        raise ValueError("Neither Service Name nor SID is valid.")

def get_db_connection():
    global active_session_key, db_sessions
    if not active_session_key or active_session_key not in db_sessions:
        raise Exception("Not connected to database yet. Please click Connect first.")
    
    info = db_sessions[active_session_key]
    return create_connection(
        info["host"],
        info["port"],
        info.get("service_name"),
        info.get("sid"),
        info["user"],
        info["password"]
    )

@app.route("/api/status", methods=["GET"])
def status():
    if active_session_key and active_session_key in db_sessions:
        info = db_sessions[active_session_key]
        return jsonify({"connected": True, "active_session": active_session_key, "info": {
            "alias": info.get("alias"),
            "user": info.get("user"),
            "host": info.get("host"),
            "instance": info.get("instance")
        }, "sessions": list(db_sessions.keys())})
    return jsonify({"connected": False})

@app.route("/api/tables", methods=["GET"])
def get_tables():
    search = request.args.get("search", "").strip().upper()
    obj_type = request.args.get("type", "ALL").strip().upper()
    all_schemas = request.args.get("all_schemas", "false").lower() == "true"
    
    if len(search) < 2 and obj_type == "ALL":
        return jsonify({"success": True, "count": 0, "tables": [], "message": "Search keyword too short"})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        search_pattern = f"%{search}%" if search else "%"
        
        type_mapping = {
            "TABLE": "('TABLE')",
            "VIEW": "('VIEW')",
            "PACKAGE": "('PACKAGE')",
            "BODY": "('PACKAGE BODY')",
            "PROCEDURE": "('PROCEDURE')",
            "FUNCTION": "('FUNCTION')",
            "TRIGGER": "('TRIGGER')"
        }
        
        if all_schemas:
            if obj_type in type_mapping:
                in_clause = type_mapping[obj_type]
                sql = f"""
                    SELECT owner || '.' || object_name AS name, object_type AS type, owner
                    FROM all_objects
                    WHERE object_type IN {in_clause}
                      AND (UPPER(object_name) LIKE :s OR UPPER(owner) LIKE :s)
                    ORDER BY owner ASC, object_name ASC
                """
            else:
                sql = """
                    SELECT owner || '.' || object_name AS name, object_type AS type, owner
                    FROM all_objects
                    WHERE object_type IN ('TABLE', 'VIEW', 'PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION', 'TRIGGER')
                      AND (UPPER(object_name) LIKE :s OR UPPER(owner) LIKE :s)
                    ORDER BY CASE object_type 
                        WHEN 'TABLE' THEN 1 
                        WHEN 'VIEW' THEN 2 
                        WHEN 'PACKAGE' THEN 3 
                        WHEN 'PACKAGE BODY' THEN 4 
                        ELSE 5 END ASC, owner ASC, object_name ASC
                """
        else:
            if obj_type in type_mapping:
                in_clause = type_mapping[obj_type]
                sql = f"""
                    SELECT object_name AS name, object_type AS type, USER as owner
                    FROM user_objects
                    WHERE object_type IN {in_clause}
                      AND UPPER(object_name) LIKE :s
                    ORDER BY object_name ASC
                """
            else:
                sql = """
                    SELECT object_name AS name, object_type AS type, USER as owner
                    FROM user_objects
                    WHERE object_type IN ('TABLE', 'VIEW', 'PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION', 'TRIGGER')
                      AND UPPER(object_name) LIKE :s
                    ORDER BY CASE object_type 
                        WHEN 'TABLE' THEN 1 
                        WHEN 'VIEW' THEN 2 
                        WHEN 'PACKAGE' THEN 3 
                        WHEN 'PACKAGE BODY' THEN 4 
                        ELSE 5 END ASC, object_name ASC
                """

        cursor.execute(sql, s=search_pattern)
        rows = cursor.fetchmany(300)
        
        items = []
        for r in rows:
            items.append({"name": r[0], "type": r[1], "owner": r[2] if len(r) > 2 else ""})
            
        cursor.close()
        conn.close()
        return jsonify({
            "success": True, 
            "count": len(items), 
            "tables": items,
            "search": search,
            "type_filter": obj_type,
            "all_schemas": all_schemas
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/table/describe", methods=["GET"])
def describe_table():
    name = request.args.get("name", "").strip().upper()
    owner = ""
    if "." in name:
        owner, name = name.split(".", 1)
        
    if not name:
        return jsonify({"success": False, "error": "Table name required"}), 400
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if owner:
            sql = """
                SELECT column_name, data_type, data_length, data_precision, data_scale, nullable
                FROM all_tab_columns
                WHERE owner = :o AND table_name = :t
                ORDER BY column_id
            """
            cursor.execute(sql, o=owner, t=name)
        else:
            sql = """
                SELECT column_name, data_type, data_length, data_precision, data_scale, nullable
                FROM user_tab_columns
                WHERE table_name = :t
                ORDER BY column_id
            """
            cursor.execute(sql, t=name)
            
        rows = cursor.fetchall()
        columns = []
        for r in rows:
            dt = r[1]
            if r[3] is not None:
                dt += f"({r[3]},{r[4] if r[4] else 0})"
            elif r[2] and dt in ('VARCHAR2', 'CHAR', 'NVARCHAR2', 'RAW'):
                dt += f"({r[2]})"
                
            columns.append({
                "column_name": r[0],
                "data_type": dt,
                "nullable": r[5]
            })
            
        cursor.close()
        conn.close()
        return jsonify({"success": True, "table_name": name, "columns": columns, "count": len(columns)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/concurrent/search", methods=["GET"])
def search_concurrent():
    search = request.args.get("search", "").strip()
    search_type = request.args.get("type", "ALL").strip().upper()
    limit = int(request.args.get("limit", 100))
    
    if not search and search_type == "ALL":
        return jsonify({"success": True, "count": 0, "items": [], "message": "Search keyword empty"})
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        search_pattern = f"%{search.upper()}%" if search else "%"
        
        where_conditions = []
        bind_params = {}
        
        if search:
            if search_type == "REQ_ID":
                where_conditions.append("TO_CHAR(fcr.request_id) = :req_id")
                bind_params["req_id"] = search
            elif search_type == "SHORT_NAME":
                where_conditions.append("UPPER(fcp_base.concurrent_program_name) LIKE :s")
                bind_params["s"] = search_pattern
            elif search_type == "CONC_NAME":
                where_conditions.append("UPPER(fcp.user_concurrent_program_name) LIKE :s")
                bind_params["s"] = search_pattern
            elif search_type == "USER_NAME":
                where_conditions.append("UPPER(fu.user_name) LIKE :s")
                bind_params["s"] = search_pattern
            elif search_type == "PKG_NAME":
                where_conditions.append("UPPER(fe.execution_file_name) LIKE :s")
                bind_params["s"] = search_pattern
            else:
                where_conditions.append("""(
                    UPPER(fcp.user_concurrent_program_name) LIKE :s 
                    OR UPPER(fcp_base.concurrent_program_name) LIKE :s
                    OR UPPER(fu.user_name) LIKE :s
                    OR UPPER(fe.execution_file_name) LIKE :s
                    OR TO_CHAR(fcr.request_id) = :raw_s
                )""")
                bind_params["s"] = search_pattern
                bind_params["raw_s"] = search

        where_clause_extra = (" AND " + " AND ".join(where_conditions)) if where_conditions else ""

        sql = f"""
        SELECT DISTINCT
            fcr.request_id,
            TO_CHAR(fcr.request_date, 'YYYY-MM-DD HH24:MI:SS') as request_date,
            fcr.phase_code,
            fcr.status_code,
            fcr.hold_flag,
            ROUND(NVL((fcr.actual_completion_date - fcr.actual_start_date), (SYSDATE - fcr.actual_start_date)) * 86400, 2) AS run_time_sec,
            fu.user_name,
            fr.responsibility_name,
            fapp.basepath,
            fapp.application_short_name,
            fcp_base.concurrent_program_name AS program_short_name,
            fcp.user_concurrent_program_name,
            CASE fe.execution_method_code 
                WHEN 'I' THEN 'PL/SQL'
                WHEN 'P' THEN 'Oracle Reports / RTF'
                WHEN 'X' THEN 'XML Publisher (RTF)'
                ELSE 'Other ('||fe.execution_method_code||')'
            END AS program_type,
            fe.execution_file_name AS package_name,
            CASE fe.execution_method_code
                WHEN 'P' THEN '$' || fapp.basepath || '/reports/US/' || fe.execution_file_name || '.rdf'
                WHEN 'I' THEN 'DB Package/Procedure: ' || fe.execution_file_name
                WHEN 'Q' THEN '$' || fapp.basepath || '/sql/' || fe.execution_file_name || '.sql'
                WHEN 'H' THEN '$' || fapp.basepath || '/bin/' || fe.execution_file_name
                WHEN 'S' THEN 'DB Object: ' || fe.execution_file_name
                ELSE 'Check $' || fapp.basepath || '/ for ' || fe.execution_file_name
            END AS source_file_path,
            fcr.argument_text,
            fcr.logfile_name,
            fcr.outfile_name,
            fcr.output_file_type,
            (SELECT MAX(ro.file_name) KEEP (DENSE_RANK LAST ORDER BY ro.output_id) 
             FROM fnd_conc_req_outputs ro 
             WHERE ro.concurrent_request_id = fcr.request_id) AS pub_outfile_name,
            (SELECT MAX(ro.file_type) KEEP (DENSE_RANK LAST ORDER BY ro.output_id) 
             FROM fnd_conc_req_outputs ro 
             WHERE ro.concurrent_request_id = fcr.request_id) AS pub_file_type
        FROM
            fnd_concurrent_requests fcr,
            fnd_concurrent_programs_tl fcp,
            fnd_responsibility_tl fr,
            fnd_user fu,
            fnd_concurrent_programs fcp_base,
            fnd_executables fe,
            fnd_application_tl fa,               
            fnd_application fapp
        WHERE 1=1
            AND fcr.CONCURRENT_PROGRAM_ID = fcp.concurrent_program_id(+)
            AND fcr.responsibility_id = fr.responsibility_id(+)
            AND fcr.requested_by = fu.user_id(+)
            AND fcp.language(+) = 'US'
            AND fr.language(+) = 'US'
            AND fcr.concurrent_program_id = fcp_base.concurrent_program_id(+)
            AND fcp_base.executable_id = fe.executable_id(+)
            AND fcp_base.application_id = fa.application_id(+)
            AND fa.application_id = fapp.application_id(+)
            AND fa.language(+) = 'US'
            {where_clause_extra}
        ORDER BY fcr.request_id DESC
        """
        
        cursor.execute(sql, **bind_params)
        rows = cursor.fetchmany(limit)
        
        columns = [desc[0] for desc in cursor.description]
        results = []
        for r in rows:
            item = {}
            for idx, col in enumerate(columns):
                item[col.lower()] = r[idx]
            results.append(item)
            
        if len(results) == 0 and search_type in ("ALL", "SHORT_NAME", "CONC_NAME"):
            fallback_where = []
            fallback_bind = {}
            if search_type == "SHORT_NAME":
                fallback_where.append("UPPER(fcp.concurrent_program_name) LIKE :s")
                fallback_bind["s"] = search_pattern
            elif search_type == "CONC_NAME":
                fallback_where.append("UPPER(fcpt.user_concurrent_program_name) LIKE :s")
                fallback_bind["s"] = search_pattern
            else:
                fallback_where.append("(UPPER(fcp.concurrent_program_name) LIKE :s OR UPPER(fcpt.user_concurrent_program_name) LIKE :s)")
                fallback_bind["s"] = search_pattern
                
            fb_where = (" AND " + " AND ".join(fallback_where)) if fallback_where else ""
            
            fallback_sql = f"""
            SELECT DISTINCT
                fcp.concurrent_program_name AS program_short_name,
                fcpt.user_concurrent_program_name,
                fapp.application_short_name,
                fapp.basepath,
                fe.execution_method_code,
                fe.execution_file_name AS package_name,
                (SELECT MAX(frt.responsibility_name) || CASE WHEN COUNT(*) > 1 THEN ' (+' || (COUNT(*)-1) || ' More)' ELSE '' END
                 FROM fnd_request_group_units frgu,
                      fnd_responsibility fr,
                      fnd_responsibility_tl frt
                 WHERE frgu.request_group_id = fr.request_group_id
                   AND frgu.application_id = fr.group_application_id
                   AND fr.responsibility_id = frt.responsibility_id
                   AND fr.application_id = frt.application_id
                   AND frt.language = 'US'
                   AND frgu.request_unit_id = fcp.concurrent_program_id
                   AND frgu.unit_application_id = fcp.application_id
                ) AS responsibility_name
            FROM
                fnd_concurrent_programs fcp,
                fnd_concurrent_programs_tl fcpt,
                fnd_executables fe,
                fnd_application fapp
            WHERE fcp.concurrent_program_id = fcpt.concurrent_program_id
              AND fcpt.language = 'US'
              AND fcp.executable_id = fe.executable_id(+)
              AND fcp.application_id = fapp.application_id
              {fb_where}
            ORDER BY fcp.concurrent_program_name
            """
            cursor.execute(fallback_sql, **fallback_bind)
            fb_rows = cursor.fetchmany(limit)
            if fb_rows:
                fb_cols = [desc[0].lower() for desc in cursor.description]
                for r in fb_rows:
                    fb_item = dict(zip(fb_cols, r))
                    
                    method_code = fb_item.get('execution_method_code') or ''
                    pkg_name = fb_item.get('package_name') or ''
                    base_path = fb_item.get('basepath') or ''
                    
                    if method_code == 'I': prog_type = 'PL/SQL'
                    elif method_code == 'P': prog_type = 'Oracle Reports / RTF'
                    elif method_code == 'X': prog_type = 'XML Publisher (RTF)'
                    else: prog_type = f'Other ({method_code})'
                    
                    if method_code == 'P': src_path = f'${base_path}/reports/US/{pkg_name}.rdf'
                    elif method_code == 'I': src_path = f'DB Package/Procedure: {pkg_name}'
                    elif method_code == 'Q': src_path = f'${base_path}/sql/{pkg_name}.sql'
                    elif method_code == 'H': src_path = f'${base_path}/bin/{pkg_name}'
                    elif method_code == 'S': src_path = f'DB Object: {pkg_name}'
                    else: src_path = f'Check ${base_path}/ for {pkg_name}'
                    
                    results.append({
                        'request_id': 'Never Run',
                        'request_date': '-',
                        'phase_code': 'C',
                        'status_code': 'X',
                        'hold_flag': 'N',
                        'run_time_sec': 0,
                        'user_name': '-',
                        'responsibility_name': fb_item.get('responsibility_name') or '-',
                        'basepath': base_path,
                        'application_short_name': fb_item.get('application_short_name') or '',
                        'program_short_name': fb_item.get('program_short_name') or '',
                        'user_concurrent_program_name': fb_item.get('user_concurrent_program_name') or '',
                        'program_type': prog_type,
                        'package_name': pkg_name,
                        'source_file_path': src_path,
                        'argument_text': '-',
                        'logfile_name': None,
                        'outfile_name': None,
                        'output_file_type': None,
                        'pub_outfile_name': None,
                        'pub_file_type': None
                    })

        cursor.close()
        conn.close()
        return jsonify({"success": True, "count": len(results), "items": results})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/concurrent/log", methods=["GET"])
def get_concurrent_log():
    global active_session_key, db_sessions
    req_id = request.args.get("request_id", "").strip()
    remote_path_arg = request.args.get("remote_path", "").strip()
    if not req_id and not remote_path_arg:
        return jsonify({"success": False, "error": "Request ID or remote_path required"}), 400
        
    try:
        log_path = remote_path_arg
        out_path = "N/A"
        out_type = "N/A"
        phase_code = ""
        status_code = ""
        arg_text = ""
        
        if req_id:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                sql = """
                    SELECT logfile_name, outfile_name, output_file_type, phase_code, status_code, argument_text
                    FROM fnd_concurrent_requests
                    WHERE request_id = :r
                """
                cursor.execute(sql, r=req_id)
                row = cursor.fetchone()
                cursor.close()
                conn.close()
                if row:
                    if not log_path or log_path == 'N/A':
                        log_path = row[0] or ""
                    out_path = row[1] or "N/A"
                    out_type = row[2] or "N/A"
                    phase_code = row[3] or ""
                    status_code = row[4] or ""
                    arg_text = row[5] or ""
            except Exception as dbe:
                print(f"Notice querying request info: {dbe}")
                
        if not log_path or log_path == 'N/A':
            return jsonify({
                "success": False, 
                "error": f"No logfile_name recorded for Request ID {req_id}"
            }), 404

        log_content = ""
        file_size = 0
        app_host = _get_app_server_host(active_session_key)

        # 1. Try local Windows filesystem if exists
        if os.path.exists(log_path):
            try:
                file_size = os.path.getsize(log_path)
                with open(log_path, "rb") as f:
                    raw_data = f.read(500000)
                try:
                    log_content = raw_data.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        log_content = raw_data.decode('cp874')
                    except Exception:
                        log_content = raw_data.decode('latin-1', errors='replace')
            except Exception as fe:
                log_content = f"Error reading local file: {fe}"
        else:
            # 2. Fetch from remote Linux App Server via SFTP
            if not app_host:
                return jsonify({"success": False, "error": "Could not determine Application Server host"})
                
            creds = get_sftp_credentials(app_host)
            if not creds:
                return jsonify({"success": False, "error": f"SFTP credentials not configured for server {app_host}"})

            try:
                ssh, sftp = _create_sftp_client(app_host, creds['username'], creds['password'])
                try:
                    st = sftp.stat(log_path)
                    file_size = st.st_size
                except Exception:
                    file_size = 0

                with sftp.file(log_path, 'rb') as f:
                    raw_data = f.read(1000000)
                    
                try:
                    log_content = raw_data.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        log_content = raw_data.decode('cp874')
                    except Exception:
                        log_content = raw_data.decode('latin-1', errors='replace')
                        
                sftp.close()
                ssh.close()
            except Exception as sftp_err:
                log_content = f"Error fetching log via SFTP from {app_host}:\n{str(sftp_err)}\n\nServer path: {log_path}"

        return jsonify({
            "success": True,
            "request_id": req_id,
            "logfile_name": log_path,
            "outfile_name": out_path,
            "output_file_type": out_type,
            "phase_code": phase_code,
            "status_code": status_code,
            "argument_text": arg_text,
            "log_content": log_content,
            "file_size": file_size,
            "app_host": app_host
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/concurrent/live_monitor", methods=["GET"])
def live_monitor_concurrent():
    req_id = request.args.get("request_id", "").strip()
    if not req_id:
        return jsonify({"success": False, "error": "Request ID required"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        sql_req = """
            SELECT fcr.request_id,
                   fcr.phase_code,
                   fcr.status_code,
                   fcp.user_concurrent_program_name,
                   fcp_base.concurrent_program_name,
                   TO_CHAR(fcr.actual_start_date, 'YYYY-MM-DD HH24:MI:SS') as start_date,
                   ROUND((NVL(fcr.actual_completion_date, SYSDATE) - fcr.actual_start_date) * 86400, 2) as elapsed_sec,
                   fcr.os_process_id,
                   fcr.oracle_process_id,
                   fcr.argument_text,
                   fcr.logfile_name,
                   fcr.outfile_name
            FROM fnd_concurrent_requests fcr
            JOIN fnd_concurrent_programs_tl fcp ON fcp.concurrent_program_id = fcr.concurrent_program_id AND fcp.language = 'US'
            JOIN fnd_concurrent_programs fcp_base ON fcp_base.concurrent_program_id = fcr.concurrent_program_id
            WHERE fcr.request_id = :r
        """
        cursor.execute(sql_req, r=req_id)
        row = cursor.fetchone()

        if not row:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": f"Request ID {req_id} not found"}), 404

        (r_id, phase_code, status_code, prog_name, prog_code, start_date, elapsed_sec, os_pid, ora_pid, args, log_path, out_path) = row

        res_data = {
            "request_id": r_id,
            "phase_code": phase_code,
            "status_code": status_code,
            "program_name": prog_name,
            "program_code": prog_code,
            "start_date": start_date,
            "elapsed_sec": elapsed_sec or 0,
            "os_pid": os_pid,
            "arguments": args,
            "is_running": (phase_code == 'R'),
            "session": None
        }

        # Check v$session for live details
        if os_pid:
            sql_sess = """
                SELECT s.sid, s.serial#, s.status, s.event, s.seconds_in_wait,
                       s.sql_id, sq.sql_text,
                       o1.object_name as entry_object,
                       p1.procedure_name as entry_procedure,
                       o2.object_name as current_object,
                       p2.procedure_name as current_procedure,
                       s.module, s.action
                FROM v$session s
                LEFT JOIN v$sql sq ON sq.sql_id = s.sql_id
                LEFT JOIN all_objects o1 ON o1.object_id = s.plsql_entry_object_id
                LEFT JOIN all_procedures p1 ON p1.object_id = s.plsql_entry_object_id AND p1.subprogram_id = s.plsql_entry_subprogram_id
                LEFT JOIN all_objects o2 ON o2.object_id = s.plsql_object_id
                LEFT JOIN all_procedures p2 ON p2.object_id = s.plsql_object_id AND p2.subprogram_id = s.plsql_subprogram_id
                WHERE s.process = :p
            """
            cursor.execute(sql_sess, p=os_pid)
            s_row = cursor.fetchone()

            if s_row:
                sid = s_row[0]
                cursor.execute("""
                    SELECT ss.value FROM v$sesstat ss
                    JOIN v$statname sn ON sn.statistic# = ss.statistic#
                    WHERE ss.sid = :s AND sn.name = 'consistent gets'
                """, s=sid)
                bg_row = cursor.fetchone()
                buffer_gets = bg_row[0] if bg_row else 0

                curr_proc = s_row[10] or s_row[8] or ""
                stage = "Processing in Database..."
                if curr_proc:
                    proc_up = curr_proc.upper()
                    if "IMPORT" in proc_up:
                        stage = "Step 1/5: Importing data from HIS"
                    elif "INSERT_TEMP" in proc_up:
                        stage = "Step 2/5: Preparing Interface Temp tables"
                    elif "MATCH_LINE" in proc_up:
                        stage = "Step 3/5: Matching Lines & Distributions"
                    elif "VALIDATE" in proc_up:
                        stage = "Step 4/5: Validating Transactions & Auto Combine"
                    elif "INTERFACE" in proc_up:
                        stage = "Step 5/5: Interfacing valid records to AR"
                    else:
                        stage = f"Running: {curr_proc}"

                res_data["session"] = {
                    "sid": sid,
                    "serial": s_row[1],
                    "status": s_row[2],
                    "event": s_row[3],
                    "seconds_in_wait": s_row[4],
                    "sql_id": s_row[5],
                    "sql_text": s_row[6] or "",
                    "entry_object": s_row[7],
                    "entry_procedure": s_row[8],
                    "current_object": s_row[9],
                    "current_procedure": s_row[10],
                    "stage_description": stage,
                    "buffer_gets": buffer_gets
                }

        cursor.close()
        conn.close()
        return jsonify({"success": True, "data": res_data})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

def resolve_plsql_object(cursor, raw_name, default_type="PACKAGE BODY"):
    # ponytail: resolves 1 to 3 dotted parts (schema.pkg.proc or pkg.proc or schema.pkg) to owner, name, and subprogram.
    parts = [p.strip().upper() for p in (raw_name or "").split(".") if p.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return {"owner": None, "name": parts[0], "proc": None, "type": default_type}
    if len(parts) >= 3:
        return {"owner": parts[0], "name": parts[1], "proc": parts[2], "type": default_type}
    
    p0, p1 = parts[0], parts[1]
    try:
        cursor.execute("""
            SELECT object_name, object_type, owner FROM all_objects 
            WHERE object_name IN (:p0, :p1) 
              AND object_type IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION')
        """, p0=p0, p1=p1)
        names = {r[0]: r for r in cursor.fetchall()}
        if p0 in names and p1 not in names:
            return {"owner": names[p0][2], "name": p0, "proc": p1, "type": names[p0][1] if "PACKAGE" in names[p0][1] else default_type}
        if p1 in names:
            return {"owner": p0, "name": p1, "proc": None, "type": names[p1][1] if "PACKAGE" in names[p1][1] else default_type}
    except Exception:
        pass
    if p0 in ("APPS", "BOLINF", "XXCUST"):
        return {"owner": p0, "name": p1, "proc": None, "type": default_type}
    return {"owner": None, "name": p0, "proc": p1, "type": default_type}

@app.route("/api/plsql/source", methods=["GET"])
def get_plsql_source():
    raw_name = request.args.get("name", "").strip().upper()
    obj_type = request.args.get("type", "").strip().upper()
    
    if not raw_name or not obj_type:
        return jsonify({"success": False, "error": "Name and Type are required"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        resolved = resolve_plsql_object(cursor, raw_name, obj_type) or {"name": raw_name, "owner": None, "proc": None, "type": obj_type}
        name = resolved.get("name") or raw_name
        owner = resolved.get("owner")
        proc = resolved.get("proc")
        
        target_types = [obj_type]
        if obj_type == "PACKAGE BODY":
            target_types = ["PACKAGE BODY", "PACKAGE"]
        elif obj_type == "PACKAGE":
            target_types = ["PACKAGE", "PACKAGE BODY"]
        else:
            target_types = [obj_type, "PACKAGE BODY", "PACKAGE", "PROCEDURE", "FUNCTION"]
            
        lines = []
        found_type = obj_type
        found_name = name
        found_owner = owner
        
        for t in target_types:
            if owner:
                cursor.execute("SELECT text FROM all_source WHERE owner = :o AND type = :t AND name = :n ORDER BY line", o=owner, t=t, n=name)
                rows = cursor.fetchall()
            else:
                cursor.execute("SELECT text FROM user_source WHERE type = :t AND name = :n ORDER BY line", t=t, n=name)
                rows = cursor.fetchall()
                if not rows:
                    cursor.execute("SELECT text, owner FROM all_source WHERE type = :t AND name = :n ORDER BY line", t=t, n=name)
                    rows = cursor.fetchall()
                    if rows and len(rows[0]) > 1:
                        found_owner = rows[0][1]
            if rows:
                lines = [r[0] for r in rows]
                found_type = t
                break
                
        if not lines and owner:
            for t in target_types:
                cursor.execute("SELECT text FROM all_source WHERE type = :t AND name = :n ORDER BY line", t=t, n=name)
                rows = cursor.fetchall()
                if rows:
                    lines = [r[0] for r in rows]
                    found_type = t
                    break

        if not lines:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": f"Object '{name}' ({obj_type}) not found in database source."}), 404

        full_code = "".join(lines)
        
        if full_code and not full_code.strip().upper().startswith("CREATE"):
            full_code = "CREATE OR REPLACE " + full_code.lstrip()

        outline = parse_plsql_outline(full_code)
        
        target_line = 1
        if proc:
            for item in outline:
                if item.get("name") == proc:
                    target_line = item.get("line", 1)
                    break
            if target_line == 1 and full_code:
                m = re.search(r"(?i)\b(PROCEDURE|FUNCTION)\s+" + re.escape(proc) + r"\b", full_code)
                if m:
                    target_line = full_code[:m.start()].count("\n") + 1

        cursor.close()
        conn.close()
        return jsonify({
            "success": True, 
            "name": found_name, 
            "owner": found_owner,
            "raw_name": raw_name, 
            "sub_program": proc,
            "target_line": target_line,
            "type": found_type, 
            "code": full_code, 
            "line_count": len(lines),
            "outline": outline
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def parse_plsql_outline(code):
    outline = []
    lines = code.split("\n")
    
    proc_pattern = re.compile(r"(?i)\bPROCEDURE\s+([A-Za-z0-9_\$#]+)")
    func_pattern = re.compile(r"(?i)\bFUNCTION\s+([A-Za-z0-9_\$#]+)")
    var_pattern = re.compile(r"(?i)\b(g_[A-Za-z0-9_\$#]+)\s+")

    for idx, line in enumerate(lines, 1):
        clean_line = line.strip()
        if clean_line.startswith("--") or clean_line.startswith("/*"):
            continue
            
        p_match = proc_pattern.search(clean_line)
        if p_match:
            name = p_match.group(1).upper()
            if name not in ['IS', 'AS', 'PACKAGE', 'BODY']:
                outline.append({"name": name, "type": "PROCEDURE", "line": idx})
            continue

        f_match = func_pattern.search(clean_line)
        if f_match:
            name = f_match.group(1).upper()
            if name not in ['IS', 'AS', 'PACKAGE', 'BODY']:
                outline.append({"name": name, "type": "FUNCTION", "line": idx})
            continue

        v_match = var_pattern.search(clean_line)
        if v_match:
            name = v_match.group(1).upper()
            outline.append({"name": name, "type": "VARIABLE", "line": idx})
            
    return outline

@app.route("/api/plsql/compile", methods=["POST"])
def compile_plsql():
    data = request.json or {}
    code = data.get("code", "").strip()
    name = data.get("name", "").strip().upper()
    obj_type = data.get("type", "").strip().upper()
    
    if not code:
        return jsonify({"success": False, "error": "Code cannot be empty"}), 400
    
    if code.endswith("/"):
        code = code[:-1].strip()
        
    if not code.upper().startswith("CREATE"):
        code = "CREATE OR REPLACE " + code
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        resolved = resolve_plsql_object(cursor, name, obj_type)
        clean_name = resolved["name"] if resolved else name

        cursor.execute(code)
        
        err_sql = """
            SELECT line, position, text
            FROM user_errors
            WHERE name = :n AND type = :t
            ORDER BY sequence
        """
        cursor.execute(err_sql, n=clean_name, t=obj_type)
        err_rows = cursor.fetchall()
        
        errors = []
        for r in err_rows:
            errors.append({"line": r[0], "position": r[1], "text": r[2]})
            
        def _auto_save_and_commit(session_alias, obj_type, obj_name, code):
            import os, subprocess, datetime
            base_repo = r"D:\WORK\EBS_Git_Repo"
            
            if not session_alias:
                session_alias = "UNKNOWN_SITE"
                
            site_dir = os.path.join(base_repo, session_alias)
            type_dir = os.path.join(site_dir, obj_type.replace(' ', '_'))
            
            os.makedirs(type_dir, exist_ok=True)
            
            file_name = f"{obj_name}.sql" if obj_name else "unknown.sql"
            file_path = os.path.join(type_dir, file_name)
            
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(code)
                    
                if not os.path.exists(os.path.join(base_repo, ".git")):
                    subprocess.run(["git", "init"], cwd=base_repo, capture_output=True)
                    subprocess.run(["git", "config", "user.name", "JNavigator Auto"], cwd=base_repo, capture_output=True)
                    subprocess.run(["git", "config", "user.email", "auto@jnavigator.local"], cwd=base_repo, capture_output=True)
                    
                subprocess.run(["git", "add", "."], cwd=base_repo, capture_output=True)
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                commit_msg = f"Auto-compile: {obj_type} {obj_name} on {session_alias} at {timestamp}"
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=base_repo, capture_output=True)
                subprocess.run(["git", "push"], cwd=base_repo, capture_output=True)
            except Exception as e:
                pass

        global active_session_key, db_sessions
        session_alias = db_sessions[active_session_key].get('alias', 'UNKNOWN_SITE') if active_session_key in db_sessions else 'UNKNOWN_SITE'
        
        # Save & commit to local git repository regardless of compile errors
        _auto_save_and_commit(session_alias, obj_type, clean_name, code)
        
        cursor.close()
        conn.close()
        
        if errors:
            return jsonify({
                "success": False,
                "compiled": False,
                "name": name,
                "type": obj_type,
                "error_count": len(errors),
                "errors": errors,
                "message": f"Compilation failed with {len(errors)} error(s)."
            })
        else:
            return jsonify({
                "success": True,
                "compiled": True,
                "name": name,
                "type": obj_type,
                "message": f"Successfully compiled {obj_type} {name} into database!"
            })
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/plsql/arguments", methods=["GET"])
def get_plsql_arguments():
    pkg_name = request.args.get("package", "").strip().upper()
    proc_name = request.args.get("procedure", "").strip().upper()
    
    if not proc_name:
        return jsonify({"success": False, "error": "Procedure name is required"}), 400
        
    if pkg_name and "." in pkg_name:
        pkg_parts = [p.strip().upper() for p in pkg_name.split(".") if p.strip()]
        pkg_name = pkg_parts[1] if len(pkg_parts) >= 3 else pkg_parts[0]
    if proc_name and "." in proc_name:
        proc_name = proc_name.split(".")[-1].strip()

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if pkg_name:
            sql = """
                SELECT argument_name, data_type, in_out, position
                FROM all_arguments
                WHERE package_name = :pkg AND object_name = :obj
                ORDER BY position
            """
            cursor.execute(sql, pkg=pkg_name, obj=proc_name)
        else:
            sql = """
                SELECT argument_name, data_type, in_out, position
                FROM all_arguments
                WHERE object_name = :obj AND package_name IS NULL
                ORDER BY position
            """
            cursor.execute(sql, obj=proc_name)
            
        rows = cursor.fetchall()
        args_list = []
        for r in rows:
            args_list.append({
                "argument_name": r[0],
                "data_type": r[1],
                "in_out": r[2],
                "position": r[3]
            })
            
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "package": pkg_name,
            "procedure": proc_name,
            "arguments": args_list,
            "count": len(args_list)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/query_count", methods=["POST"])
def query_count():
    import time
    data = request.json or {}
    sql = data.get("sql", "").strip()
    bind_vars = data.get("bind_vars", {})
    if not sql:
        return jsonify({"success": False, "error": "Query cannot be empty"}), 400

    # Wrap in SELECT COUNT(*) with PARALLEL hint for fast counting
    count_sql = f"SELECT /*+ PARALLEL */ COUNT(*) FROM ({sql})"
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        start_time = time.time()
        cursor.execute(count_sql, **bind_vars) if bind_vars else cursor.execute(count_sql)
        row = cursor.fetchone()
        total_count = row[0] if row else 0
        elapsed = round(time.time() - start_time, 3)
        cursor.close()
        conn.close()
        return jsonify({"success": True, "count": total_count, "elapsed": elapsed})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/export", methods=["GET", "POST"])
def export_data():
    if request.method == "GET":
        return "<h3>Invalid Request</h3><p>This endpoint requires a POST request to export data. Please close this tab and use the Export button in the application.</p>", 405

    if request.is_json:
        data = request.json or {}
    else:
        data = request.form.to_dict()
    # Always ensure bind_vars is a dict
    if 'bind_vars' in data and isinstance(data['bind_vars'], str):
        import json as _json
        try:
            data['bind_vars'] = _json.loads(data['bind_vars'])
        except:
            data['bind_vars'] = {}
        
    sql = data.get("sql", "").strip()
    bind_vars = data.get("bind_vars", {})
    format = data.get("format", "CSV").upper()
    table_name = data.get("table_name", "EXPORT_TABLE")
    row_limit = data.get("row_limit", "500")
    task_id = data.get("task_id", "")
    
    if task_id:
        export_tasks[task_id] = {'status': 'processing', 'rows': 0}
    
    if row_limit != "ALL":
        try:
            row_limit = int(row_limit)
        except:
            row_limit = 500
            
    if not sql:
        return jsonify({"success": False, "error": "Query cannot be empty"}), 400

    sql = sql.strip().rstrip(';')
    exec_sql = sql
    if exec_sql.upper().startswith("SELECT") and "/*+" not in exec_sql:
        exec_sql = re.sub(r'^(SELECT\s+)', r'\1/*+ PARALLEL */ ', exec_sql, flags=re.IGNORECASE)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.arraysize = 50000
        cursor.prefetchrows = 50000
        cursor.execute(exec_sql, **bind_vars) if bind_vars else cursor.execute(exec_sql)
        
        if not cursor.description:
            return jsonify({"success": False, "error": "Not a SELECT statement"}), 400
            
        columns = [desc[0] for desc in cursor.description]

        def generate_csv():
            import csv
            from io import StringIO
            output = StringIO()
            writer = csv.writer(output)
            
            yield '\ufeff' # UTF-8 BOM for Excel
            writer.writerow(columns)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
                
            total_rows = 0
            while True:
                rows = cursor.fetchmany(10000)
                if not rows:
                    break
                    
                for row in rows:
                    if row_limit != "ALL" and total_rows >= row_limit:
                        break
                    writer.writerow(row)
                    total_rows += 1
                    
                if task_id: export_tasks[task_id] = {'status': 'processing', 'rows': total_rows}
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
                
                if row_limit != "ALL" and total_rows >= row_limit:
                    break
            
            if task_id: export_tasks[task_id] = {'status': 'completed', 'rows': total_rows}
            cursor.close()
            conn.close()

        def generate_txt():
            import csv
            from io import StringIO
            output = StringIO()
            writer = csv.writer(output, delimiter='\t')
            
            writer.writerow(columns)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
                
            total_rows = 0
            while True:
                rows = cursor.fetchmany(10000)
                if not rows:
                    break
                    
                for row in rows:
                    if row_limit != "ALL" and total_rows >= row_limit:
                        break
                    # Clean up newlines in TXT to avoid breaking layout
                    clean_row = [str(v).replace('\n', ' ').replace('\r', '') if v is not None else '' for v in row]
                    writer.writerow(clean_row)
                    total_rows += 1
                    
                if task_id: export_tasks[task_id] = {'status': 'processing', 'rows': total_rows}
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
                
                if row_limit != "ALL" and total_rows >= row_limit:
                    break
            
            if task_id: export_tasks[task_id] = {'status': 'completed', 'rows': total_rows}
            cursor.close()
            conn.close()

        def generate_sql():
            total_rows = 0
            while True:
                rows = cursor.fetchmany(5000)
                if not rows:
                    break
                    
                chunk_str = ""
                for row in rows:
                    if row_limit != "ALL" and total_rows >= row_limit:
                        break
                    vals = []
                    for v in row:
                        if v is None:
                            vals.append("NULL")
                        elif isinstance(v, (int, float)):
                            vals.append(str(v))
                        else:
                            val_str = str(v).replace("'", "''")
                            vals.append(f"'{val_str}'")
                    chunk_str += f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(vals)});\n"
                    total_rows += 1
                    
                if task_id: export_tasks[task_id] = {'status': 'processing', 'rows': total_rows}
                yield chunk_str
                
                if row_limit != "ALL" and total_rows >= row_limit:
                    break
            if task_id: export_tasks[task_id] = {'status': 'completed', 'rows': total_rows}
            cursor.close()
            conn.close()

        def generate_excel():
            import xlsxwriter
            import tempfile
            import os
            
            fd, temp_path = tempfile.mkstemp(suffix='.xlsx')
            os.close(fd)
            
            try:
                workbook = xlsxwriter.Workbook(temp_path, {'constant_memory': True})
                
                sheet_idx = 1
                row_idx = 0
                worksheet = workbook.add_worksheet(f"Sheet {sheet_idx}")
                
                worksheet.write_row(0, 0, columns)
                row_idx = 1
                
                total_rows = 0
                while True:
                    rows = cursor.fetchmany(10000)
                    if not rows:
                        break
                        
                    for row in rows:
                        if row_limit != "ALL" and total_rows >= row_limit:
                            break
                            
                        if row_idx >= 1048500: # Excel row limit safety
                            sheet_idx += 1
                            worksheet = workbook.add_worksheet(f"Sheet {sheet_idx}")
                            worksheet.write_row(0, 0, columns)
                            row_idx = 1
                                
                        worksheet.write_row(row_idx, 0, [val if val is None or isinstance(val, (int, float, str)) else str(val) for val in row])
                        row_idx += 1
                        total_rows += 1
                        
                    if task_id: export_tasks[task_id] = {'status': 'processing', 'rows': total_rows}
                    if row_limit != "ALL" and total_rows >= row_limit:
                        break
                        
                workbook.close()
                if task_id: export_tasks[task_id] = {'status': 'completed', 'rows': total_rows}
                cursor.close()
                conn.close()
                
                with open(temp_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        def generate_json_export():
            import json
            yield '[\n'
            first = True
            total_rows = 0
            
            while True:
                rows = cursor.fetchmany(10000)
                if not rows:
                    break
                    
                for row in rows:
                    if row_limit != "ALL" and total_rows >= row_limit:
                        break
                    row_dict = dict(zip(columns, [val if val is None or isinstance(val, (int, float, str)) else str(val) for val in row]))
                    prefix = ',\n' if not first else ''
                    yield prefix + json.dumps(row_dict)
                    first = False
                    total_rows += 1
                    
                if row_limit != "ALL" and total_rows >= row_limit:
                    break
            yield '\n]'
            cursor.close()
            conn.close()

        save_path = data.get("save_path", "")
        if save_path:
            try:
                gen = None
                if format == 'CSV': gen = generate_csv()
                elif format == 'TXT': gen = generate_txt()
                elif format == 'SQL_INSERT': gen = generate_sql()
                elif format == 'EXCEL': gen = generate_excel()
                elif format == 'JSON': gen = generate_json_export()
                
                if gen:
                    if format == 'EXCEL':
                        with open(save_path, 'wb') as out_f:
                            for chunk in gen: out_f.write(chunk)
                    else:
                        with open(save_path, 'w', encoding='utf-8') as out_f:
                            for chunk in gen: out_f.write(chunk)
                if task_id: export_tasks[task_id] = {'status': 'completed', 'rows': 0}
            except Exception as ex:
                import traceback
                traceback.print_exc()
                if task_id: export_tasks[task_id] = {'status': 'error', 'error': str(ex)}
                return jsonify({"success": False, "error": str(ex)})

            return jsonify({"success": True, "message": f"Export completed → {save_path}"})

        if format == 'CSV':
            return Response(stream_with_context(generate_csv()), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=export.csv'})
        elif format == 'TXT':
            return Response(stream_with_context(generate_txt()), mimetype='text/plain', headers={'Content-Disposition': 'attachment; filename=export.txt'})
        elif format == 'SQL_INSERT':
            return Response(stream_with_context(generate_sql()), mimetype='text/plain', headers={'Content-Disposition': 'attachment; filename=export.sql'})
        elif format == 'EXCEL':
            return Response(stream_with_context(generate_excel()), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=export.xlsx'})
        elif format == 'JSON':
            return Response(stream_with_context(generate_json_export()), mimetype='application/json', headers={'Content-Disposition': 'attachment; filename=export.json'})
        else:
            return jsonify({"success": False, "error": "Unsupported format"}), 400
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/query", methods=["POST"])
def run_query():
    import time
    data = request.json or {}
    sql = data.get("sql", "").strip()
    bind_vars = data.get("bind_vars", {})
    raw_limit = data.get("row_limit", 500)
    if str(raw_limit).upper() == "ALL":
        row_limit = "ALL"
    else:
        try:
            row_limit = int(raw_limit)
        except ValueError:
            row_limit = 500

    if not sql:
        return jsonify({"success": False, "error": "Query cannot be empty"}), 400

    sql = sql.replace("\u2A7D", "<=").replace("\u2A7E", ">=").replace("\u2260", "<>")
    sql = sql.strip().rstrip(';')
    
    # Hidden hint injection
    exec_sql = sql
    if exec_sql.upper().startswith("SELECT") and "/*+" not in exec_sql:
        exec_sql = re.sub(r'^(SELECT\s+)', r'\1/*+ PARALLEL */ ', exec_sql, flags=re.IGNORECASE)

    def generate_ndjson():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.arraysize = 50000  # Maximize fetch size for bandwidth efficiency
            cursor.prefetchrows = 50000
            
            start_time = time.time()
            cursor.execute(exec_sql, **bind_vars) if bind_vars else cursor.execute(exec_sql)
            
            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                yield json.dumps({"success": True, "columns": columns}) + '\n'
                
                total_rows = 0
                truncated = False
                
                while True:
                    rows = cursor.fetchmany(10000)
                    if not rows:
                        break
                    
                    formatted_chunk = []
                    for row in rows:
                        if row_limit != "ALL" and total_rows >= row_limit:
                            truncated = True
                            break
                        # Clean up types that JSON doesn't support
                        formatted_chunk.append([val if val is None or isinstance(val, (int, float, str)) else str(val) for val in row])
                        total_rows += 1
                        
                    if formatted_chunk:
                        yield json.dumps(formatted_chunk) + '\n'
                        
                    if truncated:
                        break
                        
                elapsed = round(time.time() - start_time, 3)
                yield json.dumps({"is_final": True, "row_count": total_rows, "truncated": truncated, "elapsed": elapsed}) + '\n'
                
                cursor.close()
                conn.close()
            else:
                conn.commit()
                elapsed = round(time.time() - start_time, 3)
                yield json.dumps({
                    "success": True,
                    "message": f"Statement executed successfully. Rows affected: {cursor.rowcount}",
                    "elapsed": elapsed
                }) + '\n'
                cursor.close()
                conn.close()
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield json.dumps({"success": False, "error": str(e)}) + '\n'

    # Stream to Gzip (Real-time compression without filling RAM)
    def generate_gzipped():
        compressor = zlib.compressobj(wbits=31)
        for chunk in generate_ndjson():
            yield compressor.compress(chunk.encode('utf-8'))
        yield compressor.flush()

    response = Response(stream_with_context(generate_gzipped()), mimetype='application/json')
    response.headers['Content-Encoding'] = 'gzip'
    return response

@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    data = request.json or {}
    message = data.get("message", "").strip()
    api_key = data.get("api_key", "").strip()
    target_model = data.get("model", "gemini-flash-latest").strip()
    if target_model not in ["gemini-flash-latest", "gemini-pro-latest"]:
        target_model = "gemini-flash-latest"
    
    if not message:
        return jsonify({"success": False, "error": "Message is empty"}), 400
        
    try:
        profiles_data = load_saved_profiles()
        gemini_key = api_key or profiles_data.get("ai_key") or os.environ.get("GEMINI_API_KEY")
        
        if api_key:
            profiles_data["ai_key"] = api_key
            save_profiles(profiles_data)

        if gemini_key:
            try:
                client = genai.Client(api_key=gemini_key)
                
                system_instruction = "You are an expert Antigravity AI Agent & Oracle EBS ERP Specialist. Provide high-performance, precise Oracle SQL and PL/SQL code solutions."
                
                response = client.models.generate_content(
                    model=target_model,
                    contents=message,
                    config={"system_instruction": system_instruction}
                )
                
                reply_html = response.text.replace("\n", "<br>").replace("```sql", "<code>").replace("```", "</code>")
                return jsonify({"success": True, "reply": reply_html, "model_used": target_model})
            except Exception as ge:
                err_msg = str(ge)
                if "429" in err_msg or "Resource Exhausted" in err_msg or "quota" in err_msg.lower():
                    err_msg = "🚨 <b>Rate Limit Exceeded (โควต้าเต็มชั่วคราว)!</b><br>Google API ของฟรีจำกัดไว้ 15 ครั้ง/นาที กรุณารอสักครู่แล้วลองพิมพ์มาใหม่นะครับ หรือสลับไปใช้ <b>gemini-1.5-flash</b>"
                elif "400" in err_msg or "API key not valid" in err_msg:
                    err_msg = "🔑 <b>API Key ไม่ถูกต้อง!</b> กรุณาตรวจสอบ API Key อีกครั้งครับ"
                else:
                    err_msg = f"❌ <b>AI Connection Error:</b> {err_msg}"
                return jsonify({"success": True, "reply": err_msg, "model_used": target_model})
                
        reply = process_ai_fallback(message, target_model)
        return jsonify({"success": True, "reply": reply, "model_used": target_model})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def process_ai_fallback(prompt, target_model):
    prompt_upper = prompt.upper()
    
    if "ทำไรได้บ้าง" in prompt or "ทำอะไรได้บ้าง" in prompt or "HELP" in prompt_upper:
        return f"""<b>ฟังก์ชันและความสามารถของ JNavigator [{target_model}]:</b><br><br>
1. <b>⚡ SQL Query Workbench (Multi-Tab)</b>: เขียนคิวรี Oracle SQL รองรับการเปิดหลายแท็บด้วย <code>Ctrl+M</code><br>
2. <b>🛠️ PL/SQL Package Editor (Multi-Tab & VS Code Engine)</b>: แก้ไขและบันทึก Compile โค้ด Package Body/Spec ตรงสู่ Oracle DB พร้อม <code>Code Explorer Tree</code> ด้านข้าง<br>
3. <b>📋 Concurrent Requests Explorer</b>: ค้นหา Concurrent Programs ทุกฟิลด์ พร้อมคลิกเปิดโค้ด Package Body และดู Log File ได้ทันที<br>
4. <b>🔌 Multi-Database Sessions</b>: เชื่อมต่อหลาย Database พร้อมกัน (เช่น UAT / PROD) สลับใช้งานได้ด้วยคลิกเดียว<br>
5. <b>📥 Data Export Wizard (SQL Navigator Format)</b>: ส่งออกข้อมูลเป็น CSV (Thai BOM), Excel, SQL INSERT Statements, JSON, Text<br>
6. <b>📐 Draggable Resizer UI</b>: ลากปรับขนาดแถบ Sidebar และหน้าต่างโค้ดได้ตามต้องการแบบอิสระ!"""

    elif "ADI_CUSTOM" in prompt_upper or "ADI" in prompt_upper:
        return f"""<b>ข้อมูลวิเคราะห์สำหรับ <code>ADI_CUSTOM</code> / Oracle Web ADI:</b><br><br>
1. <b>คำอธิบาย</b>: <code>ADI_CUSTOM</code> คือ Package / Integrator แบบคัสตอมสำหรับ Oracle Desktop Integrator (Web ADI) เพื่ออัปโหลดข้อมูลจาก Excel สู่ Oracle EBS<br>
2. <b>ตาราง EBS ที่เกี่ยวข้อง</b>:<br>
• <code>BNE_INTEGRATORS_B / _TL</code>: เก็บรายชื่อ Web ADI Integrators<br>
• <code>BNE_INTERFACE_COLS_B</code>: เก็บโครงสร้างคอลัมน์อินเทอร์เฟซ<br>
• <code>BNE_LAYOUT_COLS</code>: จัดรูปแบบการแสดงผลบน Excel<br><br>
3. <b>การคิวรีหา Integrator</b>:<br>
<code>SELECT * FROM bne_integrators_tl WHERE user_name LIKE '%ADI_CUSTOM%';</code>"""

    elif "PTAR_GOLD_INVOICE_PKG" in prompt_upper:
        return f"""<b>วิเคราะห์โดย Antigravity Model [{target_model}] สำหรับ <code>PTAR_GOLD_INVOICE_PKG</code>:</b><br><br>
1. <b>วัตถุประสงค์</b>: สร้างรายงานและ XML ใบกำกับภาษีทองรูปพรรณ / การขายทอง<br>
2. <b>การทำงานหลัก</b>:
- อ่านค่าพารามิเตอร์ <code>ORG_ID</code>, <code>TRAN_NUM_FROM</code>, <code>TRAN_NUM_TO</code>, <code>COPY_NUM</code>
- ดึงข้อมูลยอดขายทองคำจาก <code>RA_CUSTOMER_TRX_ALL</code> และ <code>PTWINV_GOLD_SALE_ORDER_V</code>
- กรณีสินค้าคือ <code>ทองรูปพรรณ 96.5%</code> จะทำการคำนวณหักราคาซื้อทองรูปพรรณ (Deduct Purchase Price) ด้วยสูตร <code>ROUND(NVL(H_ATTRIBUTE7,0), 2) / 15.16 * QTY</code> แล้วหาฐานภาษีมูลค่าเพิ่มสุทธิ<br>
- สร้าง XML Output ตามจำนวนสำเนา <code>COPY_NUM</code> (เช่น 3 ชุด)"""
    
    elif "CONCURRENT" in prompt_upper or "REP - AR GOLD" in prompt_upper:
        return f"""<b>คำแนะนำค้นหา Concurrent Program [{target_model}]:</b><br><br>
ในแถบ <b>📋 Concurrent Requests</b> คุณสามารถเลือกประเภทการค้นหาได้จาก Dropdown:<br>
• <b>Concurrent Program Name</b>: เช่น <code>REP - AR Gold Invoice Form</code><br>
• <b>Short Name</b>: เช่น <code>PTAR_GOLD_INVOICE_2</code> หรือ <code>XLAACCPB</code><br>
• <b>Request ID</b>: เช่น <code>17486162</code> หรือ <code>16417307</code><br>
• <b>User Name</b>: เช่น <code>MERCURY</code> หรือ <code>HIMSINGH</code><br><br>
ระบบจะค้นหาข้อมูลให้อย่างแม่นยำด้วยการ Join ทุกตารางผ่าน Outer Joins ครับ!"""

    else:
        return f"""คำถามของคุณ: <b>"{prompt}"</b><br><br>
วิเคราะห์โดย Antigravity AI 🪐 [{target_model}]:<br>
ระบบรับทราบคำถามเรียบร้อยแล้ว หากต้องการผลการวิเคราะห์เจาะลึกผ่าน Gemini AI โดยตรง สามารถใส่ <b>Google Gemini API Key</b> ในช่องด้านบนแล้วกด <b>💾 Save Key</b> ได้เลยครับ!"""

STANDALONE_EDITOR_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <title>VS Code PL/SQL Editor - {{ name }}</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/loader.min.js"></script>
    <style>
        body { background: #1e1e1e; color: #d4d4d4; font-family: 'Outfit', sans-serif; padding: 12px; margin: 0; display: flex; flex-direction: column; height: 100vh; box-sizing: border-box; }
        .header { display: flex; justify-content: space-between; align-items: center; background: #252526; padding: 10px 16px; border-radius: 6px; border: 1px solid #3c3c3c; margin-bottom: 10px; }
        .editor-workspace { display: flex; flex: 1; gap: 0; overflow: hidden; position: relative; }
        .code-explorer { width: 240px; background: #252526; border: 1px solid #3c3c3c; border-radius: 6px 0 0 6px; padding: 10px; display: flex; flex-direction: column; overflow-y: auto; flex-shrink: 0; }
        .code-explorer-title { font-size: 0.8rem; font-weight: 700; text-transform: uppercase; color: #858585; border-bottom: 1px solid #3c3c3c; padding-bottom: 6px; margin-bottom: 8px; }
        .tree-node { padding: 4px 8px; border-radius: 4px; font-size: 0.83rem; cursor: pointer; display: flex; align-items: center; gap: 6px; font-family: 'JetBrains Mono', monospace; }
        .tree-node:hover { background: #37373d; color: #fff; }
        .tree-node.proc { color: #569cd6; }
        .tree-node.func { color: #dcdcaa; }
        .tree-node.var { color: #4ec9b0; }
        #editorContainer { flex: 1; border: 1px solid #3c3c3c; border-radius: 0 6px 6px 0; overflow: hidden; }
        
        /* Draggable Resizer Splitter */
        .resizer-v { width: 6px; cursor: col-resize; background: #334155; hover: background #6366f1; transition: background 0.2s; z-index: 10; }
        .resizer-v:hover { background: #6366f1; }

        .btn { background: #0e639c; color: #fff; border: none; padding: 7px 16px; border-radius: 4px; font-weight: 600; cursor: pointer; display:flex; align-items:center; gap:6px; font-size:0.88rem; }
        .btn-success { background: #10b981; }
        .btn:hover { filter: brightness(1.15); }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <span style="font-weight:700; font-size:1.15rem; color:#569cd6;">📦 {{ name }}</span>
            <span style="background:#3c3c3c; color:#ce9178; padding:3px 8px; border-radius:4px; font-size:0.78rem; font-weight:600; margin-left:10px;">{{ obj_type }}</span>
        </div>
        <button class="btn btn-success" onclick="compilePLSQL()">⚡ Compile / Save to DB</button>
    </div>
    
    <div class="editor-workspace">
        <div class="code-explorer" id="codeExplorerPanel">
            <div class="code-explorer-title">🌳 Code Explorer</div>
            <div id="treeContent" style="display:flex; flex-direction:column; gap:2px;">
                <div style="font-size:0.75rem; color:#858585;">Loading structure...</div>
            </div>
        </div>

        <div class="resizer-v" id="explorerResizer"></div>

        <div id="editorContainer"></div>
    </div>

    <div id="console" style="margin-top:8px; padding:10px; background:#1e1e1e; border:1px solid #3c3c3c; border-radius:6px; font-family:'JetBrains Mono', monospace; font-size:0.83rem; height:85px; overflow-y:auto; color:#9cdcfe;">Ready.</div>

    <script>
        const name = "{{ name }}";
        const objType = "{{ obj_type }}";
        let monacoEditor = null;

        require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' }});
        require(['vs/editor/editor.main'], function() {
            monacoEditor = monaco.editor.create(document.getElementById('editorContainer'), {
                value: "-- Loading PL/SQL source code...",
                language: 'sql',
                theme: 'vs-dark',
                fontSize: 14,
                fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
                automaticLayout: true,
                minimap: { enabled: true },
                scrollBeyondLastLine: false,
                lineNumbers: "on",
                renderLineHighlight: "all",
                cursorBlinking: "smooth"
            });
            loadSource();
        });

        // Draggable Resizer for Code Explorer Panel
        const resizer = document.getElementById('explorerResizer');
        const explorer = document.getElementById('codeExplorerPanel');
        let isDragging = false;

        resizer.addEventListener('mousedown', (e) => {
            isDragging = true;
            document.body.style.cursor = 'col-resize';
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            const newWidth = e.clientX - 12;
            if (newWidth >= 120 && newWidth <= 500) {
                explorer.style.width = newWidth + 'px';
                if (monacoEditor) monacoEditor.layout();
            }
        });

        document.addEventListener('mouseup', () => {
            isDragging = false;
            document.body.style.cursor = 'default';
        });

        async function loadSource() {
            try {
                const res = await fetch(`/api/plsql/source?name=${encodeURIComponent(name)}&type=${encodeURIComponent(objType)}`);
                const data = await res.json();
                if (data.success && monacoEditor) {
                    monacoEditor.setValue(data.code);
                    renderCodeExplorer(data.outline);
                    if (data.target_line && typeof jumpToLine === 'function') {
                        setTimeout(() => jumpToLine(data.target_line), 150);
                    }
                    const jumpMsg = data.sub_program ? ` (Navigated to ${data.sub_program} @ line ${data.target_line || 1})` : '';
                    document.getElementById('console').innerHTML = `<span style="color:#4ec9b0;">Loaded ${data.line_count} lines of code for ${data.type} ${data.name}${jumpMsg}. Ready to edit and compile.</span>`;
                } else {
                    document.getElementById('console').innerHTML = `<span style="color:#f14c4c;">Error: ${data.error}</span>`;
                }
            } catch(e) {
                document.getElementById('console').innerHTML = `<span style="color:#f14c4c;">Network Error: ${e.message}</span>`;
            }
        }

        function renderCodeExplorer(outline) {
            const tree = document.getElementById('treeContent');
            if (!outline || outline.length === 0) {
                tree.innerHTML = '<div style="font-size:0.75rem; color:#858585;">No procedures or functions found.</div>';
                return;
            }

            let html = '';
            outline.forEach(item => {
                const icon = item.type === 'PROCEDURE' ? 'p()' : (item.type === 'FUNCTION' ? 'f()' : 'v');
                const cls = item.type === 'PROCEDURE' ? 'proc' : (item.type === 'FUNCTION' ? 'func' : 'var');
                html += `
                    <div class="tree-node ${cls}" onclick="jumpToLine(${item.line})">
                        <span><b>${icon}</b> ${item.name}</span>
                    </div>
                `;
            });
            tree.innerHTML = html;
        }

        function jumpToLine(line) {
            if (!monacoEditor) return;
            monacoEditor.revealLineInCenter(line);
            monacoEditor.setPosition({ lineNumber: line, column: 1 });
            monacoEditor.focus();
        }

        window.onerror = function(message, source, lineno, colno, error) {
            const select = document.getElementById('tnsSelect');
            if(select) select.innerHTML = `<option value="">JS Error: ${message}</option>`;
            console.error(error);
        };
        window.addEventListener("unhandledrejection", function(event) { 
            const select = document.getElementById('tnsSelect');
            if(select) select.innerHTML = `<option value="">Promise Error: ${event.reason}</option>`;
            console.error(event.reason);
        });

        async function compilePLSQL() {
            if (!monacoEditor) return;
            const code = monacoEditor.getValue().trim();
            const consoleBox = document.getElementById('console');
            consoleBox.innerHTML = '<span style="color:#ce9178;">Compiling DDL in Oracle DB...</span>';

            try {
                const res = await fetch('/api/plsql/compile', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: name, type: objType, code: code })
                });
                const data = await res.json();

                if (data.success) {
                    consoleBox.innerHTML = `<span style="color:#4ec9b0; font-weight:600;">✅ ${data.message}</span>`;
                } else if (data.errors) {
                    let html = `<div style="color: #f14c4c; font-weight: 600; font-size: 0.95rem; margin-bottom: 8px;">❌ ${data.message} (${data.errors.length} ข้อผิดพลาด)</div>`;
                    html += `<div style="display:flex; flex-direction:column; gap:6px;">`;
                    data.errors.forEach((e) => {
                        let errText = String(e.text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
                        let errCodeMatch = errText.match(/^(PLS-\d+|ORA-\d+):\s*(.*)/);
                        let codeTag = '';
                        let mainReason = errText;
                        if (errCodeMatch) {
                            codeTag = `<span style="background: rgba(241, 76, 76, 0.2); color: #fca5a5; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; margin-right: 6px; border: 1px solid rgba(241,76,76,0.3);">${errCodeMatch[1]}</span>`;
                            mainReason = errCodeMatch[2];
                        }
                        
                        html += `
                            <div class="error-card" style="background: #1e1212; border: 1px solid rgba(241,76,76,0.2); border-top: 2px solid rgba(241,76,76,0.55); padding: 8px 12px; border-radius: 6px; cursor: pointer; transition: background 0.15s; display: flex; flex-direction: column; gap: 4px;"
                                 onmouseover="this.style.background='#2d2d2d'" onmouseout="this.style.background='#252526'"
                                 onclick="if(monacoEditor){monacoEditor.revealLineInCenter(${e.line}); monacoEditor.setPosition({lineNumber: ${e.line}, column: ${e.position}}); monacoEditor.focus();}">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <div style="font-size: 0.85rem; font-family: 'JetBrains Mono', monospace;">
                                        <span style="color: #569cd6; font-weight: bold;">Line ${e.line}</span>
                                        <span style="color: #858585;"> : Col ${e.position}</span>
                                    </div>
                                    <button style="background: #333; color: #d4d4d4; border: 1px solid #444; border-radius: 4px; padding: 2px 8px; font-size: 0.75rem; cursor: pointer;">🔍 ย้ายไป (Go)</button>
                                </div>
                                <div style="color: #d4d4d4; font-size: 0.85rem; line-height: 1.4; margin-top: 4px;">
                                    ${codeTag}${mainReason}
                                </div>
                            </div>
                        `;
                    });
                    html += `</div>`;
                    consoleBox.innerHTML = html;
                } else {
                    consoleBox.innerHTML = `<span style="color:#f14c4c;">❌ Error: ${data.error}</span>`;
                }
            } catch(e) {
                consoleBox.innerHTML = `<span style="color:#f14c4c;">Network Error: ${e.message}</span>`;
            }
        }

        async function backupCode(type) {
            let code = '';
            let defaultName = '';
            if (type === 'SQL') {
                code = sqlMonacoEditor ? sqlMonacoEditor.getValue() : '';
                defaultName = 'query_backup.sql';
            } else if (type === 'PLSQL') {
                code = mainMonacoEditor ? mainMonacoEditor.getValue() : '';
                defaultName = 'plsql_backup.sql';
            }
            
            if (!code) {
                alert("ไม่มีโค้ดให้ Backup");
                return;
            }

            // Show "choosing file" message (Same as export)
            let existModal = document.getElementById('exportProgressModal');
            if (existModal) existModal.remove();
            document.body.insertAdjacentHTML('beforeend', `
                <div class="modal-backdrop" id="exportProgressModal" style="display:flex; z-index:9999;">
                    <div class="modal-content" style="width: 420px; text-align:center; padding:30px;">
                        <h3 style="margin-top:0; color:var(--accent-teal);">📁 กำลังเตรียมบันทึก...</h3>
                        <div id="exportProgressText" style="color:var(--text-muted); font-size:0.9rem; margin-top:10px;">หน้าต่าง Save As กำลังเปิดขึ้น...</div>
                    </div>
                </div>
            `);

            try {
                // Use Flask endpoint which successfully runs PowerShell dialog without blocking GUI
                const res = await fetch('/api/save_backup', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({content: code, filename: defaultName})
                });
                const data = await res.json();
                
                const m = document.getElementById('exportProgressModal');
                if (m) m.remove();

                if (data.success) {
                    alert('✅ บันทึกสำเร็จ!\nSaved to: ' + data.path);
                } else if (data.error !== 'Cancelled') {
                    alert('❌ Error: ' + data.error);
                }
            } catch (err) {
                const m = document.getElementById('exportProgressModal');
                if (m) m.remove();
                alert('Backup failed: ' + err.message);
            }
        }

        async function genDDL() {
            if (!activePlsqlName) {
                alert("กรุณาเลือก Package, Procedure หรือ Function จากแถบด้านซ้ายก่อน (Please select an object first)");
                return;
            }
            
            mainMonacoEditor.setValue("-- Generating DDL for " + activePlsqlName + "...\n-- Please wait...");
            
            try {
                const res = await fetch('/api/gen_ddl', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ object_name: activePlsqlName })
                });
                const data = await res.json();
                if (data.success) {
                    mainMonacoEditor.setValue(data.ddl);
                } else {
                    mainMonacoEditor.setValue("-- Error generating DDL: \n-- " + data.error);
                }
            } catch (err) {
                mainMonacoEditor.setValue("-- Network Error: " + err.message);
            }
        }
    </script>

<!-- Modal: Live Concurrent Tracker -->
<div id="liveTrackerModal" class="modal" style="display:none; position:fixed; z-index:99999; left:0; top:0; width:100%; height:100%; background:rgba(0,0,0,0.75); backdrop-filter:blur(4px); align-items:center; justify-content:center;">
    <div class="modal-content" style="background:#0f172a; border:1px solid #334155; border-radius:12px; width:850px; max-width:95vw; max-height:90vh; display:flex; flex-direction:column; box-shadow:0 25px 50px -12px rgba(0,0,0,0.5); overflow:hidden;">
        <!-- Header -->
        <div style="display:flex; justify-content:space-between; align-items:center; padding:14px 20px; background:#1e293b; border-bottom:1px solid #334155;">
            <div style="display:flex; align-items:center; gap:10px;">
                <span style="font-size:1.3rem;">⚡</span>
                <div>
                    <div style="font-size:1.05rem; font-weight:700; color:#f8fafc;" id="ltModalTitle">Concurrent Live Tracker</div>
                    <div style="font-size:0.75rem; color:#94a3b8;" id="ltModalSubtitle">Real-Time Oracle Database Progress Monitor</div>
                </div>
            </div>
            <div style="display:flex; align-items:center; gap:12px;">
                <label style="display:flex; align-items:center; gap:6px; font-size:0.78rem; color:#38bdf8; cursor:pointer;">
                    <input type="checkbox" id="ltAutoRefreshToggle" checked onchange="toggleLtAutoRefresh()"> Auto-Refresh (2s)
                </label>
                <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.78rem;" onclick="refreshLiveTracker()">🔄 Refresh</button>
                <button style="background:transparent; border:none; color:#94a3b8; font-size:1.3rem; cursor:pointer; padding:0 4px;" onclick="closeLiveTracker()">✕</button>
            </div>
        </div>

        <!-- Body -->
        <div style="padding:20px; overflow-y:auto; flex:1; display:flex; flex-direction:column; gap:16px;" id="ltBody">
            <div style="text-align:center; padding:40px; color:#94a3b8;">
                <span class="loading-spinner"></span> Connecting to database session...
            </div>
        </div>
    </div>
</div>

<script>
let ltActiveReqId = null;
let ltTimer = null;

function openLiveTrackerPrompt() {
    const inputVal = document.getElementById('concSearchInput') ? document.getElementById('concSearchInput').value.trim() : '';
    let defaultReq = inputVal && /^\d+$/.test(inputVal) ? inputVal : '229203433';
    const reqId = prompt("ใส่ Request ID ที่ต้องการดูจุดที่กำลังรันอยู่ (Real-Time Live Monitor):", defaultReq);
    if (reqId && reqId.trim()) {
        openLiveTracker(reqId.trim());
    }
}

function openLiveTracker(reqId) {
    ltActiveReqId = reqId;
    const modal = document.getElementById('liveTrackerModal');
    if (modal) modal.style.display = 'flex';
    const title = document.getElementById('ltModalTitle');
    if (title) title.textContent = `Concurrent Live Tracker: Request #${reqId}`;
    refreshLiveTracker();
    startLtTimer();
}

function closeLiveTracker() {
    const modal = document.getElementById('liveTrackerModal');
    if (modal) modal.style.display = 'none';
    ltActiveReqId = null;
    if (ltTimer) {
        clearInterval(ltTimer);
        ltTimer = null;
    }
}

function toggleLtAutoRefresh() {
    const toggle = document.getElementById('ltAutoRefreshToggle');
    if (toggle && toggle.checked) {
        startLtTimer();
    } else {
        if (ltTimer) {
            clearInterval(ltTimer);
            ltTimer = null;
        }
    }
}

function startLtTimer() {
    if (ltTimer) clearInterval(ltTimer);
    ltTimer = setInterval(() => {
        const modal = document.getElementById('liveTrackerModal');
        if (ltActiveReqId && modal && modal.style.display === 'flex') {
            refreshLiveTracker();
        } else {
            clearInterval(ltTimer);
            ltTimer = null;
        }
    }, 2000);
}

async function refreshLiveTracker() {
    if (!ltActiveReqId) return;
    try {
        const res = await fetch(`/api/concurrent/live_monitor?request_id=${ltActiveReqId}`);
        const result = await res.json();
        const body = document.getElementById('ltBody');

        if (!result.success) {
            body.innerHTML = `<div class="alert alert-error">${result.error}</div>`;
            return;
        }

        const d = result.data;
        const s = d.session;

        let statusBadge = '';
        if (d.phase_code === 'R') {
            statusBadge = '<span style="background:#22c55e; color:#000; font-weight:bold; padding:3px 10px; border-radius:20px; font-size:0.75rem; display:inline-flex; align-items:center; gap:6px;"><span style="width:8px; height:8px; background:#000; border-radius:50%; display:inline-block;"></span> RUNNING</span>';
        } else if (d.phase_code === 'C') {
            statusBadge = `<span style="background:${d.status_code === 'C' ? '#3b82f6' : '#ef4444'}; color:#fff; font-weight:bold; padding:3px 10px; border-radius:20px; font-size:0.75rem;">COMPLETED (${d.status_code})</span>`;
        } else {
            statusBadge = `<span style="background:#f59e0b; color:#000; font-weight:bold; padding:3px 10px; border-radius:20px; font-size:0.75rem;">${d.phase_code} / ${d.status_code}</span>`;
        }

        const elapsedMins = (d.elapsed_sec / 60).toFixed(2);

        let sessionHtml = '';
        if (s) {
            sessionHtml = `
                <div style="background:linear-gradient(135deg, rgba(30,58,138,0.5), rgba(15,23,42,0.8)); border:1px solid #3b82f6; border-radius:8px; padding:14px 18px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div style="font-size:0.72rem; color:#93c5fd; text-transform:uppercase; font-weight:700; letter-spacing:0.5px;">📍 กำลังรันอยู่ในขั้นตอน (CURRENT STAGE)</div>
                        <div style="font-size:1.15rem; font-weight:700; color:#38bdf8; margin-top:2px;">${s.stage_description || 'Processing'}</div>
                        <div style="font-size:0.82rem; color:#cbd5e1; margin-top:4px;">
                            <strong>Package:</strong> <span style="color:#fcd34d;">${s.entry_object || s.current_object || '-'}</span>
                            &nbsp;➔&nbsp; <strong>Procedure:</strong> <span style="color:#4ade80;">${s.entry_procedure || s.current_procedure || '-'}</span>
                        </div>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:0.72rem; color:#94a3b8;">SID / SERIAL</div>
                        <div style="font-size:1.05rem; font-weight:700; color:#f8fafc;">${s.sid}, ${s.serial}</div>
                        <div style="font-size:0.75rem; color:#a7f3d0; margin-top:2px;">Buffer Gets: <strong>${(s.buffer_gets || 0).toLocaleString()}</strong></div>
                    </div>
                </div>

                <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                    <div style="background:#1e293b; border:1px solid #334155; border-radius:8px; padding:12px;">
                        <div style="font-size:0.72rem; color:#94a3b8; font-weight:600;">WAIT EVENT (สิ่งที่ระบบกำลังทำ)</div>
                        <div style="font-size:0.95rem; font-weight:700; color:#fbbf24; margin-top:3px;">${s.event || 'CPU (Processing in Memory)'}</div>
                        <div style="font-size:0.75rem; color:#94a3b8; margin-top:2px;">Wait: ${s.seconds_in_wait || 0}s &nbsp;|&nbsp; Status: <strong>${s.status}</strong></div>
                    </div>
                    <div style="background:#1e293b; border:1px solid #334155; border-radius:8px; padding:12px;">
                        <div style="font-size:0.72rem; color:#94a3b8; font-weight:600;">ACTIVE SQL ID</div>
                        <div style="font-size:0.95rem; font-weight:700; color:#a5b4fc; margin-top:3px;">${s.sql_id || 'PL/SQL Internal'}</div>
                        <div style="font-size:0.75rem; color:#94a3b8; margin-top:2px;">Module: ${s.module || '-'}</div>
                    </div>
                </div>

                <div style="background:#090d16; border:1px solid #1e293b; border-radius:8px; padding:12px; display:flex; flex-direction:column; gap:6px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-size:0.72rem; color:#94a3b8; font-weight:700;">⚡ ACTIVE SQL STATEMENT (คำสั่งที่กำลังรันในฐานข้อมูล)</span>
                    </div>
                    <pre style="background:#020617; border:1px solid #1e293b; border-radius:6px; padding:10px; font-family:'Fira Code', monospace; font-size:0.76rem; color:#38bdf8; max-height:160px; overflow-y:auto; white-space:pre-wrap; margin:0;">${escapeHtml(s.sql_text || 'No Active SQL Statement captured (running PL/SQL calculation)')}</pre>
                </div>
            `;
        } else {
            sessionHtml = `
                <div style="background:#1e293b; border:1px solid #334155; border-radius:8px; padding:20px; text-align:center; color:#94a3b8;">
                    ${d.is_running ? '⚠️ Process is active on App Server, but no dedicated Oracle DB Session found (may be post-processing or writing report).' : '✅ Program execution finished or session disconnected.'}
                </div>
            `;
        }

        body.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; background:#1e293b; border-radius:8px; padding:12px 18px; border:1px solid #334155;">
                <div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span style="font-size:1.1rem; font-weight:700; color:#fff;">Request #${d.request_id}</span>
                        ${statusBadge}
                    </div>
                    <div style="font-size:0.85rem; color:#cbd5e1; margin-top:3px; font-weight:600;">${escapeHtml(d.program_name)}</div>
                    <div style="font-size:0.75rem; color:#94a3b8;">Short Name: <code style="color:#a5b4fc;">${d.program_code}</code> &nbsp;|&nbsp; Start Date: ${d.start_date}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:0.72rem; color:#94a3b8; text-transform:uppercase;">เวลารวมที่ใช้ไป</div>
                    <div style="font-size:1.4rem; font-weight:700; color:#38bdf8;">${elapsedMins} <span style="font-size:0.85rem; font-weight:normal; color:#94a3b8;">นาที</span></div>
                    <div style="font-size:0.72rem; color:#64748b;">(${d.elapsed_sec} วินาที)</div>
                </div>
            </div>
            ${sessionHtml}
        `;

    } catch (err) {
        document.getElementById('ltBody').innerHTML = `<div class="alert alert-error">Failed to fetch live progress: ${err.message}</div>`;
    }
}
</script>

</body>
</html>
"""

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <title>JNavigator v1.2 - Oracle SQL &amp; PL/SQL Studio</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/loader.min.js"></script>
    <style>
        :root {
            /* === Base Background — Clean Dark Obsidian / Slate (GitHub & VS Code Modern) === */
            --bg-dark: #0d1117;
            --bg-deeper: #010409;
            --bg-surface: #161b22;
            --bg-elevated: #21262d;
            --bg-hover: #262c36;

            /* === Panel & Borders — Crisp 1px Borders === */
            --panel-bg: #161b22;
            --panel-border: #30363d;
            --panel-border-light: #3c444d;

            /* === Accent Colors — Enterprise Developer Blue === */
            --accent-primary: #1f6feb;
            --accent-primary-dim: #1158c7;
            --accent-glow: none;
            --accent-teal: #38bdf8;
            --accent-blue: #58a6ff;
            --accent-purple: #a371f7;
            --accent-gold: #d29922;

            /* === Text — High Legibility Neutral Grayscale === */
            --text-main: #e6edf3;
            --text-bright: #ffffff;
            --text-muted: #8b949e;
            --text-dim: #6e7681;

            /* === Status Colors === */
            --danger: #f85149;
            --danger-glow: transparent;
            --success: #3fb950;
            --success-glow: transparent;
            --warning: #d29922;

            /* === Syntax Code Palette === */
            --code-keyword: #ff7b72;
            --code-string: #a5d6ff;
            --code-number: #79c0ff;
            --code-comment: #8b949e;
            --code-function: #d2a8ff;
            --code-type: #ffa657;
            --code-variable: #e6edf3;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
        body {
            background-color: var(--bg-dark);
            color: var(--text-main);
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        /* Scrollbar styling — subtle and professional */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #484f58; }

        header {
            background: var(--bg-surface);
            border-bottom: 1px solid var(--panel-border);
            padding: 8px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            z-index: 100;
        }

        .logo {
            display: flex; align-items: center; gap: 10px; font-size: 1.05rem; font-weight: 600;
            color: var(--text-bright);
        }

        .header-left { display: flex; align-items: center; gap: 12px; }

        .logo-icon {
            width: 28px; height: 28px;
            background: var(--accent-primary);
            border-radius: 6px; display: flex; align-items: center; justify-content: center;
            color: #ffffff; font-weight: 700; font-size: 0.95rem;
        }

        .db-session-selector {
            display: flex; align-items: center; gap: 8px;
            background: var(--bg-dark);
            padding: 4px 10px; border-radius: 6px;
            border: 1px solid var(--panel-border);
            font-size: 0.8rem;
        }

        .db-session-selector select {
            background: transparent; border: none;
            color: var(--accent-blue); font-weight: 600;
            font-size: 0.8rem; outline: none; cursor: pointer;
        }

        .dot { width: 8px; height: 8px; border-radius: 50%; background-color: var(--danger); }
        .dot.connected { background-color: var(--success); }

        .app-container { display: flex; flex: 1; overflow: hidden; position: relative; }

        .sidebar {
            width: 340px;
            background: var(--panel-bg);
            border-right: 1px solid var(--panel-border);
            padding: 12px;
            display: flex; flex-direction: column; gap: 8px;
            overflow: hidden; flex-shrink: 0;
        }

        .sidebar-resizer {
            width: 4px; cursor: col-resize;
            background: var(--panel-bg);
            border-left: 1px solid var(--panel-border);
            transition: background 0.15s; z-index: 50;
        }
        .sidebar-resizer:hover, .sidebar-resizer.dragging { background: var(--accent-primary); }

        .section-title { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.8px; color: var(--text-dim); font-weight: 600; }

        details.conn-accordion { background: var(--bg-dark); border: 1px solid var(--panel-border); border-radius: 6px; padding: 8px 10px; flex-shrink: 0; }
        details.conn-accordion summary { outline: none; user-select: none; }

        .form-group { display: flex; flex-direction: column; gap: 4px; }
        label { font-size: 0.76rem; color: var(--text-muted); font-weight: 500; }

        input, select, textarea {
            background: var(--bg-dark);
            border: 1px solid var(--panel-border);
            color: var(--text-main);
            padding: 6px 9px; border-radius: 5px; font-size: 0.82rem;
            outline: none; transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }
        input:focus, select:focus, textarea:focus {
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 2px rgba(31, 111, 235, 0.25);
        }
        input::placeholder, textarea::placeholder { color: var(--text-dim); }

        .btn {
            background: var(--bg-elevated);
            border: 1px solid var(--panel-border);
            color: var(--text-main);
            padding: 6px 12px; border-radius: 5px; font-weight: 500;
            cursor: pointer; display: flex; align-items: center; justify-content: center;
            gap: 6px; transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease; font-size: 0.81rem;
            user-select: none;
        }
        .btn:hover { background: var(--bg-hover); border-color: var(--panel-border-light); color: var(--text-bright); }
        .btn:active { transform: scale(0.98); }
        .btn-primary { background: var(--accent-primary); border-color: var(--accent-primary); color: #ffffff; font-weight: 600; }
        .btn-primary:hover { background: var(--accent-primary-dim); border-color: var(--accent-primary-dim); }
        .btn-success {
            background: #238636;
            border-color: #2ea043;
            color: #ffffff;
            font-weight: 600;
        }
        .btn-success:hover { background: #2ea043; border-color: #3fb950; }
        .btn-secondary { background: var(--bg-surface); color: var(--text-muted); border-color: var(--panel-border); }
        .btn-secondary:hover { color: var(--text-main); background: var(--bg-elevated); border-color: var(--panel-border-light); }

        .tns-card { background: var(--bg-dark); border: 1px solid var(--panel-border); border-radius: 5px; padding: 8px 10px; font-size: 0.73rem; color: var(--text-muted); line-height: 1.4; }
        .tns-card span { color: var(--accent-blue); font-weight: 600; }

        .explorer-header { display: flex; flex-direction: column; gap: 6px; background: var(--bg-dark); padding: 10px; border-radius: 6px; border: 1px solid var(--panel-border); flex-shrink: 0; }
        .schema-toggle { display: flex; align-items: center; gap: 6px; font-size: 0.74rem; color: var(--accent-blue); cursor: pointer; user-select: none; }
        .filter-pills { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }

        .pill { padding: 4px 8px; border-radius: 4px; font-size: 0.68rem; font-weight: 600; cursor: pointer; background: var(--bg-surface); color: var(--text-muted); border: 1px solid var(--panel-border); transition: all 0.12s ease; }
        .pill.active { background: var(--accent-primary); color: #ffffff; border-color: var(--accent-primary); }
        .pill:hover:not(.active) { border-color: var(--panel-border-light); color: var(--text-main); background: var(--bg-hover); }

        .table-list { flex: 1; overflow-y: auto; background: var(--bg-dark); border: 1px solid var(--panel-border); border-radius: 6px; padding: 6px; }
        .table-item { padding: 5px 8px; border-radius: 4px; font-size: 0.8rem; cursor: pointer; display: flex; align-items: center; justify-content: space-between; transition: background 0.12s ease; color: var(--text-muted); margin-bottom: 2px; }
        .table-item:hover { background: var(--bg-hover); color: var(--text-main); }
        .table-item.active { background: rgba(31, 111, 235, 0.15); color: var(--text-bright); font-weight: 600; border-radius: 4px; border: 1px solid rgba(31, 111, 235, 0.3); }

        /* Type badges — clean, subtle */
        .type-badge { font-size: 0.63rem; padding: 2px 5px; border-radius: 3px; font-weight: 600; letter-spacing: 0.3px; }
        .type-badge.TABLE { background: rgba(56, 139, 253, 0.12); color: #58a6ff; border: 1px solid rgba(56, 139, 253, 0.25); }
        .type-badge.VIEW { background: rgba(63, 185, 80, 0.12); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.25); }
        .type-badge.PACKAGE, .type-badge.PACKAGE_BODY { background: rgba(210, 153, 34, 0.12); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.25); }
        .type-badge.PROCEDURE, .type-badge.FUNCTION, .type-badge.TRIGGER { background: rgba(163, 113, 247, 0.12); color: #bc8cff; border: 1px solid rgba(163, 113, 247, 0.25); }

        .workspace { display: flex; flex-direction: column; flex: 1; background: var(--bg-dark); overflow: hidden; }
        .mode-tabs { display: flex; background: var(--bg-deeper); border-bottom: 1px solid var(--panel-border); padding: 0 16px; overflow-x: auto; gap: 4px; }
        .mode-tab { padding: 9px 14px; font-weight: 500; font-size: 0.82rem; color: var(--text-muted); cursor: pointer; border-bottom: 2px solid transparent; display: flex; align-items: center; gap: 8px; transition: all 0.12s; white-space: nowrap; border-radius: 4px 4px 0 0; }
        .mode-tab:hover { color: var(--text-main); background: rgba(255, 255, 255, 0.03); }
        .mode-tab.active { color: var(--text-bright); border-bottom-color: var(--accent-primary); background: var(--bg-surface); font-weight: 600; }

        .tab-view { display: none; flex: 1; flex-direction: column; overflow: hidden; }
        .tab-view.active { display: flex; }

        .sql-subtabs { display: flex; background: var(--bg-deeper); padding: 6px 12px; gap: 6px; border-bottom: 1px solid var(--panel-border); overflow-x: auto; }
        .sql-subtab { padding: 4px 12px; font-size: 0.78rem; background: var(--bg-surface); color: var(--text-muted); border-radius: 4px; cursor: pointer; display: flex; align-items: center; gap: 8px; border: 1px solid var(--panel-border); transition: all 0.12s; }
        .sql-subtab:hover { color: var(--text-main); border-color: var(--panel-border-light); }
        .sql-subtab.active { background: var(--bg-elevated); color: var(--text-bright); border-color: var(--panel-border-light); font-weight: 600; }
        .sql-subtab .close-btn { font-size: 0.68rem; padding: 1px 4px; border-radius: 3px; }
        .sql-subtab .close-btn:hover { background: rgba(255,255,255,0.15); color: #fff; }

        .sql-layout { display: flex; flex-direction: column; flex: 1; overflow: hidden; }
        .editor-section { border-bottom: 1px solid var(--panel-border); padding: 8px 12px; display: flex; flex-direction: column; gap: 8px; background: var(--bg-surface); height: 220px; flex-shrink: 0; }

        #sqlEditor {
            flex: 1;
            width: 100%;
            min-height: 50px;
            border: 1px solid var(--panel-border);
            border-radius: 4px;
            overflow: hidden;
        }
        .sql-resizer-h { height: 4px; cursor: row-resize; background: var(--panel-border); flex-shrink: 0; transition: background 0.15s; z-index: 20; position: relative; }
        .sql-resizer-h:hover { background: var(--accent-primary); }

        .results-section { display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; background: var(--bg-dark); }
        .results-header { padding: 6px 14px; background: var(--bg-surface); border-bottom: 1px solid var(--panel-border); display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; color: var(--text-muted); flex-shrink: 0; flex-wrap: wrap; gap: 10px; }
        .table-wrapper { flex: 1; overflow: auto; }

        /* Data Grid — VS Code / DataGrip Style */
        table.data-grid { width: 100%; border-collapse: collapse; font-size: 0.81rem; font-family: 'JetBrains Mono', monospace; }
        table.data-grid th { position: sticky; top: 0; background: var(--bg-elevated); color: var(--text-muted); padding: 7px 12px; text-align: left; border-bottom: 1px solid var(--panel-border); font-weight: 600; white-space: nowrap; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; }
        table.data-grid td { padding: 5px 12px; border-bottom: 1px solid var(--panel-border); color: var(--text-main); white-space: nowrap; max-width: 350px; overflow: hidden; text-overflow: ellipsis; }
        table.data-grid tr:nth-child(even) td { background: rgba(255, 255, 255, 0.012); }
        table.data-grid tr:hover td { background: rgba(31, 111, 235, 0.08); color: var(--text-bright); }

        /* Data Grid Helpers */
        .row-idx {
            width: 44px; min-width: 44px; max-width: 44px;
            text-align: center; color: var(--text-dim); font-size: 0.72rem;
            user-select: none; background: var(--bg-surface); border-right: 1px solid var(--panel-border);
        }
        .null-tag {
            color: var(--text-dim); font-size: 0.7rem; font-style: italic;
            background: rgba(255, 255, 255, 0.04); padding: 1px 5px; border-radius: 3px; user-select: none;
        }

        /* Keyboard Shortcut badge */
        kbd {
            background: var(--bg-elevated);
            border: 1px solid var(--panel-border);
            border-bottom: 2px solid var(--panel-border-light);
            border-radius: 4px;
            padding: 1px 5px;
            font-size: 0.72rem;
            font-family: inherit;
            color: var(--text-bright);
            box-shadow: 0 1px 1px rgba(0,0,0,0.2);
        }

        /* Reusable Empty State Box */
        .empty-state-box {
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            padding: 40px 20px; text-align: center; color: var(--text-muted);
        }
        .empty-state-icon { font-size: 2rem; margin-bottom: 10px; opacity: 0.8; }
        .empty-state-title { font-size: 0.92rem; font-weight: 600; color: var(--text-main); margin-bottom: 6px; }
        .empty-state-desc { font-size: 0.8rem; color: var(--text-muted); max-width: 450px; line-height: 1.5; }

        /* Status Badges */
        .badge-status {
            display: inline-flex; align-items: center; gap: 5px;
            padding: 2px 8px; border-radius: 12px; font-size: 0.72rem; font-weight: 600;
        }
        .badge-status.normal { background: rgba(63, 185, 80, 0.12); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.25); }
        .badge-status.running { background: rgba(56, 139, 253, 0.12); color: #58a6ff; border: 1px solid rgba(56, 139, 253, 0.25); }
        .badge-status.error { background: rgba(248, 81, 73, 0.12); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.25); }
        .badge-status.warning { background: rgba(210, 153, 34, 0.12); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.25); }

        /* PL/SQL Layout */
        .plsql-layout { padding: 10px; display: flex; flex-direction: column; gap: 8px; flex: 1; overflow: hidden; background: var(--bg-dark); }
        .plsql-header { display: flex; justify-content: space-between; align-items: center; background: var(--bg-surface); padding: 8px 12px; border-radius: 6px; border: 1px solid var(--panel-border); }

        .editor-workspace { display: flex; flex: 1; gap: 0; overflow: hidden; position: relative; }
        .code-explorer { width: 230px; background: var(--bg-surface); border: 1px solid var(--panel-border); border-radius: 6px 0 0 6px; padding: 10px; display: flex; flex-direction: column; overflow-y: auto; flex-shrink: 0; }
        .code-explorer-title { font-size: 0.74rem; font-weight: 600; text-transform: uppercase; color: var(--text-dim); border-bottom: 1px solid var(--panel-border); padding-bottom: 6px; margin-bottom: 8px; letter-spacing: 0.8px; }
        .tree-node { padding: 4px 8px; border-radius: 4px; font-size: 0.81rem; cursor: pointer; display: flex; align-items: center; gap: 6px; font-family: 'JetBrains Mono', monospace; color: var(--text-muted); }
        .tree-node:hover { background: var(--bg-hover); color: var(--text-main); }
        .tree-node.proc { color: var(--code-function); }
        .tree-node.func { color: var(--accent-gold); }
        .tree-node.var { color: var(--code-type); }

        #monacoEditorMainContainer { flex: 1; border: 1px solid var(--panel-border); border-radius: 0 6px 6px 0; overflow: hidden; }

        .tree-node-wrapper { display: flex; align-items: center; justify-content: space-between; }
        .btn-run-proc { background: rgba(56, 139, 253, 0.1); border: 1px solid rgba(56, 139, 253, 0.25); color: var(--accent-blue); font-size: 0.63rem; padding: 2px 6px; border-radius: 3px; cursor: pointer; display: none; }
        .tree-node-wrapper:hover .btn-run-proc { display: block; }
        .btn-run-proc:hover { background: rgba(56, 139, 253, 0.25); }

        .resizer-h { height: 4px; cursor: row-resize; background: var(--bg-surface); border-top: 1px solid var(--panel-border); border-bottom: 1px solid var(--panel-border); }
        .resizer-h:hover { background: var(--accent-primary); }

        .compile-console { height: 90px; background: var(--bg-surface); border: 1px solid var(--panel-border); border-radius: 6px; padding: 8px 12px; overflow-y: auto; font-family: 'JetBrains Mono', monospace; font-size: 0.81rem; color: var(--code-type); flex-shrink: 0; line-height: 1.6; }

        .conc-layout { padding: 12px; display: flex; flex-direction: column; gap: 10px; flex: 1; overflow: hidden; background: var(--bg-dark); }
        .conc-toolbar { display: flex; gap: 10px; align-items: center; background: var(--bg-surface); padding: 8px 12px; border-radius: 6px; border: 1px solid var(--panel-border); }

        /* Assistant Layout */
        .ai-layout { display: flex; flex-direction: column; flex: 1; overflow: hidden; background: var(--bg-dark); padding: 14px; gap: 10px; }
        .ai-chat-messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding-right: 8px; }
        .chat-bubble { max-width: 80%; padding: 10px 14px; border-radius: 6px; font-size: 0.85rem; line-height: 1.5; }
        .chat-bubble.user { align-self: flex-end; background: rgba(31, 111, 235, 0.15); border: 1px solid rgba(31, 111, 235, 0.3); color: var(--text-bright); }
        .chat-bubble.ai { align-self: flex-start; background: var(--bg-surface); color: var(--text-main); border: 1px solid var(--panel-border); }
        .chat-bubble code { background: rgba(0, 0, 0, 0.4); padding: 2px 6px; border-radius: 4px; font-family: 'JetBrains Mono', monospace; color: var(--accent-blue); font-size: 0.85em; }

        .ai-input-box { display: flex; gap: 8px; background: var(--bg-surface); padding: 8px; border-radius: 6px; border: 1px solid var(--panel-border); }
        .ai-input-box input { flex: 1; background: transparent; border: none; box-shadow: none; color: var(--text-main); }

        .quick-prompts { display: flex; gap: 6px; flex-wrap: wrap; }
        .prompt-chip { background: var(--bg-surface); border: 1px solid var(--panel-border); color: var(--text-muted); padding: 4px 10px; border-radius: 4px; font-size: 0.75rem; cursor: pointer; transition: all 0.12s ease; }
        .prompt-chip:hover { background: var(--bg-elevated); color: var(--text-main); border-color: var(--panel-border-light); }

        .modal-backdrop { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(1, 4, 9, 0.75); display: none; align-items: center; justify-content: center; z-index: 1000; }
        .modal-content { background: var(--bg-surface); border: 1px solid var(--panel-border); border-radius: 8px; width: 750px; max-width: 90vw; max-height: 85vh; display: flex; flex-direction: column; box-shadow: 0 16px 36px rgba(0,0,0,0.6); overflow: hidden; }
        .modal-header { padding: 12px 18px; background: var(--bg-elevated); border-bottom: 1px solid var(--panel-border); display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: var(--text-bright); font-size: 0.88rem; }
        .modal-body { padding: 16px; flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }

        .alert { padding: 8px 12px; border-radius: 5px; font-size: 0.8rem; margin-top: 4px; }
        .alert-error { background: rgba(248, 81, 73, 0.1); border: 1px solid rgba(248, 81, 73, 0.3); color: #f85149; }
        .alert-success { background: rgba(63, 185, 80, 0.1); border: 1px solid rgba(63, 185, 80, 0.3); color: #3fb950; }

        .loading-spinner { display: inline-block; width: 13px; height: 13px; border: 2px solid rgba(255,255,255,0.15); border-radius: 50%; border-top-color: var(--accent-primary); animation: spin 0.7s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        
        .context-menu-item { padding: 6px 12px; font-size: 0.8rem; cursor: pointer; border-radius: 4px; color: var(--text-main); margin-bottom: 2px; }
        .context-menu-item:hover { background: var(--accent-primary); color: #fff; }

        @keyframes opPopIn {
            0% { transform: scale(0.96); opacity: 0; }
            100% { transform: scale(1); opacity: 1; }
        }
        .op-card-anim { animation: opPopIn 0.15s ease-out forwards; }
    </style>
</head>

<body>

    <header>
        <div class="header-left">
            <button class="btn btn-secondary" style="padding: 6px; border-radius: 4px;" onclick="toggleSidebarCollapse()" title="Toggle Sidebar (Expand/Collapse)">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg>
            </button>
            <div class="logo">
                <img src="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCABgAGADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD5ZZuc1GzUM2aYSa9BswAmo2bPFDMTwKYTSEBPYUxmoZvyqNmppADNTckUHPrTCfehvsApP5VGzUM1MZscmpSAGao2NBNMZqAN8mmM2R14ods8CopHAGBTAo6xrFvpNv50uWZjhEHVj/h71yk3jDV5HJj8iJeyhM/qab4tnaXVdjH5Yo1Cj68msXjNMZsf8JXrX/PeL/v0KT/hKtZ/57RZ/wCuQrI4zWz4d8N6j4hv7fTNKsZ7y7upFihggjaSSVz0VVXJJPoKCZSUVdjP+Eo1jp50X/foUh8T6uePNix/1yFezxfsd/H90BPwe8UDIzzZY/rXK+P/ANnv4q/DjSk1zxn8Ptb0XT5Jhbpc3ltsjMhBIXOepCnH0NOzOeONoTfLGSv6o4SPxPqasDIYpF9NmP1Fb2n6nDqMXmx5Vhw6HqDXGSJsbae1aGgTGK/2g8OhBH05FJnSdYWqNm/OkDccdaQkAc1I9jfJqvK/GakZhUEzfLTA4bxGc6tIf9lf5VmVpeIT/wATSTj+Ff5Vm98AUxACM19AfshfEHwt8PPihDrPie/bTbe502902LVEi8xtNnni2R3QUcnaeDjkBifWvAlidugqe2nntZAUJGKuKa1MMTRWIpum+p91p8OPE+p6Xqt/4Y/aY0fxJc6Vp9xqktvZazfNNJDEu5254B6deMkCua8Q6rqWtfsf69Jq2p3t9IvjqxCvdXDzMF+yMcAuSQOc4rnP2StXklvPHKTSHB8C6vjJ77Uq7d3Sn9kLXI88nxvYnH/bma71Q5ocx8y4zpVvZyd7SjrZLf0Pk6/AW4YAd6k0g4v0+jfyqLUD/pLfWpNJ/wCP5Pof5V5stND6yOyOpRhtpGbNMQ5FBPYUkhm8xNQTMdtSMcnNQTHigZxfiA51N8f3V/lVBBucCr2vZOpP/ur/ACqgp2sDTjpuI9w+G3hT4daV8O5/iL480G/8QmbVBpNpptpem1WMiPe0sjjnPOFHTj34x/i78O9G8N6vY6n4Re5l8Pa7Zx3+nPOQzxhh88DMOGZG4J64I+tVvhr4y0+20vUfBniKRk0jWNjmVRlrW4T7kwH6H2rrrXV/F3hTTW0KbTLHxD4feQywxzQfabck874yPmQnqRX3GFy6jjcKpQWltWldp+fWz7nktVaVZyv8m9GvLomvxL37O8snh+08b69c5js4vC17ZSSngebNtWNB6kkdKs6nq32b9mi+0+ZtpvPFsDxA/wAfl2xDEfSsm71Xxd4usU0cWFl4d8PwOJpYooPs1spH/LRs8yMOw/8A11x3xN8b2Oo2mneE/Dzv/YuiI6wO/DXEznMkzD3PT2+tXisHTwOGcp6K1lfS78l2XcyVB1q3M1q2m/JLb53PNrpt0zGptLP+nJ9D/KqjMWYmrWmf8fqfQ18JN3bZ7a0OlQ8YFBNMQ/Lign3pDNxmqGY8U9jUT4IpIEchr6EagWOcMoI/Cs0jiup1SxS7TB4I6EdRWFJpdyjYAVvfOKadtA2IIZ2iYENg1uab4u1fTF22Wo3EA9I5CB+XSsf+z7n+4PzpPsFwP4R/31XXhsdWwj5qMmn5EuKlubGp+LNW1QBb7ULi4A6CSQkD8OlYkszSsSTmnfYZh/CP++qPsc39wfnSxOOrYuXNVk2/MIxUdiHFW9LUm7DDoAajFnMx6AfjWpY2qwL6k9TXI3cpIvjpS0g6UE9hRfQDXZqjZqUk0z60DsMkUN2qs8IParR9BTCO1DApm3XpimG3X0q5xTDg9qLAVPs6+lNNuPSrZ56imnA7UAVxAoPSpFUCn4ptFgFJ7Cm0UUWA/9k=" alt="JNavigator Logo" style="width:30px; height:30px; border-radius:6px; object-fit:cover; border:1px solid var(--panel-border);" onerror="this.style.display='none'; document.getElementById('logoFallback').style.display='flex';">
                <div id="logoFallback" class="logo-icon" style="display:none; width:30px; height:30px; background:var(--accent-primary); border-radius:6px; align-items:center; justify-content:center; color:#fff; font-weight:700; font-size:1rem; font-family:'JetBrains Mono', monospace;">J</div>
                <span style="color: var(--text-bright); font-weight: 600; font-size: 1.05rem; letter-spacing: 0.2px;">JNavigator v1.2</span>
            </div>
        </div>

        <!-- Multi-Database Connections Active Session Selector -->
        <div class="db-session-selector">
            <div id="statusDot" class="dot"></div>
            <span style="color:var(--text-muted); font-size:0.78rem;">DB Session:</span>
            <select id="dbSessionSelect" onchange="switchDbSession(this.value)">
                <option value="">-- No DB Connected --</option>
            </select>
        </div>
    </header>

    <div class="app-container">
        <!-- Sidebar -->
        <div class="sidebar" id="sidebarPanel">
            <details id="connDetails" open class="conn-accordion">
                <summary style="cursor:pointer; display:flex; justify-content:space-between; align-items:center;">
                    <span class="section-title">DB Connection Setup</span>
                    <span id="connSummaryText" style="font-size:0.72rem; color:var(--accent-blue); font-weight:500;">▼ Click to Fold</span>
                </summary>
                
                <div style="margin-top: 8px; display:flex; flex-direction:column; gap:8px;">
                    <div class="form-group">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:2px;">
                            <label for="tnsSelect">Select TNS Alias</label>
                            <button type="button" class="btn btn-secondary" style="padding: 2px 7px; font-size: 0.7rem; gap: 3px;" onclick="loadTnsList(0, true)" title="Reload TNS List">Refresh</button>
                        </div>
                        <select id="tnsSelect" onclick="if(this.options.length <= 1 && (!tnsData || tnsData.length === 0)) loadTnsList(0, true);">
                            <option value="">-- Loading TNS entries... --</option>
                        </select>
                    </div>

                    <div class="tns-card" id="tnsInfoCard">Select a TNS alias above to view connection details.</div>

                    <div class="form-group">
                        <label for="dbUser">Username</label>
                        <input type="text" id="dbUser" placeholder="e.g. APPS, SYSTEM">
                    </div>

                    <div class="form-group">
                        <label for="dbPassword">Password</label>
                        <input type="password" id="dbPassword" placeholder="Enter password">
                    </div>

                    <div style="display: flex; align-items: center; gap: 6px; margin: 4px 0 6px 0;">
                        <input type="checkbox" id="chkSavePassword" checked style="width: auto; margin: 0; cursor: pointer;">
                        <label for="chkSavePassword" style="color: var(--text-main); font-weight: 500; cursor: pointer;">Save Password (แยกตาม TNS Alias/Host)</label>
                    </div>

                    <button class="btn btn-primary" id="btnConnect" onclick="handleConnect()">
                        <span>Connect New DB Session</span>
                    </button>

                    <div id="connectionAlert"></div>
                </div>
            </details>

            <div class="explorer-header">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span class="section-title" style="margin:0;">DB Object Explorer</span>
                    <label class="schema-toggle">
                        <input type="checkbox" id="chkAllSchemas" onchange="fetchTables()">
                        <span>All Schemas</span>
                    </label>
                </div>

                <input type="text" id="tableSearch" placeholder="Type keyword (e.g. PO, PTAR)..." oninput="debounceSearch()">
                
                <div class="filter-pills">
                    <div class="pill active" id="pillALL" onclick="setFilterType('ALL')">ALL</div>
                    <div class="pill" id="pillTABLE" onclick="setFilterType('TABLE')">TABLES</div>
                    <div class="pill" id="pillVIEW" onclick="setFilterType('VIEW')">VIEWS</div>
                    <div class="pill" id="pillPACKAGE" onclick="setFilterType('PACKAGE')">PKG SPEC</div>
                    <div class="pill" id="pillBODY" onclick="setFilterType('BODY')">PKG BODY</div>
                    <div class="pill" id="pillPROCEDURE" onclick="setFilterType('PROCEDURE')">PROC</div>
                    <div class="pill" id="pillFUNCTION" onclick="setFilterType('FUNCTION')">FUNC</div>
                </div>

                <div class="search-stats" id="searchStats" style="font-size:0.75rem; color:var(--text-muted);">Type at least 2 characters to search...</div>
            </div>

            <div class="table-list" id="tableList">
                <div class="empty-state-box" style="padding: 30px 15px;">
                    <div class="empty-state-icon" style="font-size: 1.4rem;">🔍</div>
                    <div class="empty-state-title" style="font-size: 0.82rem;">Object Explorer</div>
                    <div class="empty-state-desc" style="font-size: 0.74rem;">Type a keyword above (e.g. <code style="color:var(--accent-blue);">PO</code>, <code style="color:var(--accent-blue);">AP</code>, <code style="color:var(--accent-blue);">GL</code>) to search DB objects.</div>
                </div>
            </div>
        </div>

        <!-- Draggable Resizer Splitter Bar between Sidebar and Main Workspace -->
        <div class="sidebar-resizer" id="sidebarResizer"></div>

        <!-- Workspace Panel -->
        <div class="workspace" id="workspacePanel">
            <div class="mode-tabs">
                <div class="mode-tab active" id="tabSqlBtn" onclick="switchTab('sql')">
                    <span>SQL Workbench</span>
                </div>
                <div class="mode-tab" id="tabPlsqlBtn" onclick="switchTab('plsql')">
                    <span>PL/SQL Editor</span>
                </div>
                <div class="mode-tab" id="tabConcBtn" onclick="switchTab('conc')">
                    <span>Concurrent Requests</span>
                </div>
                <div class="mode-tab" id="tabDescBtn" onclick="switchTab('desc')">
                    <span>Table Inspector</span>
                </div>
                <div class="mode-tab" id="tabFlexBtn" onclick="switchTab('flex')">
                    <span>Flexfield Explorer</span>
                </div>
                <div class="mode-tab" id="tabAiBtn" onclick="switchTab('ai')">
                    <span>AI Assistant</span>
                </div>
            </div>

            <!-- TAB 1: Multi-Tab SQL Workbench -->
            <div class="tab-view active" id="tabSqlView">
                <div class="sql-subtabs" id="sqlSubtabsBar"></div>

                <div class="sql-layout">
                    <div class="editor-section">
                        <div class="editor-toolbar" style="display:flex; justify-content:space-between; align-items:center;">
                            <div style="display:flex; align-items:center; gap:10px;">
                                <span class="section-title" id="activeSqlTabTitle">SQL Query Editor (Untitled 1)</span>
                                <span style="font-size:0.72rem; color:var(--text-muted);">(กด <b style="color:var(--accent-blue);">Ctrl+M</b> เพิ่มแท็บ SQL)</span>
                            </div>
                            <div style="display:flex; gap:8px;">
                                <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 0.78rem;" onclick="backupCode('SQL')">Backup File</button>
                                <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 0.78rem;" onclick="createNewSqlTab()">New Tab (Ctrl+M)</button>
                                <button class="btn btn-primary" style="padding: 4px 12px; font-size: 0.82rem;" onclick="runQuery()">Run SQL (Ctrl+Enter)</button>
                            </div>
                        </div>
                        <div id="sqlEditor"></div>
                    </div>
                    <div class="sql-resizer-h" id="sqlEditorResizer"></div>

                    <div class="results-section">
                        <div class="results-header">
                            <span id="resultStats">Results will appear here</span>
                            <div class="export-actions" id="exportActions" style="display: none; align-items: center; gap: 10px; flex-wrap: wrap;">
                                <span id="elapsedBadge" style="font-size:0.75rem; color:var(--accent-blue); font-weight:600;"></span>
                                <select id="rowLimitSelect" style="padding: 4px 8px; font-size: 0.75rem; width: 120px;" onchange="runQuery()">
                                    <option value="100">100 Rows</option>
                                    <option value="500" selected>500 Rows</option>
                                    <option value="10000">10,000 Rows</option>
                                    <option value="ALL">All Rows</option>
                                </select>
                                <input type="text" id="gridSearchInput" placeholder="Filter rows (AND)..." oninput="filterGridLocally()" style="padding: 4px 8px; font-size: 0.75rem; width: 180px;">
                                <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 0.78rem;" onclick="attachContext('results')">
                                    Ask AI
                                </button>
                                <button class="btn btn-success" style="padding: 4px 10px; font-size: 0.78rem;" onclick="openExportModal()">
                                    Export Data
                                </button>
                            </div>
                        </div>
                        <div class="table-wrapper" id="tableWrapper">
                            <div class="empty-state-box">
                                <div class="empty-state-icon">⚡</div>
                                <div class="empty-state-title">No Query Executed Yet</div>
                                <div class="empty-state-desc">Write your SQL statement above and press <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to run, or pick an object from the left sidebar to inspect.</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- TAB 2: Multi-Tab VS Code PL/SQL Package Editor with Code Explorer & Draggable Resizers -->
            <div class="tab-view" id="tabPlsqlView">
                <div class="plsql-layout">
                    <!-- Multi-Tab PL/SQL Package Bar -->
                    <div class="sql-subtabs" id="plsqlSubtabsBar" style="border-radius:6px; margin-bottom:4px;"></div>

                    <div class="plsql-header">
                        <div class="plsql-meta" style="display: flex; align-items: center; gap: 10px;">
                            <span id="plsqlObjName" style="font-weight:700; font-size:1.05rem; color:#58a6ff;">No Object Selected</span>
                            <span id="plsqlObjBadge" class="type-badge PACKAGE">PACKAGE</span>
                        </div>

                        <div style="display: flex; gap: 10px; align-items: center;">
                            <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.78rem;" onclick="backupCode('PLSQL')">Backup File</button>
                            <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.78rem;" onclick="genDDL()">Gen DDL</button>
                            <button class="btn btn-secondary" id="btnToggleDiff" style="padding:4px 10px; font-size:0.78rem;" onclick="toggleDiffMode()">Compare</button>
                            <span id="lastCompileTime" style="font-size: 0.75rem; color: #8b949e; font-weight: 500;"></span>
                            <button class="btn btn-success" id="btnCompile" onclick="compilePLSQL()"><span>Compile to DB</span></button>
                        </div>
                    </div>

                    <!-- VS Code Editor + Draggable Code Explorer Splitter -->
                    <div class="editor-workspace">
                        <div class="code-explorer" id="mainCodeExplorer">
                            <div class="code-explorer-title">Code Outline</div>
                            <div id="treeContentMain" style="display:flex; flex-direction:column; gap:2px;">
                                <div style="font-size:0.75rem; color:#858585;">Select a Package to load outline tree.</div>
                            </div>
                        </div>

                        <div class="resizer-v" id="mainExplorerResizer"></div>

                        <div id="monacoEditorMainContainer"></div>
                        <div id="monacoDiffEditorMainContainer" style="display:none; flex: 1; border: 1px solid var(--panel-border); border-radius: 0 6px 6px 0; overflow: hidden; background: var(--bg-dark);"></div>
                    </div>

                    <div class="resizer-h" id="consoleResizer"></div>

                    <div class="compile-console" id="compileConsole">
                        <span style="color: var(--text-muted);">Ready to edit PL/SQL code...</span>
                    </div>
                </div>
            </div>

            <!-- TAB 3: Concurrent Request Explorer -->
            <div class="tab-view" id="tabConcView">
                <div class="conc-layout">
                    <div class="conc-toolbar">
                        <label style="font-size: 0.82rem; color: var(--accent-teal); font-weight: 600;">ค้นหาจาก:</label>
                        <select id="concSearchType" style="width: 210px;">
                            <option value="ALL">🔍 ทุกฟิลด์ (Any Match)</option>
                            <option value="CONC_NAME">📌 User Concurrent Name</option>
                            <option value="SHORT_NAME">⚡ Program Short Name</option>
                            <option value="REQ_ID">🔢 Request ID</option>
                            <option value="USER_NAME">👤 User Name</option>
                            <option value="PKG_NAME">📦 Package / Executable</option>
                        </select>
                        <input type="text" id="concSearchInput" style="flex:1;" placeholder="พิมพ์คำค้นหา (เช่น REP - AR Gold Invoice Form, PTAR_GOLD_INVOICE_2, 17486162)..." onkeydown="if(event.key==='Enter') searchConcurrent()">
                        <button class="btn" onclick="searchConcurrent()">🔍 ค้นหา Concurrent</button>
                        <div class="dropdown" style="display:inline-block; position:relative; z-index:1000;">
                            <button class="btn" style="background:linear-gradient(135deg, #0284c7, #0369a1); border:1px solid #38bdf8; font-weight:600; cursor:pointer;" onclick="toggleFndDropdown(event)" title="จัดการ FNDLOAD">🚀 Gen FNDLOAD ▼</button>
                            <div id="fndDropdownContent" class="dropdown-content" style="display:none; position:absolute; right:0; top:calc(100% + 4px); background-color:#1e293b; min-width:220px; box-shadow:0px 8px 24px 0px rgba(0,0,0,0.7); z-index:99999; border-radius:6px; border:1px solid #334155; text-align:left; overflow:hidden;">
                                <a href="javascript:void(0)" onclick="openFndLoadModal(); toggleFndDropdown(event);" style="color:#f8fafc; padding:12px 16px; text-decoration:none; display:block; border-bottom:1px solid #334155; font-size:0.85rem; font-weight:500; cursor:pointer;" onmouseover="this.style.background='#334155'" onmouseout="this.style.background='transparent'">📥 สร้างสคริปต์ (Clone Program)</a>
                                <a href="javascript:void(0)" onclick="triggerFndFolderUpload(); toggleFndDropdown(event);" style="color:#f8fafc; padding:12px 16px; text-decoration:none; display:block; font-size:0.85rem; font-weight:500; cursor:pointer;" onmouseover="this.style.background='#334155'" onmouseout="this.style.background='transparent'">📤 อัปโหลด FND โดยเลือก Folder</a>
                            </div>
                        </div>
                    </div>

                    <div class="results-section" style="flex:1;">
                        <div class="results-header">
                            <span id="concStats">Enter a keyword to search Concurrent Requests</span>
                        </div>
                        <div class="table-wrapper" id="concTableWrapper">
                            <div class="empty-state-box">
                                <div class="empty-state-icon">📋</div>
                                <div class="empty-state-title">Concurrent Requests</div>
                                <div class="empty-state-desc">Enter a program name or request ID above to search Oracle EBS Concurrent Requests.</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- TAB 4: Table Structure & Column Inspector -->
            <div class="tab-view" id="tabDescView">
                <div class="conc-layout">
                    <div class="conc-toolbar">
                        <span style="font-weight:700; font-size:1.05rem;" id="descTableName">No Table Selected</span>
                        <button class="btn btn-secondary" onclick="fetchTableStructure()">🔄 Refresh Columns</button>
                    </div>

                    <div class="results-section" style="flex:1;">
                        <div class="results-header">
                            <span id="descStats">Select a Table or View from the left panel to inspect its columns & data types</span>
                        </div>
                        <div class="table-wrapper" id="descTableWrapper">
                            <div class="empty-state-box">
                                <div class="empty-state-icon">📊</div>
                                <div class="empty-state-title">Table Inspector</div>
                                <div class="empty-state-desc">Select a Table or View from the left DB Explorer to inspect column names, data types, and nullability.</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- TAB 5: Antigravity AI Assistant Chat -->
            <div class="tab-view" id="tabFlexView">
                <div class="conc-layout">
                    <div class="conc-toolbar" style="display:flex; gap: 10px;">
                        <input type="text" id="flexKeywordInput" placeholder="Search Structure Name or Prompt (e.g. ประวัติตำแหน่ง)..." style="width: 350px; padding: 6px; border-radius: 4px; border: 1px solid var(--panel-border); background: var(--bg-dark); color: var(--text-main);" onkeydown="if(event.key==='Enter') searchFlexfields()">
                        <select id="flexTypeSelect" style="padding: 6px; border-radius: 4px; border: 1px solid var(--panel-border); background: var(--bg-dark); color: var(--text-main);">
                            <option value="ALL">All (SIT & DFF)</option>
                            <option value="SIT">SIT (Personal Analysis / KFF)</option>
                            <option value="DFF">DFF (Descriptive Flexfield)</option>
                        </select>
                        <button class="btn btn-secondary" onclick="searchFlexfields()">🔍 Search Flexfields</button>
                    </div>
                    <div class="table-wrapper" id="flexTableWrapper" style="flex: 1; overflow: auto;">
                        <div class="empty-state-box">
                            <div class="empty-state-icon">🧩</div>
                            <div class="empty-state-title">Flexfield Explorer</div>
                            <div class="empty-state-desc">Enter a keyword above to search EBS Flexfields (SIT / DFF) structure mapping.</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="tab-view" id="tabAiView">
                <div class="ai-layout">
                    <div style="display: flex; gap: 10px; align-items: center; background: var(--bg-surface); padding: 10px 14px; border-radius: 6px; border: 1px solid var(--panel-border);">
                        <span style="font-size:0.8rem; color:var(--text-muted); font-weight:600;">AI Model:</span>
                        <select id="geminiModelSelect" style="width: 240px; font-weight:500; color:var(--text-main); background:var(--bg-dark);">
                            <option value="gemini-flash-latest">Gemini Flash (Fast)</option>
                            <option value="gemini-pro-latest">Gemini Pro (Advanced)</option>
                        </select>

                        <span style="font-size:0.8rem; color:var(--text-muted); font-weight:600; margin-left: 8px;">API Key:</span>
                        <input type="password" id="geminiApiKey" style="flex:1; padding:5px 10px; font-size:0.8rem;" placeholder="Google Gemini API Key (Auto-saved)...">
                        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.75rem;" onclick="saveAiKey()">Save Key</button>
                    </div>
                    <div class="quick-prompts">
                        <div class="prompt-chip" onclick="useQuickPrompt('ช่วยวิเคราะห์การทำงานของ PTAR_GOLD_INVOICE_PKG อย่างละเอียด')">วิเคราะห์ PTAR_GOLD_INVOICE_PKG</div>
                        <div class="prompt-chip" onclick="useQuickPrompt('ช่วยค้นหา Concurrent Request ของ REP - AR Gold Invoice Form')">ค้นหา Concurrent REP - AR Gold</div>
                        <div class="prompt-chip" onclick="useQuickPrompt('อธิบายโครงสร้างตาราง RA_CUSTOMER_TRX_ALL')">อธิบายตาราง RA_CUSTOMER_TRX_ALL</div>
                        <div class="prompt-chip" onclick="useQuickPrompt('ทำไรได้บ้าง')">คำแนะนำการใช้งาน</div>
                    </div>

                    <div class="ai-chat-messages" id="aiChatMessages">
                        <div class="chat-bubble ai">
                            ยินดีต้อนรับสู่ <b>AI Assistant</b> — ระบบช่วยวิเคราะห์ PL/SQL Logic, Oracle EBS Architecture, คิวรี Concurrent Requests และให้คำแนะนำการเขียน SQL สำหรับนักพัฒนา
                        </div>
                    </div>

                    <div style="position: relative; width: 100%;">
                        <div id="aiContextBadge" style="display:none; font-size: 0.75rem; color: var(--accent-blue); align-items: center; gap: 6px; margin-bottom: 6px; background: var(--bg-surface); padding: 4px 10px; border-radius: 4px; border: 1px solid var(--panel-border); width: fit-content;">
                            <span>Context: <b id="aiContextName"></b></span> 
                            <button onclick="clearAiContext()" style="background:none; border:none; color:var(--danger); cursor:pointer; font-size:0.8rem; padding:0 4px;">✖</button>
                        </div>
                        <div id="aiContextMenu" style="display:none; position:absolute; bottom: 100%; margin-bottom: 6px; left: 0; background: var(--bg-elevated); border: 1px solid var(--panel-border); border-radius: 6px; padding: 6px; z-index: 100; box-shadow: 0 4px 16px rgba(0,0,0,0.5); min-width: 220px;">
                            <div class="context-menu-item" onclick="attachContext('sql')">SQL Query Editor ล่าสุด</div>
                            <div class="context-menu-item" onclick="attachContext('plsql')">PL/SQL Code ปัจจุบัน</div>
                            <div class="context-menu-item" onclick="attachContext('table')">โครงสร้าง Table ล่าสุด</div>
                        </div>
                        <div class="ai-input-box">
                            <button class="btn btn-secondary" onclick="showAiContextMenu()" style="padding: 4px 10px; font-size: 0.85rem;" title="แนบ Context">Context</button>
                            <input type="text" id="aiMessageInput" placeholder="พิมพ์คำถามเกี่ยวกับ Oracle SQL, PL/SQL หรือ EBS Tables..." onkeydown="if(event.key==='Enter') sendAiMessage()">
                            <button class="btn btn-primary" onclick="sendAiMessage()"><span>ส่ง</span></button>
                        </div>
                    </div>
                </div>
            </div>

        </div>
    </div>

    <!-- Hidden File Inputs -->
    <input type="file" id="rtfUploadInput" style="display:none;" accept=".rtf" onchange="handleRtfUpload(event)">
    <input type="file" id="rdfUploadInput" style="display:none;" accept=".rdf" onchange="handleRdfUpload(event)">

    <!-- Modals -->
    <!-- Operation Status Modal (Upload / Download Status) -->
    <div class="modal-backdrop" id="opStatusModal" style="z-index: 10500; backdrop-filter: blur(4px);">
        <div class="modal-content op-card-anim" style="width: 500px; max-width: 95vw; border-radius: 12px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.65); border: 1px solid var(--panel-border-light); background: var(--bg-surface);">
            <!-- Modal Header -->
            <div id="opStatusHeader" style="padding: 13px 18px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--panel-border); background: var(--bg-elevated);">
                <div style="display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 0.92rem;" id="opStatusTitleBox">
                    <span id="opStatusHeaderIcon">🔄</span>
                    <span id="opStatusHeaderTitle">กำลังดำเนินการ...</span>
                </div>
                <button type="button" onclick="closeOpStatusModal()" style="background:none; border:none; color:var(--text-muted); font-size:1.2rem; cursor:pointer; padding:2px 6px; line-height:1;" title="ปิด">✖</button>
            </div>
            <!-- Modal Body -->
            <div style="padding: 24px 22px 20px; display: flex; flex-direction: column; align-items: center; gap: 16px; text-align: center;">
                <!-- Animated Visual / Icon Area -->
                <div id="opStatusVisual" style="min-height: 60px; display: flex; align-items: center; justify-content: center;">
                    <div class="loading-spinner" style="width: 44px; height: 44px; border-width: 4px; border-top-color: var(--accent-primary);"></div>
                </div>

                <!-- Headline & Message -->
                <div style="display: flex; flex-direction: column; gap: 6px; width: 100%;">
                    <div id="opStatusHeadline" style="font-size: 1.15rem; font-weight: 700; color: var(--text-bright);">กำลังดาวน์โหลด...</div>
                    <div id="opStatusMessage" style="font-size: 0.84rem; color: var(--text-muted); line-height: 1.5; word-break: break-word;">กรุณารอสักครู่ ระบบกำลังสื่อสารกับเซิร์ฟเวอร์...</div>
                </div>

                <!-- Detail Metadata Card -->
                <div id="opStatusDetails" style="display: none; width: 100%; text-align: left; background: var(--bg-deeper); border: 1px solid var(--panel-border); border-radius: 8px; padding: 12px 14px; font-size: 0.82rem; flex-direction: column; gap: 8px;">
                    <!-- Injected dynamically by JS -->
                </div>

                <!-- Action Buttons -->
                <div id="opStatusActions" style="display: flex; gap: 10px; width: 100%; justify-content: center; margin-top: 4px; flex-wrap: wrap;">
                    <button class="btn btn-secondary" onclick="closeOpStatusModal()">ปิด</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal-backdrop" id="sftpAuthModal">
        <div class="modal-content" style="width: 450px;">
            <div class="modal-header">
                <span>🔐 SFTP Authentication Setup</span>
                <button style="background:none; border:none; color:#cbd5e1; font-size:1.2rem; cursor:pointer;" onclick="closeSftpAuthModal()">✖</button>
            </div>
            <div style="padding: 20px; display:flex; flex-direction:column; gap:14px;">
                <div style="font-size:0.85rem; color:var(--text-muted); line-height:1.5;">
                    กรุณากรอก Username และ Password สำหรับเชื่อมต่อ SFTP ไปยัง Server (<span id="sftpAuthHost" style="color:#38bdf8; font-weight:bold;"></span>)<br>
                    ระบบจะบันทึกไว้ในเครื่องของคุณและไม่ต้องกรอกอีกในครั้งต่อไป
                </div>
                <div class="form-group">
                    <label>SFTP Username:</label>
                    <input type="text" id="sftpUsernameInput" placeholder="e.g., appldev" style="background:#090d16; border:1px solid var(--panel-border); color:#fff; padding:8px; border-radius:4px; font-family:monospace;">
                </div>
                <div class="form-group">
                    <label>SFTP Password:</label>
                    <input type="password" id="sftpPasswordInput" placeholder="********" style="background:#090d16; border:1px solid var(--panel-border); color:#fff; padding:8px; border-radius:4px; font-family:monospace;" onkeydown="if(event.key==='Enter') saveSftpAuth()">
                </div>
                <button class="btn btn-primary" onclick="saveSftpAuth()" style="margin-top: 10px; width: 100%; justify-content: center; padding: 10px;">✅ บันทึกรหัสผ่าน & เชื่อมต่อ</button>
            </div>
        </div>
    </div>

    <div class="modal-backdrop" id="sftpExplorerModal">
        <div class="modal-content" style="width: 750px; max-height: 85vh;">
            <div class="modal-header">
                <span>📂 SFTP File Explorer (<span id="sftpExplorerHost" style="color:#a7f3d0;"></span>)</span>
                <button style="background:none; border:none; color:#cbd5e1; font-size:1.2rem; cursor:pointer;" onclick="closeSftpExplorerModal()">✖</button>
            </div>
            <div style="padding: 16px; display:flex; flex-direction:column; gap:12px; height: 100%; flex: 1;">
                <div style="display: flex; gap: 8px;">
                    <button class="btn" onclick="sftpGoUp()" title="Up one level" style="padding: 5px 12px; font-size: 1.1rem; background: #334155; border-color: #475569;">⬆️</button>
                    <input type="text" id="sftpPathInput" style="flex:1; background:#090d16; border:1px solid var(--panel-border); color:#60a5fa; font-family:'JetBrains Mono', monospace; font-size:0.9rem; padding:8px; border-radius:4px;" onkeydown="if(event.key==='Enter') loadSftpPath()">
                    <button class="btn btn-primary" onclick="loadSftpPath()" style="padding: 5px 16px;">🔄 GO</button>
                </div>
                
                <div style="flex: 1; min-height: 350px; background:#090d16; border:1px solid var(--panel-border); border-radius:4px; overflow-y:auto; overflow-x:auto;">
                    <table style="width:100%; border-collapse: collapse; text-align: left; font-size: 0.85rem;" id="sftpFileTable">
                        <thead>
                            <tr style="background: #1e293b; color: #94a3b8; font-size: 0.75rem; text-transform: uppercase;">
                                <th style="padding: 8px 12px; border-bottom: 1px solid #334155; width: 60%;">Name</th>
                                <th style="padding: 8px 12px; border-bottom: 1px solid #334155; width: 20%;">Size</th>
                                <th style="padding: 8px 12px; border-bottom: 1px solid #334155; width: 20%; text-align:right;">Action</th>
                            </tr>
                        </thead>
                        <tbody id="sftpFileList">
                            <!-- Files will be injected here -->
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="modal-backdrop" id="logModal" style="z-index: 10400;">
        <div class="modal-content" style="width: 920px; max-width: 95vw; height: 85vh; display:flex; flex-direction:column; background:var(--bg-surface); border:1px solid var(--panel-border-light); border-radius:10px; box-shadow: 0 16px 50px rgba(0,0,0,0.65); overflow:hidden;">
            <div class="modal-header" style="padding: 12px 18px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid var(--panel-border); background:var(--bg-elevated);">
                <div style="display:flex; align-items:center; gap:8px; font-weight:700; font-size:0.95rem;">
                    <span id="modalTitle">📑 Request Log Inspector</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <button class="btn" style="padding:4px 10px; font-size:0.75rem; background:#334155; color:#f8fafc; border:1px solid #475569;" onclick="openLogNewTab()" title="เปิด Log ในแท็บ Browser ใหม่">↗️ Open in New Tab</button>
                    <button class="btn" style="padding:4px 10px; font-size:0.75rem; background:#0284c7; color:#fff;" onclick="copyLogToClipboard()" title="คัดลอกข้อความ Log">📋 Copy Log</button>
                    <button class="btn" style="padding:4px 10px; font-size:0.75rem; background:#10b981; color:#fff;" onclick="downloadLogFile()" title="ดาวน์โหลดไฟล์ Log">📥 Download</button>
                    <button style="background:none; border:none; color:var(--text-muted); font-size:1.2rem; cursor:pointer; padding:2px 6px;" onclick="closeLogModal()" title="ปิด">✖</button>
                </div>
            </div>
            <div style="padding: 14px 18px; flex:1; display:flex; flex-direction:column; gap:10px; overflow:hidden;">
                <div id="modalMeta" style="font-size:0.82rem; color:var(--text-muted); line-height:1.5; background:var(--bg-deeper); padding:10px 14px; border-radius:6px; border:1px solid var(--panel-border);"></div>
                
                <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
                    <div style="font-size:0.78rem; font-weight:600; color:var(--accent-teal); text-transform:uppercase; letter-spacing:0.5px;">Server Log Content</div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <input type="text" id="logFilterInput" placeholder="🔍 ค้นหาใน Log (e.g. ORA-, ERROR)..." oninput="filterLogContent()" style="background:#090d16; border:1px solid var(--panel-border); color:#f8fafc; font-size:0.75rem; padding:4px 10px; border-radius:4px; width:220px;">
                        <span id="logFilterStats" style="font-size:0.72rem; color:var(--text-muted);"></span>
                    </div>
                </div>
                
                <textarea id="modalLogContent" readonly spellcheck="false" style="flex:1; width:100%; font-family:'JetBrains Mono', Consolas, 'Courier New', monospace; font-size:0.80rem; line-height:1.45; background:#090d16; color:#a7f3d0; border:1px solid var(--panel-border); border-radius:6px; padding:12px; resize:none; white-space:pre; overflow:auto;"></textarea>
            </div>
        </div>
    </div>

    <!-- MODAL: FNDLOAD Script Generator (Clone Concurrent Program) -->
    <div class="modal-backdrop" id="fndLoadModal" style="z-index: 10450;">
        <div class="modal-content" style="width: 660px; max-width: 95vw; max-height: 90vh; background:var(--bg-surface); border:1px solid var(--panel-border); border-radius:8px; box-shadow: 0 16px 36px rgba(0,0,0,0.6); overflow:hidden; display:flex; flex-direction:column;">
            <div class="modal-header" style="padding: 12px 18px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid var(--panel-border); background:var(--bg-elevated); flex-shrink:0;">
                <div style="display:flex; align-items:center; gap:8px; font-weight:600; font-size:0.92rem; color:var(--text-bright);">
                    <span>FNDLOAD Script Generator (Clone Concurrent Program)</span>
                </div>
                <button style="background:none; border:none; color:var(--text-muted); font-size:1.2rem; cursor:pointer; padding:2px 6px;" onclick="closeFndLoadModal()">✖</button>
            </div>
            <div style="padding: 16px 20px; display:flex; flex-direction:column; gap:12px; overflow-y:auto; flex:1;">
                <div style="font-size:0.82rem; color:var(--text-muted); line-height:1.4;">
                    สร้างสคริปต์ <code>config.cfg</code>, <code>download.sh</code> และ <code>upload.sh</code> สำหรับย้าย / Clone Concurrent Program ข้ามไซต์อัตโนมัติ (โฟลเดอร์ <code>C:\Users\MBx13\Desktop\GEN_FND</code>)
                </div>

                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                    <div>
                        <label style="font-size:0.76rem; font-weight:500; color:var(--text-muted); display:block; margin-bottom:4px;">Concurrent Program Short Name:</label>
                        <input type="text" id="fndProgShort" style="width:100%; font-weight:600; color:var(--accent-blue);" placeholder="e.g. SME_CONFIRM_DATA_GLTBD" oninput="onFndProgShortChanged()">
                    </div>
                    <div>
                        <label style="font-size:0.76rem; font-weight:500; color:var(--text-muted); display:block; margin-bottom:4px;">Application Short Name:</label>
                        <input type="text" id="fndAppShort" style="width:100%;" value="XXCUST" placeholder="e.g. XXCUST or SQLGL">
                    </div>
                </div>

                <div>
                    <label style="font-size:0.76rem; font-weight:500; color:var(--text-muted); display:block; margin-bottom:4px;">User Concurrent Program Name:</label>
                    <input type="text" id="fndProgName" style="width:100%;" placeholder="e.g. SME Confirm Data to View GLTBD">
                </div>

                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                    <div>
                        <label style="font-size:0.76rem; font-weight:500; color:var(--text-muted); display:block; margin-bottom:4px;">Data Source Code (XML Def):</label>
                        <input type="text" id="fndDsCode" style="width:100%;" placeholder="e.g. SME_CONFIRM_DATA_GLTBD">
                    </div>
                    <div>
                        <label style="font-size:0.76rem; font-weight:500; color:var(--text-muted); display:block; margin-bottom:4px;">Template Code (RTF Filter):</label>
                        <input type="text" id="fndTmplCode" style="width:100%;" placeholder="e.g. SME_CONFIRM_DATA_GLTBD%">
                    </div>
                </div>

                <div style="border-top:1px dashed var(--panel-border); margin:2px 0;"></div>

                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px;">
                    <div>
                        <label style="font-size:0.76rem; font-weight:500; color:var(--success); display:block; margin-bottom:4px;">Source DB Site (Download จาก):</label>
                        <select id="fndSrcDb" style="width:100%; font-weight:600;"></select>
                    </div>
                    <div>
                        <label style="font-size:0.76rem; font-weight:500; color:var(--warning); display:block; margin-bottom:4px;">Target DB Site (Clone ไปยังไซต์ใด):</label>
                        <select id="fndTgtDb" style="width:100%; font-weight:600; border:1px solid var(--panel-border); color:var(--warning); background:var(--bg-dark);"></select>
                    </div>
                </div>

                <div>
                    <label style="font-size:0.76rem; font-weight:500; color:var(--text-muted); display:block; margin-bottom:4px;">Output Directory:</label>
                    <input type="text" id="fndOutDir" style="width:100%;" value="C:\Users\MBx13\Desktop\GEN_FND">
                </div>

                <div id="fndStatusMsg" style="display:none; max-height:220px; overflow-y:auto; border-radius:6px; font-size:0.83rem;"></div>
            </div>
            <div class="modal-footer" style="padding: 12px 20px; display:flex; align-items:center; justify-content:space-between; border-top:1px solid var(--panel-border); background:var(--bg-elevated); flex-shrink:0;">
                <span id="fndNotice" style="font-size:0.75rem; color:var(--text-muted);">จะสร้างโฟลเดอร์แยกตามชื่อ Program ให้อัตโนมัติ</span>
                <div style="display:flex; gap:8px;">
                    <button class="btn btn-secondary" onclick="closeFndLoadModal()">ยกเลิก</button>
                    <button class="btn btn-primary" id="btnSubmitFnd" onclick="submitFndLoadGenerate()">สร้างไฟล์ FNDLOAD</button>
                </div>
            </div>
        </div>
    </div>

    
    <!-- FNDLOAD Upload Modal -->
    <div class="modal-backdrop" id="fndUploadModal">
        <div class="modal-content" style="width: 550px;">
            <div class="modal-header">
                <span>📤 อัปโหลด FNDLOAD จาก Folder</span>
                <button style="background:none; border:none; color:#cbd5e1; font-size:1.2rem; cursor:pointer;" onclick="closeFndUploadModal()">&times;</button>
            </div>
            <div style="padding: 16px 20px; display:flex; flex-direction:column; gap:12px;">
                <div style="font-size:0.85rem; color:var(--text-muted);">
                    ระบุ Target DB ที่ต้องการอัปโหลดไฟล์ FNDLOAD (เช่น CON_*.ldt, XML_*.ldt, TEMPLATE_*.rtf)
                </div>
                
                <div>
                    <label style="font-size:0.85rem; color:#94a3b8; display:block; margin-bottom:6px;">Folder ที่เลือก:</label>
                    <input type="text" class="form-control" id="fndUploadFolderName" disabled style="background:#1e293b; color:#38bdf8;">
                </div>

                <div>
                    <label style="font-size:0.85rem; color:#94a3b8; display:block; margin-bottom:6px;">Target DB Site (อัปโหลดไปที่ไซต์ใด):</label>
                    <select class="form-control" id="fndUploadTargetSite"></select>
                </div>
            </div>
            <div class="modal-footer" style="justify-content: flex-end; display:flex; gap:8px;">
                <button class="btn btn-secondary" onclick="closeFndUploadModal()">ยกเลิก</button>
                <button class="btn btn-primary" id="btnSubmitFndUpload" onclick="submitFndUpload()">อัปโหลดเข้า Server</button>
            </div>
        </div>
    </div>

    <div class="modal-backdrop" id="exportModal">
        <div class="modal-content" style="width: 580px;">
            <div class="modal-header">
                <span>📥 Data Export Wizard (SQL Navigator Format)</span>
                <button style="background:none; border:none; color:#cbd5e1; font-size:1.2rem; cursor:pointer;" onclick="closeExportModal()">✖</button>
            </div>
            <div style="padding: 20px; display:flex; flex-direction:column; gap:14px;">
                <div style="font-size:0.85rem; color:var(--text-muted);">เลือกรูปแบบไฟล์และโครงสร้างข้อมูลที่ต้องการส่งออก:</div>
                
                <div class="form-group">
                    <label>รูปแบบ Export Format:</label>
                    <select id="exportFormatSelect" style="padding:8px; font-weight:600; color:var(--accent-teal);">
                        <option value="CSV">📊 CSV File (UTF-8 BOM Thai Excel Compatible)</option>
                        <option value="EXCEL">📈 Excel Spreadsheet (HTML Table / XLS Format)</option>
                        <option value="JSON">json JSON Data Format</option>
                        <option value="SQL_INSERT">📝 SQL INSERT Statements (INSERT INTO table VALUES...)</option>
                        <option value="TXT">📄 Tab-Delimited Text File (.txt)</option>
                    </select>
                </div>

                <div class="form-group">
                    <label>จำนวนข้อมูลที่ต้องการนำออก (Row Limit):</label>
                    <select id="exportRowLimitSelect" style="padding:8px; font-weight:600; color:var(--text-main);">
                        <option value="CURRENT">ตามข้อมูลที่แสดงใน Grid (Current View)</option>
                        <option value="1000">1,000 แถว</option>
                        <option value="10000">10,000 แถว</option>
                        <option value="ALL">ทั้งหมด (All Rows - อาจใช้เวลานานถ้าข้อมูลเยอะมาก)</option>
                    </select>
                </div>

                <div class="form-group" id="tableNameGroup">
                    <label>ชื่อตารางสำหรับ SQL Insert Statement (Optional):</label>
                    <input type="text" id="exportTableNameInput" placeholder="e.g. TARGET_TABLE_NAME">
                </div>

                <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:10px;">
                    <button class="btn btn-secondary" onclick="closeExportModal()">ยกเลิก (Cancel)</button>
                    <button class="btn btn-success" onclick="executeExport()"><span>📥 เริ่มดาวน์โหลดไฟล์ (Export)</span></button>
                </div>
            </div>
        </div>
    </div>

    <!-- Cell Details Modal Removed -->
    <div class="modal-backdrop" id="execModal">
        <div class="modal-content" style="width: 700px;">
            <div class="modal-header">
                <span id="execModalTitle">⚡ Auto-Generate PL/SQL Execution Script</span>
                <button style="background:none; border:none; color:#cbd5e1; font-size:1.2rem; cursor:pointer;" onclick="closeExecModal()">✖</button>
            </div>
            <div class="modal-body">
                <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 8px;">
                    ระบบจะวิเคราะห์ Parameter ของ Procedure นี้อัตโนมัติ คุณสามารถวาง <code>Argument Text</code> (คั่นด้วย comma) จากหน้า Concurrent Request เพื่อให้ระบบ Map ค่าพารามิเตอร์ให้ทันที
                </div>
                <div class="form-group">
                    <label>Argument Text (Optional - Paste comma-separated arguments here):</label>
                    <input type="text" id="execArgumentInput" placeholder="e.g. 104, 'US', 2023/10/01 00:00:00, Y" oninput="generateExecScript()">
                </div>
                
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:10px;">
                    <label style="color:var(--accent-teal); font-weight:600;">Generated PL/SQL Block:</label>
                    <button class="btn" style="padding: 4px 10px; font-size: 0.75rem;" onclick="copyExecScript()">📋 Copy Script</button>
                </div>
                <textarea id="execScriptOutput" readonly style="flex:1; min-height: 220px; font-family:'JetBrains Mono', monospace; font-size:0.82rem; background:#090d16; color:#e2e8f0; border:1px solid var(--panel-border); padding:10px; resize:none;"></textarea>

                <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:8px;">
                    <button class="btn btn-secondary" onclick="closeExecModal()">ยกเลิก (Cancel)</button>
                    <button class="btn btn-success" onclick="openExecInSqlTab()"><span>⚡ เปิดใน SQL Tab ใหม่ (Open in New Tab)</span></button>
                </div>
            </div>
        </div>
    </div>

    <!-- Bind Variable Input Modal -->
    <div class="modal-backdrop" id="bindVarModal">
        <div class="modal-content" style="width: 500px;">
            <div class="modal-header">
                <span>🔗 Bind Variable Input — กรอกค่าพารามิเตอร์</span>
                <button style="background:none; border:none; color:#cbd5e1; font-size:1.2rem; cursor:pointer;" onclick="closeBindVarDialog()">✖</button>
            </div>
            <div class="modal-body" id="bindVarForm">
                <!-- Dynamically filled -->
            </div>
            <div style="padding:12px 16px; display:flex; justify-content:flex-end; gap:10px; border-top:1px solid var(--panel-border);">
                <button class="btn btn-secondary" onclick="closeBindVarDialog()">ยกเลิก</button>
                <button class="btn btn-success" onclick="confirmBindVars()">⚡ Run Query</button>
            </div>
        </div>
    </div>

    <script>
        let tnsData = [];
        let selectedTns = null;
        let activeTypeFilter = 'ALL';
        let searchTimeout = null;

        let currentColumns = [];
        let currentRows = [];
        let currentTableName = null;
        let lastExecutedSql = '';
        let lastExecutedBindVars = {};

        let activePlsqlName = "";
        let activePlsqlType = "";
        let mainMonacoEditor = null;
        let monacoDiffEditor = null;
        let isDiffMode = false;
        let sidebarCollapsed = false;

        // Multi-Tab SQL Workbench System
        let sqlTabs = [ { id: 'sql_tab_1', title: 'Untitled 1', query: '' } ];
        let activeSqlTabId = 'sql_tab_1';
        let sqlMonacoEditor = null;

        // Multi-Tab PL/SQL Package Editor System
        let plsqlTabs = [];
        let activePlsqlTabId = null;

        require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' }});
        require(['vs/editor/editor.main'], function() {

            // Define custom modern dark theme for Monaco (GitHub / VS Code Dark Modern)
            monaco.editor.defineTheme('jnav-dark', {
                base: 'vs-dark',
                inherit: true,
                rules: [
                    // Comments
                    { token: 'comment', foreground: '8b949e', fontStyle: 'italic' },
                    { token: 'comment.block', foreground: '8b949e', fontStyle: 'italic' },

                    // Keywords (SELECT, FROM, WHERE, etc.)
                    { token: 'keyword', foreground: 'ff7b72' },
                    { token: 'keyword.other', foreground: 'ff7b72' },

                    // Strings
                    { token: 'string', foreground: 'a5d6ff' },
                    { token: 'string.sql', foreground: 'a5d6ff' },

                    // Numbers
                    { token: 'number', foreground: '79c0ff' },
                    { token: 'number.float', foreground: '79c0ff' },

                    // Identifiers / column names
                    { token: 'identifier', foreground: 'e6edf3' },

                    // Operators
                    { token: 'operator', foreground: 'ff7b72' },

                    // Type names
                    { token: 'type', foreground: 'ffa657' },
                    { token: 'predefined.type', foreground: 'ffa657' },

                    // Functions
                    { token: 'function', foreground: 'd2a8ff' },
                    { token: 'function.sql', foreground: 'd2a8ff' },

                    // Delimiters
                    { token: 'delimiter', foreground: '8b949e' },
                    { token: 'delimiter.parenthesis', foreground: '8b949e' },

                    // Tags / metadata
                    { token: 'tag', foreground: 'd29922' },
                ],
                colors: {
                    // Editor background
                    'editor.background': '#0d1117',
                    'editor.foreground': '#e6edf3',

                    // Cursor
                    'editorCursor.foreground': '#58a6ff',

                    // Selection
                    'editor.selectionBackground': '#264f78',
                    'editor.inactiveSelectionBackground': '#1f344d',

                    // Line highlight
                    'editor.lineHighlightBackground': '#161b22',
                    'editor.lineHighlightBorder': '#30363d',

                    // Gutter
                    'editorLineNumber.foreground': '#6e7681',
                    'editorLineNumber.activeForeground': '#e6edf3',
                    'editorGutter.background': '#0d1117',

                    // Find/match highlight
                    'editor.findMatchBackground': '#3d4466',
                    'editor.findMatchHighlightBackground': '#2a2f50',

                    // Scrollbar
                    'scrollbarSlider.background': '#30363d80',
                    'scrollbarSlider.hoverBackground': '#3c444d80',
                    'scrollbarSlider.activeBackground': '#1f6feb60',

                    // Minimap
                    'minimap.background': '#0d1117',
                    'minimapSlider.background': '#30363d60',
                    'minimapSlider.hoverBackground': '#1f6feb40',

                    // Word highlight
                    'editor.wordHighlightBackground': '#388bfd20',
                    'editor.wordHighlightStrongBackground': '#388bfd35',

                    // Bracket pair colorization
                    'editorBracketHighlight.foreground1': '#58a6ff',
                    'editorBracketHighlight.foreground2': '#3fb950',
                    'editorBracketHighlight.foreground3': '#d29922',
                }
            });

            sqlMonacoEditor = monaco.editor.create(document.getElementById('sqlEditor'), {
                value: '',
                language: 'sql',
                theme: 'jnav-dark',
                fontSize: 14,
                fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
                automaticLayout: true,
                minimap: { enabled: false },
                wordWrap: 'on',
                scrollBeyondLastLine: false,
                renderLineHighlight: 'all',
                suggestSelection: 'first',
                bracketPairColorization: { enabled: true }
            });
            sqlMonacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, function() {
                runQuery();
            });

            mainMonacoEditor = monaco.editor.create(document.getElementById('monacoEditorMainContainer'), {
                value: "-- Select a Package, Procedure, or Function to edit PL/SQL source code...",
                language: 'sql',
                theme: 'jnav-dark',
                fontSize: 14,
                fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
                automaticLayout: true,
                minimap: { enabled: true, scale: 1 },
                scrollBeyondLastLine: false,
                lineNumbers: "on",
                renderLineHighlight: "line",
                cursorBlinking: "smooth",
                cursorSmoothCaretAnimation: "on",
                smoothScrolling: true,
                bracketPairColorization: { enabled: true },
                renderWhitespace: "selection",
                guides: { bracketPairs: true, indentation: true },
                padding: { top: 8, bottom: 8 },
            });
        });


        // Draggable Resizer Logic for Left Sidebar & Main Workspace
        const sidebarResizer = document.getElementById('sidebarResizer');
        const sidebarPanel = document.getElementById('sidebarPanel');
        let isSidebarDragging = false;

        sidebarResizer.addEventListener('mousedown', () => { isSidebarDragging = true; sidebarResizer.classList.add('dragging'); });
        document.addEventListener('mousemove', (e) => {
            if (!isSidebarDragging) return;
            const newWidth = e.clientX;
            if (newWidth >= 180 && newWidth <= 650) {
                sidebarPanel.style.width = newWidth + 'px';
                if (mainMonacoEditor) mainMonacoEditor.layout();
                if (sqlMonacoEditor) sqlMonacoEditor.layout();
                if (monacoDiffEditor) monacoDiffEditor.layout();
            }
        });
        document.addEventListener('mouseup', () => { isSidebarDragging = false; sidebarResizer.classList.remove('dragging'); });

        function toggleSidebarCollapse() {
            sidebarCollapsed = !sidebarCollapsed;
            if (sidebarCollapsed) {
                sidebarPanel.style.display = 'none';
                sidebarResizer.style.display = 'none';
            } else {
                sidebarPanel.style.display = 'flex';
                sidebarResizer.style.display = 'block';
            }
            if (mainMonacoEditor) setTimeout(() => mainMonacoEditor.layout(), 100);
            if (sqlMonacoEditor) setTimeout(() => sqlMonacoEditor.layout(), 100);
            if (monacoDiffEditor) setTimeout(() => monacoDiffEditor.layout(), 100);
        }

        // Draggable Resizer Logic for Monaco Code Explorer Splitter
        const mainExplorerResizer = document.getElementById('mainExplorerResizer');
        const mainCodeExplorer = document.getElementById('mainCodeExplorer');
        let isExplorerDragging = false;

        mainExplorerResizer.addEventListener('mousedown', () => { isExplorerDragging = true; });
        document.addEventListener('mousemove', (e) => {
            if (!isExplorerDragging) return;
            const rect = mainCodeExplorer.getBoundingClientRect();
            const newWidth = e.clientX - rect.left;
            if (newWidth >= 120 && newWidth <= 500) {
                mainCodeExplorer.style.width = newWidth + 'px';
                if (mainMonacoEditor) mainMonacoEditor.layout();
                if (monacoDiffEditor) monacoDiffEditor.layout();
            }
        });
        document.addEventListener('mouseup', () => { isExplorerDragging = false; });

        // Draggable Resizer Logic for Compile Console
        const consoleResizer = document.getElementById('consoleResizer');
        const compileConsole = document.getElementById('compileConsole');
        let isConsoleDragging = false;
        
        if (consoleResizer && compileConsole) {
            consoleResizer.addEventListener('mousedown', (e) => { 
                isConsoleDragging = true; 
                e.preventDefault(); // Prevent text selection while dragging
            });
            document.addEventListener('mousemove', (e) => {
                if (!isConsoleDragging) return;
                const rect = compileConsole.getBoundingClientRect();
                const newHeight = rect.bottom - e.clientY;
                if (newHeight >= 40 && newHeight <= 800) {
                    compileConsole.style.height = newHeight + 'px';
                    if (mainMonacoEditor) mainMonacoEditor.layout();
                    if (monacoDiffEditor) monacoDiffEditor.layout();
                }
            });
            document.addEventListener('mouseup', () => { isConsoleDragging = false; });
        }

        async function loadSavedProfiles() {
            try {
                const res = await fetch('/api/profiles');
                const data = await res.json();
                if (data.success) {
                    if (data.ai_key) {
                        document.getElementById('geminiApiKey').value = data.ai_key;
                    }
                }
                loadSessionsList();
            } catch(e) {
                console.error("Profiles error:", e);
            }
        }

        async function loadSessionsList() {
            try {
                const res = await fetch('/api/sessions');
                const data = await res.json();
                if (data.success) {
                    const select = document.getElementById('dbSessionSelect');
                    select.innerHTML = '';
                    if (data.sessions && data.sessions.length > 0) {
                        document.getElementById('statusDot').className = 'dot connected';
                        data.sessions.forEach(s => {
                            const opt = document.createElement('option');
                            opt.value = s.session_key;
                            opt.textContent = `${s.session_key}${s.is_active ? ' (Active)' : ''}`;
                            if (s.is_active) opt.selected = true;
                            select.appendChild(opt);
                        });
                    } else {
                        document.getElementById('statusDot').className = 'dot';
                        select.innerHTML = '<option value="">-- No Active DB Connected --</option>';
                    }
                }
            } catch(e) {
                console.error("Sessions error:", e);
            }
        }

        async function switchDbSession(key) {
            if (!key) return;
            try {
                const res = await fetch('/api/sessions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_key: key })
                });
                const data = await res.json();
                if (data.success) {
                    loadSessionsList();
                    fetchTables();
                }
            } catch(e) {
                console.error("Switch session error:", e);
            }
        }

        async function saveAiKey() {
            const key = document.getElementById('geminiApiKey').value.trim();
            await fetch('/api/profiles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ai_key: key })
            });
            alert("บันทึก Google Gemini API Key สำเร็จ!");
        }

        function renderSqlSubtabs() {
            const bar = document.getElementById('sqlSubtabsBar');
            let html = '';
            sqlTabs.forEach(t => {
                const isActive = t.id === activeSqlTabId;
                html += `
                    <div class="sql-subtab ${isActive ? 'active' : ''}" onclick="switchSqlSubtab('${t.id}')">
                        <span>⚡ ${t.title}</span>
                        ${sqlTabs.length > 1 ? `<span class="close-btn" onclick="event.stopPropagation(); closeSqlSubtab('${t.id}')">✖</span>` : ''}
                    </div>
                `;
            });
            bar.innerHTML = html;
            
            const activeTab = sqlTabs.find(t => t.id === activeSqlTabId);
            if (activeTab) {
                document.getElementById('activeSqlTabTitle').textContent = `SQL Query Editor (${activeTab.title})`;
            }
        }

        function createNewSqlTab() {
            const curTab = sqlTabs.find(t => t.id === activeSqlTabId);
            if (curTab && sqlMonacoEditor) {
                curTab.query = sqlMonacoEditor.getValue();
            }

            const newId = 'sql_tab_' + (new Date().getTime());
            const newNum = sqlTabs.length + 1;
            const newTab = { id: newId, title: `Untitled ${newNum}`, query: '' };
            
            sqlTabs.push(newTab);
            activeSqlTabId = newId;
            if (sqlMonacoEditor) sqlMonacoEditor.setValue('');
            renderSqlSubtabs();
            switchTab('sql');
        }

        function switchSqlSubtab(id) {
            const curTab = sqlTabs.find(t => t.id === activeSqlTabId);
            if (curTab && sqlMonacoEditor) {
                curTab.query = sqlMonacoEditor.getValue();
            }

            activeSqlTabId = id;
            const targetTab = sqlTabs.find(t => t.id === id);
            if (targetTab && sqlMonacoEditor) {
                sqlMonacoEditor.setValue(targetTab.query);
            }
            renderSqlSubtabs();
        }

        function closeSqlSubtab(id) {
            if (sqlTabs.length <= 1) return;
            sqlTabs = sqlTabs.filter(t => t.id !== id);
            if (activeSqlTabId === id) {
                activeSqlTabId = sqlTabs[sqlTabs.length - 1].id;
                if (sqlMonacoEditor) sqlMonacoEditor.setValue(sqlTabs[sqlTabs.length - 1].query);
            }
            renderSqlSubtabs();
        }

        // Multi-Tab PL/SQL Editor Functions
        function renderPlsqlSubtabs() {
            const bar = document.getElementById('plsqlSubtabsBar');
            if (plsqlTabs.length === 0) {
                bar.style.display = 'none';
                return;
            }
            bar.style.display = 'flex';

            let html = '';
            plsqlTabs.forEach(t => {
                const isActive = t.id === activePlsqlTabId;
                html += `
                    <div class="sql-subtab ${isActive ? 'active' : ''}" onclick="switchPlsqlSubtab('${t.id}')">
                        <span>📦 ${t.name}</span>
                        <span class="close-btn" onclick="event.stopPropagation(); closePlsqlSubtab('${t.id}')">✖</span>
                    </div>
                `;
            });
            bar.innerHTML = html;
        }

        function switchPlsqlSubtab(id) {
            activePlsqlTabId = id;
            const tab = plsqlTabs.find(t => t.id === id);
            if (tab) {
                activePlsqlName = tab.name;
                activePlsqlType = tab.type;
                document.getElementById('plsqlObjName').textContent = tab.name;
                document.getElementById('plsqlObjBadge').textContent = tab.type;
                if (mainMonacoEditor) mainMonacoEditor.setValue(tab.code);
                renderCodeExplorerMain(tab.outline);
            }
            renderPlsqlSubtabs();
        }

        function closePlsqlSubtab(id) {
            plsqlTabs = plsqlTabs.filter(t => t.id !== id);
            if (activePlsqlTabId === id) {
                if (plsqlTabs.length > 0) {
                    switchPlsqlSubtab(plsqlTabs[plsqlTabs.length - 1].id);
                } else {
                    activePlsqlTabId = null;
                    activePlsqlName = '';
                    document.getElementById('plsqlObjName').textContent = 'No Object Selected';
                    if (mainMonacoEditor) mainMonacoEditor.setValue('-- Select a Package to edit...');
                }
            }
            renderPlsqlSubtabs();
        }

        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key.toLowerCase() === 'm') {
                e.preventDefault();
                createNewSqlTab();
            }
        });

        let isLoadingTns = false;
        async function loadTnsList(retryCount = 0, forceReload = false) {
            const select = document.getElementById('tnsSelect');
            if (!select) return;
            
            if (isLoadingTns && !forceReload) return;
            isLoadingTns = true;

            if (retryCount === 0) {
                select.innerHTML = '<option value="">-- Loading TNS entries... --</option>';
            }

            try {
                const res = await fetch('/api/tns-list?_t=' + Date.now());
                const data = await res.json();
                
                if (data.success && data.items && data.items.length > 0) {
                    tnsData = data.items;
                    select.innerHTML = '<option value="">-- Choose TNS Alias (' + data.count + ' available) --</option>';
                    tnsData.forEach(item => {
                        const opt = document.createElement('option');
                        opt.value = item.alias;
                        opt.textContent = `${item.alias} (${item.host}:${item.port})`;
                        select.appendChild(opt);
                    });

                    const savedAlias = localStorage.getItem('last_tns_alias');
                    let targetAlias = tnsData.find(t => t.alias === savedAlias);
                    if (!targetAlias) {
                        targetAlias = tnsData.find(t => t.alias.includes('8000') || t.alias.includes('PROD') || t.alias.includes('OAG')) || tnsData[0];
                    }
                    if (targetAlias) {
                        select.value = targetAlias.alias;
                        select.dispatchEvent(new Event('change'));
                    }
                    isLoadingTns = false;
                } else {
                    isLoadingTns = false;
                    select.innerHTML = `<option value="">-- ${data.error || 'No TNS entries found'} --</option>`;
                    if (retryCount < 8) {
                        setTimeout(() => loadTnsList(retryCount + 1, true), 600);
                    }
                }
            } catch (err) {
                isLoadingTns = false;
                console.error("Failed to load TNS list (attempt " + (retryCount + 1) + "):", err);
                if (retryCount < 8) {
                    setTimeout(() => loadTnsList(retryCount + 1, true), 600);
                } else {
                    select.innerHTML = '<option value="">-- Click 🔄 Refresh to Retry Loading TNS --</option>';
                }
            }
        }

        document.getElementById('tnsSelect').addEventListener('change', async (e) => {
            const alias = e.target.value;
            if (alias) localStorage.setItem('last_tns_alias', alias);
            selectedTns = tnsData.find(t => t.alias === alias);
            const card = document.getElementById('tnsInfoCard');
            if (selectedTns) {
                card.innerHTML = `
                    Host: <span>${selectedTns.host}</span><br>
                    Port: <span>${selectedTns.port}</span><br>
                    ${selectedTns.service_name ? 'Service Name: <span>' + selectedTns.service_name + '</span>' : 'SID: <span>' + selectedTns.sid + '</span>'}
                `;
            } else {
                card.innerHTML = "Select a TNS alias above to view connection details.";
            }
            
            // Auto-fill password specifically for this TNS Alias
            if (alias) {
                try {
                    const res = await fetch('/api/profiles');
                    const data = await res.json();
                    if (data.success && data.profiles) {
                        const profiles = data.profiles;
                        const profileKey = Object.keys(profiles).find(k => profiles[k].alias === alias);
                        if (profileKey) {
                            const p = profiles[profileKey];
                            document.getElementById('dbUser').value = p.user || '';
                            if (p.password) {
                                document.getElementById('dbPassword').value = p.password;
                                document.getElementById('chkSavePassword').checked = true;
                            } else {
                                document.getElementById('dbPassword').value = '';
                                document.getElementById('chkSavePassword').checked = false;
                            }
                        } else {
                            document.getElementById('dbPassword').value = '';
                        }
                    }
                } catch(err) { console.error("Profile load error:", err); }
            }
        });

        async function handleConnect() {
            const user = document.getElementById('dbUser').value.trim();
            const password = document.getElementById('dbPassword').value.trim();
            const alertBox = document.getElementById('connectionAlert');
            const btn = document.getElementById('btnConnect');

            if (!selectedTns) {
                alertBox.innerHTML = '<div class="alert alert-error">Please select a TNS Alias first.</div>';
                return;
            }
            if (!user || !password) {
                alertBox.innerHTML = '<div class="alert alert-error">Username and Password are required.</div>';
                return;
            }

            btn.disabled = true;
            btn.innerHTML = '<span class="loading-spinner"></span> Connecting...';
            alertBox.innerHTML = '';

            try {
                const payload = {
                    alias: selectedTns.alias,
                    host: selectedTns.host,
                    port: selectedTns.port,
                    service_name: selectedTns.service_name || '',
                    sid: selectedTns.sid || '',
                    user: user,
                    password: password,
                    save_password: document.getElementById('chkSavePassword').checked
                };

                const res = await fetch('/api/connect', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();

                if (data.success) {
                    alertBox.innerHTML = `<div class="alert alert-success">Connected to ${data.session_key}!</div>`;
                    loadSessionsList();
                    
                    const connDetails = document.getElementById('connDetails');
                    connDetails.removeAttribute('open');
                    document.getElementById('connSummaryText').textContent = '▲ Folded (Connected)';
                    fetchTables();
                } else {
                    alertBox.innerHTML = `<div class="alert alert-error">${data.error}</div>`;
                }
            } catch (err) {
                alertBox.innerHTML = `<div class="alert alert-error">${err.message}</div>`;
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>Connect New DB Session</span>';
            }
        }

        function switchTab(tabName) {
            document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-view').forEach(v => v.classList.remove('active'));

            const tabMap = {
                'sql': ['tabSqlBtn', 'tabSqlView'],
                'plsql': ['tabPlsqlBtn', 'tabPlsqlView'],
                'conc': ['tabConcBtn', 'tabConcView'],
                'desc': ['tabDescBtn', 'tabDescView'],
                'flex': ['tabFlexBtn', 'tabFlexView'],
                'ai': ['tabAiBtn', 'tabAiView']
            };

            const target = tabMap[tabName] || tabMap['sql'];
            const btnEl = document.getElementById(target[0]);
            const viewEl = document.getElementById(target[1]);

            if (btnEl) btnEl.classList.add('active');
            if (viewEl) viewEl.classList.add('active');

            if (tabName === 'sql' && typeof sqlMonacoEditor !== 'undefined' && sqlMonacoEditor) {
                setTimeout(() => sqlMonacoEditor.layout(), 100);
            } else if (tabName === 'plsql' && typeof mainMonacoEditor !== 'undefined' && mainMonacoEditor) {
                setTimeout(() => {
                    mainMonacoEditor.layout();
                    if (typeof monacoDiffEditor !== 'undefined' && monacoDiffEditor) monacoDiffEditor.layout();
                }, 100);
            }
        }

        function setFilterType(type) {
            activeTypeFilter = type;
            document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
            document.getElementById('pill' + type).classList.add('active');
            fetchTables();
        }

        function debounceSearch() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                fetchTables();
            }, 300);
        }

        async function fetchTables() {
            const listEl = document.getElementById('tableList');
            const statsEl = document.getElementById('searchStats');
            const query = document.getElementById('tableSearch').value.trim();
            const allSchemas = document.getElementById('chkAllSchemas').checked;

            if (query.length < 2 && activeTypeFilter === 'ALL') {
                statsEl.textContent = "Type at least 2 characters to search...";
                listEl.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-muted); font-size: 0.82rem;">💡 Type a keyword above (e.g. PO, AP, PTAR) to search DB objects.</div>';
                return;
            }

            listEl.innerHTML = '<div style="padding: 20px; text-align: center;"><span class="loading-spinner"></span> Searching DB...</div>';
            
            try {
                const url = `/api/tables?search=${encodeURIComponent(query)}&type=${activeTypeFilter}&all_schemas=${allSchemas}`;
                const res = await fetch(url);
                const data = await res.json();
                
                if (data.success) {
                    statsEl.textContent = `Found ${data.count} items (${allSchemas ? 'All Schemas' : 'User Schema'})`;
                    renderTables(data.tables);
                } else {
                    statsEl.textContent = "Search Error";
                    listEl.innerHTML = `<div style="padding: 16px; color: var(--danger); text-align: center;">Error: ${data.error}</div>`;
                }
            } catch (err) {
                statsEl.textContent = "Network Error";
                listEl.innerHTML = `<div style="padding: 16px; color: var(--danger); text-align: center;">${err.message}</div>`;
            }
        }

        function renderTables(tables) {
            const listEl = document.getElementById('tableList');
            if (tables.length === 0) {
                listEl.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-muted);">No matching objects found.</div>';
                return;
            }

            listEl.innerHTML = '';
            tables.forEach(t => {
                const div = document.createElement('div');
                div.className = 'table-item';
                const safeTypeClass = t.type.replace(/\s+/g, '_');
                div.innerHTML = `
                    <span>${t.name}</span>
                    <span class="type-badge ${safeTypeClass}">${t.type}</span>
                `;
                div.onclick = () => selectObject(t.name, t.type, div);
                listEl.appendChild(div);
            });
        }

        function selectObject(name, type, element) {
            document.querySelectorAll('.table-item').forEach(el => el.classList.remove('active'));
            if (element) element.classList.add('active');

            currentTableName = name;

            if (type === 'TABLE' || type === 'VIEW') {
                switchTab('sql');
                if (sqlMonacoEditor) sqlMonacoEditor.setValue("SELECT * FROM " + name + " WHERE rownum <= 10");
                runQuery();
                fetchTableStructure(name);
            } else {
                switchTab('plsql');
                loadPLSQLSource(name, type);
            }
        }

        async function fetchTableStructure(targetName) {
            const name = targetName || currentTableName;
            if (!name) return;

            document.getElementById('descTableName').textContent = name;
            const wrapper = document.getElementById('descTableWrapper');
            const stats = document.getElementById('descStats');

            wrapper.innerHTML = '<div style="padding: 40px; text-align: center;"><span class="loading-spinner"></span> Inspecting Column Structure...</div>';

            try {
                const res = await fetch(`/api/table/describe?name=${encodeURIComponent(name)}`);
                const data = await res.json();

                if (data.success) {
                    stats.textContent = `Total Columns in ${name}: ${data.count}`;
                    
                    let html = `
                        <table class="data-grid">
                            <thead>
                                <tr>
                                    <th>Column Name</th>
                                    <th>Data Type & Length</th>
                                    <th>Nullable</th>
                                </tr>
                            </thead>
                            <tbody>
                    `;
                    data.columns.forEach(col => {
                        html += `
                            <tr>
                                <td style="color:#fff; font-weight:600;">${col.column_name}</td>
                                <td style="color:var(--accent-teal);">${col.data_type}</td>
                                <td style="color:${col.nullable === 'Y' ? '#94a3b8' : '#f472b6'};">${col.nullable === 'Y' ? 'NULL' : 'NOT NULL'}</td>
                            </tr>
                        `;
                    });
                    html += '</tbody></table>';
                    wrapper.innerHTML = html;
                } else {
                    stats.textContent = "Inspection Error";
                    wrapper.innerHTML = `<div class="alert alert-error" style="margin: 20px;">${data.error}</div>`;
                }
            } catch (err) {
                stats.textContent = "Network Error";
                wrapper.innerHTML = `<div class="alert alert-error" style="margin: 20px;">${err.message}</div>`;
            }
        }

        async function loadPLSQLSource(name, type) {
            activePlsqlName = name;
            activePlsqlType = type || "PACKAGE BODY";

            const consoleBox = document.getElementById('compileConsole');
            const nameLabel = document.getElementById('plsqlObjName');
            const badge = document.getElementById('plsqlObjBadge');
            
            nameLabel.textContent = `${name}`;
            badge.textContent = activePlsqlType;
            badge.className = `type-badge ${activePlsqlType.replace(/\s+/g, '_')}`;

            if (mainMonacoEditor) {
                mainMonacoEditor.setValue("-- Fetching source code from Oracle DB...");
            }
            consoleBox.innerHTML = '<span style="color: var(--text-muted);">Fetching source code...</span>';

            try {
                const res = await fetch(`/api/plsql/source?name=${encodeURIComponent(name)}&type=${encodeURIComponent(activePlsqlType)}`);
                const data = await res.json();

                if (data.success) {
                    const resolvedName = data.name || name;
                    activePlsqlName = resolvedName;
                    activePlsqlType = data.type;
                    nameLabel.textContent = data.sub_program ? `${resolvedName}.${data.sub_program}` : resolvedName;
                    badge.textContent = data.type;
                    badge.className = `type-badge ${data.type.replace(/\s+/g, '_')}`;

                    const tabId = 'plsql_tab_' + resolvedName;
                    let existingTab = plsqlTabs.find(t => t.id === tabId);
                    if (!existingTab) {
                        existingTab = { id: tabId, name: resolvedName, type: data.type, code: data.code, originalCode: data.code, outline: data.outline };
                        plsqlTabs.push(existingTab);
                    } else {
                        existingTab.code = data.code;
                        existingTab.originalCode = data.code;
                        existingTab.outline = data.outline;
                    }
                    
                    // If in diff mode, turn it off when loading a new file
                    if (isDiffMode) {
                        toggleDiffMode();
                    }
                    
                    activePlsqlTabId = tabId;
                    renderPlsqlSubtabs();

                    if (mainMonacoEditor) {
                        mainMonacoEditor.setValue(data.code);
                        if (data.target_line && typeof jumpToLineMain === 'function') {
                            setTimeout(() => jumpToLineMain(data.target_line), 150);
                        }
                    }
                    renderCodeExplorerMain(data.outline);
                    const jumpMsg = data.sub_program ? ` (Navigated to ${data.sub_program} @ line ${data.target_line || 1})` : '';
                    consoleBox.innerHTML = `<span style="color: #4ec9b0;">Loaded ${data.line_count} lines of code for ${data.type} ${resolvedName}${jumpMsg}. Ready to edit and compile.</span>`;
                } else {
                    if (mainMonacoEditor) mainMonacoEditor.setValue("");
                    consoleBox.innerHTML = `<span style="color: var(--danger);">Error loading source code: ${data.error}</span>`;
                }
            } catch (err) {
                consoleBox.innerHTML = `<span style="color: var(--danger);">Network error: ${err.message}</span>`;
            }
        }

        function renderCodeExplorerMain(outline) {
            const tree = document.getElementById('treeContentMain');
            if (!outline || outline.length === 0) {
                tree.innerHTML = '<div style="font-size:0.75rem; color:#858585;">No procedures or functions found.</div>';
                return;
            }

            let html = '';
            outline.forEach(item => {
                const icon = item.type === 'PROCEDURE' ? 'p()' : (item.type === 'FUNCTION' ? 'f()' : 'v');
                const cls = item.type === 'PROCEDURE' ? 'proc' : (item.type === 'FUNCTION' ? 'func' : 'var');
                html += `
                    <div class="tree-node-wrapper">
                        <div class="tree-node ${cls}" onclick="jumpToLineMain(${item.line})" style="flex:1;">
                            <span><b>${icon}</b> ${item.name}</span>
                        </div>
                        ${(item.type === 'PROCEDURE' || item.type === 'FUNCTION') ? `
                        <div style="display:flex; gap:4px;">
                            <button class="btn-run-proc" onclick="openExecModal('${item.name}', '${item.type}')" title="Auto-Generate Execution Script">⚡ Run</button>
                            <button class="btn-run-proc" style="color:#38bdf8; border-color:rgba(56,189,248,0.3);" onclick="extractMainQuery('${item.name}')" title="AI Extract Main SQL Query">🔍 SQL DB</button>
                        </div>` : ''}
                    </div>
                `;
            });
            tree.innerHTML = html;
        }

        function jumpToLineMain(line) {
            if (!mainMonacoEditor) return;
            mainMonacoEditor.revealLineInCenter(line);
            mainMonacoEditor.setPosition({ lineNumber: line, column: 1 });
            mainMonacoEditor.focus();
        }

        function openStandaloneEditorWindow(targetPkg, targetType) {
            const pkgName = targetPkg || activePlsqlName;
            const objType = targetType || activePlsqlType || 'PACKAGE BODY';
            if (!pkgName) return;

            if (window.pywebview && window.pywebview.api && window.pywebview.api.open_new_window) {
                window.pywebview.api.open_new_window(pkgName, objType);
            } else {
                const url = `/standalone-editor?name=${encodeURIComponent(pkgName)}&type=${encodeURIComponent(objType)}`;
                window.open(url, '_blank', 'width=1200,height=820,scrollbars=yes,resizable=yes');
            }
        }

        function openPackageFromConc(pkgName) {
            if (!pkgName || pkgName === '-') return;
            switchTab('plsql');
            loadPLSQLSource(pkgName, 'PACKAGE BODY');
        }

        async function backupCode(type) {
            let code = '';
            let defaultName = '';
            if (type === 'SQL') {
                code = sqlMonacoEditor ? sqlMonacoEditor.getValue() : '';
                defaultName = 'query_backup.sql';
            } else if (type === 'PLSQL') {
                code = mainMonacoEditor ? mainMonacoEditor.getValue() : '';
                defaultName = 'plsql_backup.sql';
            }
            
            if (!code) {
                alert("ไม่มีโค้ดให้ Backup");
                return;
            }

            downloadBlob(code, defaultName, 'text/plain');
        }

        async function genDDL() {
            if (!activePlsqlName) {
                alert("กรุณาเลือก Package, Procedure หรือ Function จากแถบด้านซ้ายก่อน (Please select an object first)");
                return;
            }
            
            if (mainMonacoEditor) {
                mainMonacoEditor.setValue("-- Generating DDL for " + activePlsqlName + "...\n-- Please wait...");
            }
            
            try {
                const res = await fetch('/api/gen_ddl', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ object_name: activePlsqlName })
                });
                const data = await res.json();
                if (mainMonacoEditor) {
                    if (data.success) {
                        mainMonacoEditor.setValue(data.ddl);
                    } else {
                        mainMonacoEditor.setValue("-- Error generating DDL: \n-- " + data.error);
                    }
                }
            } catch (err) {
                if (mainMonacoEditor) {
                    mainMonacoEditor.setValue("-- Network Error: " + err.message);
                }
            }
        }

        function toggleDiffMode() {
            if (!activePlsqlTabId) {
                alert("กรุณาเปิดไฟล์ก่อน (Please open a file first)");
                return;
            }

            const currentTab = plsqlTabs.find(t => t.id === activePlsqlTabId);
            if (!currentTab) return;

            const mainContainer = document.getElementById('monacoEditorMainContainer');
            const diffContainer = document.getElementById('monacoDiffEditorMainContainer');
            const btnToggle = document.getElementById('btnToggleDiff');

            if (!isDiffMode) {
                // Enter Diff Mode
                isDiffMode = true;
                
                // Save current edits from main editor to tab code
                if (mainMonacoEditor) {
                    currentTab.code = mainMonacoEditor.getValue();
                }

                mainContainer.style.display = 'none';
                diffContainer.style.display = 'block';
                btnToggle.innerHTML = '📝 Edit Mode';
                btnToggle.classList.replace('btn-secondary', 'btn-primary');

                if (!monacoDiffEditor) {
                    monacoDiffEditor = monaco.editor.createDiffEditor(diffContainer, {
                        theme: 'jnav-dark',
                        enableSplitViewResizing: true,
                        renderSideBySide: true,
                        originalEditable: true,
                        renderMarginRevertIcon: true
                    });
                }

                const originalModel = monaco.editor.createModel(currentTab.originalCode || '', 'sql');
                const modifiedModel = monaco.editor.createModel(currentTab.code || '', 'sql');
                monacoDiffEditor.setModel({
                    original: originalModel,
                    modified: modifiedModel
                });
                
                // Force layout update
                setTimeout(() => monacoDiffEditor.layout(), 50);

            } else {
                // Exit Diff Mode
                isDiffMode = false;
                
                // Save current edits from diff editor back to tab code
                if (monacoDiffEditor) {
                    const modifiedModel = monacoDiffEditor.getModel().modified;
                    currentTab.code = modifiedModel.getValue();
                }

                diffContainer.style.display = 'none';
                mainContainer.style.display = 'block';
                btnToggle.innerHTML = '⚖️ Compare';
                btnToggle.classList.replace('btn-primary', 'btn-secondary');

                if (mainMonacoEditor) {
                    mainMonacoEditor.setValue(currentTab.code || '');
                }
            }
        }

        async function compilePLSQL() {
            if (!mainMonacoEditor) return;
            const code = mainMonacoEditor.getValue().trim();
            const consoleBox = document.getElementById('compileConsole');
            const btn = document.getElementById('btnCompile');
            const lastCompileSpan = document.getElementById('lastCompileTime');

            if (!code) {
                consoleBox.innerHTML = '<span style="color: var(--danger);">Code is empty.</span>';
                return;
            }

            // --- Backup Prompt Before Compile ---
            if (confirm("คุณต้องการ Save Backup เก็บไว้ในเครื่องก่อนทำการ Compile ด้วยหรือไม่?\\n(ถ้าต้องการ โปรแกรมจะเปิดหน้าต่างให้เลือกโฟลเดอร์เก็บไฟล์)")) {
                const now = new Date();
                const pad = (n) => String(n).padStart(2, '0');
                const dateStr = `${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
                const defaultBackupName = `${activePlsqlName || 'plsql_backup'}_${dateStr}.sql`;
                downloadBlob(code, defaultBackupName, 'text/plain');
            }
            // ------------------------------------

            const now = new Date();
            const timeStr = now.toLocaleTimeString('th-TH');
            if (lastCompileSpan) {
                lastCompileSpan.textContent = 'ล่าสุด: ' + timeStr;
            }

            btn.disabled = true;
            btn.innerHTML = '<span class="loading-spinner"></span> Compiling...';
            consoleBox.innerHTML = '<span style="color: var(--warning);">Compiling and executing DDL in Oracle DB...</span><br><span style="color: #94a3b8; font-size: 0.8em;">(Latest compile triggered at ' + timeStr + ')</span>';

            try {
                const payload = {
                    name: activePlsqlName,
                    type: activePlsqlType,
                    code: code
                };

                const res = await fetch('/api/plsql/compile', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();

                if (data.success) {
                    consoleBox.innerHTML = `
                        <div style="color: #4ec9b0; font-weight: 600;">
                            ✅ ${data.message}
                        </div>
                    `;
                } else if (data.errors) {
                    let errHtml = `<div style="color: #f14c4c; font-weight: 600; font-size: 0.95rem; margin-bottom: 8px;">❌ ${data.message} (${data.errors.length} ข้อผิดพลาด)</div>`;
                    errHtml += `<div style="display:flex; flex-direction:column; gap:6px;">`;
                    data.errors.forEach(e => {
                        let errText = escapeHtml(e.text);
                        let errCodeMatch = errText.match(/^(PLS-\d+|ORA-\d+):\s*(.*)/);
                        let codeTag = '';
                        let mainReason = errText;
                        if (errCodeMatch) {
                            codeTag = `<span style="background: rgba(241, 76, 76, 0.2); color: #fca5a5; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; margin-right: 6px; border: 1px solid rgba(241,76,76,0.3);">${errCodeMatch[1]}</span>`;
                            mainReason = errCodeMatch[2];
                        }
                        
                        errHtml += `
                            <div class="error-card" style="background: #1e1212; border: 1px solid rgba(241,76,76,0.2); border-top: 2px solid rgba(241,76,76,0.55); padding: 8px 12px; border-radius: 6px; cursor: pointer; transition: background 0.15s; display: flex; flex-direction: column; gap: 4px;"
                                 onmouseover="this.style.background='#2d2d2d'" onmouseout="this.style.background='#252526'"
                                 onclick="if(mainMonacoEditor){mainMonacoEditor.revealLineInCenter(${e.line}); mainMonacoEditor.setPosition({lineNumber: ${e.line}, column: ${e.position}}); mainMonacoEditor.focus();}">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <div style="font-size: 0.85rem; font-family: 'JetBrains Mono', monospace;">
                                        <span style="color: #569cd6; font-weight: bold;">Line ${e.line}</span>
                                        <span style="color: #858585;"> : Col ${e.position}</span>
                                    </div>
                                    <button style="background: #333; color: #d4d4d4; border: 1px solid #444; border-radius: 4px; padding: 2px 8px; font-size: 0.75rem; cursor: pointer;">🔍 ย้ายไป (Go)</button>
                                </div>
                                <div style="color: #d4d4d4; font-size: 0.85rem; line-height: 1.4; margin-top: 4px;">
                                    ${codeTag}${mainReason}
                                </div>
                            </div>
                        `;
                    });
                    errHtml += `</div>`;
                    consoleBox.innerHTML = errHtml;
                } else {
                    consoleBox.innerHTML = `<div style="color: var(--danger);">❌ Compilation Error: ${data.error}</div>`;
                }
            } catch (err) {
                consoleBox.innerHTML = `<div style="color: var(--danger);">Network Error: ${err.message}</div>`;
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>⚡ Compile / Save to DB</span>';
            }
        }

        async function searchConcurrent() {
            const query = document.getElementById('concSearchInput').value.trim();
            const searchType = document.getElementById('concSearchType').value;
            const wrapper = document.getElementById('concTableWrapper');
            const stats = document.getElementById('concStats');

            if (!query && searchType === 'ALL') {
                stats.textContent = "Please enter a keyword or request ID to search...";
                wrapper.innerHTML = '<div style="padding: 40px; text-align: center; color: var(--text-muted);">Please enter a program name, short name, or request ID above to search.</div>';
                return;
            }

            wrapper.innerHTML = '<div style="padding: 40px; text-align: center;"><span class="loading-spinner"></span> Searching Concurrent Requests...</div>';

            try {
                const res = await fetch(`/api/concurrent/search?search=${encodeURIComponent(query)}&type=${searchType}`);
                const data = await res.json();

                if (data.success) {
                    stats.textContent = `Found ${data.count} Concurrent Requests`;
                    renderConcGrid(data.items);
                } else {
                    stats.textContent = "Query Error";
                    wrapper.innerHTML = `<div class="alert alert-error" style="margin: 20px;">${data.error}</div>`;
                }
            } catch (err) {
                stats.textContent = "Network Error";
                wrapper.innerHTML = `<div class="alert alert-error" style="margin: 20px;">${err.message}</div>`;
            }
        }

        function copyCellText(text, el) {
            if (!text || text === '-') return;
            
            // Create a temporary textarea to copy text
            const input = document.createElement('textarea');
            input.value = text;
            document.body.appendChild(input);
            input.select();
            try {
                document.execCommand('copy');
            } catch (err) {
                console.error('Failed to copy text: ', err);
            }
            document.body.removeChild(input);
            
            // Visual feedback
            const originalColor = el.style.color;
            el.style.color = '#4ec9b0';
            el.style.transition = 'color 0.2s';
            
            // Show toast notification
            const toast = document.createElement('div');
            toast.textContent = `Copied: ${text}`;
            toast.style.position = 'fixed';
            toast.style.bottom = '20px';
            toast.style.right = '20px';
            toast.style.background = '#4ec9b0';
            toast.style.color = '#000';
            toast.style.padding = '8px 16px';
            toast.style.borderRadius = '4px';
            toast.style.fontWeight = 'bold';
            toast.style.zIndex = '10000';
            toast.style.boxShadow = '0 4px 6px rgba(0,0,0,0.3)';
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s';
            document.body.appendChild(toast);
            
            // Fade in
            setTimeout(() => toast.style.opacity = '1', 10);
            
            // Fade out and remove
            setTimeout(() => {
                el.style.color = originalColor;
                toast.style.opacity = '0';
                setTimeout(() => document.body.removeChild(toast), 300);
            }, 1500);
        }

        function renderConcGrid(items) {
            const wrapper = document.getElementById('concTableWrapper');
            if (items.length === 0) {
                wrapper.innerHTML = '<div style="padding: 40px; text-align: center; color: var(--text-muted);">No concurrent requests found matching search criteria.</div>';
                return;
            }

            let html = `
                <table class="data-grid">
                    <thead>
                        <tr>
                            <th>Action</th>
                            <th>Request ID</th>
                            <th>Request Date</th>
                            <th>User Name</th>
                            <th>User Concurrent Name</th>
                            <th>Program Short</th>
                            <th>Type</th>
                            <th>Package / Executable (Click to View Body)</th>
                            <th>Run Time (Sec)</th>
                            <th>Responsibility</th>
                            <th>Source File Path</th>
                            <th>Arguments</th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            items.forEach(r => {
                const pkgName = r.package_name || '-';
                const pkgLink = pkgName !== '-' 
                    ? `<button class="btn" style="padding:3px 8px; font-size:0.75rem; background:linear-gradient(135deg, #f59e0b, #d97706);" onclick="openPackageFromConc('${pkgName}')">📦 ${escapeHtml(pkgName)}</button>`
                    : '<span style="color:#64748b;">-</span>';

                let actionColumn = '<span style="color:#64748b; font-size:0.75rem;">-</span>';
                let actions = [];

                // 1. Output View Button (PDF / Excel / RTF / HTML / Text)
                const hasPublishedOutput = !!(r.pub_outfile_name && r.pub_outfile_name !== 'N/A');
                const viewPath = hasPublishedOutput ? r.pub_outfile_name : (r.outfile_name || '');
                const pubType = (r.pub_file_type || '').toUpperCase();
                
                let viewLabel = '👁️ View';
                let viewBtnStyle = 'background:linear-gradient(135deg, #f59e0b, #d97706);';
                if (pubType === 'PDF' || (viewPath && viewPath.toLowerCase().endsWith('.pdf'))) {
                    viewLabel = '👁️ PDF';
                    viewBtnStyle = 'background:linear-gradient(135deg, #ef4444, #b91c1c);';
                } else if (pubType === 'EXCEL' || (viewPath && (viewPath.toLowerCase().endsWith('.xls') || viewPath.toLowerCase().endsWith('.xlsx')))) {
                    viewLabel = '📊 Excel';
                    viewBtnStyle = 'background:linear-gradient(135deg, #10b981, #047857);';
                } else if (pubType === 'RTF' || (viewPath && viewPath.toLowerCase().endsWith('.rtf'))) {
                    viewLabel = '📄 RTF';
                    viewBtnStyle = 'background:linear-gradient(135deg, #6366f1, #4338ca);';
                } else if (pubType === 'HTML' || (viewPath && (viewPath.toLowerCase().endsWith('.htm') || viewPath.toLowerCase().endsWith('.html')))) {
                    viewLabel = '🌐 HTML';
                    viewBtnStyle = 'background:linear-gradient(135deg, #06b6d4, #0891b2);';
                }

                if (viewPath) {
                    const safeViewPath = viewPath.replace(/'/g, "\\'");
                    const safeFileName = (viewPath.substring(viewPath.lastIndexOf('/') + 1) || 'output').replace(/'/g, "\\'");
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; ${viewBtnStyle}" onclick="viewOutputFile('${safeViewPath}', '${safeFileName}', '${pubType}')" title="View Generated Output (${pubType || 'Output'})">${viewLabel}</button>`);
                }
                
                // 2. XML Download Button (for XML Publisher or reports producing XML data)
                const hasXml = (r.outfile_name && (r.outfile_name.toLowerCase().endsWith('.xml') || hasPublishedOutput || r.output_file_type === 'XML'));
                if (hasXml && r.outfile_name) {
                    const safeXmlPath = r.outfile_name.replace(/'/g, "\\'");
                    const progName = (r.program_short_name || r.package_name || 'REPORT').replace(/'/g, "\\'");
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; background:linear-gradient(135deg, #f43f5e, #be123c);" onclick="downloadOutputXml('${safeXmlPath}', '${r.request_id || ''}', '${progName}')" title="Download Data XML file">📥 XML</button>`);
                }

                // 3. View Log Button (📑 Log - Standard Oracle EBS View Log)
                if (r.logfile_name && r.logfile_name !== 'N/A') {
                    const safeLogPath = r.logfile_name.replace(/'/g, "\\'");
                    const logFileName = (r.logfile_name.substring(r.logfile_name.lastIndexOf('/') + 1) || ('l' + r.request_id + '.req')).replace(/'/g, "\\'");
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; background:linear-gradient(135deg, #475569, #334155); border:1px solid #64748b;" onclick="viewRequestLog('${r.request_id || ''}', '${safeLogPath}', '${logFileName}')" title="View Concurrent Request Log (📑 View Log...)">📑 Log</button>`);
                }

                // 4. Oracle Reports (.rdf)
                if (r.program_type && r.program_type.includes('Oracle Reports')) {
                    const basePathMatch = r.source_file_path ? r.source_file_path.match(/^(\$[A-Z0-9_]+_TOP)/) : null;
                    const basePath = basePathMatch ? basePathMatch[1] : '$INV_TOP';
                    let searchPath = r.source_file_path ? r.source_file_path.substring(0, r.source_file_path.lastIndexOf('/')) : basePath + '/reports/US';
                    
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; background:linear-gradient(135deg, #0284c7, #0369a1);" onclick="downloadOracleReport('${(r.source_file_path || '').replace(/'/g, "\\'")}', '${r.package_name || r.program_short_name || ''}', '${basePath}')" title="Download Oracle Report (.rdf) from Server">📥 RDF</button>`);
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; background:linear-gradient(135deg, #d946ef, #a21caf);" onclick="triggerUploadRdf('${basePath}')" title="Upload and Backup RDF">📤 RDF</button>`);
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; background:linear-gradient(135deg, #7c3aed, #6d28d9);" onclick="openSftpExplorer('${searchPath}', '${r.program_short_name || ''}')" title="Browse Server Files (SFTP)">📂 SFTP</button>`);
                }
                
                // 5. RTF Template
                if (r.program_type && (r.program_type.includes('RTF') || r.program_type.includes('XML Publisher') || r.program_type.includes('PL/SQL') || r.program_type.includes('Oracle Reports'))) {
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; background:linear-gradient(135deg, #10b981, #059669);" onclick="downloadTemplate('${r.program_short_name || ''}')" title="Download RTF Template">📥 RTF</button>`);
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; background:linear-gradient(135deg, #14b8a6, #0f766e);" onclick="triggerUploadRtf('${r.program_short_name || ''}')" title="Upload and Backup RTF Template">📤 RTF</button>`);
                }

                // 6. Gen FNDLOAD Button (Clone Concurrent Program to another site)
                if (r.program_short_name) {
                    const safeProgShort = (r.program_short_name || '').replace(/'/g, "\\'");
                    const safeProgName = (r.user_concurrent_program_name || r.program_short_name || '').replace(/'/g, "\\'");
                    const safeAppShort = (r.application_short_name || 'XXCUST').replace(/'/g, "\\'");
                    actions.push(`<button class="btn btn-primary" style="padding:4px 8px; font-size:0.75rem; background:linear-gradient(135deg, #0284c7, #0369a1); border:1px solid #38bdf8;" onclick="openFndLoadModal('${safeProgShort}', '${safeProgName}', '${safeAppShort}')" title="Generate FNDLOAD Scripts (Clone Program to Site)">🚀 FND</button>`);
                }
                
                if (actions.length > 0) {
                    actionColumn = `<div style="display:flex; flex-wrap:wrap; gap:4px; max-width:270px;">${actions.join('')}</div>`;
                }

                html += `
                    <tr>
                        <td>${actionColumn}</td>
                        <td style="color: var(--accent-teal); font-weight: 600; cursor: copy;" title="Click to copy" onclick="copyCellText('${r.request_id || ''}', this)">${r.request_id || ''}</td>
                        <td style="cursor: copy;" title="Click to copy" onclick="copyCellText('${r.request_date || ''}', this)">${r.request_date || ''}</td>
                        <td style="cursor: copy;" title="Click to copy" onclick="copyCellText('${r.user_name || ''}', this)">${r.user_name || ''}</td>
                        <td style="color: #fff; font-weight: 600; cursor: copy;" title="Click to copy" onclick="copyCellText('${(r.user_concurrent_program_name || '').replace(/'/g, "\\'")}', this)">${escapeHtml(r.user_concurrent_program_name || '')}</td>
                        <td style="color: #818cf8; cursor: copy;" title="Click to copy" onclick="copyCellText('${(r.program_short_name || '').replace(/'/g, "\\'")}', this)">${escapeHtml(r.program_short_name || '')}</td>
                        <td style="cursor: copy;" title="Click to copy" onclick="copyCellText('${r.program_type || ''}', this)"><span class="type-badge ${r.program_type === 'PL/SQL' ? 'PACKAGE' : 'VIEW'}">${r.program_type || ''}</span></td>
                        <td>${pkgLink}</td>
                        <td style="cursor: copy;" title="Click to copy" onclick="copyCellText('${r.run_time_sec || 0}', this)">${r.run_time_sec || 0}</td>
                        <td style="cursor: copy;" title="Click to copy" onclick="copyCellText('${(r.responsibility_name || '').replace(/'/g, "\\'")}', this)">${escapeHtml(r.responsibility_name || '')}</td>
                        <td style="font-size: 0.76rem; color: #94a3b8; cursor: copy;" title="Click to copy" onclick="copyCellText('${(r.source_file_path || '').replace(/'/g, "\\'")}', this)">${escapeHtml(r.source_file_path || '')}</td>
                        <td style="font-size: 0.76rem; cursor: copy;" title="${escapeHtml(r.argument_text || '')} (Click to copy)" onclick="copyCellText('${(r.argument_text || '').replace(/'/g, "\\'")}', this)">${escapeHtml(r.argument_text || '')}</td>
                    </tr>
                `;
            });

            html += '</tbody></table>';
            wrapper.innerHTML = html;
        }

        // --- Operation Status Modal & Toast Helpers ---
        let currentOpLocalPath = '';

        function showToast(message, duration = 2500, type = 'info') {
            let container = document.getElementById('appToastContainer');
            if (!container) {
                container = document.createElement('div');
                container.id = 'appToastContainer';
                container.style.cssText = 'position:fixed; bottom:24px; right:24px; z-index:11000; display:flex; flex-direction:column; gap:8px; pointer-events:none;';
                document.body.appendChild(container);
            }
            const toast = document.createElement('div');
            const bg = type === 'success' ? 'rgba(16, 185, 129, 0.95)' : type === 'error' ? 'rgba(239, 68, 68, 0.95)' : 'rgba(37, 99, 235, 0.95)';
            toast.style.cssText = `background:${bg}; color:#fff; padding:10px 18px; border-radius:8px; font-size:0.85rem; font-weight:500; box-shadow:0 8px 24px rgba(0,0,0,0.4); display:flex; align-items:center; gap:8px; pointer-events:auto; transition:all 0.25s ease; opacity:0; transform:translateY(10px);`;
            toast.innerHTML = message;
            container.appendChild(toast);
            requestAnimationFrame(() => {
                toast.style.opacity = '1';
                toast.style.transform = 'translateY(0)';
            });
            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transform = 'translateY(10px)';
                setTimeout(() => toast.remove(), 300);
            }, duration);
        }

        function showOpProgress(title, message) {
            const modal = document.getElementById('opStatusModal');
            if (!modal) return;
            currentOpLocalPath = '';

            document.getElementById('opStatusHeaderIcon').textContent = '🔄';
            const headerTitle = document.getElementById('opStatusHeaderTitle');
            headerTitle.textContent = title || 'กำลังดำเนินการ...';
            headerTitle.style.color = 'var(--accent-teal)';

            document.getElementById('opStatusVisual').innerHTML = '<div class="loading-spinner" style="width: 44px; height: 44px; border-width: 4px; border-top-color: var(--accent-primary);"></div>';
            
            const headline = document.getElementById('opStatusHeadline');
            headline.textContent = title || 'กำลังดำเนินการ...';
            headline.style.color = 'var(--text-bright)';
            
            document.getElementById('opStatusMessage').textContent = message || 'กรุณารอสักครู่ ระบบกำลังสื่อสารกับเซิร์ฟเวอร์...';

            const detailsBox = document.getElementById('opStatusDetails');
            detailsBox.style.display = 'none';
            detailsBox.innerHTML = '';

            document.getElementById('opStatusActions').innerHTML = '<button class="btn btn-secondary" style="opacity:0.6; cursor:not-allowed;" disabled>⏳ กำลังดำเนินการ...</button>';

            modal.style.display = 'flex';
        }

        function showOpSuccess(title, message, details = {}, localPath = '') {
            const modal = document.getElementById('opStatusModal');
            if (!modal) return;
            currentOpLocalPath = localPath || '';

            document.getElementById('opStatusHeaderIcon').textContent = '✅';
            const headerTitle = document.getElementById('opStatusHeaderTitle');
            headerTitle.textContent = 'ดำเนินการสำเร็จ (Success)';
            headerTitle.style.color = 'var(--success)';

            document.getElementById('opStatusVisual').innerHTML = `
                <div style="width: 58px; height: 58px; border-radius: 50%; background: rgba(120, 194, 155, 0.15); border: 2px solid var(--success); display: flex; align-items: center; justify-content: center; font-size: 2rem; box-shadow: 0 0 25px rgba(120, 194, 155, 0.25);">
                    ✅
                </div>
            `;

            const headline = document.getElementById('opStatusHeadline');
            headline.textContent = title || 'ทำรายการสำเร็จเรียบร้อย!';
            headline.style.color = 'var(--success)';
            document.getElementById('opStatusMessage').textContent = message || '';

            const detailsBox = document.getElementById('opStatusDetails');
            const entries = Object.entries(details).filter(([k, v]) => v !== undefined && v !== null && v !== '');
            if (entries.length > 0) {
                detailsBox.style.display = 'flex';
                detailsBox.innerHTML = entries.map(([k, v]) => `
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:10px; border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:6px;">
                        <span style="color:var(--text-muted); font-weight:600; white-space:nowrap;">${k}:</span>
                        <span style="color:var(--text-main); word-break:break-all; text-align:right; font-family:'JetBrains Mono', monospace; font-size:0.8rem;">${v}</span>
                    </div>
                `).join('');
            } else {
                detailsBox.style.display = 'none';
            }

            let actionsHtml = '';
            if (currentOpLocalPath) {
                actionsHtml += `<button class="btn btn-primary" onclick="openLocalFolder(currentOpLocalPath)" style="background: #2563eb; color: #fff; font-weight: 600; padding: 8px 16px;">📂 เปิดโฟลเดอร์</button>`;
                actionsHtml += `<button class="btn btn-secondary" onclick="copyToClipboard(currentOpLocalPath)" style="padding: 8px 14px;">📋 คัดลอก Path</button>`;
            }
            actionsHtml += `<button class="btn" style="background:var(--bg-elevated); border:1px solid var(--panel-border-light); padding: 8px 20px; font-weight:600;" onclick="closeOpStatusModal()">ตกลง</button>`;
            document.getElementById('opStatusActions').innerHTML = actionsHtml;

            modal.style.display = 'flex';
        }

        function showOpError(title, errorMsg) {
            const modal = document.getElementById('opStatusModal');
            if (!modal) return;
            currentOpLocalPath = '';

            document.getElementById('opStatusHeaderIcon').textContent = '❌';
            const headerTitle = document.getElementById('opStatusHeaderTitle');
            headerTitle.textContent = 'การดำเนินการไม่สำเร็จ (Failed)';
            headerTitle.style.color = 'var(--danger)';

            document.getElementById('opStatusVisual').innerHTML = `
                <div style="width: 58px; height: 58px; border-radius: 50%; background: rgba(217, 119, 119, 0.15); border: 2px solid var(--danger); display: flex; align-items: center; justify-content: center; font-size: 2rem; box-shadow: 0 0 25px rgba(217, 119, 119, 0.25);">
                    ❌
                </div>
            `;

            const headline = document.getElementById('opStatusHeadline');
            headline.textContent = title || 'เกิดข้อผิดพลาด';
            headline.style.color = 'var(--danger)';
            document.getElementById('opStatusMessage').textContent = '';

            const detailsBox = document.getElementById('opStatusDetails');
            detailsBox.style.display = 'flex';
            detailsBox.innerHTML = `
                <div style="color: #f87171; font-family:'JetBrains Mono', monospace; font-size:0.8rem; word-break:break-all; line-height:1.5;">
                    ${escapeHtml(errorMsg || 'Unknown error occurred.')}
                </div>
            `;

            document.getElementById('opStatusActions').innerHTML = `
                <button class="btn btn-secondary" onclick="closeOpStatusModal()" style="padding: 8px 24px;">ปิด</button>
            `;

            modal.style.display = 'flex';
        }

        function closeOpStatusModal() {
            const modal = document.getElementById('opStatusModal');
            if (modal) modal.style.display = 'none';
        }

        async function openLocalFolder(path) {
            if (!path) return;
            try {
                const res = await fetch('/api/system/open_folder', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({path: path})
                });
                const data = await res.json();
                if (data.success) {
                    showToast('📂 เปิดโฟลเดอร์ใน Windows Explorer แล้ว', 2500, 'success');
                } else {
                    showToast('⚠️ เปิดโฟลเดอร์ไม่สำเร็จ: ' + (data.error || 'ไม่พบ Path'), 3500, 'error');
                }
            } catch(err) {
                showToast('⚠️ เกิดข้อผิดพลาดในการเปิดโฟลเดอร์: ' + err.message, 3500, 'error');
            }
        }

        function copyToClipboard(text) {
            if (!text) return;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(() => {
                    showToast('📋 คัดลอกที่อยู่ไฟล์ลงคลิปบอร์ดแล้ว', 2000, 'info');
                }).catch(() => fallbackCopy(text));
            } else {
                fallbackCopy(text);
            }
        }

        function fallbackCopy(text) {
            const ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            try {
                document.execCommand('copy');
                showToast('📋 คัดลอกที่อยู่ไฟล์แล้ว', 2000, 'info');
            } catch (e) {
                showToast('⚠️ ไม่สามารถคัดลอกได้', 2000, 'error');
            }
            ta.remove();
        }

        async function viewOutputFile(remotePath, filename, fileType) {
            if (!remotePath) { 
                showOpError('ไม่พบไฟล์ Output', 'ไม่มีที่อยู่ไฟล์ Output สำหรับคำขอนี้บนเซิร์ฟเวอร์'); 
                return; 
            }
            showToast(`👁️ กำลังโหลด ${fileType || 'Output'}...`, 2000, 'info');
            try {
                const res = await fetch('/api/sftp/check_auth', { 
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({})
                });
                let auth = { configured: true };
                try {
                    auth = await res.json();
                } catch (pe) {
                    console.error("Auth check parse error", pe);
                }
                
                if (!auth.configured) {
                    showSftpAuthPrompt(auth.host || '', () => viewOutputFile(remotePath, filename, fileType));
                    return;
                }
                
                const url = '/api/sftp/view_output?remote_path=' + encodeURIComponent(remotePath) + '&filename=' + encodeURIComponent(filename);
                window.open(url, '_blank');
            } catch (e) {
                showOpError('เกิดข้อผิดพลาดในการเปิดไฟล์ Output', e.message || String(e));
            }
        }
        
        async function downloadOutputXml(remotePath, requestId, programName) {
            if (!remotePath) { 
                showOpError('ไม่พบที่อยู่ไฟล์ XML', 'ไม่มีพาธของไฟล์ XML สำหรับคำขอนี้'); 
                return; 
            }
            showOpProgress('📥 กำลังดาวน์โหลด Data XML', `กำลังดึงไฟล์ XML สำหรับ Request #${requestId || ''} จาก Server...`);
            try {
                const res = await fetch('/api/sftp/download_xml', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ 
                        remote_path: remotePath, 
                        request_id: requestId,
                        program_name: programName 
                    })
                });
                let data;
                try {
                    data = await res.json();
                } catch(pe) {
                    const text = await res.text();
                    showOpError('เกิดข้อผิดพลาดจากเซิร์ฟเวอร์', text || pe.message);
                    return;
                }
                
                if (data.error === "NOT_CONFIGURED") {
                    closeOpStatusModal();
                    showSftpAuthPrompt(data.host || '', () => downloadOutputXml(remotePath, requestId, programName));
                    return;
                }
                
                if (data.success) {
                    try {
                        const browserUrl = '/api/sftp/view_output?remote_path=' + encodeURIComponent(remotePath) + '&filename=' + encodeURIComponent(data.filename) + '&download=1';
                        const link = document.createElement('a');
                        link.href = browserUrl;
                        link.download = data.filename;
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                    } catch(dErr) {
                        console.log('Browser download notice:', dErr);
                    }

                    showOpSuccess('ดาวน์โหลด Data XML สำเร็จ!', 'ดาวน์โหลดไฟล์ XML ลงเครื่อง และสำรองข้อมูลลง Git เรียบร้อยแล้ว', {
                        '📄 Request ID': requestId || '-',
                        '📄 ชื่อไฟล์ (File)': data.filename,
                        '📁 บันทึกไว้ที่ (Local)': data.local_path,
                        '🌐 ไซต์ (Site)': data.site || 'Current DB',
                        '🛡️ Git Backup': '✅ D:\\WORK\\EBS_Git_Repo'
                    }, data.local_path);
                } else {
                    showOpError('ดาวน์โหลด Data XML ไม่สำเร็จ', data.error);
                }
            } catch (e) { 
                showOpError('เกิดข้อผิดพลาดในการดาวน์โหลด', e.message || String(e)); 
            }
        }

        async function downloadOracleReport(sourceFilePath, programName, basePath) {
            showOpProgress('📥 กำลังดาวน์โหลด Oracle Report', `กำลังค้นหาและดาวน์โหลดไฟล์ ${programName || ''}.rdf จาก Server ผ่าน SFTP...`);
            try {
                const res = await fetch('/api/sftp/download_oracle_report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        source_file_path: sourceFilePath,
                        program_name: programName,
                        base_path: basePath
                    })
                });
                const data = await res.json();

                if (data.success) {
                    showOpSuccess('ดาวน์โหลด Oracle Report สำเร็จ!', 'ระบบดาวน์โหลดไฟล์ RDF และสำรองข้อมูลลง Git เรียบร้อยแล้ว', {
                        '📄 ชื่อไฟล์ (File)': data.filename,
                        '📁 บันทึกไว้ที่ (Local)': data.path,
                        '🌐 ไซต์ (Site)': data.site || 'Current DB',
                        '🛡️ Git Backup': '✅ D:\\WORK\\EBS_Git_Repo'
                    }, data.path);
                } else if (data.error === 'NOT_CONFIGURED') {
                    closeOpStatusModal();
                    showSftpAuthPrompt(data.host || '', () => {
                        downloadOracleReport(sourceFilePath, programName, basePath);
                    });
                } else {
                    showOpError('ดาวน์โหลด Oracle Report ไม่สำเร็จ', data.error);
                }
            } catch (err) {
                showOpError('เกิดข้อผิดพลาดในการเชื่อมต่อ', err.message || String(err));
            }
        }

        async function openMTPuTTY(basePath, programName) {
            try {
                const res = await fetch('/api/open_mtputty', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({basePath: basePath, programName: programName})
                });
                const data = await res.json();
                if (!data.success) {
                    showOpError('เปิด MTPuTTY ไม่สำเร็จ', data.error);
                } else {
                    showToast('🚀 เปิด MTPuTTY สำเร็จแล้ว (คัดลอกคำสั่งลงคลิปบอร์ดแล้ว)', 3000, 'success');
                }
            } catch (err) {
                showOpError('เกิดข้อผิดพลาดในการเปิด MTPuTTY', err.message || String(err));
            }
        }

        async function downloadTemplate(templateCode, concurrentName) {
            showOpProgress('📥 กำลังดาวน์โหลด RTF Template', `กำลังดึง Template ${templateCode} จากฐานข้อมูล XDO_LOBS...`);
            try {
                const res = await fetch('/api/download_template', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({template_code: templateCode})
                });
                const data = await res.json();
                
                if (data.success) {
                    showOpSuccess('ดาวน์โหลด RTF Template สำเร็จ!', 'ระบบดึงไฟล์ Template จากฐานข้อมูล และสำรองลง Git เรียบร้อยแล้ว', {
                        '📄 Template Code': data.template_code || templateCode,
                        '📁 บันทึกไว้ที่ (Local)': data.path,
                        '🌐 ไซต์ (Site)': data.site || 'Current DB',
                        '🛡️ Git Backup': '✅ D:\\WORK\\EBS_Git_Repo'
                    }, data.path);
                } else {
                    showOpError('ดาวน์โหลด RTF Template ไม่สำเร็จ', data.error);
                }
            } catch (err) {
                showOpError('เกิดข้อผิดพลาดในการดาวน์โหลด', err.message || String(err));
            }
        }

        window.currentLogReqId = '';
        window.currentLogPath = '';
        window.currentLogFileName = '';
        window.rawLogContent = '';

        async function viewRequestLog(reqId, logPath, logFileName) {
            const modal = document.getElementById('logModal');
            const meta = document.getElementById('modalMeta');
            const content = document.getElementById('modalLogContent');
            const filterInput = document.getElementById('logFilterInput');
            const filterStats = document.getElementById('logFilterStats');

            window.currentLogReqId = reqId || '';
            window.currentLogPath = logPath || '';
            window.currentLogFileName = logFileName || ('l' + reqId + '.req');
            window.rawLogContent = '';

            if (filterInput) filterInput.value = '';
            if (filterStats) filterStats.textContent = '';

            document.getElementById('modalTitle').innerHTML = `📑 Request Log Inspector <span style="color:var(--accent-teal); font-weight:normal; font-size:0.85rem;">(Request #${reqId})</span>`;
            meta.innerHTML = '<span class="loading-spinner" style="display:inline-block; vertical-align:middle; width:16px; height:16px; border-width:2px; margin-right:6px;"></span> กำลังดาวน์โหลด Log จาก Application Server (SFTP)...';
            content.value = '';
            modal.style.display = 'flex';

            try {
                const res = await fetch(`/api/concurrent/log?request_id=${encodeURIComponent(reqId)}&remote_path=${encodeURIComponent(logPath || '')}`);
                const data = await res.json();

                if (data.success) {
                    window.currentLogPath = data.logfile_name || window.currentLogPath;
                    window.rawLogContent = data.log_content || '';

                    const sizeKb = data.file_size ? ` (${(data.file_size / 1024).toFixed(1)} KB)` : '';
                    meta.innerHTML = `
                        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                            <div>
                                <b>Request ID:</b> <span style="color:var(--accent-teal); font-weight:700;">${data.request_id}</span> &nbsp;|&nbsp;
                                <b>Phase:</b> <span style="color:#38bdf8;">${data.phase_code || '-'}</span> &nbsp;|&nbsp;
                                <b>Status:</b> <span style="color:#4ade80;">${data.status_code || '-'}</span>
                                ${data.app_host ? ` &nbsp;|&nbsp; <b>Server:</b> <code style="color:#fcd34d;">${data.app_host}</code>` : ''}
                            </div>
                        </div>
                        <div style="margin-top:6px; font-size:0.77rem; color:#94a3b8; line-height:1.4;">
                            <b>Log File:</b> <code style="color:#a7f3d0;">${data.logfile_name}</code>${sizeKb}
                            ${data.outfile_name && data.outfile_name !== 'N/A' ? `<br><b>Output File:</b> <code>${data.outfile_name}</code> (${data.output_file_type || ''})` : ''}
                            ${data.argument_text ? `<br><b>Arguments:</b> <code>${data.argument_text}</code>` : ''}
                        </div>
                    `;
                    content.value = data.log_content || '(Log file is empty)';
                } else {
                    meta.innerHTML = `<span style="color:var(--danger); font-weight:600;">❌ ข้อผิดพลาด: ${data.error}</span>`;
                    content.value = data.error || 'Could not load log file from server.';
                }
            } catch (err) {
                meta.innerHTML = `<span style="color:var(--danger); font-weight:600;">❌ Network Error: ${err.message}</span>`;
                content.value = String(err);
            }
        }

        function openLogNewTab() {
            if (!window.currentLogPath) {
                showToast('ไม่พบที่อยู่ของไฟล์ Log', 2000, 'warning');
                return;
            }
            const url = '/api/sftp/view_output?remote_path=' + encodeURIComponent(window.currentLogPath) + '&filename=' + encodeURIComponent(window.currentLogFileName || 'request.log');
            window.open(url, '_blank');
        }

        function copyLogToClipboard() {
            const content = document.getElementById('modalLogContent').value;
            if (!content) return;
            navigator.clipboard.writeText(content).then(() => {
                showToast('📋 คัดลอก Log เรียบร้อยแล้ว', 2000, 'success');
            }).catch(e => {
                showToast('ไม่สามารถคัดลอกได้: ' + e.message, 2000, 'error');
            });
        }

        function downloadLogFile() {
            if (!window.currentLogPath) return;
            const url = '/api/sftp/view_output?remote_path=' + encodeURIComponent(window.currentLogPath) + '&filename=' + encodeURIComponent(window.currentLogFileName || 'request.log') + '&download=1';
            const a = document.createElement('a');
            a.href = url;
            a.download = window.currentLogFileName || 'request.log';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }

        function filterLogContent() {
            const query = (document.getElementById('logFilterInput').value || '').trim().toLowerCase();
            const contentEl = document.getElementById('modalLogContent');
            const statsEl = document.getElementById('logFilterStats');
            const fullText = window.rawLogContent || '';

            if (!query) {
                contentEl.value = fullText;
                statsEl.textContent = '';
                return;
            }

            const lines = fullText.split('\n');
            const matchedLines = lines.filter(line => line.toLowerCase().includes(query));
            statsEl.textContent = `พบ ${matchedLines.length} บรรทัด`;
            contentEl.value = matchedLines.join('\n');
        }

        function closeLogModal() {
            document.getElementById('logModal').style.display = 'none';
        }

        // --- FNDLOAD Script Generator Functions ---
        let cachedProfilesList = [];

        function getActiveSessionKey() {
            return document.getElementById('dbSessionSelect')?.value || '';
        }

        async function loadFndProfilesDropdown() {
            try {
                const res = await fetch('/api/profiles');
                const data = await res.json();
                if (data.success && data.profiles) {
                    cachedProfilesList = Object.keys(data.profiles);
                }
            } catch(e) {}
            
            const cur = getActiveSessionKey();
            if (cur && !cachedProfilesList.includes(cur)) {
                cachedProfilesList.unshift(cur);
            }
        }

        
        let pendingFndFiles = null;

        function toggleFndDropdown(e) {
            if (e) {
                e.stopPropagation();
                e.preventDefault();
            }
            const content = document.getElementById('fndDropdownContent');
            if (content) {
                const currentDisplay = window.getComputedStyle(content).display;
                content.style.display = (currentDisplay === 'none') ? 'block' : 'none';
            }
        }

        document.addEventListener('click', function(e) {
            const content = document.getElementById('fndDropdownContent');
            if (content && content.style.display === 'block') {
                if (!e.target.closest('.dropdown')) {
                    content.style.display = 'none';
                }
            }
        });

        function triggerFndFolderUpload() {
            let input = document.getElementById('fndFolderUploadInput');
            if (!input) {
                input = document.createElement('input');
                input.type = 'file';
                input.id = 'fndFolderUploadInput';
                input.webkitdirectory = true;
                input.directory = true;
                input.style.display = 'none';
                input.onchange = handleFndFolderSelection;
                document.body.appendChild(input);
            }
            input.value = ''; // Reset
            input.click();
        }

        async function handleFndFolderSelection(event) {
            const files = event.target.files;
            if (!files || files.length === 0) return;

            pendingFndFiles = files;
            let folderName = files[0].webkitRelativePath ? files[0].webkitRelativePath.split('/')[0] : 'Selected_Folder';
            document.getElementById('fndUploadFolderName').value = folderName;

            const targetSelect = document.getElementById('fndUploadTargetSite');
            targetSelect.innerHTML = '';
            
            try {
                const res = await fetch('/api/get_saved_profiles');
                const data = await res.json();
                if (data.success && data.profiles) {
                    for (const [key, profile] of Object.entries(data.profiles)) {
                        const opt = document.createElement('option');
                        opt.value = key;
                        opt.textContent = `${key}`;
                        targetSelect.appendChild(opt);
                    }
                }
            } catch (err) {}

            document.getElementById('fndUploadModal').style.display = 'flex';
        }

        function closeFndUploadModal() {
            document.getElementById('fndUploadModal').style.display = 'none';
            pendingFndFiles = null;
        }

        async function submitFndUpload() {
            if (!pendingFndFiles || pendingFndFiles.length === 0) return;
            const targetSite = document.getElementById('fndUploadTargetSite').value;
            if (!targetSite) {
                alert('โปรดเลือก Target DB');
                return;
            }

            closeFndUploadModal();
            
            const formData = new FormData();
            formData.append('target_profile', targetSite);
            for (let i = 0; i < pendingFndFiles.length; i++) {
                formData.append('files', pendingFndFiles[i], pendingFndFiles[i].name);
            }

            let folderName = pendingFndFiles[0].webkitRelativePath ? pendingFndFiles[0].webkitRelativePath.split('/')[0] : 'Folder';
            showOpProgress('📤 อัปโหลด FNDLOAD', `กำลังส่งไฟล์และอัปโหลดเข้า ${targetSite}... (โปรดรอสักครู่)`);

            try {
                const res = await fetch('/api/fndload/upload_folder', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (data.success) {
                    showOpSuccess('อัปโหลด FNDLOAD สำเร็จ!', 'ข้อมูลทั้งหมดถูกนำเข้า Server เรียบร้อย', {
                        'Target Site': targetSite,
                        'Folder': folderName,
                        'Log Output': 'แสดงใน Log Viewer'
                    });
                    if (data.log) {
                        setTimeout(() => {
                            window.rawLogContent = data.log.join('\n');
                            window.currentLogFileName = `upload_${folderName}.log`;
                            document.getElementById('logViewerContent').textContent = window.rawLogContent;
                            document.getElementById('logViewerModal').style.display = 'flex';
                        }, 500);
                    }
                } else if (data.error === 'NOT_CONFIGURED') {
                    closeOpStatusModal();
                    showSftpAuthPrompt(data.host || '', () => {
                        submitFndUpload();
                    });
                } else {
                    showOpError('อัปโหลด FNDLOAD ไม่สำเร็จ', data.error);
                }
            } catch (err) {
                showOpError('เกิดข้อผิดพลาดในการเชื่อมต่อ', err.message || String(err));
            }
        }

        async function openFndLoadModal(progShort, progName, appShort) {
            await loadFndProfilesDropdown();
            
            const modal = document.getElementById('fndLoadModal');
            const inputShort = document.getElementById('fndProgShort');
            const inputName = document.getElementById('fndProgName');
            const inputApp = document.getElementById('fndAppShort');
            const inputDs = document.getElementById('fndDsCode');
            const inputTmpl = document.getElementById('fndTmplCode');
            const srcSelect = document.getElementById('fndSrcDb');
            const tgtSelect = document.getElementById('fndTgtDb');
            const statusMsg = document.getElementById('fndStatusMsg');
            const btnSubmit = document.getElementById('btnSubmitFnd');
            
            statusMsg.style.display = 'none';
            statusMsg.innerHTML = '';
            btnSubmit.disabled = false;
            btnSubmit.textContent = '🚀 สร้างไฟล์ FNDLOAD';
            
            const pShort = progShort || (document.getElementById('concSearchInput') ? document.getElementById('concSearchInput').value.trim() : '') || '';
            inputShort.value = pShort;
            inputName.value = progName || pShort;
            inputApp.value = appShort || 'XXCUST';
            inputDs.value = pShort;
            inputTmpl.value = pShort ? (pShort + '%') : '';

            let optionsHtml = '';
            cachedProfilesList.forEach(k => {
                optionsHtml += `<option value="${escapeHtml(k)}">${escapeHtml(k)}</option>`;
            });
            
            srcSelect.innerHTML = optionsHtml;
            tgtSelect.innerHTML = optionsHtml;
            
            const activeKey = getActiveSessionKey();
            if (activeKey) {
                srcSelect.value = activeKey;
                const otherTgt = cachedProfilesList.find(k => k !== activeKey);
                if (otherTgt) tgtSelect.value = otherTgt;
            }
            
            modal.style.display = 'flex';
        }

        function closeFndLoadModal() {
            const modal = document.getElementById('fndLoadModal');
            if (modal) modal.style.display = 'none';
        }

        function onFndProgShortChanged() {
            const val = document.getElementById('fndProgShort').value.trim();
            document.getElementById('fndDsCode').value = val;
            document.getElementById('fndTmplCode').value = val ? (val + '%') : '';
        }

        async function submitFndLoadGenerate() {
            const prog = document.getElementById('fndProgShort').value.trim();
            const appShort = document.getElementById('fndAppShort').value.trim();
            const dsCode = document.getElementById('fndDsCode').value.trim();
            const tmplCode = document.getElementById('fndTmplCode').value.trim();
            const srcProfile = document.getElementById('fndSrcDb').value;
            const tgtProfile = document.getElementById('fndTgtDb').value;
            const outDir = document.getElementById('fndOutDir').value.trim();
            const statusMsg = document.getElementById('fndStatusMsg');
            const btnSubmit = document.getElementById('btnSubmitFnd');

            if (!prog) {
                alert('กรุณากรอก Concurrent Program Short Name');
                return;
            }
            if (!tgtProfile) {
                alert('กรุณาเลือก Target DB Site ที่ต้องการ Clone ไป');
                return;
            }

            btnSubmit.disabled = true;
            btnSubmit.textContent = '⏳ กำลังสร้างไฟล์...';

            try {
                const res = await fetch('/api/fndload/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        program_name: prog,
                        app_short_name: appShort,
                        ds_code: dsCode,
                        tmpl_code: tmplCode,
                        src_profile: srcProfile,
                        tgt_profile: tgtProfile,
                        output_dir: outDir
                    })
                });
                const data = await res.json();

                statusMsg.style.display = 'block';
                if (data.success) {
                    // Store folder path globally — avoid backslash issue in inline onclick
                    window._lastFndFolder = data.folder;
                    window._lastFndParams = {
                        program_name: prog, app_short_name: appShort,
                        ds_code: dsCode, tmpl_code: tmplCode,
                        src_profile: srcProfile, output_dir: outDir
                    };
                    statusMsg.innerHTML = `
                        <div style="background:rgba(63, 185, 80, 0.1); border:1px solid rgba(63, 185, 80, 0.3); color:#3fb950; padding:12px; border-radius:6px;">
                            <div style="font-weight:600; font-size:0.88rem; margin-bottom:4px; color:#3fb950;">สร้างไฟล์ Script สำหรับ ${escapeHtml(data.program)} สำเร็จ!</div>
                            <div style="font-size:0.78rem; color:var(--text-muted); margin-bottom:6px;">Folder: <span style="color:var(--text-main);">${escapeHtml(data.folder)}</span></div>
                            <div style="font-size:0.76rem; color:var(--text-muted); margin-bottom:10px;">
                                • <code>config.cfg</code> (SRC: ${escapeHtml(data.src_jdbc||'-')} ➔ TGT: ${escapeHtml(data.tgt_jdbc||'-')})<br>
                                • <code>download.sh</code> / <code>upload.sh</code>
                            </div>
                            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                                <button class="btn btn-primary" style="font-size:0.78rem; padding:4px 12px; font-weight:500;" onclick="runFndDownload()">Gen &amp; ดึงไฟล์จาก Server</button>
                                <button class="btn btn-secondary" style="font-size:0.78rem; padding:4px 12px;" onclick="downloadFndZip()">Download ZIP</button>
                                <button class="btn btn-secondary" style="font-size:0.78rem; padding:4px 12px;" onclick="openGeneratedFolder()">เปิด Folder</button>
                                <button class="btn btn-secondary" style="font-size:0.78rem; padding:4px 10px;" onclick="closeFndLoadModal()">ปิด</button>
                            </div>
                        </div>
                    `;
                    btnSubmit.textContent = 'สร้างเรียบร้อย';
                    showToast(`สร้างไฟล์ FNDLOAD สำหรับ ${prog} สำเร็จ!`, 3000, 'success');
                } else {
                    statusMsg.innerHTML = `
                        <div style="background:rgba(248, 81, 73, 0.1); border:1px solid rgba(248, 81, 73, 0.3); color:#f85149; padding:10px; border-radius:6px;">
                            เกิดข้อผิดพลาด: ${escapeHtml(data.error || 'Unknown error')}
                        </div>
                    `;
                    btnSubmit.disabled = false;
                    btnSubmit.textContent = 'ลองใหม่';
                }
            } catch(err) {
                statusMsg.style.display = 'block';
                statusMsg.innerHTML = `<div style="background:#7f1d1d; border:1px solid #dc2626; color:#fecaca; padding:10px; border-radius:6px;">❌ Network Error: ${escapeHtml(err.message)}</div>`;
                btnSubmit.disabled = false;
                btnSubmit.textContent = '🚀 สร้างไฟล์ FNDLOAD';
            }
        }

        function openGeneratedFolder(targetPath) {
            const path = targetPath || window._lastFndFolder;
            if (!path) { showToast('ไม่พบ path', 3000, 'error'); return; }
            fetch('/api/system/open_folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path })
            }).then(r => r.json()).then(d => {
                if (d.success) showToast('✅ เปิด Folder แล้ว', 2000, 'success');
                else showToast('เปิด Folder ไม่ได้: ' + (d.error || ''), 4000, 'error');
            }).catch(e => showToast('Error: ' + e.message, 3000, 'error'));
        }

        async function downloadFndZip(folderPath) {
            const fp = folderPath || window._lastFndFolder;
            if (!fp) { showToast('ไม่พบ folder path', 3000, 'error'); return; }
            try {
                showToast('⏳ กำลังบีบอัดไฟล์...', 2000);
                const res = await fetch('/api/fndload/download_zip', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ folder: fp })
                });
                if (!res.ok) {
                    const errText = await res.text();
                    showToast('Download ไม่สำเร็จ: ' + errText.substring(0,80), 4000, 'error');
                    return;
                }
                const blob = await res.blob();
                const name = fp.replace(/\\/g,'/').split('/').filter(Boolean).pop() || 'fndload';
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = name + '.zip';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(a.href);
                showToast('✅ Download ZIP สำเร็จ!', 3000, 'success');
            } catch(e) {
                showToast('Download Error: ' + e.message, 4000, 'error');
            }
        }

        async function runFndDownload() {
            const p = window._lastFndParams;
            if (!p) { showToast('กรุณา Gen Script ก่อน', 3000, 'error'); return; }
            const statusMsg = document.getElementById('fndStatusMsg');
            const btnSubmit = document.getElementById('btnSubmitFnd');
            statusMsg.style.display = 'block';
            statusMsg.innerHTML = `
                <div style="background:#1e3a5f; border:1px solid #3b82f6; color:#93c5fd; padding:12px; border-radius:6px;">
                    <div style="font-weight:700; margin-bottom:6px;">⏳ กำลัง SSH เข้า Server และรัน FNDLOAD...</div>
                    <div style="font-size:0.78rem; color:#64748b;">อาจใช้เวลา 1-3 นาที กรุณาอย่าปิดหน้าต่างนี้</div>
                </div>`;
            btnSubmit.disabled = true;

            try {
                const res = await fetch('/api/fndload/run_download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(p)
                });
                const data = await res.json();
                const logHtml = (data.log || []).slice(-30).map(l =>
                    `<div style="font-family:monospace;font-size:0.72rem;color:${l.startsWith('[ERR]')||l.startsWith('[FATAL]')?'#fca5a5':l.startsWith('  ✓')?'#6ee7b7':'#94a3b8'}">${escapeHtml(l)}</div>`
                ).join('');

                if (data.success && data.files && data.files.length > 0) {
                    window._lastFndFolder = data.folder;
                    statusMsg.innerHTML = `
                        <div style="background:rgba(63, 185, 80, 0.1); border:1px solid rgba(63, 185, 80, 0.3); color:#3fb950; padding:12px; border-radius:6px;">
                            <div style="font-weight:600; margin-bottom:6px; color:#3fb950;">ดึงไฟล์จาก Server สำเร็จ! (${data.files.length} ไฟล์)</div>
                            <div style="font-size:0.76rem; margin-bottom:8px; color:var(--text-muted);">${data.files.map(f=>`• <code>${escapeHtml(f)}</code>`).join('<br>')}</div>
                            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px;">
                                <button class="btn btn-secondary" style="font-size:0.78rem; padding:4px 12px;" onclick="downloadFndZip()">Download ZIP</button>
                                <button class="btn btn-secondary" style="font-size:0.78rem; padding:4px 12px;" onclick="openGeneratedFolder()">เปิด Folder</button>
                            </div>
                            <details style="font-size:0.72rem;"><summary style="cursor:pointer;color:var(--text-muted);">ดู Log</summary>
                                <div style="margin-top:6px; max-height:200px; overflow-y:auto; background:var(--bg-dark); padding:8px; border-radius:4px; border:1px solid var(--panel-border);">${logHtml}</div>
                            </details>
                        </div>`;
                    showToast(`ดึงไฟล์ ${data.files.length} ไฟล์จาก Server สำเร็จ!`, 4000, 'success');
                } else if (data.error === 'NOT_CONFIGURED') {
                    statusMsg.innerHTML = `
                        <div style="background:rgba(210, 153, 34, 0.1); border:1px solid rgba(210, 153, 34, 0.3); color:#d29922; padding:12px; border-radius:6px;">
                            <div style="font-weight:600; margin-bottom:4px;">ยังไม่ได้ตั้งค่า SFTP Credentials</div>
                            <div style="font-size:0.8rem; color:var(--text-muted);">กรุณาเชื่อมต่อ SFTP ผ่าน <b>แถบ SFTP Explorer</b> ก่อน (Server: ${escapeHtml(data.host||'')})</div>
                        </div>`;
                } else {
                    statusMsg.innerHTML = `
                        <div style="background:rgba(248, 81, 73, 0.1); border:1px solid rgba(248, 81, 73, 0.3); color:#f85149; padding:12px; border-radius:6px;">
                            <div style="font-weight:600; margin-bottom:4px;">${escapeHtml(data.error||'ไม่สำเร็จ')}</div>
                            ${data.files && data.files.length > 0 ? `<div style="font-size:0.76rem; color:var(--warning); margin-bottom:6px;">ได้ไฟล์บางส่วน: ${data.files.map(f=>`<code>${escapeHtml(f)}</code>`).join(', ')}</div>` : ''}
                            <details style="font-size:0.72rem;"><summary style="cursor:pointer; color:var(--text-muted);">ดู Log</summary>
                                <div style="margin-top:6px; max-height:150px; overflow-y:auto; background:var(--bg-dark); padding:8px; border-radius:4px; border:1px solid var(--panel-border);">${logHtml}</div>
                            </details>
                        </div>`;
                }
            } catch(e) {
                statusMsg.innerHTML = `<div style="background:rgba(248, 81, 73, 0.1); border:1px solid rgba(248, 81, 73, 0.3); color:#f85149; padding:10px; border-radius:6px;">Network Error: ${escapeHtml(e.message)}</div>`;
            } finally {
                btnSubmit.disabled = false;
                btnSubmit.textContent = 'สร้างไฟล์ FNDLOAD';
            }
        }

        function openExportModal() {
            document.getElementById('exportTableNameInput').value = currentTableName || 'EXPORT_TABLE';
            document.getElementById('exportModal').style.display = 'flex';
        }

        function closeExportModal() {
            document.getElementById('exportModal').style.display = 'none';
        }

        function makeCellEditable(cell) {
            if (cell.querySelector('textarea')) return;
            const val = cell.textContent;
            if (val === 'null' && cell.querySelector('i')) return;
            cell.innerHTML = `<textarea style="width:100%; min-height:40px; background:var(--bg-deeper); color:var(--text-main); border:1px solid var(--accent-teal); padding:4px; font-family:inherit; font-size:inherit; resize:vertical; outline:none; box-sizing:border-box;" onblur="this.parentNode.innerHTML = escapeHtml(this.value)">${val}</textarea>`;
            const ta = cell.querySelector('textarea');
            ta.focus();
            ta.select();
        }

        async function executeExport() {
            const format = document.getElementById('exportFormatSelect').value;
            const customTableName = document.getElementById('exportTableNameInput').value.trim() || 'EXPORT_TABLE';
            const exportRowLimit = document.getElementById('exportRowLimitSelect').value;

            if (exportRowLimit === 'CURRENT') {
                if (!currentColumns || currentColumns.length === 0) {
                    alert('ไม่มีข้อมูลสำหรับส่งออก');
                    return;
                }
                doExportWithData(format, customTableName, currentColumns, currentRows);
                closeExportModal();
                return;
            }

            if (!lastExecutedSql) {
                alert('ไม่มีข้อมูลคำสั่ง SQL ล่าสุด');
                return;
            }
            
            // Step 1: Ask user where to save via PowerShell native dialog
            let defaultName = `export_${Date.now()}`;
            if (format === 'CSV') defaultName += '.csv';
            else if (format === 'TSV' || format === 'TXT') defaultName += '.txt';
            else if (format === 'SQL' || format === 'SQL_INSERT') defaultName += '.sql';
            else if (format === 'EXCEL') defaultName += '.xlsx';
            else if (format === 'JSON') defaultName += '.json';

            closeExportModal();

            // Show "choosing file" message
            let existModal = document.getElementById('exportProgressModal');
            if (existModal) existModal.remove();
            document.body.insertAdjacentHTML('beforeend', `
                <div class="modal-backdrop" id="exportProgressModal" style="display:flex; z-index:9999;">
                    <div class="modal-content" style="width: 420px; text-align:center; padding:30px;">
                        <h3 style="margin-top:0; color:var(--accent-teal);">📁 เลือกที่บันทึกไฟล์...</h3>
                        <div id="exportProgressText" style="color:var(--text-muted); font-size:0.9rem; margin-top:10px;">หน้าต่าง Save As กำลังเปิดขึ้น...</div>
                    </div>
                </div>
            `);

            let savePath;
            try {
                const pathRes = await fetch('/api/choose_save_path', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({filename: defaultName})
                });
                const pathData = await pathRes.json();
                if (!pathData.success) {
                    const m = document.getElementById('exportProgressModal');
                    if (m) m.remove();
                    return;
                }
                savePath = pathData.path;
            } catch(e) {
                const m = document.getElementById('exportProgressModal');
                if (m) m.remove();
                alert('Error: ' + e.message);
                return;
            }

            // Step 2: Update UI to show exporting
            const modalContent = document.querySelector('#exportProgressModal .modal-content');
            if (modalContent) {
                modalContent.innerHTML = `
                    <span class="loading-spinner" style="width:28px; height:28px; margin-bottom:15px; border-width:3px;"></span>
                    <h3 style="margin-top:0; color:var(--accent-teal);">Exporting Data...</h3>
                    <div id="exportProgressText" style="color:var(--text-main); font-size:1.05rem; margin-top:10px;">กำลังเขียนไฟล์ กรุณารอ...</div>
                    <div style="margin-top:8px; font-size:0.78rem; color:var(--text-muted); word-break:break-all;">→ ${savePath}</div>
                `;
            }

            // Step 3: Export DIRECTLY via Python (bypass Flask)
            try {
                const resultStr = await window.pywebview.api.export_to_file(
                    lastExecutedSql,
                    JSON.stringify(lastExecutedBindVars),
                    format,
                    savePath,
                    String(exportRowLimit),
                    customTableName
                );
                const result = JSON.parse(resultStr);
                const p = document.getElementById('exportProgressText');
                if (result.success) {
                    if (p) p.innerHTML = `✅ เสร็จสิ้น! ส่งออก ${result.rows.toLocaleString()} แถว`;
                    setTimeout(() => {
                        const m = document.getElementById('exportProgressModal');
                        if (m) m.remove();
                    }, 2500);
                } else {
                    if (p) p.innerHTML = `❌ เกิดข้อผิดพลาด: ${result.error}`;
                }
            } catch(err) {
                const p = document.getElementById('exportProgressText');
                if (p) p.innerHTML = `❌ Error: ${err.message}`;
            }
        }

        function checkExportProgress(taskId) {
            fetch('/api/export_progress?task_id=' + taskId)
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        const progEl = document.getElementById('exportProgressText');
                        if (progEl) {
                            if (data.status === 'processing') {
                                progEl.innerHTML = `กำลังประมวลผล: <b>${data.rows.toLocaleString()}</b> แถว...`;
                            } else if (data.status === 'completed') {
                                progEl.innerHTML = `✅ เสร็จสิ้น! (รวม ${data.rows.toLocaleString()} แถว) ไฟล์กำลังถูกดาวน์โหลด...`;
                                clearInterval(window.exportProgressInterval);
                                setTimeout(() => {
                                    const m = document.getElementById('exportProgressModal');
                                    if(m) m.remove();
                                }, 3000);
                            } else if (data.status === 'error') {
                                progEl.innerHTML = `❌ เกิดข้อผิดพลาด: ${data.error}`;
                                clearInterval(window.exportProgressInterval);
                            }
                        }
                    }
                })
                .catch(e => console.error(e));
        }

        function doExportWithData(format, customTableName, columns, rows) {
            const oldCols = currentColumns;
            const oldRows = currentRows;
            const oldName = currentTableName;
            
            currentColumns = columns;
            currentRows = rows;
            currentTableName = customTableName;

            if (format === 'CSV') exportCSV();
            else if (format === 'JSON') exportJSON();
            else if (format === 'EXCEL') exportExcelHTML();
            else if (format === 'SQL_INSERT') exportSQLInsert(customTableName);
            else if (format === 'TXT') exportTabDelimitedText();

            currentColumns = oldCols;
            currentRows = oldRows;
            currentTableName = oldName;
        }

        function exportSQLInsert(tableName) {
            let sqlContent = `-- SQL INSERT Statements generated by JNavigator\n-- Table: ${tableName}\n\n`;
            currentRows.forEach(row => {
                const vals = row.map(val => {
                    if (val === null || val === undefined) return 'NULL';
                    if (typeof val === 'number') return val;
                    return `'${String(val).replace(/'/g, "''")}'`;
                }).join(", ");
                sqlContent += `INSERT INTO ${tableName} (${currentColumns.join(", ")}) VALUES (${vals});\n`;
            });
            downloadBlob(sqlContent, `${tableName}_inserts_${new Date().toISOString().slice(0,10)}.sql`, 'text/plain;charset=utf-8;');
        }

        function exportExcelHTML() {
            let html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="utf-8"/></head><body><table border="1"><thead><tr>`;
            currentColumns.forEach(c => html += `<th style="background-color:#1e293b; color:#fff;">${c}</th>`);
            html += '</tr></thead><tbody>';

            currentRows.forEach(row => {
                html += '<tr>';
                row.forEach(val => {
                    html += `<td>${val === null ? '' : escapeHtml(val)}</td>`;
                });
                html += '</tr>';
            });
            html += '</tbody></table></body></html>';

            downloadBlob(html, `${currentTableName || 'export'}_${new Date().toISOString().slice(0,10)}.xls`, 'application/vnd.ms-excel;charset=utf-8;');
        }

        function exportTabDelimitedText() {
            let txt = currentColumns.join("\t") + "\n";
            currentRows.forEach(row => {
                txt += row.map(val => val === null ? '' : String(val).replace(/\t/g, ' ')).join("\t") + "\n";
            });
            downloadBlob(txt, `${currentTableName || 'export'}_${new Date().toISOString().slice(0,10)}.txt`, 'text/plain;charset=utf-8;');
        }

        async function downloadBlob(content, filename, contentType) {
            let existModal = document.getElementById('exportProgressModal');
            if (existModal) existModal.remove();
            document.body.insertAdjacentHTML('beforeend', `
                <div class="modal-backdrop" id="exportProgressModal" style="display:flex; z-index:9999;">
                    <div class="modal-content" style="width: 420px; text-align:center; padding:30px;">
                        <h3 style="margin-top:0; color:var(--accent-teal);">📁 กำลังเตรียมบันทึก...</h3>
                        <div id="exportProgressText" style="color:var(--text-muted); font-size:0.9rem; margin-top:10px;">หน้าต่าง Save As กำลังเปิดขึ้น...</div>
                    </div>
                </div>
            `);

            try {
                const res = await fetch('/api/save_backup', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({content: content, filename: filename})
                });
                const data = await res.json();
                
                const m = document.getElementById('exportProgressModal');
                if (m) m.remove();

                if (data.success) {
                    alert('✅ บันทึกสำเร็จ!\nSaved to: ' + data.path);
                } else if (data.error !== 'Cancelled') {
                    alert('❌ Error: ' + data.error);
                }
            } catch (err) {
                const m = document.getElementById('exportProgressModal');
                if (m) m.remove();
                alert('Backup failed: ' + err.message);
            }
        }

        async function runQuery() {
            let sql = sqlMonacoEditor ? sqlMonacoEditor.getValue().trim() : '';
            if (!sql) return;

            sql = sql.replace(/\u29FD/g, "<=").replace(/\u29FE/g, ">=").replace(/\u2260/g, "<>");

            // Detect bind variables (:VARNAME) in SQL
            const bindPattern = /:[A-Za-z_][A-Za-z0-9_]*/g;
            const strippedSql = sql.replace(/'[^']*'/g, "''"); // ignore inside strings
            const bindMatches = [...new Set(strippedSql.match(bindPattern) || [])].map(b => b.substring(1));

            if (bindMatches.length > 0) {
                // Show bind variable dialog
                showBindVarDialog(sql, bindMatches);
                return;
            }

            await executeSQL(sql, {});
        }

        function showBindVarDialog(sql, bindVars) {
            let formHtml = `
                <div style="font-size:0.85rem; color:var(--text-muted); margin-bottom:12px;">
                    ตรวจพบ Bind Variable ใน Query กรุณากรอกค่าก่อนรัน:
                </div>
            `;
            bindVars.forEach(v => {
                formHtml += `
                    <div class="form-group" style="margin-bottom:10px;">
                        <label style="color:var(--accent-teal); font-weight:700;">:${v}</label>
                        <input type="text" id="bind_${v}" placeholder="ค่าสำหรับ :${v}" style="width:100%; margin-top:4px;">
                    </div>
                `;
            });

            document.getElementById('bindVarForm').innerHTML = formHtml;
            document.getElementById('bindVarModal').style.display = 'flex';
            window._pendingBindSQL = sql;
            window._pendingBindVars = bindVars;

            // Focus first input
            setTimeout(() => {
                const first = document.querySelector('#bindVarForm input');
                if (first) first.focus();
            }, 100);
        }

        function closeBindVarDialog() {
            document.getElementById('bindVarModal').style.display = 'none';
            window._pendingBindSQL = null;
            window._pendingBindVars = null;
        }

        async function confirmBindVars() {
            const bindVars = window._pendingBindVars || [];
            const sql = window._pendingBindSQL;
            if (!sql) return;

            const bindDict = {};
            bindVars.forEach(v => {
                const val = document.getElementById('bind_' + v).value.trim();
                bindDict[v] = val;
            });
            closeBindVarDialog();
            await executeSQL(sql, bindDict);
        }

        async function executeSQL(sql, bindVars) {
            const wrapper = document.getElementById('tableWrapper');
            const stats = document.getElementById('resultStats');
            const exportBox = document.getElementById('exportActions');
            
            try {
                const rowLimitEl = document.getElementById('rowLimitSelect');
                const rowLimit = rowLimitEl ? rowLimitEl.value : '500';

                lastExecutedSql = sql;
                lastExecutedBindVars = bindVars;

                wrapper.innerHTML = '<div style="padding: 40px; text-align: center;"><span class="loading-spinner"></span> Executing Query...</div>';
                exportBox.style.display = 'none';
                
                window._currentCount = 0;
                if (rowLimit === "ALL") {
                    stats.innerHTML = `<span class="loading-spinner" style="width:12px;height:12px;border-width:2px;border-top-color:#7ab3d4;"></span> <i style="color:var(--text-muted);">Counting total rows...</i>`;
                    fetch('/api/query_count', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ sql: sql, bind_vars: bindVars })
                    }).then(r => r.json()).then(data => {
                        if (data.success) {
                            window._currentCount = data.count;
                            stats.innerHTML = `Rows returned: ${data.count.toLocaleString()} (All Rows)`;
                        }
                    }).catch(e => console.error(e));
                } else {
                    stats.innerHTML = `<span class="loading-spinner" style="width:12px;height:12px;border-width:2px;"></span> <i style="color:var(--text-muted);">Executing Query...</i>`;
                }

                const res = await fetch('/api/query', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sql: sql, bind_vars: bindVars, row_limit: rowLimit })
                });
                
                if (!res.ok) throw new Error("HTTP Error " + res.status);
                
                const reader = res.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let renderTimeout = null;
                
                vsColumns = [];
                vsRows = [];
                
                while (true) {
                    const { done, value } = await reader.read();
                    if (value) {
                        buffer += decoder.decode(value, { stream: true });
                        let lines = buffer.split('\n');
                        buffer = lines.pop(); // keep the last incomplete line
                        
                        for (let line of lines) {
                            if (!line.trim()) continue;
                            const parsed = JSON.parse(line);
                            
                            if (parsed.success === false) {
                                wrapper.innerHTML = `<div class="alert alert-error" style="margin: 20px;"><b>Database Error:</b> ${parsed.error}</div>`;
                                stats.textContent = 'Execution Error';
                                return;
                            }
                            
                            if (parsed.columns) {
                                vsColumns = parsed.columns;
                                currentColumns = parsed.columns; // for export
                                initGridHeader();
                            } else if (parsed.is_final) {
                                const limitText = rowLimit === "ALL" ? '(All Rows)' : `(Limited to ${rowLimit})`;
                                stats.textContent = `Rows returned: ${parsed.row_count} ${parsed.truncated ? limitText : ''}`;
                                if (document.getElementById('elapsedBadge')) {
                                    document.getElementById('elapsedBadge').textContent = `⏱ ${parsed.elapsed}s`;
                                }
                                exportBox.style.display = 'flex';
                                document.getElementById('gridSearchInput').value = '';
                                currentRows = vsRows; // for export
                            } else if (parsed.message) {
                                wrapper.innerHTML = `<div class="alert alert-success" style="margin: 20px;">${parsed.message}</div>`;
                                stats.textContent = `Statement Executed ⏱ ${parsed.elapsed}s`;
                            } else if (Array.isArray(parsed)) {
                                parsed.forEach(r => vsRows.push(r));
                                if (!renderTimeout) {
                                    renderTimeout = setTimeout(() => {
                                        handleGridScroll();
                                        renderTimeout = null;
                                    }, 80);
                                }
                            }
                        }
                    }
                    if (done) break;
                }
                
                if (renderTimeout) {
                    clearTimeout(renderTimeout);
                    handleGridScroll();
                }
                if (vsRows.length === 0 && vsColumns.length > 0) {
                    wrapper.innerHTML = `
                        <div class="empty-state-box">
                            <div class="empty-state-icon">📋</div>
                            <div class="empty-state-title">Query Executed Successfully</div>
                            <div class="empty-state-desc">0 rows returned matching your query.</div>
                        </div>`;
                }

            } catch (err) {
                stats.textContent = 'Execution Error';
                wrapper.innerHTML = `<div class="alert alert-error" style="margin: 20px;"><b>Client/Network Error:</b> ${err.message}<br><br><pre style="font-size:0.75rem; white-space:pre-wrap; color:#f87171;">${err.stack}</pre></div>`;
                console.error("ExecuteSQL Error:", err);
            }
        }

        let vsColumns = [];
        let vsRows = [];
        const ROW_HEIGHT = 28;

        function initGridHeader() {
            const wrapper = document.getElementById('tableWrapper');
            
            wrapper.innerHTML = `
                <table class="data-grid" id="gridTable" style="margin-bottom:0;">
                    <thead id="gridHead" style="position: sticky; top: 0; z-index: 10; box-shadow: 0 1px 0 var(--panel-border); background: var(--bg-elevated);">
                        <tr id="gridHeadRow"></tr>
                    </thead>
                    <tbody id="gridBody"></tbody>
                </table>
            `;
            let html = '<th class="row-idx" style="width: 44px; min-width: 44px; max-width: 44px; text-align: center; color: var(--text-dim);">#</th>';
            vsColumns.forEach((c, i) => {
                html += `
                <th style="vertical-align: top; min-width: 120px; position: relative;">
                    <div style="margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; user-select: none;" onclick="toggleColMenu(${i})">
                        <span>${c}</span>
                        <span style="font-size: 10px; color: var(--text-muted);">▼</span>
                    </div>
                    <div id="colMenu_${i}" class="col-menu" style="display: none; position: absolute; top: 100%; left: 0; background: var(--bg-elevated); border: 1px solid var(--panel-border); padding: 8px; z-index: 100; box-shadow: 0 4px 12px rgba(0,0,0,0.5); border-radius: 4px; min-width: 160px; font-weight: normal;">
                        <div style="padding: 6px; cursor: pointer; color: var(--text-main); border-radius: 2px;" onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background=''" onclick="sortGrid(${i}, 'ASC')">🔼 เรียงน้อยไปมาก (ASC)</div>
                        <div style="padding: 6px; cursor: pointer; color: var(--text-main); border-radius: 2px;" onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background=''" onclick="sortGrid(${i}, 'DESC')">🔽 เรียงมากไปน้อย (DESC)</div>
                        <hr style="border: 0; border-top: 1px solid var(--panel-border); margin: 6px 0;">
                        <input type="text" class="col-filter-input" data-idx="${i}" placeholder="Filter ${c}..." oninput="filterGridLocally()" style="width: 100%; box-sizing: border-box; padding: 4px 6px; font-size: 0.75rem; background: rgba(0,0,0,0.2); border: 1px solid var(--panel-border); color: var(--text-main); border-radius: 3px; font-family: inherit;">
                    </div>
                </th>`;
            });
            document.getElementById('gridHeadRow').innerHTML = html;
            wrapper.onscroll = handleGridScroll;
            handleGridScroll();
        }

        function toggleColMenu(idx) {
            const menu = document.getElementById('colMenu_' + idx);
            const isVisible = menu.style.display === 'block';
            document.querySelectorAll('.col-menu').forEach(m => m.style.display = 'none');
            if (!isVisible) menu.style.display = 'block';
        }

        document.addEventListener('click', function(e) {
            if (!e.target.closest('.col-menu') && !e.target.closest('th > div')) {
                document.querySelectorAll('.col-menu').forEach(m => m.style.display = 'none');
            }
        });

        function sortGrid(colIdx, dir) {
            document.querySelectorAll('.col-menu').forEach(m => m.style.display = 'none');
            if (!vsRows || vsRows.length === 0) return;
            
            vsRows.sort((a, b) => {
                let valA = a[colIdx];
                let valB = b[colIdx];
                if (valA === null) valA = '';
                if (valB === null) valB = '';
                
                if (typeof valA === 'number' && typeof valB === 'number') {
                    return dir === 'ASC' ? valA - valB : valB - valA;
                }
                
                valA = String(valA).toLowerCase();
                valB = String(valB).toLowerCase();
                if (valA < valB) return dir === 'ASC' ? -1 : 1;
                if (valA > valB) return dir === 'ASC' ? 1 : -1;
                return 0;
            });
            
            // Re-apply to currentRows to persist sort through filtering
            currentRows = [...vsRows];
            handleGridScroll();
        }

        function handleGridScroll() {
            const wrapper = document.getElementById('tableWrapper');
            const tbody = document.getElementById('gridBody');
            if (!tbody) return;
            
            const scrollTop = wrapper.scrollTop;
            const viewportHeight = wrapper.clientHeight;
            
            const totalRows = vsRows.length;
            let startIndex = Math.floor(scrollTop / ROW_HEIGHT) - 10; // buffer
            if (startIndex < 0) startIndex = 0;
            
            let endIndex = startIndex + Math.ceil(viewportHeight / ROW_HEIGHT) + 20; // buffer
            if (endIndex > totalRows) endIndex = totalRows;
            
            const paddingTop = startIndex * ROW_HEIGHT;
            const paddingBottom = (totalRows - endIndex) * ROW_HEIGHT;
            const totalCols = vsColumns.length + 1;
            
            let html = '';
            if (paddingTop > 0) {
                html += `<tr style="height: ${paddingTop}px;"><td colspan="${totalCols}" style="padding:0; border:none;"></td></tr>`;
            }
            
            for (let i = startIndex; i < endIndex; i++) {
                const row = vsRows[i];
                html += `<tr style="height: ${ROW_HEIGHT}px;">`;
                html += `<td class="row-idx">${i + 1}</td>`;
                row.forEach(val => {
                    let displayVal;
                    let cellStyle = 'cursor: cell;';
                    if (val === null || val === undefined) {
                        displayVal = '<span class="null-tag">NULL</span>';
                    } else {
                        if (typeof val === 'number') {
                            cellStyle += ' text-align: right; font-variant-numeric: tabular-nums;';
                        }
                        displayVal = escapeHtml(val);
                    }
                    html += `<td class="clickable" ondblclick="makeCellEditable(this)" style="${cellStyle}" title="Double-click to view or copy full text">${displayVal}</td>`;
                });
                html += '</tr>';
            }
            
            if (paddingBottom > 0) {
                html += `<tr style="height: ${paddingBottom}px;"><td colspan="${totalCols}" style="padding:0; border:none;"></td></tr>`;
            }
            
            tbody.innerHTML = html;
        }

        function filterGridLocally() {
            if (!currentColumns || !currentRows) return;
            const query = document.getElementById('gridSearchInput').value.toLowerCase();
            
            const colFilters = [];
            document.querySelectorAll('.col-filter-input').forEach(input => {
                const val = input.value.toLowerCase().trim();
                if (val) colFilters.push({ idx: parseInt(input.dataset.idx), val: val });
            });

            if (!query && colFilters.length === 0) {
                vsRows = currentRows;
            } else {
                vsRows = currentRows.filter(row => {
                    let match = true;
                    if (query) {
                        match = row.some(val => val !== null && String(val).toLowerCase().includes(query));
                    }
                    if (!match) return false;

                    for (let cf of colFilters) {
                        const cellVal = row[cf.idx];
                        if (cellVal == null || !String(cellVal).toLowerCase().includes(cf.val)) {
                            return false;
                        }
                    }
                    return true;
                });
            }

            document.getElementById('resultStats').textContent = `Filtered rows: ${vsRows.length} / ${currentRows.length}`;
            handleGridScroll(); // Update only body, keep header intact so inputs don't lose focus
        }

        let execPackage = '';
        let execProcedure = '';
        let execParams = [];

        async function openExecModal(procName, objType) {
            execPackage = activePlsqlName;
            execProcedure = procName;
            execParams = [];
            document.getElementById('execModalTitle').textContent = `⚡ Execute ${objType}: ${execPackage}.${execProcedure}`;
            document.getElementById('execArgumentInput').value = '';
            document.getElementById('execScriptOutput').value = '-- Fetching parameters from Oracle Database...';
            document.getElementById('execModal').style.display = 'flex';

            try {
                const res = await fetch(`/api/plsql/arguments?package=${encodeURIComponent(execPackage)}&procedure=${encodeURIComponent(execProcedure)}`);
                const data = await res.json();
                if (data.success) {
                    execParams = data.arguments;
                    generateExecScript();
                } else {
                    document.getElementById('execScriptOutput').value = `-- Error fetching arguments: ${data.error}`;
                }
            } catch(e) {
                document.getElementById('execScriptOutput').value = `-- Network Error: ${e.message}`;
            }
        }

        function closeExecModal() {
            document.getElementById('execModal').style.display = 'none';
        }

        function generateExecScript() {
            const rawArgs = document.getElementById('execArgumentInput').value;
            let argList = [];
            
            // Simple split by comma, respecting quotes could be better, but basic split is often enough for quick testing
            if (rawArgs.trim()) {
                let current = '';
                let inQuotes = false;
                for (let i = 0; i < rawArgs.length; i++) {
                    const c = rawArgs[i];
                    if (c === "'") inQuotes = !inQuotes;
                    if (c === ',' && !inQuotes) {
                        argList.push(current.trim());
                        current = '';
                    } else {
                        current += c;
                    }
                }
                argList.push(current.trim());
            }

            let script = `DECLARE\n`;
            
            execParams.forEach((p, idx) => {
                let defaultVal = 'NULL';
                if (idx < argList.length && argList[idx]) {
                    let val = argList[idx];
                    // Clean up if it's not already quoted but should be a string (varchar, date)
                    if (['VARCHAR2', 'CHAR', 'DATE'].includes(p.data_type)) {
                        if (!val.startsWith("'") || !val.endsWith("'")) {
                            val = `'${val.replace(/'/g, "''")}'`;
                        }
                    }
                    defaultVal = val;
                }
                let typeStr = p.data_type;
                if (p.data_type === 'VARCHAR2') typeStr += '(4000)';
                script += `  v_${p.argument_name || 'RETURN_VAL'} ${typeStr} := ${defaultVal};\n`;
            });

            script += `BEGIN\n`;
            script += `  -- Call the procedure\n`;
            
            const hasReturn = execParams.some(p => p.position === 0 && !p.argument_name); // Function return
            
            let callStr = `  `;
            if (hasReturn) {
                callStr += `v_RETURN_VAL := `;
            }
            
            callStr += `${execPackage}.${execProcedure}`;
            
            const realArgs = execParams.filter(p => p.argument_name);
            if (realArgs.length > 0) {
                callStr += `(\n`;
                realArgs.forEach((p, idx) => {
                    const comma = idx < realArgs.length - 1 ? ',' : '';
                    callStr += `    ${p.argument_name} => v_${p.argument_name}${comma}\n`;
                });
                callStr += `  );\n`;
            } else {
                callStr += `;\n`;
            }
            
            script += callStr;
            script += `  COMMIT;\n`;
            script += `END;\n/`;

            document.getElementById('execScriptOutput').value = script;
        }

        function copyExecScript() {
            const out = document.getElementById('execScriptOutput');
            out.select();
            document.execCommand('copy');
            alert('Copied to clipboard!');
        }

        function openExecInSqlTab() {
            const script = document.getElementById('execScriptOutput').value;
            closeExecModal();
            createNewSqlTab();
            if (sqlMonacoEditor) sqlMonacoEditor.setValue(script);
        }

        async function extractMainQuery(procName) {
            const code = mainMonacoEditor ? mainMonacoEditor.getValue() : '';
            if (!code || !activePlsqlName) return;
            const apiKey = document.getElementById('geminiApiKey').value.trim();
            const modelSelect = document.getElementById('geminiModelSelect').value;

            // Show loading state in the SQL Editor Tab
            switchTab('sql');
            createNewSqlTab();
            const tabName = `Ext: ${procName}`;
            document.getElementById('activeSqlTabTitle').textContent = `SQL Query Editor (${tabName})`;
            if (sqlMonacoEditor) sqlMonacoEditor.setValue(`-- 🪐 Antigravity AI [${modelSelect}] is extracting the main SQL query\n-- from ${activePlsqlName}.${procName}...\n-- Please wait, this may take a few seconds...`);
            
            try {
                const res = await fetch('/api/plsql/extract_query', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        package: activePlsqlName, 
                        procedure: procName, 
                        code: code,
                        api_key: apiKey,
                        model: modelSelect
                    })
                });
                const data = await res.json();
                
                if (data.success) {
                    if (sqlMonacoEditor) sqlMonacoEditor.setValue(`-- Extracted by 🪐 Antigravity AI from ${activePlsqlName}.${procName}\n\n${data.query}`);
                } else {
                    if (sqlMonacoEditor) sqlMonacoEditor.setValue(`-- Failed to extract query.\n-- Error: ${data.error}`);
                }
            } catch(e) {
                if (sqlMonacoEditor) sqlMonacoEditor.setValue(`-- Network Error: ${e.message}`);
            }
        }

        function exportCSV() {
            if (!currentColumns || currentColumns.length === 0) return;

            let csvContent = "\uFEFF";
            csvContent += currentColumns.map(c => `"${c.replace(/"/g, '""')}"`).join(",") + "\n";

            currentRows.forEach(row => {
                const line = row.map(val => {
                    if (val === null || val === undefined) return '""';
                    return `"${String(val).replace(/"/g, '""')}"`;
                }).join(",");
                csvContent += line + "\n";
            });

            downloadBlob(csvContent, (currentTableName || "export_data") + "_" + new Date().toISOString().slice(0,10) + ".csv", 'text/csv;charset=utf-8;');
        }

        function exportJSON() {
            if (!currentColumns || currentColumns.length === 0) return;

            const jsonObjects = currentRows.map(row => {
                let obj = {};
                currentColumns.forEach((col, idx) => {
                    obj[col] = row[idx];
                });
                return obj;
            });

            const jsonStr = JSON.stringify(jsonObjects, null, 2);
            downloadBlob(jsonStr, (currentTableName || "export_data") + "_" + new Date().toISOString().slice(0,10) + ".json", 'application/json;charset=utf-8;');
        }

        let aiAttachedContext = null;

        function showAiContextMenu() {
            const menu = document.getElementById('aiContextMenu');
            menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
        }

        document.addEventListener('click', function(e) {
            const menu = document.getElementById('aiContextMenu');
            if(menu && !e.target.closest('.ai-input-box') && menu.style.display === 'block') {
                menu.style.display = 'none';
            }
        });

        function attachContext(type) {
            const menu = document.getElementById('aiContextMenu');
            if(menu) menu.style.display = 'none';
            
            const badge = document.getElementById('aiContextBadge');
            const nameSpan = document.getElementById('aiContextName');
            
            if (type === 'sql') {
                let sql = sqlMonacoEditor ? sqlMonacoEditor.getValue().trim() : '';
                if (!sql) { alert('ไม่มี SQL Code ใน Editor'); return; }
                aiAttachedContext = `\n\n[CONTEXT: SQL QUERY]\n\`\`\`sql\n${sql}\n\`\`\``;
                nameSpan.textContent = 'SQL Query Editor';
            } else if (type === 'plsql') {
                let code = mainMonacoEditor ? mainMonacoEditor.getValue().trim() : '';
                if (!code) { alert('ไม่มี PL/SQL Code ใน Editor'); return; }
                aiAttachedContext = `\n\n[CONTEXT: PL/SQL PACKAGE]\n\`\`\`sql\n${code}\n\`\`\``;
                nameSpan.textContent = `PL/SQL: ${activePlsqlName || 'Code'}`;
            } else if (type === 'table') {
                if (!currentTableName || !currentColumns || currentColumns.length === 0) {
                    alert('กรุณา Query ข้อมูล Table ก่อนเพื่อดึงโครงสร้าง'); return;
                }
                aiAttachedContext = `\n\n[CONTEXT: TABLE STRUCTURE ${currentTableName}]\nColumns: ${currentColumns.join(', ')}`;
                nameSpan.textContent = `Table: ${currentTableName}`;
            } else if (type === 'results') {
                if (!window.vsColumns || window.vsColumns.length === 0 || !window.vsRows || window.vsRows.length === 0) {
                    alert('ไม่มีผลลัพธ์ข้อมูลที่จะส่งให้ AI (No data results available)'); return;
                }
                
                // Construct a Markdown table of the first 100 rows to avoid token explosion
                let maxRows = 100;
                let dataToAttach = window.vsColumns.join(" | ") + "\n";
                dataToAttach += window.vsColumns.map(() => "---").join(" | ") + "\n";
                
                let limitMsg = '';
                if (window.vsRows.length > maxRows) {
                    limitMsg = `*(Note: Showing only first ${maxRows} rows out of ${window.vsRows.length} to save AI tokens)*\n\n`;
                }
                
                for (let i = 0; i < Math.min(window.vsRows.length, maxRows); i++) {
                    let rowStr = window.vsRows[i].map(c => {
                        let cellVal = c === null ? "NULL" : String(c).replace(/[\r\n]+/g, " ");
                        if (cellVal.length > 200) cellVal = cellVal.substring(0, 197) + "...";
                        return cellVal;
                    }).join(" | ");
                    dataToAttach += rowStr + "\n";
                }
                
                let sql = sqlMonacoEditor ? sqlMonacoEditor.getValue().trim() : '';
                
                aiAttachedContext = `\n\n[CONTEXT: SQL QUERY AND RESULTS]\n${limitMsg}**SQL:**\n\`\`\`sql\n${sql}\n\`\`\`\n\n**RESULTS:**\n\`\`\`text\n${dataToAttach}\n\`\`\``;
                nameSpan.textContent = 'SQL Results Data';
            }
            
            badge.style.display = 'flex';
        }

        function clearAiContext() {
            aiAttachedContext = null;
            document.getElementById('aiContextBadge').style.display = 'none';
        }

        function useQuickPrompt(text) {
            document.getElementById('aiMessageInput').value = text;
            sendAiMessage();
        }

        async function sendAiMessage() {
            const input = document.getElementById('aiMessageInput');
            const apiKey = document.getElementById('geminiApiKey').value.trim();
            const modelSelect = document.getElementById('geminiModelSelect').value;
            let msg = input.value.trim();
            if (!msg && !aiAttachedContext) return;

            const container = document.getElementById('aiChatMessages');
            
            const userDiv = document.createElement('div');
            userDiv.className = 'chat-bubble user';
            userDiv.textContent = msg + (aiAttachedContext ? '\n[📎 Context Attached]' : '');
            container.appendChild(userDiv);
            
            if (aiAttachedContext) {
                msg += aiAttachedContext;
                clearAiContext();
            }
            
            input.value = '';
            container.scrollTop = container.scrollHeight;

            const aiDiv = document.createElement('div');
            aiDiv.className = 'chat-bubble ai';
            aiDiv.innerHTML = `<span class="loading-spinner"></span> 🪐 <b>[${modelSelect}]</b> กำลังประมวลผลคำตอบ...`;
            container.appendChild(aiDiv);
            container.scrollTop = container.scrollHeight;

            try {
                const res = await fetch('/api/ai/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: msg, api_key: apiKey, model: modelSelect })
                });
                const data = await res.json();

                if (data.success) {
                    aiDiv.innerHTML = data.reply;
                } else {
                    aiDiv.innerHTML = `<span style="color: var(--danger);">Error: ${data.error}</span>`;
                }
            } catch (err) {
                aiDiv.innerHTML = `<span style="color: var(--danger);">Network Error: ${err.message}</span>`;
            } finally {
                container.scrollTop = container.scrollHeight;
            }
        }

        function escapeHtml(str) {
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        // SQL Editor Draggable Resizer (between editor and results)
        const sqlEditorResizer = document.getElementById('sqlEditorResizer');
        const sqlLayout = document.querySelector('.sql-layout');
        let isSqlResizerDragging = false;
        let sqlEditorSection = null;

        if (sqlEditorResizer) {
            sqlEditorResizer.addEventListener('mousedown', () => {
                isSqlResizerDragging = true;
                sqlEditorSection = document.querySelector('.editor-section');
            });
            document.addEventListener('mousemove', (e) => {
                if (!isSqlResizerDragging || !sqlEditorSection) return;
                const rect = sqlEditorSection.getBoundingClientRect();
                const newH = e.clientY - rect.top;
                if (newH >= 80 && newH <= 700) {
                    sqlEditorSection.style.height = newH + 'px';
                    sqlEditorSection.style.flexShrink = '0';
                }
            });
            document.addEventListener('mouseup', () => { isSqlResizerDragging = false; });
        }

        // --- SFTP Explorer JS ---
        let currentSftpHost = '';
        let currentSftpPath = '';
        let sftpAuthSuccessCallback = null;

        function closeSftpAuthModal() {
            document.getElementById('sftpAuthModal').style.display = 'none';
        }

        function closeSftpExplorerModal() {
            document.getElementById('sftpExplorerModal').style.display = 'none';
        }

        let currentUploadTemplateCode = '';
        let currentUploadRdfBasePath = '';
        
        function triggerUploadRtf(templateCode) {
            if (!templateCode) return;
            currentUploadTemplateCode = templateCode;
            document.getElementById('rtfUploadInput').click();
        }
        
        async function handleRtfUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('template_code', currentUploadTemplateCode);
            
            event.target.value = ''; // Reset input
            
            showOpProgress('📤 กำลังอัปโหลด RTF Template', `กำลังสำรองเวอร์ชันเดิมใน XDO_LOBS และอัปเดตไฟล์ ${file.name}...`);
            
            try {
                const res = await fetch('/api/upload_template', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (data.success) {
                    showOpSuccess('อัปโหลด RTF Template สำเร็จ!', 'สำรองข้อมูลเดิมและอัปเดต Template ใหม่ลงฐานข้อมูลเรียบร้อยแล้ว', {
                        '📄 Template Code': data.template_code || currentUploadTemplateCode,
                        '📄 ไฟล์ที่อัปโหลด': data.filename || file.name,
                        '🌐 ไซต์ (Site)': data.site || 'Current DB',
                        '🛡️ DB Backup': 'สร้างสำเนา _BK_YYYYMMDD ในตาราง XDO_LOBS',
                        '📁 Local Backup': data.backup_path || 'D:\\WORK\\WORK\\Template_Backup',
                        '🌳 Git Repository': '✅ บันทึกสำเนาลง Git เรียบร้อย'
                    }, data.backup_path);
                } else {
                    showOpError('อัปโหลด RTF Template ไม่สำเร็จ', data.error);
                }
            } catch (err) {
                showOpError('เกิดข้อผิดพลาดในการอัปโหลด', err.message || String(err));
            }
        }
        
        function triggerUploadRdf(basePath) {
            if (!basePath) return;
            currentUploadRdfBasePath = basePath;
            document.getElementById('rdfUploadInput').click();
        }
        
        async function handleRdfUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('base_path', currentUploadRdfBasePath);
            
            event.target.value = ''; // Reset input
            
            showOpProgress('📤 กำลังอัปโหลด Oracle Report (RDF)', `กำลังสำรองไฟล์เดิม และอัปโหลด ${file.name} ขึ้น Server (fs1/fs2)...`);
            
            try {
                const res = await fetch('/api/sftp/upload_rdf', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (data.success) {
                    showOpSuccess('อัปโหลดและสำรองไฟล์สำเร็จ!', 'ไฟล์ถูกอัปโหลดขึ้น Server และจัดเก็บ Backup เรียบร้อยแล้ว', {
                        '📄 ชื่อไฟล์ (File)': data.filename || file.name,
                        '🌐 ไซต์ (Site)': data.site || 'Current DB',
                        '🛡️ Server Backup': 'เปลี่ยนชื่อเป็น _YYYYMMDD_HHMMSS บน Server',
                        '📁 Local Backup': data.backup_folder || 'D:\\WORK\\WORK\\RDF_Backup',
                        '🌳 Git Repository': '✅ อัปเดตลง Git เรียบร้อย'
                    }, data.backup_folder);
                } else if (data.error === 'NOT_CONFIGURED') {
                    closeOpStatusModal();
                    showSftpAuthPrompt(data.host || '', () => {
                        showToast('ยืนยันรหัสผ่านเรียบร้อย กรุณากดเลือกไฟล์อัปโหลดใหม่อีกครั้ง');
                        triggerUploadRdf(currentUploadRdfBasePath);
                    });
                } else {
                    showOpError('อัปโหลด Oracle Report ไม่สำเร็จ', data.error);
                }
            } catch (err) {
                showOpError('เกิดข้อผิดพลาดในการอัปโหลด', err.message || String(err));
            }
        }

        async function openFileZilla(basePath, programName) {
            document.body.style.cursor = 'wait';
            try {
                const res = await fetch('/api/open_filezilla', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({basePath: basePath})
                });
                const data = await res.json();
                
                if (data.success) {
                    // Success, FileZilla should open
                } else if (data.error === 'NOT_CONFIGURED') {
                    showSftpAuthPrompt(data.host || '', () => {
                        openFileZilla(basePath, programName);
                    });
                } else {
                    alert('Failed to launch FileZilla: ' + data.error);
                }
            } catch (err) {
                alert('Error: ' + err);
            }
            document.body.style.cursor = 'default';
        }

        async function openSftpExplorer(basePath, programName) {
            currentSftpHost = ''; // Let Python backend determine it from active_session_key
            currentSftpPath = basePath || '.';
            
            document.getElementById('sftpExplorerHost').textContent = 'Loading...';
            document.getElementById('sftpPathInput').value = currentSftpPath;
            
            loadSftpPath();
        }

        async function loadSftpPath() {
            const host = currentSftpHost;
            const path = document.getElementById('sftpPathInput').value.trim();
            const tbody = document.getElementById('sftpFileList');
            
            tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:20px;"><span class="loading-spinner"></span> Loading directory...</td></tr>';
            document.getElementById('sftpExplorerModal').style.display = 'flex';
            
            try {
                const res = await fetch('/api/sftp/list_dir', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({host: host, path: path})
                });
                const data = await res.json();
                
                if (!data.success) {
                    if (data.error === 'NOT_CONFIGURED') {
                        document.getElementById('sftpExplorerModal').style.display = 'none';
                        showSftpAuthPrompt(data.host || host, () => {
                            document.getElementById('sftpExplorerModal').style.display = 'flex';
                            loadSftpPath();
                        });
                        return;
                    }
                    tbody.innerHTML = `<tr><td colspan="3" style="color:#f14c4c; padding:20px;">Error: ${data.error}</td></tr>`;
                    return;
                }
                
                currentSftpHost = data.host;
                document.getElementById('sftpExplorerHost').textContent = currentSftpHost;
                
                currentSftpPath = data.path;
                document.getElementById('sftpPathInput').value = currentSftpPath;
                
                let html = '';
                if (data.files && data.files.length > 0) {
                    data.files.forEach(f => {
                        const icon = f.is_dir ? '📁' : '📄';
                        const nameColor = f.is_dir ? '#60a5fa' : '#e2e8f0';
                        const sizeStr = f.is_dir ? '-' : (f.size / 1024).toFixed(1) + ' KB';
                        const fullPath = (currentSftpPath === '/' ? '/' : currentSftpPath + '/') + f.name;
                        
                        let actionHtml = '';
                        if (f.is_dir) {
                            actionHtml = `<button class="btn" style="padding:2px 8px; font-size:0.75rem;" onclick="openSftpDir('${fullPath.replace(/'/g, "\\'")}')">Open</button>`;
                        } else {
                            actionHtml = `<button class="btn btn-primary" style="padding:2px 8px; font-size:0.75rem; background:linear-gradient(135deg, #10b981, #059669);" onclick="downloadSftpFile('${fullPath.replace(/'/g, "\\'")}', '${f.name.replace(/'/g, "\\'")}')">💾 Download</button>`;
                        }
                        
                        html += `
                            <tr style="border-bottom: 1px solid #1e293b; transition: background 0.2s;" onmouseover="this.style.background='#1e293b'" onmouseout="this.style.background='transparent'">
                                <td style="padding:8px 12px; cursor:${f.is_dir ? 'pointer' : 'default'};" ${f.is_dir ? `onclick="openSftpDir('${fullPath.replace(/'/g, "\\'")}')"` : ''}>
                                    <span style="margin-right:6px;">${icon}</span>
                                    <span style="color:${nameColor}; font-family:'JetBrains Mono', monospace;">${f.name}</span>
                                </td>
                                <td style="padding:8px 12px; color:#94a3b8; font-size:0.8rem;">${sizeStr}</td>
                                <td style="padding:8px 12px; text-align:right;">${actionHtml}</td>
                            </tr>
                        `;
                    });
                } else {
                    html = '<tr><td colspan="3" style="text-align:center; padding:20px; color:#94a3b8;">(Empty Directory)</td></tr>';
                }
                tbody.innerHTML = html;
                
            } catch (err) {
                tbody.innerHTML = `<tr><td colspan="3" style="color:#f14c4c; padding:20px;">Network Error: ${err.message}</td></tr>`;
            }
        }

        function openSftpDir(path) {
            document.getElementById('sftpPathInput').value = path;
            loadSftpPath();
        }

        function sftpGoUp() {
            let p = document.getElementById('sftpPathInput').value.trim();
            if (p === '/' || p === '') return;
            let parts = p.split('/');
            parts.pop();
            if (parts.length === 0 || (parts.length === 1 && parts[0] === '')) {
                document.getElementById('sftpPathInput').value = '/';
            } else {
                document.getElementById('sftpPathInput').value = parts.join('/');
            }
            loadSftpPath();
        }

        function showSftpAuthPrompt(host, onSuccess) {
            document.getElementById('sftpAuthHost').textContent = host;
            document.getElementById('sftpUsernameInput').value = '';
            document.getElementById('sftpPasswordInput').value = '';
            sftpAuthSuccessCallback = onSuccess;
            document.getElementById('sftpAuthModal').style.display = 'flex';
            document.getElementById('sftpUsernameInput').focus();
        }

        async function saveSftpAuth() {
            const host = document.getElementById('sftpAuthHost').textContent;
            const username = document.getElementById('sftpUsernameInput').value.trim();
            const password = document.getElementById('sftpPasswordInput').value;
            
            if (!username || !password) {
                alert('Please enter Username and Password');
                return;
            }
            
            const btn = document.querySelector('#sftpAuthModal .btn-primary');
            const origText = btn.innerHTML;
            btn.innerHTML = 'Testing Connection... <span class="loading-spinner" style="width:12px;height:12px;border-width:2px;"></span>';
            btn.disabled = true;
            
            try {
                const res = await fetch('/api/sftp/connect', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({host: host, username: username, password: password})
                });
                const data = await res.json();
                
                if (data.success) {
                    closeSftpAuthModal();
                    if (sftpAuthSuccessCallback) sftpAuthSuccessCallback();
                } else {
                    alert('❌ Connection failed: ' + data.error);
                }
            } catch (err) {
                alert('❌ Network Error: ' + err.message);
            } finally {
                btn.innerHTML = origText;
                btn.disabled = false;
            }
        }

        async function downloadSftpFile(remotePath, fileName) {
            const host = currentSftpHost;
            showOpProgress('📥 กำลังดาวน์โหลดไฟล์จาก SFTP', `กำลังดาวน์โหลด ${fileName}...`);
            
            try {
                const res = await fetch('/api/sftp/download', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({host: host, remote_path: remotePath, file_name: fileName})
                });
                const data = await res.json();
                
                if (data.success) {
                    showOpSuccess('ดาวน์โหลดสำเร็จ!', 'ดาวน์โหลดไฟล์จากเซิร์ฟเวอร์ลงเครื่องเรียบร้อยแล้ว', {
                        '📄 ชื่อไฟล์ (File)': data.filename || fileName,
                        '📁 บันทึกไว้ที่ (Local)': data.local_path,
                        '🌐 ไซต์ (Site)': data.site || 'Current DB'
                    }, data.local_path);
                } else {
                    showOpError('ดาวน์โหลดไฟล์ไม่สำเร็จ', data.error);
                }
            } catch (err) {
                showOpError('เกิดข้อผิดพลาดในการดาวน์โหลด', err.message || String(err));
            }
        }

        async function searchFlexfields() {
            const keyword = (document.getElementById('flexKeywordInput').value || '').trim();
            const ftype = document.getElementById('flexTypeSelect').value;
            const wrapper = document.getElementById('flexTableWrapper');
            if (!keyword) {
                wrapper.innerHTML = `
                    <div class="empty-state-box">
                        <div class="empty-state-icon">🧩</div>
                        <div class="empty-state-title">Flexfield Explorer</div>
                        <div class="empty-state-desc">Enter a keyword above to search EBS Flexfields (SIT / DFF) structure mapping.</div>
                    </div>`;
                return;
            }
            wrapper.innerHTML = '<div style="padding: 40px; text-align: center;"><span class="loading-spinner"></span> Searching Flexfields...</div>';
            try {
                const res = await fetch('/api/flexfield_search', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ keyword: keyword, type: ftype })
                });
                const data = await res.json();
                if (!data.success) {
                    wrapper.innerHTML = `<div class="alert alert-error" style="margin: 20px;">${escapeHtml(data.error || 'Search failed')}</div>`;
                    return;
                }
                if (!data.results || data.results.length === 0) {
                    wrapper.innerHTML = `
                        <div class="empty-state-box">
                            <div class="empty-state-icon">🔍</div>
                            <div class="empty-state-title">No Flexfield Segments Found</div>
                            <div class="empty-state-desc">No flexfields match "${escapeHtml(keyword)}". Try another prompt or structure keyword.</div>
                        </div>`;
                    return;
                }
                let html = `
                    <table class="data-grid">
                        <thead>
                            <tr>
                                <th class="row-idx" style="width:44px; min-width:44px; text-align:center;">#</th>
                                <th style="width: 90px;">Type</th>
                                <th>Structure / Flexfield Name</th>
                                <th style="width: 60px; text-align: right;">Seq</th>
                                <th>Prompt / Label</th>
                                <th>Mapped Column</th>
                            </tr>
                        </thead>
                        <tbody>
                `;
                data.results.forEach((r, idx) => {
                    const typeBadge = r[0] === 'DFF' ? 'VIEW' : 'PACKAGE';
                    html += `
                        <tr>
                            <td class="row-idx">${idx + 1}</td>
                            <td><span class="type-badge ${typeBadge}">${escapeHtml(r[0] || '')}</span></td>
                            <td style="font-weight: 600; color: var(--text-bright);">${escapeHtml(r[1] || '')}</td>
                            <td style="text-align: right; font-variant-numeric: tabular-nums;">${escapeHtml(r[2] != null ? r[2] : '')}</td>
                            <td style="color: var(--accent-teal);">${escapeHtml(r[3] || '')}</td>
                            <td style="font-family: 'JetBrains Mono', monospace; color: #79c0ff; font-weight: 600;">${escapeHtml(r[4] || '')}</td>
                        </tr>
                    `;
                });
                html += '</tbody></table>';
                wrapper.innerHTML = html;
            } catch (err) {
                wrapper.innerHTML = `<div class="alert alert-error" style="margin: 20px;">Network Error: ${escapeHtml(err.message)}</div>`;
            }
        }

        renderSqlSubtabs();
        loadTnsList();
        loadSavedProfiles();

        // Poll for AI Commands from backend
        setInterval(async () => {
            try {
                const res = await fetch('/api/ai/commands');
                const data = await res.json();
                if (data.commands && data.commands.length > 0) {
                    data.commands.forEach(cmd => {
                        if (cmd.type === 'RUN_SQL') {
                            switchTab('sql');
                            createNewSqlTab();
                            if (sqlMonacoEditor) sqlMonacoEditor.setValue(cmd.sql);
                            // Give monaco a moment to set value before running
                            setTimeout(() => {
                                runQuery();
                            }, 300);
                        }
                    });
                }
            } catch (e) {
                // Ignore polling errors
            }
        }, 2000);
    </script>
</body>
</html>
"""
import time



@app.route("/api/save_backup", methods=["POST"])
def api_save_backup():
    """Save backup file: opens native Save As dialog, writes content to chosen path."""
    global active_session_key, db_sessions
    data = request.json or {}
    content = data.get("content", "")
    default_name = data.get("filename", "backup.sql")
    if not content:
        return jsonify({"success": False, "error": "No content to save"})
        
    initial_dir = None
    if active_session_key and active_session_key in db_sessions:
        db_name = db_sessions[active_session_key].get('alias', '')
        if db_name:
            initial_dir = os.path.join(r"D:\WORK\WORK", db_name)
            
    path = _native_save_dialog(default_name, initial_dir)
    if not path:
        return jsonify({"success": False, "error": "Cancelled"})
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True, "path": path})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/choose_save_path", methods=["POST"])
def api_choose_save_path():
    """Opens native Save As dialog and returns chosen path."""
    data = request.json or {}
    default_name = data.get("filename", "export.csv")
    path = _native_save_dialog(default_name)
    if path:
        return jsonify({"success": True, "path": path})
    return jsonify({"success": False, "error": "Cancelled"})


def start_server():
    app.run(host="127.0.0.1", port=5055, debug=False, threaded=True)

def main():
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    time.sleep(0.6)

    webview.create_window(
        "JNavigator - Oracle SQL & PL/SQL Studio",
        "http://127.0.0.1:5055",
        js_api=desktop_api,
        width=1420,
        height=920,
        resizable=True
    )
    webview.start()
    os._exit(0)

export_tasks = {}

@app.route("/api/export_progress", methods=["GET"])
def export_progress():
    task_id = request.args.get('task_id')
    if not task_id or task_id not in export_tasks:
        return jsonify({"success": False, "status": "unknown"})
    return jsonify({"success": True, **export_tasks[task_id]})

@app.route("/api/open_filezilla", methods=["POST"])
def api_open_filezilla():
    global active_session_key, db_sessions
    data = request.json or {}
    
    host = _get_app_server_host(active_session_key)
    if not host:
        return jsonify({"success": False, "error": "Could not determine host"})
        
    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED", "host": host})
    base_path = data.get("basePath", "").strip()
    
    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])
        remote_path = base_path if base_path else "."
        if '$' in remote_path:
            stdin, stdout, stderr = ssh.exec_command(f'echo {remote_path}')
            remote_path = stdout.read().decode('utf-8').strip()
        sftp.close()
        ssh.close()
    except Exception:
        remote_path = base_path
        
    import urllib.parse
    user = urllib.parse.quote(creds['username'])
    pwd = urllib.parse.quote(creds['password'])
    
    fz_exe = r"C:\Users\MBx13\Desktop\FileZilla FTP Client\filezilla.exe"
    if not os.path.exists(fz_exe):
        fz_exe = r"C:\Program Files\FileZilla FTP Client\filezilla.exe"
        if not os.path.exists(fz_exe):
            return jsonify({"success": False, "error": f"FileZilla not found at {fz_exe}"})
            
    fz_url = f"sftp://{user}:{pwd}@{host}{remote_path}"
    
    import subprocess
    try:
        subprocess.Popen([fz_exe, fz_url])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/open_mtputty", methods=["POST"])
def api_open_mtputty():
    debug_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mtputty_debug.log")
    with open(debug_log, "a", encoding="utf-8") as f:
        f.write(f"\n--- open_mtputty called ---\n")
    
    data = request.json or {}
    base_path = data.get("basePath", "$INV_TOP")
    if base_path.startswith("$SQL") and base_path.endswith("_TOP"):
        base_path = "$" + base_path[4:]
        
    prog_name = data.get("programName", "")
    
    with open(debug_log, "a", encoding="utf-8") as f:
        f.write(f"base_path: {base_path}, prog_name: {prog_name}\n")
    
    if active_session_key not in db_sessions:
        with open(debug_log, "a", encoding="utf-8") as f: f.write("Error: No active DB session.\n")
        return jsonify({"success": False, "error": "No active DB session."})
        
    db_host = db_sessions[active_session_key].get('host')
    with open(debug_log, "a", encoding="utf-8") as f: f.write(f"db_host: {db_host}\n")
    
    if not db_host:
        with open(debug_log, "a", encoding="utf-8") as f: f.write("Error: Could not determine DB host.\n")
        return jsonify({"success": False, "error": "Could not determine DB host."})
        
    xml_path = r"C:\Users\MBx13\AppData\Roaming\TTYPlus\mtputty.xml"
    with open(debug_log, "a", encoding="utf-8") as f: f.write(f"xml_path: {xml_path}\n")
    
    if not os.path.exists(xml_path):
        with open(debug_log, "a", encoding="utf-8") as f: f.write("Error: mtputty.xml not found.\n")
        return jsonify({"success": False, "error": "mtputty.xml not found in application directory."})
        
    import xml.etree.ElementTree as ET
    import subprocess
    import time
    
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        target_session = None
        target_group = None
        target_host = None
        target_user = None
        
        # Find the group that has the DB host
        for group_node in root.findall(".//Node[@Type='0']"):
            group_name = group_node.find("DisplayName")
            group_name_text = group_name.text if group_name is not None else ""
            
            # Check if DB host is in this group
            found_db = False
            for session_node in group_node.findall(".//Node[@Type='1']"):
                srv = session_node.find("ServerName")
                if srv is not None and srv.text == db_host:
                    found_db = True
                    break
                    
            if found_db:
                # DB found, now find the APP node
                for session_node in group_node.findall(".//Node[@Type='1']"):
                    dn_elem = session_node.find("DisplayName")
                    un_elem = session_node.find("UserName")
                    
                    dn_text = dn_elem.text if dn_elem is not None else ""
                    un_text = un_elem.text if un_elem is not None else ""
                    
                    if "app" in dn_text.lower() or "app" in un_text.lower():
                        target_session = dn_text
                        target_group = group_name_text
                        break
            
            if target_session:
                break
                
        if not target_session:
            # Fallback to matching by TNS alias (e.g., PYT_PROD_8000 -> group PYT, env prod)
            tns_alias = db_sessions[active_session_key].get('alias', '')
            with open(debug_log, "a", encoding="utf-8") as f: f.write(f"Fallback TNS alias matching: {tns_alias}\n")
            
            if tns_alias:
                parts = tns_alias.split('_')
                if len(parts) >= 2:
                    prefix = parts[0].lower() # e.g. pyt
                    env = parts[1].lower()    # e.g. prod
                    
                    # Search inside matching group first
                    for group_node in root.findall(".//Node[@Type='0']"):
                        group_name = group_node.find("DisplayName")
                        group_name_text = group_name.text if group_name is not None else ""
                        
                        if group_name_text.lower() == prefix:
                            for session_node in group_node.findall(".//Node[@Type='1']"):
                                dn_elem = session_node.find("DisplayName")
                                un_elem = session_node.find("UserName")
                                dn_text = dn_elem.text.lower() if dn_elem is not None and dn_elem.text else ""
                                un_text = un_elem.text.lower() if un_elem is not None and un_elem.text else ""
                                
                                if ("app" in dn_text or "app" in un_text) and (env in dn_text or env in un_text):
                                    target_session = dn_elem.text if dn_elem is not None else ""
                                    target_group = group_name_text
                                    
                                    srv_elem = session_node.find("ServerName")
                                    target_host = srv_elem.text if srv_elem is not None else db_host
                                    target_user = un_elem.text if un_elem is not None else ""
                                    break
                            if target_session:
                                break
                                
                    # If still not found, search all nodes for prefix and env
                    if not target_session:
                        for session_node in root.findall(".//Node[@Type='1']"):
                            dn_elem = session_node.find("DisplayName")
                            un_elem = session_node.find("UserName")
                            dn_text = dn_elem.text.lower() if dn_elem is not None and dn_elem.text else ""
                            un_text = un_elem.text.lower() if un_elem is not None and un_elem.text else ""
                            
                            if ("app" in dn_text or "app" in un_text) and (env in dn_text or env in un_text):
                                if prefix in dn_text:
                                    target_session = dn_elem.text if dn_elem is not None else ""
                                    target_group = ""
                                    srv_elem = session_node.find("ServerName")
                                    target_host = srv_elem.text if srv_elem is not None else db_host
                                    target_user = un_elem.text if un_elem is not None else ""
                                    break
                                    
        with open(debug_log, "a", encoding="utf-8") as f: f.write(f"target_session: {target_session}, target_host: {target_host}, target_user: {target_user}, target_group: {target_group}\n")
        
        if not target_session:
            return jsonify({"success": False, "error": f"Could not find matching APP node for DB host {db_host} or TNS {db_sessions[active_session_key].get('alias')} in mtputty.xml."})
            
        mtputty_exe = r"C:\Users\MBx13\Desktop\MTPuTTY\mtputty.exe"
        if not os.path.exists(mtputty_exe):
            with open(debug_log, "a", encoding="utf-8") as f: f.write(f"Error: mtputty.exe not found at {mtputty_exe}\n")
            return jsonify({"success": False, "error": f"MTPuTTY not found at {mtputty_exe}"})
            
        # Prepare the command for clipboard (just $ and path)
        cmd_str = f"{base_path}/reports/US"
        import subprocess
        subprocess.run("clip", input=cmd_str.encode('utf-8'), check=True, shell=True)
        
        # Launch MTPuTTY directly
        session_arg = f"{target_group}\\{target_session}" if target_group else target_session
        mtputty_cmd = [mtputty_exe, "-s", session_arg]
        
        with open(debug_log, "a", encoding="utf-8") as f: f.write(f"Launching MTPuTTY: {mtputty_cmd}\n")
        subprocess.Popen(mtputty_cmd)
        
        return jsonify({"success": True, "message": "Launched MTPuTTY. The command has been copied to your clipboard!"})
        
    except Exception as e:
        import traceback
        with open(debug_log, "a", encoding="utf-8") as f: f.write(f"Exception: {traceback.format_exc()}\n")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/fndload/generate", methods=["POST"])
def api_fndload_generate():
    # ponytail: generates config.cfg, download.sh, upload.sh for EBS Concurrent Program migration. Upgrade with auto-SFTP execution if needed.
    data = request.json or {}
    prog = data.get("program_name", "").strip().upper()
    app_short = data.get("app_short_name", "XXCUST").strip().upper() or "XXCUST"
    ds_code = data.get("ds_code", "").strip() or prog
    tmpl_code = data.get("tmpl_code", "").strip() or f"{prog}%"
    src_profile = data.get("src_profile", "").strip()
    tgt_profile = data.get("tgt_profile", "").strip()
    base_dir = data.get("output_dir", "").strip() or r"C:\Users\MBx13\Desktop\GEN_FND"
    
    if not prog:
        return jsonify({"success": False, "error": "Program short name is required"}), 400
    if not tgt_profile:
        return jsonify({"success": False, "error": "Target DB site is required"}), 400

    profiles_data = load_saved_profiles()
    profiles = profiles_data.get("profiles", {})
    
    def resolve_jdbc(key):
        p = profiles.get(key)
        if not p:
            global active_session_key, db_sessions
            if active_session_key in db_sessions and (key == active_session_key or not key):
                p = db_sessions[active_session_key]
            elif key in db_sessions:
                p = db_sessions[key]
        if not p:
            return ""
        host = p.get("host", "")
        port = p.get("port", 1521)
        if p.get("service_name"):
            return f"{host}:{port}/{p.get('service_name')}"
        elif p.get("sid"):
            return f"{host}:{port}:{p.get('sid')}"
        return f"{host}:{port}"

    src_jdbc = resolve_jdbc(src_profile)
    tgt_jdbc = resolve_jdbc(tgt_profile)
    
    out_dir = os.path.join(base_dir, prog)
    os.makedirs(out_dir, exist_ok=True)
    
    # 1. config.cfg
    cfg_text = f"""# ============================================================
# FND_LOAD Generator - Config File
# ============================================================

# Database credentials
DB_USER=apps
DB_PASS=apps

# Application
APP_SHORT_NAME={app_short}

# Program names (comma separated, no space)
PROGRAM_NAMES={prog}

# XML Definition - data source code (comma separated)
DS_CODE={ds_code}

# RTF Template - template code filter
TMPL_CODE={tmpl_code}

# JDBC Connection strings
SRC_JDBC={src_jdbc}
TGT_JDBC={tgt_jdbc}

# FND_LOAD options
UPLOAD_MODE=REPLACE
CUSTOM_MODE=FORCE
"""
    with open(os.path.join(out_dir, "config.cfg"), "w", encoding="utf-8") as f:
        f.write(cfg_text)

    # 2. download.sh
    dl_text = f"""#!/bin/bash
# Source Oracle EBS Environment if available
[ -f "$HOME/.bash_profile" ] && source "$HOME/.bash_profile" 2>/dev/null

WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$WORK_DIR/download_$(date +%Y%m%d_%H%M%S).log"
SQLPLUS_CONN="apps/apps"

echo "============================================================" | tee "$LOG"
echo " DOWNLOAD — $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG"
echo "============================================================" | tee -a "$LOG"

echo "[1/4] Concurrent Program (afcpprog)..." | tee -a "$LOG"
sqlplus -S $SQLPLUS_CONN << 'SQLEOF' > /tmp/fnd_concurrent.tmp
SET PAGESIZE 0 FEEDBACK OFF VERIFY OFF HEADING OFF ECHO OFF TRIMSPOOL ON LINESIZE 32767 WRAP OFF
SPOOL /tmp/fnd_concurrent.tmp
SELECT '$FND_TOP/bin/FNDLOAD apps/{apps_pass} 0 Y DOWNLOAD $FND_TOP/patch/115/import/afcpprog.lct '
    || 'CON_' || ROWNUM || '_' || fcp.concurrent_program_name || '.ldt'
    || ' PROGRAM CONCURRENT_PROGRAM_NAME=' || fcp.concurrent_program_name
    || ' APPLICATION_SHORT_NAME=' || app.application_short_name
FROM FND_CONCURRENT_PROGRAMS FCP, FND_APPLICATION APP
WHERE FCP.APPLICATION_ID = APP.APPLICATION_ID
AND fcp.concurrent_program_name IN ('{prog}');
SPOOL OFF
EXIT;
SQLEOF
while IFS= read -r cmd; do [ -n "$cmd" ] && eval "$cmd" >> "$LOG" 2>&1 && echo "  [OK]" || echo "  [FAIL]"; done < /tmp/fnd_concurrent.tmp

echo "[2/4] XML Publisher Definition (xdotmpl)..." | tee -a "$LOG"
sqlplus -S $SQLPLUS_CONN << 'SQLEOF' > /tmp/fnd_xml.tmp
SET PAGESIZE 0 FEEDBACK OFF VERIFY OFF HEADING OFF ECHO OFF TRIMSPOOL ON LINESIZE 32767 WRAP OFF
SPOOL /tmp/fnd_xml.tmp
SELECT '$FND_TOP/bin/FNDLOAD apps/{apps_pass} 0 Y DOWNLOAD $XDO_TOP/patch/115/import/xdotmpl.lct'
    || ' XML_' || ROWNUM || '_' || XDDT.DATA_SOURCE_CODE || '.ldt'
    || ' XDO_DS_DEFINITIONS APPLICATION_SHORT_NAME=' || XDDT.APPLICATION_SHORT_NAME
    || ' DATA_SOURCE_CODE=' || XDDT.DATA_SOURCE_CODE
FROM XDO_DS_DEFINITIONS_TL XDDT, XDO_DS_DEFINITIONS_B XDDB
WHERE XDDB.APPLICATION_SHORT_NAME = XDDT.APPLICATION_SHORT_NAME
AND XDDB.DATA_SOURCE_CODE = XDDT.DATA_SOURCE_CODE
AND TRUNC(SYSDATE) BETWEEN TRUNC(NVL(XDDB.START_DATE,SYSDATE-1)) AND TRUNC(NVL(XDDB.END_DATE,SYSDATE+1))
AND (XDDT.data_source_code LIKE '{ds_code}');
SPOOL OFF
EXIT;
SQLEOF
while IFS= read -r cmd; do [ -n "$cmd" ] && eval "$cmd" >> "$LOG" 2>&1 && echo "  [OK]" || echo "  [FAIL]"; done < /tmp/fnd_xml.tmp

echo "[3/4] RTF Template (XDOLoader)..." | tee -a "$LOG"
sqlplus -S $SQLPLUS_CONN << 'SQLEOF' > /tmp/fnd_rtf_down.tmp
SET PAGESIZE 0 FEEDBACK OFF VERIFY OFF HEADING OFF ECHO OFF TRIMSPOOL ON LINESIZE 32767 WRAP OFF
SPOOL /tmp/fnd_rtf_down.tmp
SELECT 'java oracle.apps.xdo.oa.util.XDOLoader DOWNLOAD'
    || ' -DB_USERNAME apps'
    || ' -DB_PASSWORD apps'
    || ' -JDBC_CONNECTION ''{src_jdbc}'''
    || ' -LOB_TYPE TEMPLATE'
    || ' -APPS_SHORT_NAME ' || XDO.APPLICATION_SHORT_NAME
    || ' -LOB_CODE '        || XDO.LOB_CODE
    || ' -LANGUAGE '        || XDO.LANGUAGE
    || ' -TERRITORY '       || XDO.TERRITORY
FROM XDO_TEMPLATES_B XTB, XDO_TEMPLATES_TL XTT, XDO_LOBS XDO
WHERE XTT.APPLICATION_SHORT_NAME  = XTB.APPLICATION_SHORT_NAME
AND XTB.TEMPLATE_CODE             = XTT.TEMPLATE_CODE
AND XTB.TEMPLATE_CODE(+)          = XDO.LOB_CODE
AND XTT.APPLICATION_SHORT_NAME(+) = XDO.APPLICATION_SHORT_NAME
AND ((XDO.LOB_TYPE = 'TEMPLATE_SOURCE' AND XDO.XDO_FILE_TYPE = 'RTF')
  OR (XDO.LOB_TYPE = 'TEMPLATE'        AND XDO.XDO_FILE_TYPE <> 'XSL-FO'))
AND (XTT.TEMPLATE_CODE LIKE '{tmpl_code}');
SPOOL OFF
EXIT;
SQLEOF
while IFS= read -r cmd; do [ -n "$cmd" ] && eval "$cmd" >> "$LOG" 2>&1 && echo "  [OK]" || echo "  [FAIL]"; done < /tmp/fnd_rtf_down.tmp

echo "[4/4] Request Group (afcpreqg)..." | tee -a "$LOG"
sqlplus -S $SQLPLUS_CONN << 'SQLEOF' > /tmp/fnd_grp_down.tmp
SET PAGESIZE 0 FEEDBACK OFF VERIFY OFF HEADING OFF ECHO OFF TRIMSPOOL ON LINESIZE 32767 WRAP OFF
SPOOL /tmp/fnd_grp_down.tmp
SELECT 'FNDLOAD apps/apps O Y DOWNLOAD $FND_TOP/patch/115/import/afcpreqg.lct '
    || 'GROUP_' || ROWNUM || '_' || APP.APPLICATION_SHORT_NAME || '_' || FCP.CONCURRENT_PROGRAM_NAME || '.ldt'
    || ' REQUEST_GROUP REQUEST_GROUP_NAME=''' || REQG.REQUEST_GROUP_NAME
    || ''' APPLICATION_SHORT_NAME='''        || APP.APPLICATION_SHORT_NAME
    || ''' REQUEST_GROUP_UNIT UNIT_NAME='''  || FCP.CONCURRENT_PROGRAM_NAME || ''''
FROM FND_REQUEST_GROUP_UNITS REQU, FND_REQUEST_GROUPS REQG,
     FND_APPLICATION APP, FND_CONCURRENT_PROGRAMS FCP
WHERE REQG.REQUEST_GROUP_ID    = REQU.REQUEST_GROUP_ID
AND REQU.REQUEST_UNIT_ID       = FCP.CONCURRENT_PROGRAM_ID
AND REQG.APPLICATION_ID        = APP.APPLICATION_ID
AND FCP.ENABLED_FLAG           = 'Y'
AND FCP.CONCURRENT_PROGRAM_NAME IN ('{prog}');
SPOOL OFF
EXIT;
SQLEOF
while IFS= read -r cmd; do [ -n "$cmd" ] && eval "$cmd" >> "$LOG" 2>&1 && echo "  [OK]" || echo "  [FAIL]"; done < /tmp/fnd_grp_down.tmp

echo "Done! Files generated:" | tee -a "$LOG"
ls -1 $WORK_DIR/*.ldt $WORK_DIR/*.rtf $WORK_DIR/*.xsl 2>/dev/null | tee -a "$LOG"
"""
    with open(os.path.join(out_dir, "download.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(dl_text)

    # 3. upload.sh
    ul_text = f"""#!/bin/bash
# Source Oracle EBS Environment if available
[ -f "$HOME/.bash_profile" ] && source "$HOME/.bash_profile" 2>/dev/null

WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$WORK_DIR/upload_$(date +%Y%m%d_%H%M%S).log"
DB_USER="apps"
DB_PASS="apps"
TGT_JDBC="{tgt_jdbc}"

echo "============================================================" | tee "$LOG"
echo " UPLOAD (Direct File Method) — $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG"
echo "============================================================" | tee -a "$LOG"

echo "[1/4] Concurrent Program (afcpprog)..." | tee -a "$LOG"
for f in "$WORK_DIR"/CON_*.ldt; do
  [ -e "$f" ] || continue
  echo "  >> Uploading: $(basename "$f")" | tee -a "$LOG"
  FNDLOAD $DB_USER/$DB_PASS 0 Y UPLOAD $FND_TOP/patch/115/import/afcpprog.lct "$f" UPLOAD_MODE=REPLACE CUSTOM_MODE=FORCE >> "$LOG" 2>&1
done

echo "[2/4] XML Publisher Definition (xdotmpl)..." | tee -a "$LOG"
for f in "$WORK_DIR"/XML_*.ldt; do
  [ -e "$f" ] || continue
  echo "  >> Uploading: $(basename "$f")" | tee -a "$LOG"
  FNDLOAD $DB_USER/$DB_PASS 0 Y UPLOAD $XDO_TOP/patch/115/import/xdotmpl.lct "$f" >> "$LOG" 2>&1
done

echo "[3/4] RTF Template (XDOLoader)..." | tee -a "$LOG"
for f in "$WORK_DIR"/TEMPLATE_SOURCE_*.rtf; do
  [ -e "$f" ] || continue
  echo "  >> Uploading: $(basename "$f")" | tee -a "$LOG"
  java oracle.apps.xdo.oa.util.XDOLoader UPLOAD \\
    -DB_USERNAME $DB_USER -DB_PASSWORD $DB_PASS \\
    -JDBC_CONNECTION "$TGT_JDBC" \\
    -LOB_TYPE TEMPLATE_SOURCE \\
    -APPS_SHORT_NAME {app_short} \\
    -LOB_CODE {ds_code} \\
    -LANGUAGE en -TERRITORY TH \\
    -XDO_FILE_TYPE RTF \\
    -FILE_CONTENT_TYPE 'application/rtf' \\
    -FILE_NAME "$f" >> "$LOG" 2>&1
done

echo "[4/4] Request Group (afcpreqg)..." | tee -a "$LOG"
for f in "$WORK_DIR"/GROUP_*.ldt; do
  [ -e "$f" ] || continue
  echo "  >> Uploading: $(basename "$f")" | tee -a "$LOG"
  FNDLOAD $DB_USER/$DB_PASS 0 Y UPLOAD $FND_TOP/patch/115/import/afcpreqg.lct "$f" UPLOAD_MODE=REPLACE CUSTOM_MODE=FORCE >> "$LOG" 2>&1
done
echo "Upload complete! Log saved to: $LOG" | tee -a "$LOG"
"""
    with open(os.path.join(out_dir, "upload.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(ul_text)

    # 4. Copy generate.sh if available
    gen_sh_src = r"C:\Users\MBx13\Desktop\GEN_FND\genfnd\generate.sh"
    if not os.path.exists(gen_sh_src):
        gen_sh_src = r"C:\Users\MBx13\Desktop\GEN_FND\generate.sh"
    if os.path.exists(gen_sh_src):
        try:
            import shutil
            shutil.copy2(gen_sh_src, os.path.join(out_dir, "generate.sh"))
        except Exception:
            pass

    return jsonify({
        "success": True,
        "folder": out_dir,
        "program": prog,
        "files": ["config.cfg", "download.sh", "upload.sh"],
        "src_jdbc": src_jdbc,
        "tgt_jdbc": tgt_jdbc
    })

@app.route("/api/fndload/run_download", methods=["POST"])
def api_fndload_run_download():
    """
    SSH into EBS source server, run FNDLOAD/XDOLoader to generate LDT/XML/RTF files,
    then SFTP-pull them back to local output_dir/<PROG>/.
    Returns list of downloaded files.
    """
    global active_session_key, db_sessions
    data = request.json or {}
    prog      = data.get("program_name", "").strip().upper()
    app_short = data.get("app_short_name", "XXCUST").strip().upper() or "XXCUST"
    ds_code   = data.get("ds_code", "").strip() or prog
    tmpl_code = data.get("tmpl_code", "").strip() or f"{prog}%"
    src_profile = data.get("src_profile", "").strip()
    base_dir  = data.get("output_dir", "").strip() or r"C:\Users\MBx13\Desktop\GEN_FND"
    ssh_host  = data.get("ssh_host", "").strip()  # override; else auto-detect from src_profile

    if not prog:
        return jsonify({"success": False, "error": "program_name required"}), 400

    # Resolve SSH host from src_profile session key
    if not ssh_host:
        ssh_host = _get_app_server_host(src_profile or active_session_key)
    if not ssh_host:
        return jsonify({"success": False, "error": f"Cannot determine SSH host for profile: {src_profile}"}), 400

    creds = get_sftp_credentials(ssh_host)
    if not creds:
        return jsonify({"success": False, "error": f"NOT_CONFIGURED", "host": ssh_host}), 400

    # Resolve JDBC for XDOLoader
    profiles_data = load_saved_profiles()
    profiles = profiles_data.get("profiles", {})
    def resolve_jdbc(key):
        p = profiles.get(key) or db_sessions.get(key)
        if not p: return ""
        host = p.get("host",""); port = p.get("port",1521)
        if p.get("service_name"): return f"{host}:{port}/{p['service_name']}"
        if p.get("sid"): return f"{host}:{port}:{p['sid']}"
        return f"{host}:{port}"
    def resolve_pass(key):
        p = profiles.get(key) or db_sessions.get(key)
        if not p: return "apps"
        return p.get("password", "apps")
        
    src_jdbc = resolve_jdbc(src_profile or active_session_key)
    apps_pass = resolve_pass(src_profile or active_session_key)

    out_dir = os.path.join(base_dir, prog)
    os.makedirs(out_dir, exist_ok=True)

    # Remote temp dir
    remote_tmp = f"/tmp/fndload_{prog}_{int(__import__('time').time())}"

    downloaded = []
    log_lines  = []

    try:
        ssh, sftp = _create_sftp_client(ssh_host, creds["username"], creds["password"])

        def run(cmd, timeout=120):
            # Source Oracle env so $FND_TOP and $XDO_TOP are populated
            full_cmd = f"[ -f ~/.bash_profile ] && . ~/.bash_profile >/dev/null 2>&1; [ -f ~/.profile ] && . ~/.profile >/dev/null 2>&1; {cmd}"
            log_lines.append(f"$ {cmd.replace(apps_pass, '***')}")
            stdin, stdout, stderr = ssh.exec_command(full_cmd, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            if out: log_lines.extend(out.splitlines())
            if err: log_lines.extend([f"[ERR] {l}" for l in err.splitlines()])
            return out

        run(f"mkdir -p {remote_tmp}")

        # 1. Concurrent Program (afcpprog)
        log_lines.append(f"\n--- [1/4] Concurrent Program ---")
        ldt_name = f"CON_{prog}.ldt"
        run(f"cd {remote_tmp} && $FND_TOP/bin/FNDLOAD apps/{apps_pass} 0 Y DOWNLOAD "
            f"$FND_TOP/patch/115/import/afcpprog.lct {ldt_name} "
            f"PROGRAM CONCURRENT_PROGRAM_NAME={prog} APPLICATION_SHORT_NAME={app_short}")

        # 2. XML Publisher Data Definition (xdotmpl)
        log_lines.append(f"\n--- [2/4] XML Definition ---")
        xml_name = f"XML_{ds_code}.ldt"
        run(f"cd {remote_tmp} && $FND_TOP/bin/FNDLOAD apps/{apps_pass} 0 Y DOWNLOAD "
            f"$XDO_TOP/patch/115/import/xdotmpl.lct {xml_name} "
            f"XDO_DS_DEFINITIONS APPLICATION_SHORT_NAME={app_short} DATA_SOURCE_CODE={ds_code}")

        # 3. RTF Templates (XDOLoader)
        log_lines.append(f"\n--- [3/4] RTF Templates ---")
        run(f"cd {remote_tmp} && java oracle.apps.xdo.oa.util.XDOLoader DOWNLOAD "
            f"-DB_USERNAME apps -DB_PASSWORD {apps_pass} "
            f"-JDBC_CONNECTION {src_jdbc} "
            f"-LOB_TYPE TEMPLATE -APPS_SHORT_NAME {app_short} "
            f"-LOB_CODE {ds_code} -LANGUAGE en -TERRITORY TH "
            f"2>&1 | head -100", timeout=180)

        # 4. Request Group (afcpreqg) — non-fatal
        log_lines.append(f"\n--- [4/4] Request Group ---")
        run(f"cd {remote_tmp} && $FND_TOP/bin/FNDLOAD apps/{apps_pass} 0 Y DOWNLOAD "
            f"$FND_TOP/patch/115/import/afcpreqg.lct GROUP_{prog}.ldt "
            f"REQUEST_GROUP REQUEST_GROUP_NAME='' "
            f"APPLICATION_SHORT_NAME={app_short} "
            f"REQUEST_GROUP_UNIT UNIT_NAME={prog}")

        # Pull files back
        log_lines.append(f"\n--- Pulling files from {remote_tmp} ---")
        try:
            remote_files = sftp.listdir(remote_tmp)
        except Exception:
            remote_files = []

        for fname in remote_files:
            rpath = f"{remote_tmp}/{fname}"
            lpath = os.path.join(out_dir, fname)
            try:
                sftp.get(rpath, lpath)
                downloaded.append(fname)
                log_lines.append(f"  ✓ {fname}")
            except Exception as fe:
                log_lines.append(f"  ✗ {fname}: {fe}")

        # Cleanup remote tmp
        run(f"rm -rf {remote_tmp}")
        sftp.close()
        ssh.close()

    except Exception as e:
        log_lines.append(f"[FATAL] {e}")
        return jsonify({"success": False, "error": str(e), "log": log_lines}), 500

    # Save log
    log_path = os.path.join(out_dir, "run_download.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    return jsonify({
        "success": True,
        "program": prog,
        "folder": out_dir,
        "files": downloaded,
        "log": log_lines
    })

@app.route("/api/fndload/download_zip", methods=["POST"])

def api_fndload_download_zip():
    """Zip the generated FNDLOAD folder and stream it back as a download."""
    data = request.get_json(silent=True) or {}
    folder = data.get("folder", "").strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({"success": False, "error": "Folder not found"}), 404
    import zipfile, io
    buf = io.BytesIO()
    base = os.path.basename(folder)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, arcname=os.path.join(base, fname))
    buf.seek(0)
    from flask import send_file
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name=f"{base}.zip")

@app.route("/api/system/open_folder", methods=["POST"])
def api_system_open_folder():
    data = request.get_json(silent=True) or {}
    target_path = data.get("path", "").strip()
    if not target_path:
        return jsonify({"success": False, "error": "No path provided"})
        
    target_path = os.path.normpath(target_path)
    if not os.path.exists(target_path):
        return jsonify({"success": False, "error": f"Path not found: {target_path}"})
        
    try:
        import subprocess
        if os.path.isfile(target_path):
            # If it's a file, open the folder and highlight the file
            subprocess.Popen(['explorer', '/select,', target_path])
            folder = os.path.dirname(target_path)
        else:
            # If it's a folder, just open it
            subprocess.Popen(['explorer', target_path])
            folder = target_path
            
        return jsonify({"success": True, "folder": folder})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/gen_ddl", methods=["POST"])
def api_gen_ddl():
    data = request.json or {}
    raw_name = data.get("object_name", "").strip()
    
    if not raw_name:
        return jsonify({"success": False, "error": "No object name provided"})
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        resolved = resolve_plsql_object(cursor, raw_name, "PACKAGE")
        object_name = resolved["name"] if resolved else raw_name
        owner = resolved.get("owner") if resolved else None

        # Find owner for cross-schema DDL generation
        if not owner:
            cursor.execute("SELECT owner FROM all_objects WHERE object_name = :name AND object_type IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION') FETCH FIRST 1 ROWS ONLY", name=object_name)
            owner_row = cursor.fetchone()
            owner = owner_row[0] if owner_row else None
        
        # Format DDL properly
        cursor.execute("""
            BEGIN
                DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'PRETTY', true);
                DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR', true);
            END;
        """)
        
        def fetch_ddl(obj_type, name, schema):
            if schema:
                cursor.execute(f"SELECT DBMS_METADATA.GET_DDL('{obj_type}', :name, :schema) FROM DUAL", name=name, schema=schema)
            else:
                cursor.execute(f"SELECT DBMS_METADATA.GET_DDL('{obj_type}', :name) FROM DUAL", name=name)
            clob = cursor.fetchone()[0]
            return clob.read() if hasattr(clob, 'read') else str(clob)
        
        ddl_text = ""
        # Try to get PACKAGE first (gets both spec and body)
        try:
            ddl_text = fetch_ddl('PACKAGE', object_name, owner)
        except Exception as e:
            # Fallbacks for procedure / function
            try:
                ddl_text = fetch_ddl('PROCEDURE', object_name, owner)
            except:
                try:
                    ddl_text = fetch_ddl('FUNCTION', object_name, owner)
                except Exception as ex2:
                    return jsonify({"success": False, "error": f"Could not find DDL for {object_name}. Verify it exists and you have privileges."})
                    
        return jsonify({"success": True, "ddl": ddl_text.strip()})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})

def _get_app_server_host(session_key=None):
    global active_session_key, db_sessions
    if not session_key:
        session_key = active_session_key
        
    sess_info = db_sessions.get(session_key, {}) if session_key else {}
    db_host = (sess_info.get('host') or '').strip()
    tns_alias = (sess_info.get('alias') or '').strip()
    
    if not tns_alias and session_key and '@' in session_key:
        tns_alias = session_key.split('@')[-1].strip()
        
    tns_upper = tns_alias.upper() if tns_alias else ""
    db_host_lower = db_host.lower()
    
    # 1. Direct site mappings based on known DB Host & TNS alias
    # PYT (Phyathai)
    if "PYT" in tns_upper or "10.22.252." in db_host:
        if "PROD" in tns_upper or db_host == "10.22.252.62" or ("8000" in tns_upper and "DEV" not in tns_upper):
            return "10.22.252.65"
        elif "TST" in tns_upper or "TEST" in tns_upper or "8010" in tns_upper or db_host == "10.22.252.217":
            return "10.22.252.216"
        elif "DEV" in tns_upper or "8004" in tns_upper or "UAT" in tns_upper or db_host == "10.22.252.219":
            return "10.22.252.218"
        return "10.22.252.65"

    # TTS / ESM / PGY (Easy Money)
    if ((any(k in tns_upper for k in ["TTS", "ESM"]) or "PGY" in tns_upper) and "MCR" not in tns_upper) or "easymoney.com" in db_host_lower:
        if "8060" in tns_upper or "UAT1" in tns_upper:
            return "192.168.13.10"
        elif "8020" in tns_upper or "PRE" in tns_upper:
            return "192.168.13.1"
        elif "PROD" in tns_upper or "esm-prod-db" in db_host_lower:
            return "192.168.23.1"
        elif "8010" in tns_upper or "UAT" in tns_upper or "DEV" in tns_upper or "8030" in tns_upper:
            return "192.168.13.10"
        return "192.168.23.1"

    # BST (Bangkok Synthetics)
    if "BST" in tns_upper or "10.64.100." in db_host:
        if "PROD" in tns_upper or "8000" in tns_upper or db_host == "10.64.100.9":
            return "10.64.100.9"
        elif "DEV" in tns_upper or "8090" in tns_upper or db_host == "10.64.100.52":
            return "10.64.100.52"
        elif "TEST" in tns_upper or "8070" in tns_upper or db_host == "10.64.100.51":
            return "10.64.100.51"
        elif "PRE" in tns_upper or "8050" in tns_upper or db_host == "10.64.100.7":
            return "10.64.100.7"
        return "10.64.100.9"

    # OAG (Office of Attorney General)
    if "OAG" in tns_upper or "172.16.11." in db_host or "ago.go.th" in db_host_lower or "10.3.22.20" in db_host or "oag-dev" in db_host_lower:
        if "PROD" in tns_upper or "hrm-db1-prod" in db_host_lower:
            return "172.16.11.48"
        elif "UAT" in tns_upper or "8010" in tns_upper or db_host == "172.16.11.29":
            return "172.16.11.51"
        elif "PRE" in tns_upper or "8020" in tns_upper:
            return "172.16.11.51"
        elif "DEV" in tns_upper or "10.3.22." in db_host or "oag-dev" in db_host_lower:
            return "10.3.22.21"
        return "172.16.11.48"

    # TOAT
    if "TOAT" in tns_upper or "192.168.0.14" in db_host:
        if "PROD" in tns_upper or "8000" in tns_upper or db_host == "192.168.0.140":
            return "192.168.0.140"
        return "192.168.0.143"

    # SME Bank
    if "SME" in tns_upper or "smebank.co.th" in db_host_lower or "192.168.225." in db_host or "192.168.199." in db_host:
        if "199.42" in db_host or "199.41" in db_host:
            return "192.168.199.41"
        return "192.168.225.117"

    # ABAC
    if "ABAC" in tns_upper or "10.1.6." in db_host:
        if "PROD" in tns_upper:
            return "10.1.6.10"
        return "10.1.6.13"

    # VIS (Vision)
    if "VIS" in tns_upper:
        return "10.3.22.212"

    # MCR
    if "MCR" in tns_upper:
        if "DEV" in tns_upper:
            return "10.3.22.55"
        return "10.3.22.25"

    # Fallback Strategy 1: Check FileZilla XML
    import xml.etree.ElementTree as ET
    for fz_xml_path in [r"C:\Users\MBx13\Downloads\FileZilla.xml", r"C:\Users\MBx13\Desktop\FileZilla.xml"]:
        if os.path.exists(fz_xml_path):
            try:
                fz_tree = ET.parse(fz_xml_path)
                fz_root = fz_tree.getroot()
                if tns_alias:
                    import re
                    alias_lower = tns_alias.lower()
                    nums = re.findall(r'\d{4}', tns_alias)
                    prefix = alias_lower.split('_')[0] if '_' in alias_lower else alias_lower
                    allowed_prefixes = [prefix] if prefix else []
                    if 'tts' in allowed_prefixes: allowed_prefixes.append('esm')
                    if 'esm' in allowed_prefixes: allowed_prefixes.append('tts')
                    
                    for server_node in fz_root.findall(".//Server"):
                        name_elem = server_node.find("Name")
                        if name_elem is not None and name_elem.text:
                            name_text = name_elem.text.lower()
                            if name_text == alias_lower:
                                host = server_node.find("Host")
                                if host is not None and host.text: return host.text
                                
                            has_prefix = any(p in name_text for p in allowed_prefixes) if allowed_prefixes else True
                            if has_prefix:
                                if nums and any(num in name_text for num in nums):
                                    host = server_node.find("Host")
                                    if host is not None and host.text: return host.text
            except Exception:
                pass

    # Fallback Strategy 2: mtputty.xml
    xml_path = r"C:\Users\MBx13\AppData\Roaming\TTYPlus\mtputty.xml"
    if os.path.exists(xml_path):
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            alias_lower = tns_alias.lower() if tns_alias else ""
            env = ""
            for e in ['tst', 'uat', 'dev', 'prod', 'pre', 'test', 'vis', 'bg']:
                if e in alias_lower:
                    env = e
                    break
                    
            for session_node in root.findall(".//Node[@Type='1']"):
                dn_elem = session_node.find("DisplayName")
                if dn_elem is not None and dn_elem.text and dn_elem.text.lower() == alias_lower:
                    srv = session_node.find("ServerName")
                    if srv is not None and srv.text:
                        return srv.text
                        
            prefix = alias_lower.split('_')[0] if '_' in alias_lower else alias_lower
            if prefix and env:
                for session_node in root.findall(".//Node[@Type='1']"):
                    dn_elem = session_node.find("DisplayName")
                    un_elem = session_node.find("UserName")
                    dn_text = dn_elem.text.lower() if dn_elem is not None and dn_elem.text else ""
                    un_text = un_elem.text.lower() if un_elem is not None and un_elem.text else ""
                    if prefix in dn_text and ("app" in dn_text or "app" in un_text) and (env in dn_text or env in un_text):
                        srv = session_node.find("ServerName")
                        if srv is not None and srv.text:
                            return srv.text
        except Exception:
            pass

    return db_host

def _patch_paramiko_legacy_support():
    try:
        from paramiko.transport import Transport
        from paramiko.rsakey import RSAKey
        from cryptography.hazmat.primitives import hashes
        
        if 'ssh-rsa' not in Transport._preferred_keys:
            Transport._preferred_keys = Transport._preferred_keys + ('ssh-rsa',)
        Transport._key_info['ssh-rsa'] = RSAKey
        RSAKey.HASHES['ssh-rsa'] = hashes.SHA1
        RSAKey.HASHES['ssh-dss'] = hashes.SHA1
    except Exception as e:
        print(f"Notice patching paramiko: {e}")

_patch_paramiko_legacy_support()

def _create_sftp_client(host, username, password):
    import paramiko
    _patch_paramiko_legacy_support()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(
            hostname=host, 
            username=username, 
            password=password, 
            timeout=10,
            look_for_keys=False,
            allow_agent=False
        )
    except Exception as e:
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=host, 
                username=username, 
                password=password, 
                timeout=10,
                look_for_keys=False,
                allow_agent=False,
                disabled_algorithms={'pubkeys': ['rsa-sha2-256', 'rsa-sha2-512']}
            )
        except Exception:
            raise e
            
    sftp = ssh.open_sftp()
    return ssh, sftp

@app.route("/api/sftp/connect", methods=["POST"])
def api_sftp_connect():
    global active_session_key, db_sessions
    data = request.json or {}
    host = data.get("host", "").strip()
    if not host:
        host = _get_app_server_host(active_session_key)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    
    if not host or not username or not password:
        return jsonify({"success": False, "error": "Missing credentials or host"})
        
    try:
        ssh, sftp = _create_sftp_client(host, username, password)
        sftp.close()
        ssh.close()
        
        # Save to profile
        profiles = load_sftp_profiles()
        profiles[host] = {"username": username, "password": password}
        save_sftp_profiles(profiles)
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/sftp/list_dir", methods=["POST"])
def api_sftp_list_dir():
    global active_session_key, db_sessions
    data = request.json or {}
    host = data.get("host", "").strip()
    if not host:
        host = _get_app_server_host(active_session_key)
        
    path = data.get("path", "").strip()
    
    if not host:
        return jsonify({"success": False, "error": "Could not determine host"})
        
    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED", "host": host})
    
    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])
        
        # Resolve environment variables
        if '$' in path:
            stdin, stdout, stderr = ssh.exec_command(f'echo {path}')
            path = stdout.read().decode('utf-8').strip()
            
        if not path:
            path = '.'
            
        # Get directory listing
        files = []
        for attr in sftp.listdir_attr(path):
            import stat
            is_dir = stat.S_ISDIR(attr.st_mode)
            files.append({
                "name": attr.filename,
                "is_dir": is_dir,
                "size": attr.st_size,
                "mtime": attr.st_mtime
            })
            
        # Sort folders first, then alphabetically
        files.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        
        sftp.close()
        ssh.close()
        
        return jsonify({"success": True, "path": path, "files": files, "host": host})
    except Exception as e:
        err_msg = str(e)
        if "creds" in locals() and isinstance(creds, dict):
            err_msg = f"Host: {host}, User: {creds.get('username')}, Error: {str(e)}"
        print("SFTP LIST DIR ERROR:", err_msg)
        return jsonify({"success": False, "error": err_msg})

@app.route("/api/sftp/read_text", methods=["POST"])
def api_sftp_read_text():
    global active_session_key, db_sessions
    data = request.json or {}
    host = data.get("host", "").strip()
    if not host:
        host = _get_app_server_host(active_session_key)
        
    remote_path = data.get("remote_path", "").strip()
    if not host:
        return jsonify({"success": False, "error": "Could not determine host"})
        
    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED", "host": host})
        
    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])
        with sftp.file(remote_path, 'rb') as f:
            content = f.read()
            
        try:
            text = content.decode('utf-8')
        except UnicodeDecodeError:
            text = content.decode('latin-1', errors='replace')
            
        sftp.close()
        ssh.close()
        return jsonify({"success": True, "text": text})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/sftp/download", methods=["POST"])
def api_sftp_download():
    global active_session_key, db_sessions
    data = request.json or {}
    host = data.get("host", "").strip()
    if not host:
        host = _get_app_server_host(active_session_key)
        
    remote_path = data.get("remote_path", "").strip()
    file_name = data.get("file_name", "").strip()
    
    if not host:
        return jsonify({"success": False, "error": "Could not determine host"})
    
    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED"})
    
    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])
        
        db_name = db_sessions.get(active_session_key, {}).get('alias', 'UNKNOWN_DB')
        output_folder = os.path.join(r"D:\WORK\WORK\DOWNLOADS", db_name)
            
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            
        local_path = os.path.join(output_folder, file_name)
        sftp.get(remote_path, local_path)
        
        sftp.close()
        ssh.close()
        
        return jsonify({"success": True, "local_path": local_path, "filename": file_name, "site": db_name})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

def git_report_save(session_alias, obj_category, report_name, file_name, file_content):
    import os, subprocess, datetime
    base_repo = r"D:\WORK\EBS_Git_Repo"
    
    if not session_alias:
        session_alias = "UNKNOWN_SITE"
        
    type_dir = os.path.join(base_repo, session_alias, obj_category, report_name)
    os.makedirs(type_dir, exist_ok=True)
    
    file_path = os.path.join(type_dir, file_name)
    
    try:
        if isinstance(file_content, bytes):
            with open(file_path, "wb") as f:
                f.write(file_content)
        else:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(file_content)
                
        if not os.path.exists(os.path.join(base_repo, ".git")):
            subprocess.run(["git", "init"], cwd=base_repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "JNavigator Auto"], cwd=base_repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "auto@jnavigator.local"], cwd=base_repo, capture_output=True)
            
        subprocess.run(["git", "add", "."], cwd=base_repo, capture_output=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_msg = f"Auto-save: {obj_category} {report_name} ({file_name}) on {session_alias} at {timestamp}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=base_repo, capture_output=True)
        subprocess.run(["git", "push"], cwd=base_repo, capture_output=True)
    except Exception:
        pass

@app.route("/api/sftp/check_auth", methods=["GET", "POST"])
def api_sftp_check_auth():
    global active_session_key, db_sessions
    data = request.get_json(silent=True) or {}
    host = request.args.get("host") or data.get("host") or ""
    if not host:
        host = _get_app_server_host(active_session_key)
    if not host:
        return jsonify({"configured": False, "error": "No host"})
    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"configured": False, "host": host})
    return jsonify({"configured": True, "host": host})

@app.route("/api/sftp/view_output", methods=["GET"])
def api_sftp_view_output():
    global active_session_key, db_sessions
    host = request.args.get("host", "").strip()
    if not host:
        host = _get_app_server_host(active_session_key)
        
    remote_path = request.args.get("remote_path", "").strip()
    filename = request.args.get("filename", "").strip() or os.path.basename(remote_path)
    force_download = request.args.get("download", "0").strip() == "1"
    
    if not host or not remote_path:
        return "Host or remote_path parameter missing", 400
        
    creds = get_sftp_credentials(host)
    if not creds:
        return f"""
        <html>
        <head><title>SFTP Authentication Required</title></head>
        <body style="background:#0f172a;color:#fff;font-family:sans-serif;padding:40px;text-align:center;">
            <h2>🔐 SFTP Authentication Required</h2>
            <p>Please configure credentials for server <b>{host}</b> in JNavigator, then refresh this page.</p>
        </body>
        </html>
        """, 401
        
    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])
        with sftp.file(remote_path, 'rb') as f:
            content = f.read()
        sftp.close()
        ssh.close()
        
        ext = os.path.splitext(filename)[1].lower()
        if ext == '.pdf':
            mimetype = 'application/pdf'
            disposition = 'attachment' if force_download else 'inline'
        elif ext in ['.xls', '.xlsx']:
            mimetype = 'application/vnd.ms-excel'
            disposition = 'attachment'
        elif ext == '.rtf':
            mimetype = 'application/rtf'
            disposition = 'attachment'
        elif ext in ['.xml']:
            mimetype = 'application/xml'
            disposition = 'attachment' if force_download else 'inline'
        elif ext in ['.html', '.htm']:
            mimetype = 'text/html'
            disposition = 'attachment' if force_download else 'inline'
        else:
            mimetype = 'text/plain'
            disposition = 'attachment' if force_download else 'inline'
            try:
                content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    content = content.decode('cp874').encode('utf-8')
                except Exception:
                    content = content.decode('latin-1', errors='replace').encode('utf-8')
            
        res = Response(content, mimetype=mimetype)
        res.headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        return res
    except Exception as e:
        return f"Error retrieving output file from server: {e}", 500

@app.route("/api/sftp/download_xml", methods=["POST"])
def api_sftp_download_xml():
    global active_session_key, db_sessions
    data = request.get_json(silent=True) or {}
    remote_path = data.get("remote_path", "").strip()
    request_id = data.get("request_id", "").strip()
    program_name = data.get("program_name", "").strip() or "REPORT"
    
    host = _get_app_server_host(active_session_key)
    if not host:
        return jsonify({"success": False, "error": "Could not determine host"})
        
    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED", "host": host})
        
    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])
        db_name = db_sessions.get(active_session_key, {}).get('alias', 'UNKNOWN_DB')
        
        output_folder = os.path.join(r"D:\WORK\WORK\XML", db_name, program_name)
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            
        filename = f"{request_id}.xml" if request_id else os.path.basename(remote_path)
        if not filename.lower().endswith('.xml'):
            filename += ".xml"
            
        local_path = os.path.join(output_folder, filename)
        sftp.get(remote_path, local_path)
        
        try:
            with open(local_path, "rb") as f:
                content = f.read()
            git_report_save(db_name, "XML", program_name, filename, content)
        except Exception:
            pass
            
        sftp.close()
        ssh.close()
        return jsonify({"success": True, "local_path": local_path, "filename": filename, "remote_path": remote_path, "site": db_name, "git_backup": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/sftp/download_oracle_report", methods=["POST"])
def api_sftp_download_oracle_report():
    global active_session_key, db_sessions
    data = request.json or {}
    source_file_path = data.get("source_file_path", "").strip()
    program_name = data.get("program_name", "").strip()
    base_path = data.get("base_path", "").strip()
    
    host = _get_app_server_host(active_session_key)
    if not host:
        return jsonify({"success": False, "error": "Could not determine application server host."})
        
    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED", "host": host})
        
    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])
        
        target_path = source_file_path
        if not target_path or not target_path.lower().endswith('.rdf'):
            if program_name:
                rdf_name = program_name if program_name.lower().endswith('.rdf') else f"{program_name}.rdf"
                target_path = f"{base_path}/reports/US/{rdf_name}" if base_path else f"./reports/US/{rdf_name}"
            else:
                target_path = f"{base_path}/reports/US"
                
        if '$' in target_path:
            resolve_cmd = f"[ -f ~/.bash_profile ] && . ~/.bash_profile >/dev/null 2>&1; [ -f ~/.profile ] && . ~/.profile >/dev/null 2>&1; echo '===JNAV==='; echo {target_path}"
            stdin, stdout, stderr = ssh.exec_command(resolve_cmd)
            full_out = stdout.read().decode('utf-8').strip()
            
            if '===JNAV===' in full_out:
                eval_path = full_out.split('===JNAV===')[-1].strip()
            else:
                eval_path = full_out
                
            if eval_path and eval_path != target_path and not eval_path.startswith('/reports/US'):
                target_path = eval_path
            else:
                target_path = eval_path or target_path
            
        file_name = os.path.basename(target_path)
        if not file_name or file_name == '.' or not file_name.lower().endswith('.rdf'):
            if program_name:
                file_name = program_name if program_name.lower().endswith('.rdf') else f"{program_name}.rdf"
            else:
                file_name = "report.rdf"
                
        dir_name = os.path.dirname(target_path) or "."
        candidate_remotes = [
            target_path,
            target_path.replace('/reports/US/custom/', '/reports/custom/US/'),
            target_path.replace('/reports/custom/US/', '/reports/US/custom/'),
            f"{dir_name}/{file_name}",
            f"{dir_name}/{file_name.upper()}",
            f"{dir_name}/{file_name.lower()}"
        ]
        if '/fs1/' in target_path:
            candidate_remotes.append(target_path.replace('/fs1/', '/fs2/'))
            candidate_remotes.append(target_path.replace('/fs1/', '/fs2/').replace('/reports/US/custom/', '/reports/custom/US/'))
        elif '/fs2/' in target_path:
            candidate_remotes.append(target_path.replace('/fs2/', '/fs1/'))
            candidate_remotes.append(target_path.replace('/fs2/', '/fs1/').replace('/reports/US/custom/', '/reports/custom/US/'))
            
        found_remote = None
        for cand in candidate_remotes:
            try:
                sftp.stat(cand)
                found_remote = cand
                break
            except (FileNotFoundError, IOError):
                pass
                
        if not found_remote:
            try:
                files_in_dir = sftp.listdir(dir_name)
                for f in files_in_dir:
                    if f.lower() == file_name.lower():
                        found_remote = f"{dir_name}/{f}"
                        file_name = f
                        break
            except Exception:
                pass

        # If still not found, execute dynamic find on server
        if not found_remote:
            find_cmd = f"[ -f ~/.bash_profile ] && . ~/.bash_profile >/dev/null 2>&1; [ -f ~/.profile ] && . ~/.profile >/dev/null 2>&1; find $APPL_TOP /TST /UAT /PROD /u01 /u02 /u03 /apps /oracle /d01 /d02 -type f -name '{file_name}' 2>/dev/null | head -n 1"
            stdin, stdout, stderr = ssh.exec_command(find_cmd)
            found = stdout.read().decode('utf-8').strip()
            if found:
                try:
                    sftp.stat(found)
                    found_remote = found
                    target_path = found
                    file_name = os.path.basename(found)
                except Exception:
                    pass
                
        if not found_remote:
            sftp.close()
            ssh.close()
            return jsonify({"success": False, "error": f"File '{file_name}' not found on server at {target_path}"})
            
        db_name = db_sessions.get(active_session_key, {}).get('alias', 'UNKNOWN_DB')
        report_name_folder = os.path.splitext(file_name)[0]
        output_folder = os.path.join(r"D:\WORK\WORK\RDF", db_name, report_name_folder)
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            
        local_path = os.path.join(output_folder, file_name)
        sftp.get(found_remote, local_path)
        
        try:
            with open(local_path, "rb") as f:
                git_report_save(db_name, "RDF", report_name_folder, file_name, f.read())
        except Exception:
            pass
        
        sftp.close()
        ssh.close()
        
        return jsonify({"success": True, "path": local_path, "filename": file_name, "site": db_name, "git_backup": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/upload_template", methods=["POST"])
def api_upload_template():
    global active_session_key, db_sessions

    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"})

    file = request.files['file']
    template_code = request.form.get("template_code", "").strip()

    if not template_code:
        return jsonify({"success": False, "error": "No template code provided"})

    ssh_host = _get_app_server_host(active_session_key)
    if not ssh_host:
        return jsonify({"success": False, "error": "Cannot determine SSH host from session"})

    creds = get_sftp_credentials(ssh_host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED", "host": ssh_host})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT application_short_name, language, territory
            FROM xdo_lobs
            WHERE lob_code = :code AND rownum = 1
        ''', code=template_code)
        meta_row = cursor.fetchone()

        if not meta_row:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": f"Template code {template_code} not found in XDO_LOBS. Cannot upload via XDOLoader."})

        app_short = meta_row[0] or "XXCUST"
        lang = meta_row[1] or "en"
        terr = meta_row[2] or "TH"

        db = db_sessions.get(active_session_key, {})
        host = db.get("host", ""); port = db.get("port", 1521)
        if db.get("service_name"): src_jdbc = f"{host}:{port}/{db['service_name']}"
        elif db.get("sid"): src_jdbc = f"{host}:{port}:{db['sid']}"
        else: src_jdbc = f"{host}:{port}"

        import tempfile
        import time
        import os

        temp_dir = tempfile.gettempdir()
        local_temp_path = os.path.join(temp_dir, file.filename)
        file.save(local_temp_path)

        ssh, sftp = _create_sftp_client(ssh_host, creds['username'], creds['password'])
        remote_tmp_dir = f"/tmp/xdo_upload_{int(time.time())}"

        ssh.exec_command(f"mkdir -p {remote_tmp_dir}")
        remote_file_path = f"{remote_tmp_dir}/{file.filename}"
        sftp.put(local_temp_path, remote_file_path)

        apps_pass = db.get("password", "apps")

        cmd = (
            f"[ -f ~/.bash_profile ] && . ~/.bash_profile >/dev/null 2>&1; "
            f"[ -f ~/.profile ] && . ~/.profile >/dev/null 2>&1; "
            f"cd {remote_tmp_dir} && "
            f"java oracle.apps.xdo.oa.util.XDOLoader UPLOAD "
            f"-DB_USERNAME apps -DB_PASSWORD {apps_pass} "
            f"-JDBC_CONNECTION {src_jdbc} "
            f"-LOB_TYPE TEMPLATE_SOURCE -APPS_SHORT_NAME {app_short} "
            f"-LOB_CODE {template_code} -LANGUAGE {lang} -TERRITORY {terr} "
            f"-XDO_FILE_TYPE RTF -FILE_CONTENT_TYPE 'application/rtf' "
            f"-FILE_NAME {file.filename} -CUSTOM_MODE FORCE "
            f"2>&1"
        )

        stdin, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode('utf-8', errors='replace')
        err = stderr.read().decode('utf-8', errors='replace')

        sftp.remove(remote_file_path)
        ssh.exec_command(f"rmdir {remote_tmp_dir}")
        ssh.close()
        try:
            os.remove(local_temp_path)
        except:
            pass

        if "Error" in out or "Exception" in out:
            raise Exception(f"XDOLoader Error:\n{out}\n{err}")

        db_name = db.get('alias', 'UNKNOWN_DB')
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Uploaded and compiled successfully via XDOLoader!",
            "template_code": template_code,
            "filename": file.filename,
            "site": db_name,
            "backup_path": "XDOLoader Uploaded (Server)"
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/sftp/upload_rdf", methods=["POST"])
def api_sftp_upload_rdf():
    global active_session_key, db_sessions
    
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"})
        
    file = request.files['file']
    base_path = request.form.get("base_path", "").strip()
    
    host = _get_app_server_host(active_session_key)
    if not host:
        return jsonify({"success": False, "error": "Could not determine host"})
        
    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED", "host": host})
    
    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])
        
        filename = file.filename
        if not filename:
            filename = "uploaded_report.rdf"
            
        remote_path = f"{base_path}/reports/US" if base_path else "."
        if '$' in remote_path:
            resolve_cmd = f"[ -f ~/.bash_profile ] && . ~/.bash_profile >/dev/null 2>&1; [ -f ~/.profile ] && . ~/.profile >/dev/null 2>&1; echo '===JNAV==='; echo {remote_path}"
            stdin, stdout, stderr = ssh.exec_command(resolve_cmd)
            full_out = stdout.read().decode('utf-8').strip()
            
            if '===JNAV===' in full_out:
                eval_path = full_out.split('===JNAV===')[-1].strip()
            else:
                eval_path = full_out
                
            # If the file exists already somewhere in the app filesystem, find its exact directory
            find_cmd = f"[ -f ~/.bash_profile ] && . ~/.bash_profile >/dev/null 2>&1; [ -f ~/.profile ] && . ~/.profile >/dev/null 2>&1; find $APPL_TOP /TST /UAT /PROD /u01 /u02 /u03 /apps /oracle /d01 /d02 -type f -name '{filename}' 2>/dev/null | head -n 1"
            stdin, stdout, stderr = ssh.exec_command(find_cmd)
            find_path = stdout.read().decode('utf-8').strip()
            if find_path:
                remote_path = os.path.dirname(find_path)
            elif eval_path and eval_path != remote_path and not eval_path.startswith('/reports/US'):
                remote_path = eval_path
            else:
                remote_path = eval_path or remote_path
                
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bk_filename = f"{os.path.splitext(filename)[0]}_{timestamp}{os.path.splitext(filename)[1]}"
        
        db_name = db_sessions.get(active_session_key, {}).get('alias', 'UNKNOWN_DB')
        report_name_folder = os.path.splitext(filename)[0]
        backup_folder = os.path.join(r"D:\WORK\WORK\RDF_Backup", db_name, report_name_folder)
        if not os.path.exists(backup_folder):
            os.makedirs(backup_folder)
        
        fs_paths = [remote_path]
        if '/fs1/' in remote_path:
            fs_paths.append(remote_path.replace('/fs1/', '/fs2/'))
        elif '/fs2/' in remote_path:
            fs_paths.append(remote_path.replace('/fs2/', '/fs1/'))
            
        new_file_content = file.read()
        
        log_messages = []
        for path in fs_paths:
            full_path = f"{path}/{filename}"
            full_bk_path = f"{path}/{bk_filename}"
            
            try:
                sftp.stat(full_path)
                local_bk_path = os.path.join(backup_folder, f"fs_{bk_filename}")
                sftp.get(full_path, local_bk_path)
                sftp.rename(full_path, full_bk_path)
                log_messages.append(f"Backed up on server as {bk_filename} and local RDF_Backup")
            except FileNotFoundError:
                pass
                
            with sftp.file(full_path, 'wb') as f:
                f.write(new_file_content)
            log_messages.append(f"Uploaded {filename} to {path}")
            
        git_report_save(db_name, "RDF", report_name_folder, filename, new_file_content)
        
        sftp.close()
        ssh.close()
        
        return jsonify({
            "success": True, 
            "message": "\n".join(log_messages),
            "filename": filename,
            "backup_folder": backup_folder,
            "site": db_name,
            "git_backup": True
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/download_template", methods=["POST"])
def api_download_template():
    global active_session_key, db_sessions
    data = request.json or {}
    template_code = data.get("template_code", "").strip()
    
    if not template_code:
        return jsonify({"success": False, "error": "No template code provided"})
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # LOB_TYPE = 'TEMPLATE_SOURCE' มักจะเป็นไฟล์ Word (.rtf)
        # LOB_TYPE = 'TEMPLATE' มักจะเป็นไฟล์ที่แปลงแล้ว (.xsl)
        sql_query = """
            SELECT file_name, file_data
            FROM xdo_lobs
            WHERE lob_code = :template_code
              AND lob_type IN ('TEMPLATE', 'TEMPLATE_SOURCE')
              AND file_name LIKE '%.rtf'
        """
        
        cursor.execute(sql_query, template_code=template_code)
        
        db_name = db_sessions.get(active_session_key, {}).get('alias', 'UNKNOWN_DB')
        output_folder = os.path.join(r"D:\WORK\WORK\TEMPLATE", db_name, template_code)
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
            
        files_downloaded = 0
        downloaded_paths = []
        for row in cursor:
            file_name = row[0]
            file_data = row[1] 
            
            if file_data is not None:
                # อ่านข้อมูล BLOB
                if isinstance(file_data, bytes):
                    blob_data = file_data
                else:
                    blob_data = file_data.read()
                
                # สร้าง Path สำหรับเซฟไฟล์
                output_path = os.path.join(output_folder, file_name)
                
                # เขียนไฟล์ลงเครื่อง (ใช้โหมด 'wb' สำหรับ Binary File)
                with open(output_path, "wb") as f:
                    f.write(blob_data)
                
                try:
                    git_report_save(db_name, "TEMPLATE", template_code, file_name, blob_data)
                except Exception:
                    pass
                    
                downloaded_paths.append(output_path)
                files_downloaded += 1
                
        cursor.close()
        conn.close()
        
        if files_downloaded > 0:
            first_path = downloaded_paths[0] if downloaded_paths else ""
            return jsonify({
                "success": True, 
                "path": first_path,
                "paths": downloaded_paths,
                "template_code": template_code,
                "site": db_name,
                "git_backup": True
            })
        else:
            return jsonify({"success": False, "error": f"No RTF template found for Code: {template_code}"})
            
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()})


@app.route("/api/flexfield_search", methods=["POST"])
def api_flexfield_search():
    global active_session_key, db_sessions
    data = request.json or {}
    keyword = data.get("keyword", "").strip()
    ftype = data.get("type", "ALL")

    if not active_session_key:
        return jsonify({"success": False, "error": "No active database connection"})

    if not keyword:
        return jsonify({"success": False, "error": "Please enter a search keyword"})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        results = []
        columns = []

        if ftype in ["ALL", "SIT"]:
            sql_sit = """
                SELECT 
                    'SIT/KFF' AS flex_type,
                    fifs.id_flex_structure_name AS structure_name,
                    fifse.segment_num AS seq,
                    NVL(fifse.form_left_prompt, fifse.segment_name) AS prompt_name,
                    fifse.application_column_name AS mapped_column
                FROM fnd_id_flex_structures_vl fifs
                JOIN fnd_id_flex_segments_vl fifse 
                  ON fifs.id_flex_num = fifse.id_flex_num 
                 AND fifs.id_flex_code = fifse.id_flex_code
                WHERE fifs.id_flex_code = 'PEA'
                  AND (LOWER(fifs.id_flex_structure_name) LIKE LOWER(:kw)
                       OR LOWER(fifse.form_left_prompt) LIKE LOWER(:kw)
                       OR LOWER(fifse.segment_name) LIKE LOWER(:kw))
                ORDER BY fifs.id_flex_structure_name, fifse.segment_num
            """
            cursor.execute(sql_sit, kw=f'%{keyword}%')
            for r in cursor:
                results.append(list(r))
            
            if not columns:
                columns = [col[0] for col in cursor.description]

        if ftype in ["ALL", "DFF"]:
            sql_dff = """
                SELECT 
                    'DFF' AS flex_type,
                    fdfc.descriptive_flexfield_name AS structure_name,
                    fdfc.column_seq_num AS seq,
                    fdfc.form_left_prompt AS prompt_name,
                    fdfc.application_column_name AS mapped_column
                FROM fnd_descr_flex_col_usage_vl fdfc
                WHERE (LOWER(fdfc.form_left_prompt) LIKE LOWER(:kw)
                   OR LOWER(fdfc.descriptive_flexfield_name) LIKE LOWER(:kw)
                   OR LOWER(fdfc.descriptive_flex_context_name) LIKE LOWER(:kw))
                ORDER BY fdfc.descriptive_flexfield_name, fdfc.column_seq_num
            """
            cursor.execute(sql_dff, kw=f'%{keyword}%')
            dff_results = [list(r) for r in cursor]
            
            if not columns and cursor.description:
                columns = [col[0] for col in cursor.description]
                
            results.extend(dff_results)

        cursor.close()
        conn.close()

        return jsonify({"success": True, "columns": columns, "results": results})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})

ai_commands_queue = []

@app.route('/api/ai/commands', methods=['GET'])
def get_ai_commands():
    global ai_commands_queue
    cmds = ai_commands_queue.copy()
    ai_commands_queue.clear()
    return jsonify({"commands": cmds})

@app.route('/api/ai/push_sql', methods=['POST'])
def push_sql_command():
    data = request.json or {}
    sql = data.get('sql')
    if sql:
        ai_commands_queue.append({"type": "RUN_SQL", "sql": sql})
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "No SQL provided"})

@app.route("/api/fndload/upload_folder", methods=["POST"])
def api_fndload_upload_folder():
    global active_session_key, db_sessions
    target_profile = request.form.get("target_profile", "").strip()
    files = request.files.getlist("files")

    if not target_profile or not files:
        return jsonify({"success": False, "error": "Missing profile or files"})

    host = _get_app_server_host(target_profile)
    if not host:
        return jsonify({"success": False, "error": f"Cannot determine SSH host for profile: {target_profile}"})

    creds = get_sftp_credentials(host)
    if not creds:
        return jsonify({"success": False, "error": "NOT_CONFIGURED", "host": host})

    try:
        ssh, sftp = _create_sftp_client(host, creds['username'], creds['password'])

        import time
        import os
        remote_tmp = f"/tmp/fndload_upload_{int(time.time())}"
        ssh.exec_command(f"mkdir -p {remote_tmp}")

        log_lines = []
        log_lines.append(f"--- Uploading to {remote_tmp} on {host} ---")

        app_short = "XXCUST"
        ds_code = ""

        for f in files:
            if not f.filename: continue

            if f.filename == 'config.cfg':
                content_cfg = f.read().decode('utf-8', errors='ignore')
                for line in content_cfg.splitlines():
                    if line.startswith('APP_SHORT='): app_short = line.split('=')[1].strip()
                    elif line.startswith('DS_CODE='): ds_code = line.split('=')[1].strip()
                f.seek(0)

            import tempfile
            temp_dir = tempfile.gettempdir()
            local_temp_path = os.path.join(temp_dir, f.filename)
            f.save(local_temp_path)

            remote_file = f"{remote_tmp}/{f.filename}"
            sftp.put(local_temp_path, remote_file)
            log_lines.append(f"Uploaded: {f.filename}")
            try:
                os.remove(local_temp_path)
            except Exception:
                pass

        profiles_data = load_saved_profiles()
        profiles = profiles_data.get("profiles", {})
        p = profiles.get(target_profile) or db_sessions.get(target_profile)
        if not p:
            return jsonify({"success": False, "error": "Profile not found"})

        db_host = p.get("host",""); port = p.get("port",1521)
        if p.get("service_name"): tgt_jdbc = f"{db_host}:{port}/{p['service_name']}"
        elif p.get("sid"): tgt_jdbc = f"{db_host}:{port}:{p['sid']}"
        else: tgt_jdbc = f"{db_host}:{port}"

        apps_pass = p.get("password", "apps")

        def run(cmd, timeout=180):
            full_cmd = f"[ -f ~/.bash_profile ] && . ~/.bash_profile >/dev/null 2>&1; [ -f ~/.profile ] && . ~/.profile >/dev/null 2>&1; {cmd}"
            log_lines.append(f"$ {cmd.replace(apps_pass, '***')}")
            stdin, stdout, stderr = ssh.exec_command(full_cmd, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            if out: log_lines.extend(out.splitlines())
            if err: log_lines.extend([f"[ERR] {l}" for l in err.splitlines()])

        run(f"cd {remote_tmp} && for f in CON_*.ldt; do [ -e \"$f\" ] || continue; "
            f"$FND_TOP/bin/FNDLOAD apps/{apps_pass} 0 Y UPLOAD $FND_TOP/patch/115/import/afcpprog.lct \"$f\" UPLOAD_MODE=REPLACE CUSTOM_MODE=FORCE; "
            f"done")

        run(f"cd {remote_tmp} && for f in XML_*.ldt; do [ -e \"$f\" ] || continue; "
            f"$FND_TOP/bin/FNDLOAD apps/{apps_pass} 0 Y UPLOAD $XDO_TOP/patch/115/import/xdotmpl.lct \"$f\"; "
            f"done")

        run(f"cd {remote_tmp} && for f in TEMPLATE_SOURCE_*.rtf; do [ -e \"$f\" ] || continue; "
            f"java oracle.apps.xdo.oa.util.XDOLoader UPLOAD "
            f"-DB_USERNAME apps -DB_PASSWORD {apps_pass} "
            f"-JDBC_CONNECTION '{tgt_jdbc}' "
            f"-LOB_TYPE TEMPLATE_SOURCE -APPS_SHORT_NAME {app_short} "
            f"-LOB_CODE {ds_code} -LANGUAGE en -TERRITORY TH -XDO_FILE_TYPE RTF -FILE_CONTENT_TYPE 'application/rtf' "
            f"-FILE_NAME \"$f\" -CUSTOM_MODE FORCE; "
            f"done", timeout=300)

        run(f"cd {remote_tmp} && for f in GROUP_*.ldt; do [ -e \"$f\" ] || continue; "
            f"$FND_TOP/bin/FNDLOAD apps/{apps_pass} 0 Y UPLOAD $FND_TOP/patch/115/import/afcpreqg.lct \"$f\" UPLOAD_MODE=REPLACE CUSTOM_MODE=FORCE; "
            f"done")

        ssh.exec_command(f"rm -rf {remote_tmp}")
        sftp.close()
        ssh.close()

        return jsonify({"success": True, "log": log_lines})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


if __name__ == "__main__":

    main()

