"""Export only the author's requested 12+4 table, with absolute values and changes."""
import argparse
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def change(value, baseline):
    return ((Decimal(str(value)) / Decimal(str(baseline)) - 1) * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--paper', type=Path, required=True)
    args = ap.parse_args()
    data = json.loads((ROOT / 'source/12plus4-author-table.json').read_text())
    archive = {(r['ec'], r['series'], r['range_size']): r for r in json.loads((ROOT / 'data.json').read_text())['observations']}
    out = args.paper / 'figures/pilot'; out.mkdir(parents=True, exist_ok=True)
    qa = ROOT / 'qa'; qa.mkdir(exist_ok=True)
    lines = [r'% Generated from the author-supplied EC 12+4 table.',
        r'\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lrrrrrrr@{}}',
        r'\toprule',
        r'Range & \multicolumn{3}{c}{Throughput (MiB/s)} & \multicolumn{2}{c}{RustFS latency (ms)} & \multicolumn{2}{c}{Hint 64 KiB latency (ms)} \\',
        r'\cmidrule(lr){2-4}\cmidrule(lr){5-6}\cmidrule(l){7-8}',
        r'& RustFS & Hint 256 KiB & Hint 64 KiB & P50 & P99 & P50 & P99 \\',
        r'\midrule']
    checks = []
    rounding_corrections = []
    for row in data['rows']:
        for prefix, series in [('base', '105p1'), ('hint256', 'dyn256k'), ('hint64', 'dyn64k')]:
            assert row[prefix + '_mib_s'] == archive['12+4', series, row['size']]['mib_per_s']
        for prefix, series in [('base', '105p1'), ('hint64', 'dyn64k')]:
            for metric in ['p50', 'p99']:
                assert row[f'{prefix}_{metric}_ms'] == archive['12+4', series, row['size']][metric + '_ms']
        formulas = [('hint256_gain', 'hint256_mib_s', 'base_mib_s'),
                    ('hint64_gain', 'hint64_mib_s', 'base_mib_s'),
                    ('p50_change', 'hint64_p50_ms', 'base_p50_ms'),
                    ('p99_change', 'hint64_p99_ms', 'base_p99_ms')]
        calculated = {}
        for key, value, baseline in formulas:
            result = change(row[value], row[baseline])
            supplied = Decimal(str(row[key]))
            assert abs(result - supplied) <= Decimal('0.1'), (row['size'], key, result, row[key])
            calculated[key] = result
            if result != supplied:
                rounding_corrections.append({'size': row['size'], 'metric': key,
                                             'supplied': str(supplied), 'calculated': str(result)})
            checks.append({'size': row['size'], 'metric': key, 'percent': str(result)})
        def absolute(key, digits, bold=False):
            s = f'{row[key]:.{digits}f}'
            return r'\textbf{' + s + '}' if bold else s
        def annotated(key, digits, percent, bold=False):
            return absolute(key, digits, bold) + r'\;(' + f'{calculated[percent]:+.1f}' + r'\%)'
        size = row['size'][:-3] + r'\,' + row['size'][-3:]
        if archive['12+4', '105p1', row['size']]['errors']: size += r'$^{\dagger}$'
        cells = [size, absolute('base_mib_s', 2),
                 annotated('hint256_mib_s', 2, 'hint256_gain'),
                 annotated('hint64_mib_s', 2, 'hint64_gain', True),
                 absolute('base_p50_ms', 1), absolute('base_p99_ms', 1),
                 annotated('hint64_p50_ms', 1, 'p50_change', True),
                 annotated('hint64_p99_ms', 1, 'p99_change', True)]
        lines.append(' & '.join(cells) + r' \\')
    lines += [r'\bottomrule', r'\end{tabular*}']
    (out / 'rustfs-range-table.tex').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (qa / '12plus4-checks.json').write_text(json.dumps({'absolute_values_checked': 49, 'percentage_values_checked': len(checks),
        'percent_basis': data['percent_basis'], 'changes': checks, 'rounding_corrections': rounding_corrections,
        'displayed_ec': '12+4',
        'other_ec_omitted': 'Explicit author request', 'request_rates': 'Retained in source; omitted from display because throughput and fixed request size determine rate.'}, indent=2) + '\n')
    print('Exported seven rows: 49 absolute values and 28 verified percentages.')


if __name__ == '__main__':
    main()
