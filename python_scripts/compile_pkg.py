import urllib.request
import json
import gzip

url = "http://127.0.0.1:5055/api/plsql/compile"

with open("PTPO_RECEIPT_FORM_NEW_PKG_body_clean.pls", "r", encoding="utf-8") as f:
    file_content = f.read()

try:
    data_json = json.loads(file_content)
    sql_text = data_json.get("code", "")
except:
    sql_text = file_content

if "CREATE OR REPLACE PACKAGE BODY PTPO_RECEIPT_FORM_NEW_PKG\nPACKAGE BODY" in sql_text:
    sql_text = sql_text.replace("CREATE OR REPLACE PACKAGE BODY PTPO_RECEIPT_FORM_NEW_PKG\nPACKAGE BODY", "CREATE OR REPLACE PACKAGE BODY")

if not sql_text.strip().upper().startswith("CREATE"):
    sql_text = "CREATE OR REPLACE " + sql_text

data = {
    "code": sql_text,
    "name": "PTPO_RECEIPT_FORM_NEW_PKG",
    "type": "PACKAGE BODY",
    "session_key": "APPS@TTS_PROD_8000_19C_1521"
}

req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json', 'Accept-Encoding': 'gzip, deflate'})
try:
    with urllib.request.urlopen(req) as response:
        content = response.read()
        if response.info().get('Content-Encoding') == 'gzip':
            result = gzip.decompress(content).decode('utf-8')
        else:
            result = content.decode('utf-8')
        print(result)
except Exception as e:
    print("Error:", e)
