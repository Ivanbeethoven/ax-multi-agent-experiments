# Final workload-figure review — 2026-10-23

The active figures are fuse-profile (four panels in one horizontal row) and
fuse-time (two per-task temporal panels). At the 7-inch manuscript width they
are 2.45 and 2.00 inches tall, reduced from 5.05 and 2.95 inches. Labels, ticks
and legends remain at least 8 pt; the minimum printed size is 8.00016 pt.

Core and Exploratory method panels share the left method labels and a 0–40%
scale. Both denominators, all six metadata stars and error segments remain.
The read/write ECDF titles give exact Core/Exploratory sample counts. The
method-panel pair and ECDF pair have equal dimensions within their respective
explicit alignment groups, with all four panels sharing top/bottom bounds.
Different widths reflect the different plot types; the 1.5 pt audit tolerance
was not relaxed. Logarithmic write ticks below 1 KiB use bytes. Legend regions
contain no data marks. No smoothing, resampling, omission or new analysis was
performed. The time legend uses repository names, with full task IDs retained
in the README table and derived records; all eight curves and time windows remain.

Rendered alignment, PDF glyph and collision audits pass with no failures or
warnings. Final figure PDFs are vector graphics with editable SVG companions.
The source preflight has 18 passes, 3 nonblocking warnings and 0 failures:

- PNG without TIFF: the paper uses vector PDF; PNG is only a preview.
- PNG at 300 rather than 600 dpi: no plot bitmap is embedded in the paper.
- Static positive-value-guard detection: plot.py explicitly checks np.all(x > 0).
  Observed read sizes start at 4096 bytes and writes at one byte. No pseudocount
  or zero-size filtering is used.

Historical fuse-methods and fuse-request-sizes exports are no longer included.
Source ID/hash reconciliation, status semantics, setup/teardown and the Astropy
capture tail remain documented in the companion README. The analyzer and all
derived records are byte-identical to the pre-layout-revision files. The data
remain descriptive WSL FUSE-handler observations, not storage speedups or
SWE-bench task-success evidence.

Both Windows XeLaTeX PDFs compile without unresolved references/citations,
overfull boxes or missing glyphs. Both workload figures now share page 3;
the new compact methods diagram is on page 5. Full-document contact sheets and
figure pages were visually inspected. Both PDFs have 15 pages, with body ending
on page 13. Underfull diagnostics and uneven vertical spacing remain. The
neutral-name preview still has the previously documented name/source-identity
residue. No margin, font-size or global spacing reduction was introduced.

Current compact-figure audits and manuscript checks are also saved under
validation/compact-figures/. Earlier selective-refinement and architecture-v2
reports describe historical geometry and page counts.
