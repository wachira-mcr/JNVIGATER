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
    t_old = doc_old.Tables(2)
    print(f"Old Table 2: {t_old.Rows.Count} rows")
    for r in range(1, min(t_old.Rows.Count+1, 4)):
        try:
            row = t_old.Rows(r)
            text = [cell.Range.Text.replace('\r','').replace('\x07','')[:15].strip() for cell in row.Cells]
            print(f"Old Row {r} ({row.Cells.Count} cells): {text}")
        except Exception as e:
            print(f"Old Row {r} err: {e}")
            
    doc_new = word.Documents.Open(new_path, ReadOnly=True)
    t_new = doc_new.Tables(2)
    print(f"\nNew Table 2: {t_new.Rows.Count} rows")
    for r in range(1, min(t_new.Rows.Count+1, 4)):
        try:
            row = t_new.Rows(r)
            text = [cell.Range.Text.replace('\r','').replace('\x07','')[:15].strip() for cell in row.Cells]
            print(f"New Row {r} ({row.Cells.Count} cells): {text}")
        except Exception as e:
            print(f"New Row {r} err: {e}")
            
    doc_old.Close(False)
    doc_new.Close(False)
except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
