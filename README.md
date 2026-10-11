# Employee Attendance & Analytics API

A REST API built with **FastAPI** and **MongoDB** for managing employee records, attendance punch-in/punch-out, attendance corrections, and analytics.

## Features

- Create and list employees.
- Record attendance punch-in and punch-out.
- Retrieve attendance records with filters and pagination.
- Regularize attendance records.
- Generate monthly employee attendance analytics.
- Summarize attendance by department.
- View late-arrival leaderboards.
- Analyze department attendance trends.
- Inspect MongoDB query execution plans through admin explain endpoints.

## Requirements

- Python 3.11 or later
- MongoDB with a valid connection string
- Dependencies listed in `requirements.txt`

## Setup and Run

### 1. Create and activate a virtual environment

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a private `.env` file in the project root and configure your MongoDB connection:

```env
MONGO_URI=your_mongodb_connection_string
MONGO_DB=attendance_db
```

Replace the placeholder with your own MongoDB connection string. Do not commit `.env` files, passwords, or other secrets to GitHub.

### 4. Start the API

```powershell
uvicorn app.main:app --reload
```

The API runs locally at `http://127.0.0.1:8000`.

Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

## Main Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/employees` | Create an employee |
| GET | `/employees` | List employees |
| POST | `/attendance/punch-in` | Record punch-in |
| POST | `/attendance/punch-out` | Record punch-out |
| GET | `/attendance` | Retrieve attendance records |
| PATCH | `/attendance/{emp_code}/{date}` | Correct an attendance record |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly employee analytics |
| GET | `/analytics/departments/summary` | Department summary |
| GET | `/analytics/leaderboard/late` | Late-arrival leaderboard |
| GET | `/analytics/departments/{department}/trend` | Department attendance trend |
| GET | `/admin/explain/{endpoint}` | Inspect query execution plans |

See the interactive API documentation for request parameters, schemas, and response details.

## Database

The application uses MongoDB to store employee and attendance data. Configure a database that is accessible to the running application before making API requests.

Keep database credentials private and never publish them in the repository.

## Project Documentation

- `REVIEW.md` — code review notes.
- `DECISIONS.md` — implementation decisions and trade-offs.
- `requirements.txt` — Python dependencies.

## Running Tests

If the project tests are available, install the dependencies and run:

```powershell
python -m pytest
```

Review the test output to confirm which tests pass in your environment.