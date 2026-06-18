import os
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
from services.config import load_local_env_override

load_local_env_override()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

# Service role client — bypasses RLS, for backend use only.
# Never expose this key to the frontend.
sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
