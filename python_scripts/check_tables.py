import win32com.client
import os

dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
old_path = os.path.join(dir_path, 'CLATHAROTSJV_V5_11SEP2023.rtf')
new_path = os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7.rtf')

word = win32com.client.Dispatch("Word.Application")
word.Visible = False

try:
    doc_new = word.Documents.Open(new_path, ReadOnly=True)
    print(f"New doc has {doc_new.Tables.Count} tables.")
    if doc_new.Tables.Count > 0:
        table = doc_new.Tables(1)
        print(f"Table 1 has {table.Rows.Count} rows and {table.Columns.Count} columns.")
        for r in range(1, min(table.Rows.Count + 1, 5)):
            row_text = []
            for c in range(1, table.Columns.Count + 1):
                try:
                    cell_text = table.Cell(r, c).Range.Text.replace('\r', '').replace('\x07', '').strip()
                    row_text.append(cell_text)
                except:
                    row_text.append("N/A")
            print(f"Row {r}: {row_text}")
    doc_new.Close(False)
    
    doc_old = word.Documents.Open(old_path, ReadOnly=True)
    print(f"\nOld doc has {doc_old.Tables.Count} tables.")
    if doc_old.Tables.Count > 0:
        table = doc_old.Tables(1)
        print(f"Table 1 has {table.Rows.Count} rows and {table.Columns.Count} columns.")
        for r in range(1, min(table.Rows.Count + 1, 5)):
            row_text = []
            for c in range(1, table.Columns.Count + 1):
                try:
                    cell_text = table.Cell(r, c).Range.Text.replace('\r', '').replace('\x07', '').strip()
                    row_text.append(cell_text)
                except:
                    row_text.append("N/A")
            print(f"Row {r}: {row_text}")
    doc_old.Close(False)

except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
