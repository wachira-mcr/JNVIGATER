import win32com.client
import os

dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
old_path = os.path.join(dir_path, 'CLATHAROTSJV_V5_11SEP2023.rtf')
new_path = os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7.rtf')

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0 

def print_table(table, name):
    print(f"\n--- {name} ---")
    cells = table.Range.Cells
    for i in range(1, cells.Count + 1):
        try:
            c = cells(i)
            text = c.Range.Text.replace('\r','').replace('\x07','')[:15].strip()
            print(f"Cell {i} (Row {c.RowIndex}, Col {c.ColumnIndex}): {text}")
        except Exception as e:
            pass

try:
    doc_old = word.Documents.Open(old_path, ReadOnly=True)
    print_table(doc_old.Tables(2), "Old Table 2")
    doc_old.Close(False)

    doc_new = word.Documents.Open(new_path, ReadOnly=True)
    print_table(doc_new.Tables(2), "New Table 2")
    doc_new.Close(False)
except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
