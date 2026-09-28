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
    
    for c in range(1, 13):
        try:
            cell = t.Range.Cells(c)
            ffs = cell.Range.FormFields
            print(f"Col {c}: {ffs.Count} fields")
            for i in range(1, ffs.Count + 1):
                f = ffs(i)
                help_text = getattr(f, 'HelpText', '')
                status_text = getattr(f, 'StatusText', '')
                print(f"  Field {i}: Name='{f.Name}', Def='{f.TextInput.Default}', Help='{help_text}', Stat='{status_text}'")
        except Exception as e:
            pass
    doc_old.Close(False)

except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
