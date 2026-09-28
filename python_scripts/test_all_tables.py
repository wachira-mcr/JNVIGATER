import win32com.client
import os

dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
old_path = os.path.join(dir_path, 'CLATHAROTSJV_V5_11SEP2023.rtf')

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0 

try:
    doc_old = word.Documents.Open(old_path, ReadOnly=True)
    print(f"Old doc tables: {doc_old.Tables.Count}")
    for i in range(1, doc_old.Tables.Count + 1):
        t = doc_old.Tables(i)
        print(f"Table {i} has {t.Range.Cells.Count} cells.")
        # print first few cells
        for c in range(1, min(t.Range.Cells.Count + 1, 15)):
            try:
                cell = t.Range.Cells(c)
                text = cell.Range.Text.replace('\r','').replace('\x07','')[:15].strip()
                print(f"  Cell {c} (Row {cell.RowIndex}, Col {cell.ColumnIndex}): {text}")
            except:
                pass
    doc_old.Close(False)

except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
