import win32com.client
import os
import shutil

dir_path = r'D:\WORK\WORK\TTS (EASY_Money)\REP - AR (Gold) Output Tax Summary'
old_path = os.path.join(dir_path, 'CLATHAROTSJV_V5_11SEP2023.rtf')
new_path = os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7.rtf')
out_path = os.path.join(dir_path, 'CLA_TH_AROTSJV_New_v7_Updated.rtf')

# Copy new to out and edit out
shutil.copyfile(new_path, out_path)

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0 

try:
    doc_out = word.Documents.Open(out_path)
    t_out = doc_out.Tables(2)
    # Row 3 is the data row. Let's see how many cells it has.
    cells_out = []
    # Using Range.Cells because of merged cells
    start_idx = 12 # Because row 1 has 9 cells, row 2 has 2 cells => total 11 header cells? Let's be sure.
    # Actually, we can get Row 3 cells by iterating through table cells and checking RowIndex
    for i in range(1, t_out.Range.Cells.Count + 1):
        c = t_out.Range.Cells(i)
        if c.RowIndex == 3:
            cells_out.append(c)
    print(f"Row 3 has {len(cells_out)} cells")

    # Add form fields. Word VBA:
    # doc.FormFields.Add(Range=cell.Range, Type=wdFieldFormTextInput)
    def add_ff(cell, default_text, stat_text):
        # We need to preserve cell's existing text if any, but it's empty
        rng = cell.Range
        rng.Collapse(1) # Collapse to start
        ff = doc_out.FormFields.Add(rng, 70) # wdFieldFormTextInput = 70
        ff.TextInput.Default = default_text
        ff.StatusText = stat_text

    # Col 1: for-each
    add_ff(cells_out[0], "F", "<?for-each:G_TAX_INVOICE_NUMBER?>")
    
    # Col 2: Date
    add_ff(cells_out[1], "DATE", "<?TAX_INVOICE_DATE?>")
    
    # Col 3: Invoice Num
    add_ff(cells_out[2], "TAX_INVOICE", "<?TAX_INVOICE_NUMBER?>")
    
    # Col 4: Customer Name
    add_ff(cells_out[3], "CUSTOMER_NAME", "<?CUSTOMER_NAME?>")
    
    # Col 5: Tax ID
    add_ff(cells_out[4], "CUSTOMER_TAX_ID", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?CUSTOMER_TAX_ID?></fo:bidi-override>')
    
    # Col 6: HQ / Branch. We put both.
    add_ff(cells_out[5], "HQ", "<?CP_HQ?>")
    # Add a space between them
    rng = cells_out[5].Range
    rng.Collapse(0) # End
    rng.MoveEnd(4, -1) # wdCharacter=-1. Actually, easier: just add another formfield.
    ff2 = doc_out.FormFields.Add(rng, 70)
    ff2.TextInput.Default = "BRANCH"
    ff2.StatusText = '="<?BRANCH_NUMBER?>"'
    
    # Col 7: Invoice Amount (from Old Col 9)
    add_ff(cells_out[6], "INV_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_INVOICE_AMOUNT;\'999G990D99\'?></fo:bidi-override>')
    
    # Col 8: Blank
    
    # Col 9: Base Amount (from Old Col 7)
    add_ff(cells_out[8], "BASE_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_BASE_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')
    
    # Col 10: Tax Amount (from Old Col 8)
    add_ff(cells_out[9], "TAX_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_TAX_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')
    
    # Col 10 (End for-each)
    rng_end = cells_out[9].Range
    rng_end.Collapse(0) # Collapse to end
    rng_end.MoveEnd(1, -1) # Character, -1 (Move before cell marker)
    ff_end = doc_out.FormFields.Add(rng_end, 70)
    ff_end.TextInput.Default = "E"
    ff_end.StatusText = "<?end for-each?>"

    doc_out.Save()
    doc_out.Close()
    print("Done generating updated RTF.")

except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
