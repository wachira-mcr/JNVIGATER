import win32com.client
import os

dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
old_path = os.path.join(dir_path, 'CLATHAROTSJV_V5_11SEP2023.rtf')

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0 

try:
    doc_old = word.Documents.Open(old_path, ReadOnly=True)
    t = doc_old.Tables(3)
    
    print(f"Old Table 3 has {t.Rows.Count} rows.")
    for r in range(1, t.Rows.Count + 1):
        print(f"\n--- Row {r} ---")
        try:
            row = t.Rows(r)
            for c_idx, cell in enumerate(row.Cells):
                ffs = cell.Range.FormFields
                ff_info = []
                for i in range(1, ffs.Count + 1):
                    f = ffs(i)
                    ff_info.append(f"[{f.TextInput.Default} | {getattr(f, 'StatusText', '')}]")
                text = cell.Range.Text.replace('\r','').replace('\x07','')[:15].strip()
                print(f"Col {c_idx+1}: Text='{text}', FFs={ff_info}")
        except Exception as e:
            print(f"Row error: {e}")
            # fallback to iterate all cells and filter by row
            for i in range(1, t.Range.Cells.Count + 1):
                cell = t.Range.Cells(i)
                if cell.RowIndex == r:
                    ffs = cell.Range.FormFields
                    ff_info = []
                    for k in range(1, ffs.Count + 1):
                        f = ffs(k)
                        ff_info.append(f"[{f.TextInput.Default} | {getattr(f, 'StatusText', '')}]")
                    text = cell.Range.Text.replace('\r','').replace('\x07','')[:15].strip()
                    print(f"Cell Col {cell.ColumnIndex}: Text='{text}', FFs={ff_info}")

    doc_old.Close(False)

except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
