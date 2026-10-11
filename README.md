# Employee Attendance & Analytics API

FastAPI + PyMongo implementation of the supplied OpenAPI v2 contract. Python 3.11+ and MongoDB 6.0+ are required (MongoDB 7 is recommended for `$densify` and `$setWindowFields`).

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit MONGO_URI and MONGO_DB if needed
# Start MongoDB separately, e.g. docker run --rm -p 27017:27017 mongo:7
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```

`MONGO_URI` and `MONGO_DB` environment variables override `.env`. Indexes are created idempotently on application startup. `/health` pings MongoDB.

## Key implementation notes

- Instants are validated as epoch milliseconds, truncated to seconds, stored as UTC BSON datetimes, and serialized as epoch milliseconds.
- Unique indexes enforce employee-code and attendance natural-key uniqueness under concurrency.
- Derived values use the supplied business rules and half-up decimal rounding.
- Attendance listing is paginated in MongoDB. Analytics use aggregation pipelines, including a rank-based leaderboard and a densified trend.
- Manual regularizations append an audit entry and use a compare-and-set update to avoid silently overwriting a concurrent edit.

## Before submitting

1. Run the app against a clean MongoDB and seed the supplied sample data.
2. Exercise every route and edge cases from `openapi.yaml` (overnight shifts, exact grace/overtime boundaries, half-day rounding, missing logs, ties, weekend records and concurrent requests).
3. Run `python -m py_compile app/main.py` and review `REVIEW.md` / `DECISIONS.md` in your own words.

## Verification status

- `python -m py_compile app/main.py` passes in the preparation environment.
- The business-rule tests are included in `tests/test_rules.py`.
- Full integration tests against MongoDB have **not** been run in this environment because Python dependencies could not be installed here and no MongoDB server is available. Run the commands above locally before submitting.
- The trend endpoint builds its date spine in MongoDB using `$documents` and `$densify`, including when there are no attendance rows. The explain endpoint builds the corresponding aggregation for the trend and full aggregation pipelines for the monthly/summary/leaderboard endpoints.
- Explain plan index selection is MongoDB-version- and data-dependent. Run the explain endpoints on the supplied large dataset and verify the expected `IXSCAN` and no `COLLSCAN` before submission.
