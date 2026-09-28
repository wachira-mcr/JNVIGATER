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
    cells_out = []
    for i in range(1, t_out.Range.Cells.Count + 1):
        c = t_out.Range.Cells(i)
        if c.RowIndex == 3:
            cells_out.append(c)

    def add_ff(cell, default_text, stat_text):
        rng = cell.Range
        rng.Collapse(0) # Collapse to end
        rng.MoveEnd(1, -1) # Move before cell marker
        ff = doc_out.FormFields.Add(rng, 70) 
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
    
    # Col 6: HQ / Branch
    add_ff(cells_out[5], "HQ", "<?CP_HQ?>")
    add_ff(cells_out[5], "BRANCH", '="<?BRANCH_NUMBER?>"')
    
    # Col 7: Invoice Amount (from Old Col 9) - With IF conditions
    add_ff(cells_out[6], "c", "<?if:CF_SUMMARY_LINE_SHOW='Y'?>")
    add_ff(cells_out[6], "INV_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_INVOICE_AMOUNT;\'999G990D99\'?></fo:bidi-override>')
    add_ff(cells_out[6], "e", "<?end if?>")
    
    # Col 8: Blank
    
    # Col 9: Base Amount (from Old Col 7)
    add_ff(cells_out[8], "BASE_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_BASE_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')
    
    # Col 10: Tax Amount (from Old Col 8)
    add_ff(cells_out[9], "TAX_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_TAX_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')
    
    # Col 10: End for-each
    add_ff(cells_out[9], "E", "<?end for-each?>")

    doc_out.Save()
    doc_out.Close()
    print("Done generating fully updated RTF.")

except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
