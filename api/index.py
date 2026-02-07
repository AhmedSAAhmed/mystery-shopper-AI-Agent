import sys
import os

# Add the parent directory to sys.path so we can import 'server'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the FastAPI app
from server import app

# Vercel looks for 'app' in this module
