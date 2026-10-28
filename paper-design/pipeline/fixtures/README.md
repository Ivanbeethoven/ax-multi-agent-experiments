# Regression fixtures

These CSVs are copies of `/home/hxy/exp-results/{c2,c3,c4,c5}/w{1,2,3,4}.csv`
from the 2026-10-28 run. They are checked in as read-only samples for
`validate.py`; the source directory is never changed by the pipeline.

`ceilings.json` is the matching C2 G2 raw-disk ceiling artifact copied from
`/home/hxy/exp-results/c2/ceilings.json`. It is passed to `summarize.py` by
`selftest.sh`. Exceeding that reference no longer implies a cache hit or invalid
result. The three CSVs under `bad/` are intentional negative fixtures:
they have too few columns, too many columns, and an invalid status value.

Two historical exports (C3 W2 and W3) had an unquoted trailing comma in the
`note` field, which made a CSV reader see an extra column. Their field content
is preserved here after joining that comma into `note`, so the regression
sample exercises the required eleven-column contract. A ten-column fixture is
created in memory by `validate.py --self-test` and must fail.

The stricter 2026-10-28 validator additionally found nine fractional `read_bytes`
values in C3 W2 (for example 0.290000). The original file is preserved, and now
serves as a negative fixture. Its rows cannot enter performance summaries. The
self-test expects this rejection; it does not silently round or repair the data.
Passing the other 14 files is format validation only, not final-protocol qualification.
