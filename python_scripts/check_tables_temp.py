import win32com.client
import os

old_path = r'C:\Users\MBx13\.gemini\antigravity\scratch\old.rtf'
new_path = r'C:\Users\MBx13\.gemini\antigravity\scratch\new.rtf'

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0 # wdAlertsNone

try:
    doc_new = word.Documents.Open(new_path, ReadOnly=True, ConfirmConversions=False)
    print(f"New doc has {doc_new.Tables.Count} tables.")
    if doc_new.Tables.Count > 0:
        table = doc_new.Tables(1)
        print(f"New Table 1 has {table.Rows.Count} rows and {table.Columns.Count} columns.")
    doc_new.Close(False)
    
    doc_old = word.Documents.Open(old_path, ReadOnly=True, ConfirmConversions=False)
    print(f"\nOld doc has {doc_old.Tables.Count} tables.")
    if doc_old.Tables.Count > 0:
        table = doc_old.Tables(1)
        print(f"Old Table 1 has {table.Rows.Count} rows and {table.Columns.Count} columns.")
    doc_old.Close(False)

except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
