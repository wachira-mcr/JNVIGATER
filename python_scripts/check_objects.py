import app
import json

try:
    # app.py's get_db_connection does not take arguments and uses active_session_key
    # We will just manually connect using oracledb using the session credentials
    import oracledb
    
    # We know the active session is APPS@TTS_INET_PRE_8020
    # Let's get the credentials from the json file directly or use app's db_sessions
    app.load_saved_profiles()
    info = app.db_sessions.get("APPS@TTS_INET_PRE_8020")
    
    if not info:
        print("Session info not found!")
    else:
        conn = app.create_connection(
            info["host"],
            info["port"],
            info.get("service_name"),
            info.get("sid"),
            info["user"],
            info["password"]
        )
        cursor = conn.cursor()
        
        # Check objects
        cursor.execute("SELECT object_name, object_type FROM all_objects WHERE object_name LIKE 'PT_AR_VOUCHER%'")
        for row in cursor.fetchall():
            print("OBJECT:", row)
            
        cursor.execute("SELECT object_name, object_type FROM all_objects WHERE object_name LIKE 'PT%AR%VOUCHER%'")
        for row in cursor.fetchall():
            print("WIDER OBJECT:", row)
            
except Exception as e:
    print("Error:", e)
