# Background: agent toolchains

One compact figure: per-profile call counts and recorded read/write bytes. These are workload observations on ScorpioFS, separate from BrewFS performance experiments.

Inputs: [telemetry repository](https://github.com/Ivanbeethoven/scorpiofs-agent-fs-telemetry), revision `74e36ba70172cc31d896452e45cfef7642ce1bbf`. `data.json` retains five core profiles, full operation counts, model/machine labels, UID/status counts, exclusions, and input hashes.

```powershell
python experiments/agent-toolchain-observations/render.py --paper paper
# Refresh after fetching the independent source checkout:
python experiments/agent-toolchain-observations/render.py --source D:/code/scorpiofs-agent-fs-telemetry --paper paper
```

Use Python with Matplotlib, Arial, and the local figure QA scripts (`--qa-scripts` overrides their location). Outputs: vector PDF, editable SVG, PNG, generated TeX values, plotted data, and panel-alignment evidence. Review a refreshed snapshot before committing it.

All statuses and mounted phases are included. Metadata uses the paper's six methods: lookup, getattr, getxattr, readdirplus, opendir, releasedir. Read, write, and all remaining methods complete the partition. MiB means 2^20 bytes. Byte totals sum the profile's `bytes` field, not `requested_size`; they do not measure source diff size or storage/network traffic. One profile is available per configuration, so no replicate-based error bars are drawn. Profiles are ordered by tool name.

The permission reference is excluded; supplemental retries and diagnostic traces are not pooled into the core profiles. Kimi uses a distinct discovery profile with 97.5% UID-0 events. The source reports two machines. Its Markdown documents disagree about the exact edited file; the paper uses their shared inspection/edit/readback/verification scope.

Raw TSVs are outside the public repository and the documented local archive is absent on this machine. Verification reconciles the published CSV with JSON, operation/status/UID/header totals, read/write counts, and all 32 timeline bins. This is verification of published aggregates, not a rerun from raw events.
