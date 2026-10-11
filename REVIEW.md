# Starter-code review

| # | Where | What is wrong | How to notice | Fix |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Uses naive/local time and doesn't model IST or overnight attendance dates. | Run with a UTC server and a punch just after midnight for a night shift. | Normalize instants to UTC, derive IST attendance date, compare to shift start on that date. |
| 2 | `compute_late_minutes` | Grace boundary/elapsed calculation can be wrong for negative elapsed times and uses a float-based calculation. | Test exactly 09:40:00 and 09:40:01 for a 09:30 shift. | Truncate to seconds; only report elapsed whole minutes when elapsed seconds are strictly greater than 600. |
| 3 | `compute_work_hours` | Python `round` uses ties-to-even, not required half-up rounding. | Test a duration whose hour value is exactly halfway at two decimals. | Use `Decimal` with `ROUND_HALF_UP`. |
| 4 | `compute_overtime` | Ignores the 30-minute minimum and mishandles overnight shift end day. | Test 29:59 and 30:00 after shift end, plus a 22:00–06:00 shift. | Calculate the correct shift-end instant and only count overtime at 30 minutes or more. |
| 5 | `/health` | Always returns success without checking MongoDB. | Stop MongoDB and call `/health`. | Ping MongoDB and return 503 on failure. |
| 6 | `create_employee` | Check-then-insert is racy; duplicate employee codes can be inserted concurrently. | Send concurrent POSTs with the same code. | Unique database index; translate `DuplicateKeyError` to 409. |
| 7 | `create_employee` | `created_at` is naive and returned as an ISO date/string rather than epoch milliseconds; internal `_id` handling is ad hoc. | Inspect the response and timezone behavior. | Store UTC BSON datetime and serialize it as integer epoch milliseconds. |
| 8 | `EmployeeIn` | Missing contract validation for code, email, lengths, date/time formats and unequal shift boundaries. | Submit malformed values and identical shift times. | Add Pydantic constraints and date/shift validation. |
| 9 | `list_employees` | `skip = page * page_size` skips the first page and one extra page of data. | Request page 1 with page size 2. | Use `(page - 1) * page_size`. |
| 10 | `list_employees` | `total` ignores the department filter. | Filter to a department with fewer employees than the company total. | Count with the same query filter. |
| 11 | `list_employees` | No deterministic sort, pagination bounds, or database indexes. | Repeat requests and use invalid/huge page sizes. | Sort by `emp_code`, validate page parameters, and index query fields. |
| 12 | `punch_in` | Unknown employee causes a `None` dereference/500. | Punch in with an unknown code. | Return 404 before using employee shift data. |
| 13 | `punch_in` | Treats epoch milliseconds as seconds and uses machine-local timezone. | Send a valid epoch-millisecond timestamp. | Validate the timestamp and convert from UTC milliseconds. |
| 14 | `punch_in` | Uses the raw calendar date, not the IST attendance date or overnight-shift rule. | Punch in after midnight for an overnight shift. | Calculate the date under R1. |
| 15 | `punch_in` | Check-then-insert allows duplicate attendance under concurrent requests. | Send simultaneous punch-ins for the same employee/date. | Unique compound index on `(emp_code, date)` and handle duplicate-key conflict. |
| 16 | `punch_in` | Does not validate presence-only status values and accepts malformed timestamps. | Submit `ABSENT`, a seconds timestamp, float, or string. | Strict Pydantic validation; permit only PRESENT/WFH/ON_DUTY. |
| 17 | `list_attendance` | Loads the full matching collection into Python and sorts/slices in memory. | Run against 100,000 records and monitor latency/memory. | Use indexed MongoDB sort, skip, limit and `count_documents`. |
| 18 | `list_attendance` | Does not validate date strings, reversed ranges, status, or pagination limits. | Send `date_from > date_to`, malformed dates, or page_size 101. | Validate query parameters and return 422. |
| 19 | `list_attendance` | Response can expose BSON datetimes and inconsistent legacy fields/history. | Read a legacy row without `history` or `half_day`. | Normalize every record into the contract response shape and epoch-ms timestamps. |
| 20 | Whole starter | Required punch-out, regularization, analytics, and explain endpoints are absent. | Inspect OpenAPI routes / call endpoints. | Implement all contract endpoints. |

## Considered but not defects

- `emp_code` is the business identifier; MongoDB `_id` should remain internal.
- Calendar dates and shift times are strings by design, while actual instants are BSON datetimes.
- `history` should only record manual corrections, not punch-in/out events.
- A missing attendance document is not equivalent to an explicit `ABSENT` record.
