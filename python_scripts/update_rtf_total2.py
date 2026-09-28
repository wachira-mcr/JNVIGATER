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
    cells_row4 = []
    for i in range(1, t_out.Range.Cells.Count + 1):
        c = t_out.Range.Cells(i)
        if c.RowIndex == 3:
            cells_row3.append(c)
        elif c.RowIndex == 4:
            cells_row4.append(c)

    def add_ff(cell, default_text, stat_text):
        rng = cell.Range
        rng.Collapse(0) # Collapse to end
        rng.MoveEnd(1, -1) # Move before cell marker
        ff = doc_out.FormFields.Add(rng, 70) 
        ff.TextInput.Default = default_text
        ff.StatusText = stat_text

    # --- ROW 3: DATA ---
    add_ff(cells_row3[0], "F", "<?for-each:G_TAX_INVOICE_NUMBER?>")
    add_ff(cells_row3[0], "NO", "<?position()?>")
    add_ff(cells_row3[1], "DATE", "<?TAX_INVOICE_DATE?>")
    add_ff(cells_row3[2], "TAX_INVOICE", "<?TAX_INVOICE_NUMBER?>")
    add_ff(cells_row3[3], "CUSTOMER_NAME", "<?CUSTOMER_NAME?>")
    add_ff(cells_row3[4], "CUSTOMER_TAX_ID", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?CUSTOMER_TAX_ID?></fo:bidi-override>')
    add_ff(cells_row3[5], "HQ", "<?CP_HQ?>")
    add_ff(cells_row3[5], "BRANCH", '="<?BRANCH_NUMBER?>"')
    add_ff(cells_row3[6], "c", "<?if:CF_SUMMARY_LINE_SHOW='Y'?>")
    add_ff(cells_row3[6], "INV_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_INVOICE_AMOUNT;\'999G990D99\'?></fo:bidi-override>')
    add_ff(cells_row3[6], "e", "<?end if?>")
    add_ff(cells_row3[8], "BASE_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_BASE_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')
    add_ff(cells_row3[9], "TAX_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CF_TAX_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')
    add_ff(cells_row3[9], "E", "<?end for-each?>")

    # --- ROW 4: TOTAL ---
    add_ff(cells_row4[6], "csummary", "<?if:CP_SHOW_SUMMARY='Y'?>")
    add_ff(cells_row4[6], "T_INV_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CS_INVOICE_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')
    add_ff(cells_row4[6], "endif", "<?end if?>")
    add_ff(cells_row4[8], "T_BASE_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CS_BASE_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')
    add_ff(cells_row4[9], "T_VAT_AMT", '<fo:bidi-override direction="ltr" unicode-bidi="bidi-override"><?format-number:CS_VAT_AMOUNT_DISP;\'999G990D99\'?></fo:bidi-override>')

    doc_out.Save()
    doc_out.Close()
    print("RTF update complete.")
except Exception as e:
    print(f"Error: {e}")
finally:
    word.Quit()
