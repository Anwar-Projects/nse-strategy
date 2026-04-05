#!/usr/bin/env python3
"""
Fetch IEOD attachments from Gmail using IMAP
Searches for Global Datafeeds emails and downloads attachments
"""
import imaplib
import email
from email.header import decode_header
from pathlib import Path
import os
import re
from datetime import datetime, timedelta

# Configuration
IMAP_SERVER = "imap.gmail.com"
EMAIL_ACCOUNT = "annu19@gmail.com"
EMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
DOWNLOAD_DIR = Path("/root/nse_strategy/incoming")
CSV_DIR = Path("/root/nse_strategy")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

def clean_filename(filename):
    """Clean filename"""
    if isinstance(filename, bytes):
        filename = filename.decode()
    return re.sub(r'[^\w\-\.]', '_', filename)

def fetch_attachments():
    """Fetch IEOD attachments from Gmail"""
    print(f"[{datetime.now()}] Fetching from {EMAIL_ACCOUNT}...")
    
    if not EMAIL_PASSWORD:
        print("ERROR: Set GMAIL_APP_PASSWORD environment variable")
        print("Generate at: https://myaccount.google.com/apppasswords")
        return 0
    
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("\"Data Feed\"")
        
        # Search last 60 days for Global Datafeeds emails
        date_since = (datetime.now() - timedelta(days=60)).strftime("%d-%b-%Y")
        search = f'(SINCE "{date_since}")'
        _, data = mail.search(None, search)
        
        email_ids = data[0].split()
        print(f"Found {len(email_ids)} emails")
        
        downloaded = 0
        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            msg = email.message_from_bytes(msg_data[0][1])
            
            subject = ""
            if msg["Subject"]:
                decoded = decode_header(msg["Subject"])[0]
                subject = decoded[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(decoded[1] or 'utf-8')
            
            print(f"\nEmail: {subject[:50]}...")
            
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                if part.get('Content-Disposition') is None:
                    continue
                
                filename = part.get_filename()
                if filename:
                    filename = clean_filename(filename)
                    
                    if 'GFDLCM' in filename or 'STOCK' in filename:
                        # Save to incoming for ZIP, CSV_DIR for CSV
                        if filename.endswith('.zip'):
                            filepath = DOWNLOAD_DIR / filename
                        else:
                            filepath = CSV_DIR / filename
                        
                        if filepath.exists():
                            print(f"  Already have: {filename}")
                            continue
                        
                        with open(filepath, 'wb') as f:
                            f.write(part.get_payload(decode=True))
                        
                        print(f"  Downloaded: {filename}")
                        downloaded += 1
        
        mail.logout()
        print(f"\n[{datetime.now()}] Downloaded {downloaded} new files")
        return downloaded
        
    except Exception as e:
        print(f"ERROR: {e}")
        return 0

if __name__ == "__main__":
    fetch_attachments()
