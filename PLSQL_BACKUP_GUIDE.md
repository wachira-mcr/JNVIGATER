# PL/SQL Backup → Git (MCR_BACKUP)

วิธีเดียวกับที่ JNavigator ทำอัตโนมัติตอนกด Compile (`_backup_old_source`, `_backup_rel_dir` ใน `app.py`)
ใช้เมื่อ compile ด้วยเครื่องมืออื่น (AI, SQL*Plus, Toad, PL/SQL Developer ฯลฯ)

> 🔒 **กฎเหล็ก:** backup โค้ดเดิมก่อน compile ทุกครั้ง — backup ไม่สำเร็จ = **ห้าม compile**
> 🚫 AI ห้าม compile ลง PROD (alias มี `PROD` หรือ `8000` ที่ไม่ใช่ DEV)

## Repo

| | |
|---|---|
| GitHub | https://github.com/wachira-mcr/MCR_BACKUP (branch `main`) |
| Local clone | `D:\WORK\EBS_Git_Repo` ← ใช้ที่นี่ที่เดียว (ห้ามมีหลาย clone เดี๋ยว push ชน) |
| Commit identity | `JNavigator Auto <auto@jnavigator.local>` |

ครั้งแรกในเครื่องใหม่:
```bash
git clone https://github.com/wachira-mcr/MCR_BACKUP D:/WORK/EBS_Git_Repo
```

## โครงสร้างโฟลเดอร์

```
<ALIAS>\
  <ชื่อ Concurrent Program>\        ← ถ้า object เป็น executable ของ concurrent
    <OBJECT>.sql                     ← โค้ดล่าสุด (หลัง compile)
    _before_compile\
      <OBJECT>_<TYPE>_<yyyyMMdd_HHmmss>.sql   ← โค้ดเดิม (ก่อน compile)
  <TYPE>\                            ← ถ้าไม่ใช่ concurrent เช่น PACKAGE_BODY, PACKAGE, PROCEDURE
    ...เหมือนกัน
```

- `<ALIAS>` = TNS alias เช่น `PYT_UAT_8004_DEV`
- `<TYPE>` = object type แทนช่องว่างด้วย `_` เช่น `PACKAGE BODY` → `PACKAGE_BODY`
- ชื่อ concurrent: ตัวอักษร `\ / : * ? " < > |` แทนด้วย `_`
- ถ้า 1 package มีหลาย concurrent → เอาตัวที่ enabled ก่อน

หาชื่อ concurrent:
```sql
SELECT pt.user_concurrent_program_name
FROM apps.fnd_executables e
JOIN apps.fnd_concurrent_programs p
  ON p.executable_id = e.executable_id AND p.executable_application_id = e.application_id
JOIN apps.fnd_concurrent_programs_tl pt
  ON pt.concurrent_program_id = p.concurrent_program_id AND pt.application_id = p.application_id AND pt.language = 'US'
WHERE UPPER(e.execution_file_name) = :obj OR UPPER(e.execution_file_name) LIKE :obj || '.%'
ORDER BY p.enabled_flag DESC, p.concurrent_program_id;
```

## ขั้นตอน (ทุกครั้ง)

**ก่อน compile**
1. ดึงโค้ดเดิม: `SELECT text FROM dba_source WHERE owner=:o AND name=:n AND type=:t ORDER BY line`
   - package → backup ทั้ง `PACKAGE` และ `PACKAGE BODY` (เฉพาะตัวที่จะ compile)
   - ไม่มีแถว = object ใหม่ → แจ้ง "ไม่มี source เดิม" แล้วข้ามได้
2. เขียนไฟล์ `_before_compile\<OBJECT>_<TYPE>_<ts>.sql` = `CREATE OR REPLACE ` + source + `\n/\n` (รันกลับได้ทันที)
   - **ห้ามเขียนทับ/ลบไฟล์ backup เก่า**
