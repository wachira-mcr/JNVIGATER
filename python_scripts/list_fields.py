import win32com.client
import os
import sys

def print_fields(doc_path):
    print(f"\n--- Fields for {os.path.basename(doc_path)} ---")
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    doc = None
    try:
        doc = word.Documents.Open(doc_path, ReadOnly=True)
        print(f"Total fields: {doc.FormFields.Count}")
        for i, fld in enumerate(doc.FormFields):
            help_text = getattr(fld, 'HelpText', '')
            status_text = getattr(fld, 'StatusText', '')
            print(f"Field {i+1}: Name='{fld.Name}', Result='{fld.Result}', HelpText='{help_text}', StatusText='{status_text}'")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if doc:
            doc.Close(False)

if __name__ == '__main__':
    dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
    print_fields(os.path.join(dir_path, 'CLATHAROTSJV_V5_11SEP2023.rtf'))
    print_fields(os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7.rtf'))
