import re

try:
    with open(r'D:\WORK\WORK\PAO\PL GL Before Interface with HIS (Custom)\XML\XXPLGL003 - after fix.xml', 'r', encoding='utf-8') as f:
        content = f.read()
    
    match = re.search(r'<sqlStatement name="Q_MAIN"[^>]*>(.*?)</sqlStatement>', content, re.DOTALL)
    if match:
        print("FOUND Q_MAIN")
        print(match.group(1).strip()[:1000])  # Print first 1000 chars
        print("...")
        print(match.group(1).strip()[-1000:]) # Print last 1000 chars
    else:
        print("Q_MAIN not found")
except Exception as e:
    print("Error:", e)
