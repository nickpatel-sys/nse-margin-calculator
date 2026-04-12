# NSEMargins — Documentation Wiki

NSEMargins is a local web application for calculating NSE F&O SPAN margins on a portfolio of futures and options positions. It downloads official end-of-day data from NSE archives, stores it in a local SQLite database, and provides a REST API consumed by a single-page frontend.

---

## Pages

| Page | Contents |
|------|----------|
| [User Guide](User-Guide.md) | Using the web interface — adding positions, reading results |
| [API Reference](API-Reference.md) | All REST endpoints, request/response shapes |
| [Margin Engine](Margin-Engine.md) | How SPAN, exposure, spread, and variation margin are calculated |
| [Data Pipeline](Data-Pipeline.md) | NSE data sources, download strategy, parsers, scheduler |
| [Architecture](Architecture.md) | Database schema, configuration reference, deployment |
| [Deployment](Deployment.md) | Dockerizing the app and deploying to AWS EC2 free tier |

---

## Quick Start

```bash
# Windows — activate venv and start server
venv\Scripts\activate
python run.py
# Open http://localhost:5000
```

Data is downloaded automatically on startup for the most recent trading day. The scheduler re-fetches every evening at 18:30, 19:00, and 19:30 IST (Mon–Fri).

---

## Data Modes

The app operates in one of two modes depending on which NSE files are available:

| Mode | Source | Accuracy |
|------|--------|----------|
| **Live SPAN** | Official SPAN XML (`nsccl.{YYYYMMDD}.s.zip`) + bhavcopy | Exact — uses NSE's official 16-scenario risk arrays |
| **Estimated** | Bhavcopy only | Approximate — PSR estimated from config rates; results typically within 2–5% of exchange values |

The status chip in the top-right corner of the UI shows the current mode.
