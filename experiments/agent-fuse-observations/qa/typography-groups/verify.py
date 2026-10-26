"""Read-only final PDF/data verification plus a screen-size QA raster."""
from pathlib import Path
import json
import re
import pymupdf as fitz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3] / 'paper'
before = json.loads((HERE / 'before-plotted-data.json').read_text())
after = json.loads((HERE / 'plotted-data.json').read_text())
assert before == after
before_text = (HERE / 'before-observations.tex').read_text(encoding='utf-8')
after_text = (ROOT / 'sections/02-agent-observations.tex').read_text(encoding='utf-8')
caption = re.compile(r'\\caption\{.*?(?=\\label\{fig:agent-fuse-combined\})', re.S)
assert caption.sub('CAPTION\n', before_text) == caption.sub('CAPTION\n', after_text)
doc = fitz.open(ROOT / 'figures/agent-fuse/fuse-single-column.pdf')
page = doc[0]
spans = [s for b in page.get_text('dict')['blocks'] if 'lines' in b
         for line in b['lines'] for s in line['spans'] if s['text'].strip()]
fonts = sorted({s['font'] for s in spans})
sizes = sorted({round(s['size'], 4) for s in spans})
assert all('Arial' in font for font in fonts), fonts
assert sizes == [9.0], sizes
assert abs(page.rect.width - 237.6) < .01
assert abs(page.rect.height - 208.8) < .01
page.get_pixmap(matrix=fitz.Matrix(100 / 72, 100 / 72)).save(HERE / 'final-size-100dpi.png')
report = dict(plotted_data_identical=True, all_events=after['all_events'],
              tasks=len(after['bursts']), sizes=sizes, fonts=fonts,
              pdf_size_pt=list(page.rect)[2:],
              caption_only_manuscript_change=True,
              display_aliases={'Core': 'Initial runs', 'Exploratory': 'Extended runs'},
              exclusions=after['exclusions'])
(HERE / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
