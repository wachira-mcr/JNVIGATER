import re
import os

def extract_all_tags(doc_path):
    print(f"\n--- All Tags for {os.path.basename(doc_path)} ---")
    with open(doc_path, 'r', encoding='latin-1') as f:
        content = f.read()
    content_no_nl = content.replace('\n', '').replace('\r', '')
    
    tags = re.findall(r'<\?.*?\?>', content_no_nl)
    # Also find any form fields
    fields = re.findall(r'\{\\\*\\formfield(.*?)\}\}\}', content_no_nl)
    print(f"Total form fields found: {len(fields)}")
    for t in tags:
        print(f"Tag: {t}")

if __name__ == '__main__':
    dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
    extract_all_tags(os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7.rtf'))
