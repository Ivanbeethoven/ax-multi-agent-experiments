# Regression fixtures

These CSVs are copies of `/home/hxy/exp-results/{c2,c3,c4,c5}/w{1,2,3,4}.csv`
from the 2026-10-28 run. They are checked in as read-only samples for
`validate.py`; the source directory is never changed by the pipeline.

Two historical exports (C3 W2 and W3) had an unquoted trailing comma in the
`note` field, which made a CSV reader see an extra column. Their field content
is preserved here after joining that comma into `note`, so the regression
sample exercises the required eleven-column contract. A ten-column fixture is
created in memory by `validate.py --self-test` and must fail.
