import oracledb
import os

try:
    oracledb.init_oracle_client()
    conn = oracledb.connect(user="apps", password="pytapps", host="10.22.252.217", port=1531, service_name="TST")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT l.file_name, l.file_data
        FROM xdo_lobs l
        WHERE l.lob_code = 'XXPLGL003'
          AND l.lob_type IN ('TEMPLATE', 'TEMPLATE_SOURCE')
          AND l.file_name LIKE '%.rtf'
    """)
    for row in cursor:
        file_name = row[0]
        file_data = row[1]
        
        blob_data = file_data.read()
        
        output_dir = r"D:\WORK\WORK\PAO\PL GL Before Interface with HIS (Custom)"
        output_path = os.path.join(output_dir, file_name)
        
        with open(output_path, "wb") as f:
            f.write(blob_data)
            
        print(f"Successfully downloaded {file_name} to {output_path}")
        
    cursor.close()
    conn.close()
except Exception as e:
    print("Error:", e)
