import urllib.request
import json
import gzip

url = "http://127.0.0.1:5055/api/query"
sql = """
SELECT r.request_id, r.phase_code, r.status_code, p.user_concurrent_program_name, p.concurrent_program_name
FROM fnd_concurrent_requests r, fnd_concurrent_programs_vl p
WHERE r.concurrent_program_id = p.concurrent_program_id
AND r.request_id = 17004853
"""
data = {"query": sql}
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
