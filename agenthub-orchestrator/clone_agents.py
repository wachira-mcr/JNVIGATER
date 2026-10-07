import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

# Force UTF-8 for stdout
sys.stdout.reconfigure(encoding='utf-8')

SRC_DIR = Path(r"C:\Users\MBx13\AppData\Local\Programs\antigravity")
PROGRAMS_DIR = Path(r"C:\Users\MBx13\AppData\Local\Programs")

workers = [
    {"id": "worker1", "title": "Antigravity Worker 1"},
    {"id": "worker2", "title": "Antigravity Worker 2"},
    {"id": "worker3", "title": "Antigravity Worker 3"},
]

print("[START] Cloning Antigravity...")

for w in workers:
    dest_dir = PROGRAMS_DIR / f"antigravity-{w['id']}"
    exe_name = f"{w['id'].capitalize()}.exe"
    
    if dest_dir.exists():
        print(f"Deleting old {dest_dir}...")
        shutil.rmtree(dest_dir, ignore_errors=True)
        
    print(f"Copying files for {w['title']}...")
    shutil.copytree(SRC_DIR, dest_dir)
    
    print(f"Patching internal config...")
    # Extract ASAR
    asar_path = dest_dir / "resources" / "app.asar"
    subprocess.run(["npx", "asar", "extract", "app.asar", "app"], cwd=dest_dir/"resources", check=True, shell=True)
    
    # Patch package.json
    pkg_path = dest_dir / "resources" / "app" / "package.json"
    with open(pkg_path, "r", encoding="utf-8") as f:
        pkg = json.load(f)
        
    pkg["name"] = f"antigravity-{w['id']}"
    pkg["productName"] = w["title"]
    
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2)
        
    # Repack ASAR
    subprocess.run(["npx", "asar", "pack", "app", "app.asar"], cwd=dest_dir/"resources", check=True, shell=True)
    shutil.rmtree(dest_dir / "resources" / "app", ignore_errors=True)
    
    # Rename EXE
    old_exe = dest_dir / "Antigravity.exe"
    new_exe = dest_dir / exe_name
    if old_exe.exists():
        old_exe.rename(new_exe)
        
    print(f"[OK] Created {w['title']} successfully!\n")

print("[DONE] Successfully cloned all 3 agents.")
