"""
Local development launcher for Gym Booking System.
Imports the Flask application from api/index.py (which also serves Vercel Serverless).
"""

import os
from api.index import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Gym Booking System server running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
