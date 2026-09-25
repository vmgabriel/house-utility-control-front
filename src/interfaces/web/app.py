"""Flask application factory."""

import os

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1"
TRUTHY_VALUES = ("true", "1", "yes", "on")


def create_app() -> Flask:
    app = Flask(__name__)

    # Load configuration from environment variables
    app.config.from_prefixed_env()

    # Ensure DRF_API_BASE_URL is available
    app.config["DRF_API_BASE_URL"] = os.getenv("DRF_API_BASE_URL", DEFAULT_API_BASE_URL)

    # Set debug mode from FLASK_DEBUG
    debug = os.getenv("FLASK_DEBUG", "false").lower() in TRUTHY_VALUES
    app.debug = debug

    @app.route("/")
    def index():
        return "Budget Tracker Frontend - Checkpoint 1 Successful"

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=app.debug, port=5000)
