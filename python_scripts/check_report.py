import app
import json

try:
    conn = app.get_db_connection("APPS@TTS_INET_PRE_8020")
    cursor = conn.cursor()
    
    # Check if there is an XML Publisher data definition
    cursor.execute("""
        SELECT data_source_code, data_source_status 
        FROM xdo_ds_definitions_b 
        WHERE data_source_code LIKE '%PT_AR_VOUCHER%'
    """)
    rows = cursor.fetchall()
    print("XDO_DS_DEFINITIONS:", rows)
    
    # Check for any package containing PT_AR_VOUCHER
    cursor.execute("""
        SELECT distinct name, type 
        FROM all_source 
        WHERE upper(text) LIKE '%PT_AR_VOUCHER%'
    """)
    rows = cursor.fetchall()
    print("ALL_SOURCE Matches:", rows)
    
    # Check if there's a package with this exact name
    cursor.execute("""
        SELECT object_name, object_type 
        FROM all_objects 
        WHERE object_name LIKE 'PT_AR_VOUCHER%'
    """)
    rows = cursor.fetchall()
    print("ALL_OBJECTS:", rows)
    
except Exception as e:
    print("Error:", e)
