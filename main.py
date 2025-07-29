import os
import json
import random
import gspread
import gspread.exceptions
import pandas as pd
from moviepy.editor import *
from moviepy.config import change_settings
from datetime import datetime, timedelta
import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import schedule
import time
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from config import *
from instagram_api import InstagramAPI
import requests
import io
from video_creator import VideoCreator
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle

# Configure MoviePy to use a different text rendering method
try:
    change_settings({"IMAGEMAGICK_BINARY": "magick"})
except:
    # If ImageMagick is not available, use PIL for text rendering
    pass

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler('instagram_agent.log'),
        logging.StreamHandler()
    ]
)

# --- GOOGLE SHEETS CONFIG ---
SHEET_NAME = 'Instagram quotes'  # The name of your Google Sheet
SHEET_WORKSHEET_INDEX = 0       # 0 for the first sheet
MANAGE_QUOTES_IN_SHEET = False  # Set to False to skip quote deletion/marking (if no edit permissions)

# Add these helper functions near the top (after imports and config):
def get_music_index_from_sheet():
    import gspread, os, tempfile
    import logging

    google_creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if not google_creds_json:
        raise Exception("GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable not set.")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(google_creds_json)
        temp_creds_path = f.name

    try:
        gc = gspread.service_account(filename=temp_creds_path)
        worksheet = gc.open(SHEET_NAME).get_worksheet(SHEET_WORKSHEET_INDEX)
        print("Reading from:", sheet.title, "in spreadsheet:", sheet.spreadsheet.title)

        header_row = worksheet.row_values(1)
        logging.info(f"Columns found: {header_row}")
        if 'music_index' not in header_row:
            raise Exception("'music_index' column not found in sheet")

        col_index = header_row.index('music_index') + 1  # 1-based index
        cell_value = worksheet.cell(2, col_index).value
        logging.info(f"Read music_index value from cell (2, {col_index}): {cell_value}")
        return int(cell_value)

    except Exception as e:
        logging.error(f"Error in get_music_index_from_sheet: {e}")
        return 0
    finally:
        os.unlink(temp_creds_path)




def set_music_index_in_sheet(index):
    import gspread, os, tempfile
    import logging
    import traceback

    google_creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if not google_creds_json:
        raise Exception("GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable not set.")
    
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(google_creds_json)
            temp_creds_path = f.name

        gc = gspread.service_account(filename=temp_creds_path)
        worksheet = gc.open(SHEET_NAME).get_worksheet(SHEET_WORKSHEET_INDEX)
        print("Reading from:", sheet.title, "in spreadsheet:", sheet.spreadsheet.title)
        header_row = worksheet.row_values(1)
        logging.info(f"Columns found: {header_row}")
        if 'music_index' not in header_row:
            raise Exception("'music_index' column not found in sheet")

        col_index = header_row.index('music_index') + 1
        logging.info(f"Attempting to update cell (2, {col_index}) to {index}")
        worksheet.update_cell(2, col_index, str(index))
        logging.info("Successfully updated music_index")

        confirm = worksheet.cell(2, col_index).value
        if str(confirm) != str(index):
            logging.warning(f"Update mismatch: wrote {index}, but read back {confirm}")
        else:
            logging.info(f"Confirmed update: music_index = {confirm}")

    except Exception as e:
        logging.error(f"Exception while updating music index: {e}")
        logging.error(traceback.format_exc())
    finally:
        if 'temp_creds_path' in locals() and os.path.exists(temp_creds_path):
            os.unlink(temp_creds_path)


    except Exception as e:
        logging.error(f"Exception while updating music index: {e}")
        logging.error(traceback.format_exc())
    finally:
        # Always clean up the temp file
        if 'temp_creds_path' in locals() and os.path.exists(temp_creds_path):
            os.unlink(temp_creds_path)

