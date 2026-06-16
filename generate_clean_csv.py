"""
generate_clean_csv.py
─────────────────────
Reads the QA Test Log from Google Sheets, generates tex_clean.csv
and appends to tex_master.csv for historical trend data.
Uploads both to Google Drive with public read access.
"""

import csv
import os
import pickle
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

SPREADSHEET_ID  = "1x0WAJ_1v5eTamaFSNgkkrNYurxEQTfbU3TdWkqCmXw0"
QA_LOG_TAB      = "🧪 QA Test Log"
DRIVE_FOLDER_ID = "1RcWyUsG3FrEkSpLkeqkVZsWVPpF6vUOy"
TOKEN_FILE      = "token.pickle"
CLEAN_FILE      = "tex_clean.csv"
MASTER_FILE     = "tex_master.csv"

def get_services():
    with open(TOKEN_FILE, "rb") as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    sheets = build("sheets", "v4", credentials=creds)
    drive  = build("drive",  "v3", credentials=creds)
    return sheets, drive

def read_sheet(sheets):
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{QA_LOG_TAB}'!A1:M500",
    ).execute()
    rows = result.get("values", [])
    if not rows:
        print("⚠️  No data found in QA Test Log")
        return []

    header_idx = 0
    for i, row in enumerate(rows[:5]):
        if len(row) > 5:
            header_idx = i
            break

    headers = [h.strip().lower() for h in rows[header_idx]]
    data_rows = rows[header_idx + 1:]

    feedback_idx = next((i for i, h in enumerate(headers) if 'feedback' in h and 'request' in h), None)
    status_idx   = next((i for i, h in enumerate(headers) if 'pass' in h or h == 'status'), None)
    date_idx     = next((i for i, h in enumerate(headers) if h == 'date'), None)

    if feedback_idx is None or status_idx is None:
        print(f"❌ Could not find required columns. Headers: {headers[:10]}")
        return []

    records = []
    for row in data_rows:
        while len(row) <= max(feedback_idx, status_idx):
            row.append("")
        feedback = row[feedback_idx].strip()
        status   = row[status_idx].strip()
        date     = row[date_idx].strip() if date_idx and len(row) > date_idx else ""
        if feedback and status in ("PASS", "FAIL", "Pass", "Fail"):
            records.append({
                "feedback": feedback,
                "status":   status.upper(),
                "date":     date,
            })
    return records

def write_clean_csv(records):
    with open(CLEAN_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Feedback", "Status"])
        for r in records:
            writer.writerow([r["feedback"], r["status"]])
    print(f"✅ {CLEAN_FILE}: {len(records)} rows")

def update_master_csv(records):
    """Append new records to master CSV, avoiding duplicates by date+feedback+status"""
    existing = set()
    if os.path.exists(MASTER_FILE):
        with open(MASTER_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing.add((row.get("Date",""), row.get("Feedback",""), row.get("Status","")))

    new_records = []
    for r in records:
        key = (r["date"], r["feedback"], r["status"])
        if key not in existing:
            new_records.append(r)
            existing.add(key)

    mode = "a" if os.path.exists(MASTER_FILE) else "w"
    with open(MASTER_FILE, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Feedback", "Status"])
        if mode == "w":
            writer.writeheader()
        for r in new_records:
            writer.writerow({"Date": r["date"], "Feedback": r["feedback"], "Status": r["status"]})

    print(f"✅ {MASTER_FILE}: {len(new_records)} new records added")

def get_or_create_file(drive, filename, folder_id):
    """Get existing file ID or return None"""
    existing = drive.files().list(
        q=f"name='{filename}' and '{folder_id}' in parents and trashed=false",
        fields="files(id)"
    ).execute().get("files", [])
    return existing[0]["id"] if existing else None

def upload_file(drive, file_path, folder_id):
    filename  = os.path.basename(file_path)
    media     = MediaFileUpload(file_path, mimetype="text/csv")
    file_id   = get_or_create_file(drive, filename, folder_id)

    if file_id:
        drive.files().update(fileId=file_id, media_body=media).execute()
        print(f"  Updated {filename}")
    else:
        meta    = {"name": filename, "parents": [folder_id]}
        result  = drive.files().create(body=meta, media_body=media, fields="id").execute()
        file_id = result["id"]
        print(f"  Created {filename}")

    # Always ensure public read access
    try:
        drive.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"}
        ).execute()
    except:
        pass

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    print(f"  📄 {url}")
    return file_id, url

if __name__ == "__main__":
    print("🔐 Connecting to Google...")
    sheets, drive = get_services()
    print("✅ Connected")

    print("\n📋 Reading QA Test Log...")
    records = read_sheet(sheets)

    if not records:
        print("❌ No data to process")
        exit(1)

    print(f"  Found {len(records)} records")

    write_clean_csv(records)
    update_master_csv(records)

    print("\n📤 Uploading to Drive...")
    clean_id,  clean_url  = upload_file(drive, CLEAN_FILE,  DRIVE_FOLDER_ID)
    master_id, master_url = upload_file(drive, MASTER_FILE, DRIVE_FOLDER_ID)

    # Save file IDs for dashboard to use
    with open("drive_file_ids.txt", "w") as f:
        f.write(f"CLEAN_FILE_ID={clean_id}\n")
        f.write(f"MASTER_FILE_ID={master_id}\n")
        f.write(f"CLEAN_URL={clean_url}\n")
        f.write(f"MASTER_URL={master_url}\n")

    print(f"\n✅ Done")
    print(f"  tex_clean.csv:  {clean_url}")
    print(f"  tex_master.csv: {master_url}")

    if os.path.exists(CLEAN_FILE):  os.remove(CLEAN_FILE)
    if os.path.exists(MASTER_FILE): os.remove(MASTER_FILE)
