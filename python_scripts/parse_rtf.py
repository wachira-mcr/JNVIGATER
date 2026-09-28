import re
import os

def extract_tags(doc_path):
    print(f"\n--- Tags for {os.path.basename(doc_path)} ---")
    with open(doc_path, 'r', encoding='latin-1') as f:
        content = f.read()
    # Remove newlines to make regex easier for RTF splits if they exist
    content_no_nl = content.replace('\n', '').replace('\r', '')
    
    # Also we can look for {\*\ffstattext ... } or {\*\ffdeftext ... } to get form fields
    fields = re.findall(r'{\\\*\\ffname (.*?)}.*?{\\\*\\ffdeftext (.*?)}.*?{\\\*\\ffstattext (.*?)}', content_no_nl)
    if fields:
        for f in fields:
            print(f"Field: Name={f[0]}, Def={f[1]}, Stat={f[2]}")
    else:
        # Fallback to just finding <?...?>
        tags = re.findall(r'<\?.*?\?>', content_no_nl)
        for t in tags:
            print(f"Tag: {t}")

if __name__ == '__main__':
    dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
    extract_tags(os.path.join(dir_path, 'CLATHAROTSJV_V5_11SEP2023.rtf'))
    extract_tags(os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7.rtf'))
