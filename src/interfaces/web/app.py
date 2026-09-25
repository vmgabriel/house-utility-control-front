"""Flask application factory placeholder."""

from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_prefixed_env()

    @app.route("/")
    def index():
        return "Budget Tracker Frontend - Checkpoint 0 Successful"

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
