import win32com.client
import os

dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
old_path = os.path.join(dir_path, 'CLATHAROTSJV_V5_11SEP2023.rtf')
new_path = os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7.rtf')

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0 

try:
    doc_old = word.Documents.Open(old_path, ReadOnly=True)
    print(f"Old doc opened. FormFields: {doc_old.FormFields.Count}")
    if doc_old.Tables.Count > 0:
        # Find which table has the details
        for i, t in enumerate(doc_old.Tables):
            try:
                text = t.Cell(1,1).Range.Text
                print(f"Table {i+1} Cell 1,1: {text.strip()[:20]}")
            except Exception as e:
                print(f"Table {i+1} err: {e}")
    doc_old.Close(False)

    doc_new = word.Documents.Open(new_path, ReadOnly=True)
    print(f"New doc opened. FormFields: {doc_new.FormFields.Count}")
    if doc_new.Tables.Count > 0:
        for i, t in enumerate(doc_new.Tables):
            try:
                text = t.Cell(1,1).Range.Text
                print(f"New Table {i+1} Cell 1,1: {text.strip()[:20]}")
            except Exception as e:
                print(f"New Table {i+1} err: {e}")
    doc_new.Close(False)
except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