3. ตรวจไฟล์มีจริง ไม่ว่าง
4. push:
   ```bash
   cd D:/WORK/EBS_Git_Repo
   git pull --rebase --autostash
   git add "<ALIAS>/<FOLDER>/_before_compile/<FILE>.sql"
   git -c user.name="JNavigator Auto" -c user.email=auto@jnavigator.local commit -m "Backup <ALIAS> <OBJECT> <TYPE> before compile <yyyy-MM-dd HH:mm:ss>"
   git push
   ```
   - commit/push ไม่ผ่าน → **แจ้งผู้ใช้ก่อน compile** (ไฟล์ local ต้องยังอยู่)
5. แจ้ง path + commit hash ให้ผู้ใช้

**หลัง compile**
6. เช็ค `user_errors` / `dba_errors` รายงานผลจริง
7. เขียนโค้ดใหม่ทับ `<ALIAS>\<FOLDER>\<OBJECT>.sql` แล้ว commit `"Auto-compile: <TYPE> <OBJECT> on <ALIAS> at <ts>"` + push

## Script (Python)

ใช้ `db.py` ในโปรเจกต์นี้ — รันก่อน compile:
```python
import sys, os, re, subprocess, datetime
sys.path.insert(0, r"C:\Users\MBx13\.gemini\antigravity\scratch")
import db

REPO = r"D:\WORK\EBS_Git_Repo"

def backup_before_compile(alias, obj, obj_type, owner="APPS"):
    obj, obj_type = obj.upper(), obj_type.upper()
    cur = db.connect(alias).cursor()
    cur.execute("SELECT text FROM dba_source WHERE owner=:o AND name=:n AND type=:t ORDER BY line",
                o=owner, n=obj, t=obj_type)
    lines = [r[0] for r in cur.fetchall()]
    if not lines:
        return "ไม่มี source เดิม (object ใหม่)"
    folder = obj_type.replace(" ", "_")
    try:
        cur.execute("""SELECT pt.user_concurrent_program_name
            FROM apps.fnd_executables e
            JOIN apps.fnd_concurrent_programs p ON p.executable_id=e.executable_id AND p.executable_application_id=e.application_id
            JOIN apps.fnd_concurrent_programs_tl pt ON pt.concurrent_program_id=p.concurrent_program_id AND pt.application_id=p.application_id AND pt.language='US'
            WHERE UPPER(e.execution_file_name)=:n OR UPPER(e.execution_file_name) LIKE :n||'.%'
            ORDER BY p.enabled_flag DESC, p.concurrent_program_id""", n=obj)
        row = cur.fetchone()
        if row and row[0]:
            folder = re.sub(r'[\\/:*?"<>|]+', "_", row[0]).strip(" .")
    except Exception:
        pass
    ts = datetime.datetime.now()
    rel = os.path.join(alias, folder, "_before_compile", f"{obj}_{obj_type.replace(' ', '_')}_{ts:%Y%m%d_%H%M%S}.sql")
    path = os.path.join(REPO, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="") as f:   # "x" = ไม่เขียนทับ
        f.write("CREATE OR REPLACE " + "".join(lines).rstrip() + "\n/\n")
    git = lambda *a: subprocess.run(["git", "-c", "user.name=JNavigator Auto", "-c", "user.email=auto@jnavigator.local", *a],
                                    cwd=REPO, capture_output=True, text=True)
    git("pull", "--rebase", "--autostash")
    git("add", rel)
    c = git("commit", "-m", f"Backup {alias} {obj} {obj_type} before compile {ts:%Y-%m-%d %H:%M:%S}")
    p = git("push")
    if c.returncode or p.returncode:
        raise RuntimeError(f"Backup เขียนไฟล์แล้วที่ {path} แต่ commit/push ไม่ผ่าน: {(c.stderr + p.stderr).strip()}")
    return f"{path} (pushed {git('rev-parse', '--short', 'HEAD').stdout.strip()})"

# print(backup_before_compile("PYT_UAT_8004_DEV", "PYT_PO_EDI_PKG", "PACKAGE BODY"))
```
