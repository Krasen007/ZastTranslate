import os

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials as _OAuthCredentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    HAS_GOOGLE_API = True
except ImportError:
    HAS_GOOGLE_API = False

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

class YouTubePublisher:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.client_secrets_file = os.path.join(base_dir, 'client_secret.json')
        self.token_file = os.path.join(base_dir, 'token.pickle')
        self.credentials = None
        self.youtube = None

    def is_configured(self):
        """Check if client_secret.json exists."""
        return os.path.exists(self.client_secrets_file)

    def authenticate(self):
        """Authenticate via OAuth2 and build the youtube service."""
        if not HAS_GOOGLE_API:
            raise ImportError(
                "Missing Google API client libraries. "
                "Please run the 'Install' or 'Update' step in Pinokio to install the required dependencies: "
                "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            )

        if not self.is_configured():
            raise FileNotFoundError(f"Missing {self.client_secrets_file}. Please download it from Google Cloud Console.")

        if os.path.exists(self.token_file):
            try:
                # JSON token (preferred, written by _save_credentials below).
                self.credentials = _OAuthCredentials.from_authorized_user_file(self.token_file, SCOPES)
            except Exception:
                self.credentials = None
            if self.credentials is None:
                # Legacy pickle token (pre-review versions): load defensively,
                # verify the type, then immediately migrate to JSON.
                import pickle as _pickle
                try:
                    with open(self.token_file, 'rb') as token:
                        legacy = _pickle.load(token)
                    if isinstance(legacy, _OAuthCredentials):
                        self.credentials = legacy
                    else:
                        print(f"[YouTube] Ignoring unexpected token content in {self.token_file}; re-authenticating.")
                        self.credentials = None
                except Exception as e:
                    print(f"[YouTube] Could not load legacy token ({e}); re-authenticating.")
                    self.credentials = None

        if not self.credentials or not self.credentials.valid:
            if self.credentials and self.credentials.expired and self.credentials.refresh_token:
                self.credentials.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, SCOPES)
                # This will open a browser window for authentication
                self.credentials = flow.run_local_server(port=0)

            self._save_credentials()

        self.youtube = build('youtube', 'v3', credentials=self.credentials)

    def _save_credentials(self):
        """Persist OAuth credentials as JSON with owner-only permissions."""
        with open(self.token_file, 'w', encoding='utf-8') as token:
            token.write(self.credentials.to_json())
        try:
            os.chmod(self.token_file, 0o600)
        except (OSError, NotImplementedError):
            pass  # Windows ACLs / exotic FS: best effort only

    def update_metadata(self, video_id, localizations):
        """
        localizations format:
        {
            "fr": {"title": "Titre", "description": "Desc"},
            "es": {"title": "Titulo", "description": "Desc"}
        }
        """
        if not self.youtube:
            self.authenticate()

        # Fetch existing video snippet and localizations
        video_response = self.youtube.videos().list(
            id=video_id,
            part='snippet,localizations'
        ).execute()

        if not video_response.get('items'):
            raise ValueError(f"Video {video_id} not found.")

        video = video_response['items'][0]
        snippet = video.get('snippet', {})
        existing_localizations = video.get('localizations', {})

        # Ensure defaultLanguage is set (YouTube API requirement for updating localizations)
        if 'defaultLanguage' not in snippet:
            snippet['defaultLanguage'] = 'en' # Fallback default
            video['snippet'] = snippet

        # Update localizations
        for lang_code, data in localizations.items():
            existing_localizations[lang_code] = {
                'title': data.get('title', ''),
                'description': data.get('description', '')
            }

        video['localizations'] = existing_localizations

        # Send update request
        self.youtube.videos().update(
            part='snippet,localizations',
            body=video
        ).execute()

    def upload_caption(self, video_id, language, name, srt_path):
        """Upload an SRT file as a caption track."""
        if not self.youtube:
            self.authenticate()

        body = {
            'snippet': {
                'videoId': video_id,
                'language': language,
                'name': name,
                'isDraft': False
            }
        }

        media = MediaFileUpload(srt_path, mimetype='application/octet-stream', resumable=True)

        self.youtube.captions().insert(
            part='snippet',
            body=body,
            media_body=media
        ).execute()
