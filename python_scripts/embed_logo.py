import os

with open(r'C:\Users\MBx13\.gemini\antigravity\scratch\logo_small_b64.txt', 'r', encoding='utf-8') as f:
    b64_str = f.read().strip()

with open(r'C:\Users\MBx13\.gemini\antigravity\scratch\app.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = '<img src="/logo.jpg" alt="JNavigator Logo"'
replacement = f'<img src="{b64_str}" alt="JNavigator Logo"'

if target in content:
    content = content.replace(target, replacement)
    with open(r'C:\Users\MBx13\.gemini\antigravity\scratch\app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Successfully embedded logo base64 into app.py!')
else:
    print('Target not found')