class InstagramAIAgent:
    def __init__(self):
        self.progress_data = self.load_progress()
        self.drive_service = None
        self.instagram_api = None
        self.video_creator = VideoCreator()
        
        if USE_GOOGLE_DRIVE:
            self.setup_google_drive()
        
        if ENABLE_INSTAGRAM_POSTING:
            self.setup_instagram_api()
    
    def load_progress(self):
        """Load progress data to track effect only (no quote/music progress)."""
        if os.path.exists(PROGRESS_FILE):
            try:
                with open(PROGRESS_FILE, 'r') as f:
                    data = json.load(f)
                    # Remove quote_index and music_index if present
                    data.pop('quote_index', None)
                    data.pop('music_index', None)
                    if 'effect_index' not in data:
                        data['effect_index'] = 0
                    logging.info(f"Loaded progress: Effect {data.get('effect_index', 0)}")
                    return data
            except Exception as e:
                logging.error(f"Error loading progress: {e}")
        # Initialize with first items (no quote/music index)
        return {'last_reset': datetime.now().isoformat(), 'effect_index': 0}
    
    def save_progress(self):
        """Save current progress (effect only, no quote/music progress)."""
        try:
            # Remove quote_index and music_index if present
            self.progress_data.pop('quote_index', None)
            self.progress_data.pop('music_index', None)
            with open(PROGRESS_FILE, 'w') as f:
                json.dump(self.progress_data, f, indent=2)
            logging.info(f"Saved progress: Effect {self.progress_data.get('effect_index', 0)}")
        except Exception as e:
            logging.error(f"Error saving progress: {e}")
    
    def setup_google_drive(self):
        """Setup Google Drive API for storing videos."""
        try:
            scopes = ['https://www.googleapis.com/auth/drive']
            credentials = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=scopes)
            self.drive_service = build('drive', 'v3', credentials=credentials)
            
            # Create or find the folder
            self.drive_folder_id = self.get_or_create_drive_folder()
            logging.info(f"Google Drive setup complete. Folder ID: {self.drive_folder_id}")
        except Exception as e:
            logging.error(f"Error setting up Google Drive: {e}")
            self.drive_service = None
    
    def setup_instagram_api(self):
        """Setup Instagram API for posting."""
        try:
            self.instagram_api = InstagramAPI(
                INSTAGRAM_ACCESS_TOKEN,
                INSTAGRAM_USER_ID,
                upload_to_drive=self.upload_to_drive,
                drive_service=self.drive_service
            )
            if self.instagram_api:
                logging.info("Instagram API setup complete")
            else:
                logging.warning("Instagram API setup failed - posting will be disabled")
        except Exception as e:
            logging.error(f"Error setting up Instagram API: {e}")
            self.instagram_api = None
    
    def get_or_create_drive_folder(self):
        """Get or create the Google Drive folder for videos."""
        try:
            # Search for existing folder
            query = f"name='{DRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.drive_service.files().list(q=query).execute()
            files = results.get('files', [])
            
            if files:
                return files[0]['id']
            
            # Create new folder
            folder_metadata = {
                'name': DRIVE_FOLDER_NAME,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = self.drive_service.files().create(body=folder_metadata, fields='id').execute()
            logging.info(f"Created Google Drive folder: {DRIVE_FOLDER_NAME}")
            return folder.get('id')
        except Exception as e:
            logging.error(f"Error creating Drive folder: {e}")
            return None
    
    def upload_to_drive(self, file_path, filename):
        """Upload video to Google Drive."""
        if not self.drive_service or not DRIVE_FOLDER_ID:
            print("[Drive] Google Drive not available, skipping upload")
            logging.warning("Google Drive not available, skipping upload")
            return None
        try:
            print(f"[Drive] Uploading '{filename}' to Google Drive...")
            file_metadata = {
                'name': filename,
                'parents': [DRIVE_FOLDER_ID]
            }
            media = MediaFileUpload(file_path, resumable=True)
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id',
                supportsAllDrives=True
            ).execute()
            file_id = file.get('id')
            print(f"[Drive] Upload complete! File ID: {file_id}")
            logging.info(f"Uploaded to Google Drive: {filename} (ID: {file_id})")
            # --- Make the file public automatically ---
            try:
                scopes = ['https://www.googleapis.com/auth/drive']
                credentials = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_PATH, scopes=scopes)
                drive_service = build('drive', 'v3', credentials=credentials)
                permission = {
                    'type': 'anyone',
                    'role': 'reader'
                }
                drive_service.permissions().create(
                    fileId=file_id,
                    body=permission,
                    supportsAllDrives=True
                ).execute()
                print(f"[Drive] File {file_id} is now public.")
                logging.info(f"Made file {file_id} public.")
            except Exception as e:
                print(f"[Drive] Failed to set file public: {e}")
                logging.error(f"Failed to set file public: {e}")
            return file_id
        except Exception as e:
            print(f"[Drive] Error uploading to Drive: {e}")
            logging.error(f"Error uploading to Drive: {e}")
            return None
    
    def check_weekly_reset(self):
        """Check if we should reset to the first quote (weekly)."""
        if not RESET_WEEKLY:
            return False
        
        last_reset = datetime.fromisoformat(self.progress_data.get('last_reset', datetime.now().isoformat()))
        days_since_reset = (datetime.now() - last_reset).days
        
        if days_since_reset >= 7:
            self.progress_data['quote_index'] = 0
            self.progress_data['music_index'] = 0
            self.progress_data['last_reset'] = datetime.now().isoformat()
            self.save_progress()
            logging.info("Weekly reset: Starting from first quote and music")
            return True
        
        return False
    
    def list_available_sheets(self):
        """List all available Google Sheets to help debug sheet access."""
        try:
            gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_PATH)
            all_sheets = gc.openall()
            
            if not all_sheets:
                logging.info("No Google Sheets found. Please check:")
                logging.info("1. Your service account has access to any sheets")
                logging.info("2. Sheets are shared with your service account email")
                return
            
            logging.info("Available Google Sheets:")
            for sheet in all_sheets:
                logging.info(f"- '{sheet.title}' (ID: {sheet.id})")
                
        except Exception as e:
            logging.error(f"Error listing sheets: {e}")
    
    def get_quotes_from_sheet(self):
        """Fetch quotes from Google Sheets."""
        try:
            import tempfile
            import os
            google_creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            if not google_creds_json:
                raise Exception("GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable not set.")
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(google_creds_json)
                temp_creds_path = f.name
            gc = gspread.service_account(filename=temp_creds_path)
            os.unlink(temp_creds_path)
            worksheet = gc.open(SHEET_NAME).get_worksheet(SHEET_WORKSHEET_INDEX)
            records = worksheet.get_all_records()
            df = pd.DataFrame(records)
            
            # Check if we have the required columns
            required_columns = ['Quote', 'Author']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                logging.error(f"Missing required columns in Google Sheet: {missing_columns}")
                logging.info(f"Available columns: {list(df.columns)}")
                logging.info("Please ensure your Google Sheet has columns named 'Quote' and 'Author'")
                return None
            
            if df.empty:
                logging.error("Google Sheet is empty. Please add some quotes.")
                return None
            
            logging.info(f"Successfully fetched {len(df)} quotes from Google Sheets.")
            logging.info(f"Columns found: {list(df.columns)}")
            return df
            
        except Exception as e:
            logging.error(f"Error connecting to Google Sheets: {e}")
            return None
    
    def get_sequential_quote(self, quotes_df):
        """Get the next unused quote from the sheet (where 'Used' is not set). Returns (quote, author, index)."""
        if quotes_df is None or quotes_df.empty:
            return None, None, None
        # Only consider quotes where 'Used' is not set/empty/false
        unused_mask = ~quotes_df.get('Used', '').astype(str).str.lower().isin(['yes', 'true', '1'])
        unused_quotes = quotes_df[unused_mask]
        if unused_quotes.empty:
            logging.info("All quotes have been used. Resetting 'Used' column for all quotes.")
            # Reset all 'Used' values to blank
            import gspread
            google_creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            if google_creds_json:
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    f.write(google_creds_json)
                    temp_creds_path = f.name
                gc = gspread.service_account(filename=temp_creds_path)
                os.unlink(temp_creds_path)
            else:
                gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_PATH)
            worksheet = gc.open(SHEET_NAME).get_worksheet(SHEET_WORKSHEET_INDEX)
            # Set all 'Used' cells to blank
            records = worksheet.get_all_records()
            df = pd.DataFrame(records)
            if 'Used' in df.columns:
                used_col = df.columns.get_loc('Used') + 1
                for i in range(2, len(df) + 2):
                    worksheet.update_cell(i, used_col, '')
            # Re-fetch quotes
            quotes_df = self.get_quotes_from_sheet()
            unused_mask = ~quotes_df.get('Used', '').astype(str).str.lower().isin(['yes', 'true', '1'])
            unused_quotes = quotes_df[unused_mask]
            if unused_quotes.empty:
                logging.error("No quotes available after reset.")
                return None, None, None
        # Get the first unused quote
        first_unused = unused_quotes.iloc[0]
        quote = first_unused['Quote']
        author = first_unused['Author']
        index = first_unused.name  # This is the DataFrame index, matches row in sheet minus header
        return quote, author, index
    
    def get_sequential_music(self):
        """Get the next music file from Google Drive, tracking progress in the sheet."""
        try:
            music_files = self.list_drive_music_files()
            logging.info(f"Music files found: {music_files}")
            if not music_files:
                logging.error("No .mp3 files found in the Drive music folder.")
                return None

            music_files.sort(key=lambda x: x['name'])  # Consistent order
            try:
                music_index = int(get_music_index_from_sheet())
            except Exception:
                music_index = 0
            
            logging.info(f"Music index from sheet: {music_index} (type: {type(music_index)})")
            if music_index >= len(music_files):
                music_index = 0  # Wrap around if files were removed
                logging.info(f"Music index wrapped to 0 due to out of range.")

            selected_file = music_files[music_index]
            temp_path = f"temp_{selected_file['name']}"
            self.download_drive_file(selected_file['id'], temp_path)

            # Move to next music
            next_index = (music_index + 1) % len(music_files)
            logging.info(f"Calling set_music_index_in_sheet with next_index={next_index}")
            set_music_index_in_sheet(next_index)
            logging.info("Returned from set_music_index_in_sheet")

            logging.info(f"Selected Music: {temp_path}")
            return temp_path
        except Exception as e:
            logging.error(f"Error selecting music: {e}")
            return None
    
    def create_instagram_caption(self, quote, author):
        """Create Instagram caption with quote, author, and hashtags."""
        caption_parts = []
        
        if INCLUDE_QUOTE_IN_CAPTION:
            caption_parts.append(f'"{quote}"')
        
        if INCLUDE_AUTHOR_IN_CAPTION:
            caption_parts.append(f"- {author}")
        
        caption = "\n\n".join(caption_parts)
        
        if ADD_HASHTAGS:
            hashtags = " ".join(DEFAULT_HASHTAGS)
            caption += f"\n\n{hashtags}"
        
        return caption
    
    def post_to_instagram(self, video_path, quote, author):
        """Post video to Instagram."""
        if not self.instagram_api or not POST_TO_INSTAGRAM:
            print("[Instagram] Posting disabled or API not available.")
            logging.info("Instagram posting disabled or API not available")
            return False
        
        try:
            caption = self.create_instagram_caption(quote, author)
            print("[Instagram] Preparing to post to Instagram...")
            logging.info("Posting to Instagram with caption: ...")
            
            file_id = self.upload_to_drive(video_path, os.path.basename(video_path))
            if not file_id:
                print("[Drive] Failed to upload video to Google Drive.")
                logging.error("Failed to upload video to Google Drive")
                return False

            print("[Drive] Making file public...")
            # Make file public and get the link
            if not self.instagram_api.set_drive_file_public(file_id):
                print("[Drive] Failed to set Drive file public.")
                logging.error("Failed to set Drive file public")
                return False

            public_url = f"https://drive.google.com/uc?id={file_id}&export=download"
            print(f"[Drive] Public video URL: {public_url}")
            logging.info(f"Public video URL: {public_url}")

            print("[Instagram] Posting video to Instagram...")
            # Post to Instagram using the public URL
            success = self.instagram_api.post_video(public_url, caption)
            
            if success:
                print("[Instagram] 🎉 Successfully posted to Instagram!")
                logging.info("Successfully posted to Instagram!")
                return True
            else:
                print("[Instagram] ❌ Failed to post to Instagram.")
                logging.error("Failed to post to Instagram")
                return False
                
        except Exception as e:
            print(f"[Instagram] ❌ Error posting to Instagram: {e}")
            logging.error(f"Error posting to Instagram: {e}")
            return False
    
    def get_optimal_posting_time(self):
        """Get the next optimal posting time."""
        current_time = datetime.now()
        current_hour = current_time.hour
        current_minute = current_time.minute
        
        for time_str in OPTIMAL_POSTING_TIMES:
            hour, minute = map(int, time_str.split(':'))
            if hour > current_hour or (hour == current_hour and minute > current_minute):
                return hour, minute
        
        # If all times have passed today, return first time tomorrow
        return int(OPTIMAL_POSTING_TIMES[0].split(':')[0]), int(OPTIMAL_POSTING_TIMES[0].split(':')[1])
    
    def create_video(self):
        """Main function to create a video."""
        logging.info("Starting Instagram AI Agent...")
        # Check weekly reset (optional, can be removed if not needed)
        # self.check_weekly_reset()
        # Get quotes
        quotes_df = self.get_quotes_from_sheet()
        if quotes_df is None or quotes_df.empty:
            logging.error("Could not fetch quotes. Exiting.")
            return False
        # Get next unused quote and music
        quote, author, quote_index = self.get_sequential_quote(quotes_df)
        if not quote or not author or quote_index is None:
            logging.error("Could not get quote. Exiting.")
            return False
        music_file = self.get_sequential_music()
        if not music_file:
            logging.error("Could not get music file. Exiting.")
            return False
        logging.info(f"Selected Quote: '{quote}' by {author}")
        # --- Effect cycling logic ---
        from config import AVAILABLE_EFFECTS
        effect_index = self.progress_data.get('effect_index', 0)
        effect = AVAILABLE_EFFECTS[effect_index % len(AVAILABLE_EFFECTS)]
        self.progress_data['effect_index'] = (effect_index + 1) % len(AVAILABLE_EFFECTS)
        # --- Video creation with keyframe logic ---
        logging.info(f"Creating video with effect: {effect}")
        video_filename = self.video_creator.create_video_with_pil_text_and_blur_keyframe(
            quote, author, music_file, effect
        )
        if not video_filename:
            logging.error("Video creation failed.")
            return False
        logging.info(f"Video created successfully: {video_filename}")
        logging.info(f"Quote: '{quote}' by {author}")
        # Upload to Google Drive if enabled
        drive_id = None
        public_url = None
        if UPLOAD_TO_DRIVE:
            drive_id = self.upload_to_drive(video_filename, video_filename)
            if drive_id:
                logging.info(f"Video uploaded to Google Drive with ID: {drive_id}")
                public_url = f"https://drive.google.com/uc?id={drive_id}&export=download"
                print("Public video URL:", public_url)
        # Use test1.py style Instagram posting with the generated public_url
        if public_url:
            IG_USER_ID = INSTAGRAM_USER_ID
            ACCESS_TOKEN = INSTAGRAM_ACCESS_TOKEN
            VIDEO_URL = public_url
            CAPTION = self.create_instagram_caption(quote, author)
            # 1. Create media container
            media_container_url = f'https://graph.facebook.com/v19.0/{IG_USER_ID}/media'
            media_container_payload = {
                'media_type': 'REELS',
                'video_url': VIDEO_URL,
                'caption': CAPTION,
                'access_token': ACCESS_TOKEN
            }
            media_container_resp = requests.post(media_container_url, data=media_container_payload)
            print('Media container response:', media_container_resp.json())
            container_id = media_container_resp.json().get('id')
            print('Container ID:', container_id)
            # 2. Poll for status
            status_url = f'https://graph.facebook.com/v19.0/{container_id}?fields=status_code&access_token={ACCESS_TOKEN}'
            while True:
                status_resp = requests.get(status_url)
                status = status_resp.json().get('status_code')
                print('Status:', status)
                if status == 'FINISHED':
                    break
                elif status == 'ERROR':
                    print('Error:', status_resp.json())
                    return False
                time.sleep(5)
            # 3. Publish media
            publish_url = f'https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish'
            publish_payload = {
                'creation_id': container_id,
                'access_token': ACCESS_TOKEN
            }
            publish_resp = requests.post(publish_url, data=publish_payload)
            print('Publish response:', publish_resp.json())
            # Delete the video from Google Drive after successful Instagram post
            if publish_resp.json().get('id') and drive_id:
                self.delete_drive_file(drive_id)
                print(f"[Drive] Deleted video from Google Drive: {drive_id}")
                logging.info(f"Deleted video from Google Drive: {drive_id}")
            # Mark the used quote in Google Sheets after successful Instagram post
            if publish_resp.json().get('id'):
                self.mark_quote_as_used(quote_index)
        # Save effect progress only (no quote/music progress)
        # Clean up the downloaded temp music file
        if music_file and music_file.startswith("temp_") and os.path.exists(music_file):
            os.remove(music_file)
            logging.info(f"Deleted temporary music file: {music_file}")
        return True

    def post_video_direct_url(self, public_url, caption):
        # Step 1: Create media container
        media_url = f"https://graph.facebook.com/v18.0/{self.ig_user_id}/media"
        params = {
            "media_type": "REELS",
            "video_url": public_url,
            "caption": caption,
            "access_token": self.access_token
        }
        resp = requests.post(media_url, data=params)
        creation_id = resp.json().get("id")
        if not creation_id:
            logging.error(f"Failed to create media container: {resp.json()}")
            return False

        # Step 2: Poll for status
        for i in range(12):
            status_url = f"https://graph.facebook.com/v18.0/{creation_id}?fields=status_code&access_token={self.access_token}"
            status_resp = requests.get(status_url)
            status_code = status_resp.json().get("status_code")
            if status_code == "FINISHED":
                break
            elif status_code == "ERROR":
                logging.error(f"Instagram processing error: {status_resp.json()}")
                return False
            time.sleep(10)
        if status_code == "FINISHED":
            publish_url = f"https://graph.facebook.com/v18.0/{self.ig_user_id}/media_publish"
            params = {
                "creation_id": creation_id,
                "access_token": self.access_token
            }
            publish_resp = requests.post(publish_url, data=params)
            logging.info(f"Publish response: {publish_resp.json()}")
            return True
        else:
            logging.error("Media was not ready after waiting.")
            return False

    def list_drive_music_files(self):
        """List all .mp3 files in the Google Drive music folder."""
        try:
            query = f"'{DRIVE_MUSIC_FOLDER_ID}' in parents and mimeType='audio/mpeg' and trashed=false"
            results = self.drive_service.files().list(q=query, fields="files(id, name)").execute()
            return results.get('files', [])
        except Exception as e:
            logging.error(f"Error listing music files in Drive: {e}")
            return []

    def download_drive_file(self, file_id, destination_path):
        """Download a file from Google Drive to a local path."""
        try:
            request = self.drive_service.files().get_media(fileId=file_id)
            with open(destination_path, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
            logging.info(f"Downloaded file {file_id} to {destination_path}")
            return destination_path
        except Exception as e:
            logging.error(f"Error downloading file from Drive: {e}")
            return None

    def mark_quote_as_used(self, quote_index):
        """Mark a quote as used by updating the 'Used' column in the correct position."""
        try:
            import tempfile
            import os
            import pandas as pd
            import gspread
            import logging

            google_creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            if not google_creds_json:
                raise Exception("GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable not set.")

            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(google_creds_json)
                temp_creds_path = f.name

            gc = gspread.service_account(filename=temp_creds_path)
            os.unlink(temp_creds_path)  # Clean up temp file

            worksheet = gc.open(SHEET_NAME).get_worksheet(SHEET_WORKSHEET_INDEX)
            records = worksheet.get_all_records()
            df = pd.DataFrame(records)

            header_row = worksheet.row_values(1)

            if 'Used' not in header_row:
                # Add 'Used' to the first empty column
                worksheet.update_cell(1, len(header_row) + 1, 'Used')
                used_col_index = len(header_row) + 1
                logging.info("Added 'Used' column to Google Sheet")
            else:
                # Find the actual column index of 'Used'
                used_col_index = header_row.index('Used') + 1  # 1-indexed

            row_to_update = quote_index + 2  # +2 because of 1-indexing and header
            worksheet.update_cell(row_to_update, used_col_index, 'yes')
            logging.info(f"Marked quote at index {quote_index} (row {row_to_update}) as used")
            print(f"[Sheets] Marked quote in row {row_to_update} as used")
            return True

        except Exception as e:
            logging.error(f"Error marking quote as used: {e}")
            print(f"[Sheets] Error marking quote as used: {e}")
            return False


 
    def delete_drive_file(self, file_id):
        
        if not self.drive_service:
            logging.warning("Google Drive service not initialized. Cannot delete file.")
            return
        try:
            self.drive_service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
            logging.info(f"Deleted file from Google Drive: {file_id}")
        except Exception as e:
            if "insufficientFilePermissions" in str(e):
                logging.warning(f"Cannot delete file {file_id} - file is public. This is normal behavior.")
                print(f"[Drive] File {file_id} is public and cannot be deleted via API. This is expected.")
            else:
                logging.error(f"Error deleting file from Google Drive: {e}")

    def delete_quote_from_sheet(self, quote_index):
        """Delete the used quote from Google Sheets to prevent reuse."""
        try:
            # Always get credentials from environment variable (GitHub Actions)
            google_creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            if not google_creds_json:
                raise Exception("GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable not set.")
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(google_creds_json)
                temp_creds_path = f.name
            gc = gspread.service_account(filename=temp_creds_path)
            os.unlink(temp_creds_path)  # Clean up temp file
            worksheet = gc.open(SHEET_NAME).get_worksheet(SHEET_WORKSHEET_INDEX)
            # Delete the row (add 2 because sheets are 1-indexed and we have a header row)
            row_to_delete = quote_index + 2
            worksheet.delete_rows(row_to_delete)
            logging.info(f"Deleted quote at index {quote_index} (row {row_to_delete}) from Google Sheets")
            print(f"[Sheets] Deleted used quote from row {row_to_delete}")
            return True
        except Exception as e:
            logging.error(f"Error deleting quote from sheet: {e}")
            print(f"[Sheets] Error deleting quote: {e}")
            # Fallback: mark as used instead of deleting
            print(f"[Sheets] Falling back to marking quote as used...")
            return self.mark_quote_as_used(quote_index)

def main():
    """Main execution function."""
    agent = InstagramAIAgent()
    
    # First, let's see what sheets are available
    logging.info("Checking available Google Sheets...")
    agent.list_available_sheets()
    
    # Then try to create the video
    return agent.create_video()

if __name__ == "__main__":
    main() 
