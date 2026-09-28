import urllib.request
import json

url = "http://127.0.0.1:5055/api/plsql/source?name=PTPO_RECEIPT_FORM_NEW_PKG&type=PACKAGE%20BODY&session_key=APPS@TTS_PROD_8000_19C_1521"
try:
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read().decode('utf-8'))
        if "code" in data:
            with open("PTPO_RECEIPT_FORM_NEW_PKG_body_clean.pls", "w", encoding="utf-8") as f:
                f.write(data["code"])
            print("Successfully saved PTPO_RECEIPT_FORM_NEW_PKG_body_clean.pls")
        else:
            print("Failed, no code in response:", data)
except Exception as e:
    print("Error:", e)
