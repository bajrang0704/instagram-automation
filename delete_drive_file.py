import sys
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

GOOGLE_CREDENTIALS_PATH = 'credentials.json'  # Path to your service account file

def delete_drive_file(file_id):
    scopes = ['https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=scopes)
    drive_service = build('drive', 'v3', credentials=credentials)
    try:
        drive_service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        print(f"Successfully deleted file from Google Drive: {file_id}")
    except Exception as e:
        print(f"Error deleting file from Google Drive: {e}")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python delete_drive_file.py <file_id>")
        sys.exit(1)
    file_id = sys.argv[1]
    delete_drive_file(file_id) 