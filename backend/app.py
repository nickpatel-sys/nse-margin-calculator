"""
Flask application factory.
"""

import logging
import os
from pathlib import Path

from flask import Flask, send_from_directory

from backend.extensions import db, scheduler
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


def create_app(config_object=None) -> Flask:
    app = Flask(
        __name__,
        static_folder=str(Path(__file__).parent.parent / "frontend"),
        static_url_path="",
    )

    # ── Config ────────────────────────────────────────────────────────────────
    app.config.from_object(config_object or Config)

    # Ensure the data directory exists
    Path(app.config["DATA_DIR"]).mkdir(parents=True, exist_ok=True)

    # ── Extensions ────────────────────────────────────────────────────────────
    db.init_app(app)

    # ── Blueprints ────────────────────────────────────────────────────────────
    from backend.api.span_status import bp as span_bp
    from backend.api.instruments import bp as instruments_bp
    from backend.api.margin import bp as margin_bp

    app.register_blueprint(span_bp)
    app.register_blueprint(instruments_bp)
    app.register_blueprint(margin_bp)

    # Import models so SQLAlchemy knows about them, then create tables
    import backend.models.db  # noqa: F401
    with app.app_context():
        db.create_all()
        # Add columns that were introduced after initial schema creation.
        # db.create_all() does not ALTER existing tables, so we do it explicitly.
        _apply_schema_migrations(app)

    # ── Serve frontend ────────────────────────────────────────────────────────
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # WERKZEUG_RUN_MAIN is set to "true" only in the reloader child process.
    # Skip scheduler + startup load in the parent (stat-watcher) process.
    import os
    _is_reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if not app.config.get("TESTING") and not scheduler.running and (
        not app.debug or _is_reloader_child
    ):
        from backend.span.scheduler import init_scheduler
        init_scheduler(app)

        # On startup, attempt to load today's data if not already present
        _startup_data_load(app)

    return app


def _apply_schema_migrations(app: Flask):
    """Run idempotent ALTER TABLE statements for columns added after initial release."""
    migrations = [
        "ALTER TABLE contracts ADD COLUMN prev_settlement REAL",
        "ALTER TABLE contracts ADD COLUMN underlying_isin TEXT",
    ]
    with app.app_context():
        for sql in migrations:
            try:
                db.session.execute(db.text(sql))
                db.session.commit()
            except Exception:
                db.session.rollback()
                # Column already exists — safe to ignore


def _startup_data_load(app: Flask):
    """Load today's SPAN data on startup if not already in the DB."""
    def _load():
        with app.app_context():
            from backend.utils.date_utils import most_recent_trading_day, today_ist
            from backend.span.downloader import already_downloaded, download_for_date
            from backend.span.orchestrator import parse_downloaded_file
            from backend.models.db import SpanFile

            trade_date = most_recent_trading_day(today_ist())
            if already_downloaded(trade_date):
                logging.getLogger(__name__).info(
                    "Startup: SPAN data for %s already loaded.", trade_date
                )
                return

            logging.getLogger(__name__).info(
                "Startup: attempting to load SPAN data for %s", trade_date
            )
            zip_path, file_type = download_for_date(trade_date)
            if zip_path is None:
                logging.getLogger(__name__).warning(
                    "Startup: could not download data for %s", trade_date
                )
                return

            span_file = SpanFile.query.filter_by(trade_date=trade_date).first()
            parse_downloaded_file(zip_path, file_type, trade_date, span_file)

    import threading
    t = threading.Thread(target=_load, daemon=True)
    t.start()
