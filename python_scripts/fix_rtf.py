import win32com.client
import os
import shutil

dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
new_path = os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7.rtf')
out_path = os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7_Updated.rtf')

# Overwrite with fresh copy
shutil.copyfile(new_path, out_path)

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0 

try:
    doc_out = word.Documents.Open(out_path)
    t_out = doc_out.Tables(2)
    
    cells_row3 = []
    for i in range(1, t_out.Range.Cells.Count + 1):
        c = t_out.Range.Cells(i)
        if c.RowIndex == 3:
            cells_row3.append(c)

    def add_ff(cell, default_text, stat_text):
        rng = cell.Range
        rng.Collapse(0) # Collapse to end
        rng.MoveEnd(1, -1) # Move before cell marker
        ff = doc_out.FormFields.Add(rng, 70) 
        ff.TextInput.Default = default_text
        ff.StatusText = stat_text

    # --- ROW 3: DATA ---
    # Col 1: for-each AND position()
    add_ff(cells_row3[0], "F", "<?for-each:G_TAX_INVOICE_NUMBER?>")
    add_ff(cells_row3[0], "NO", "<?position()?>")
    
    # We do NOT touch the rest of the RTF here since it's just adding to the existing file... wait!
    # I copied from CLA_TH_AROTSJV_New_v7.rtf which means I have to re-do ALL fields!
