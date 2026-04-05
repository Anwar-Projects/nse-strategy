#!/usr/bin/env python3
"""
Fetch IEOD data attachments from Gmail
Searches for Global Datafeeds emails and downloads ZIP/CSV attachments
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
EMAIL_ACCOUNT = "annu19@gmail.com"  # Update with your email
EMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")  # Set app password
DOWNLOAD_DIR = Path("/root/nse_strategy/incoming")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

def clean_filename(filename):
    """Clean up filename"""
    if isinstance(filename, bytes):
        filename = filename.decode()
    return re.sub(r'[^\w\-\.]', '_', filename)

def fetch_ieod_attachments():
    """Fetch IEOD attachments from Gmail"""
    print(f"Fetching IEOD data from {EMAIL_ACCOUNT}...")
    
    if not EMAIL_PASSWORD:
        print("ERROR: Set GMAIL_APP_PASSWORD environment variable")
        print("Generate app password at: https://myaccount.google.com/apppasswords")
        return 0
    
    try:
        # Connect to Gmail IMAP
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("inbox")
        
        # Search for recent emails (last 7 days)
        date_since = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
        
        # Search for Global Datafeeds emails with attachments
        search_criteria = f'(SINCE "{date_since}" FROM "globaldatafeeds")'
        _, data = mail.search(None, search_criteria)
        
        email_ids = data[0].split()
        print(f"Found {len(email_ids)} emails from Global Datafeeds")
        
        downloaded = 0
        
        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # Get subject
            subject = ""
            if msg["Subject"]:
                subject_decoded = decode_header(msg["Subject"])[0]
                subject = subject_decoded[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(subject_decoded[1] or 'utf-8')
            
            print(f"\nEmail: {subject[:50]}...")
            
            # Find attachments
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                if part.get('Content-Disposition') is None:
                    continue
                
                filename = part.get_filename()
                if filename:
                    filename = clean_filename(filename)
                    
                    # Check if it's IEOD data file
                    if 'GFDLCM' in filename or 'STOCK' in filename:
                        filepath = DOWNLOAD_DIR / filename
                        
                        if filepath.exists():
                            print(f"  Already have: {filename}")
                            continue
                        
                        # Download attachment
                        with open(filepath, 'wb') as f:
                            f.write(part.get_payload(decode=True))
                        
                        print(f"  Downloaded: {filename} ({filepath.stat().st_size:,} bytes)")
                        downloaded += 1
        
        mail.logout()
        print(f"\n✓ Downloaded {downloaded} new files")
        return downloaded
        
    except Exception as e:
        print(f"ERROR: {str(e)[:100]}")
        return 0

if __name__ == "__main__":
    fetch_ieod_attachments()
