# Design decisions

1. **Indexes.** I use a unique `employees.emp_code` index to make employee identity race-safe, and a unique `(emp_code, date)` attendance index to guarantee one record per attendance day. Department/join-date supports headcount queries; date/employee and employee/date indexes support filtered lists, monthly analytics and ordered reads. I considered a standalone status index but rejected it because status is low-cardinality and the compound date/status index is more useful for the contract's common filters.

2. **Punch-in race.** Both requests may initially see no row, but both attempt the insert. MongoDB's unique compound index accepts only one; the losing insert raises `DuplicateKeyError`, which the API maps to 409. There is no check-then-insert correctness dependency.

3. **Ties.** The leaderboard uses MongoDB `$rank` over total late minutes and filters rank after ranking. Every employee tied at the cutoff rank is returned; rank gaps follow competition ranking. Employee code is the deterministic tie-order.

4. **Headcount.** Department summary starts from employees who joined by month end, then `$lookup`s matching attendance. Preserving employees with no matching logs means they still contribute to headcount.

5. **100x scale.** I would benchmark query plans and move expensive trend/headcount work toward pre-aggregated daily department metrics, with an explicit freshness strategy and load tests.
