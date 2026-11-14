# Current measurement displays

`render.py` verifies the frozen input hashes and all 472 source values, then
generates four review tables, three figures, one compact LaTeX table, and the
manuscript's numeric macros. The figures use a 240 pt canvas and 10 pt text.
The paper includes the I/O table plus the workload and lifecycle figures.

From this directory, with matplotlib and the figure QA tools available:

```powershell
python render.py --paper <paper-root> --metrics ../evidence/writing-metrics-20261114 --qa-tools <figure-qa-scripts>
```

If the metrics are in the paper's handoff directory, pass that directory to
`--metrics`. `--source-root` defaults to this experiment directory. Output
tables are under `tables/`; PDF, editable SVG, PNG and LaTeX outputs go to
the paper's `figures/idcai/`. `qa/` records source mapping and rendered audits.

Each result is one observation. Preparation and operation timers are separate;
the publication subtotal is not added to its total. A new implementation or
batch must have a separate input ledger rather than replace a frozen record.
