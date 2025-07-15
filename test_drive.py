import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

GOOGLE_CREDENTIALS_PATH = 'credentials.json'  # Update if your service account file is named differently
DRIVE_FOLDER_ID = "0ALWlt6PDMm9zUk9PVA" # Set this to your folder ID or None

TEST_FILE = 'instagram_video_20250715_143359.mp4'

# Create a small test file
def create_test_file():
    with open(TEST_FILE, 'w') as f:
        f.write('This is a test upload to Google Drive.')

def upload_to_drive(file_path, filename):
    scopes = ['https://www.googleapis.com/auth/drive']
    credentials = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=scopes)
    drive_service = build('drive', 'v3', credentials=credentials)
    file_metadata = {'name': filename}
    if DRIVE_FOLDER_ID:
        file_metadata['parents'] = [DRIVE_FOLDER_ID]
    media = MediaFileUpload(file_path, resumable=True)
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id',
        supportsAllDrives=True
    ).execute()
    file_id = file.get('id')
    print(f'Uploaded file ID: {file_id}')
    # Make file public
    permission = {'type': 'anyone', 'role': 'reader'}
    drive_service.permissions().create(
        fileId=file_id,
        body=permission,
        supportsAllDrives=True
    ).execute()
    print(f'File {file_id} is now public.')
    public_url = f'https://drive.google.com/uc?id={file_id}&export=download'
    print(f'Public URL: {public_url}')
    return file_id, public_url

if __name__ == '__main__':
    create_test_file()
    upload_to_drive(TEST_FILE, TEST_FILE) 