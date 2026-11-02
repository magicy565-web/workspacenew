#!/usr/bin/env python3
"""Bundle a built local magazine into one HTML file for private offline inspection.
This does not approve any asset rights or publish/send the page.
"""
import argparse
from pathlib import Path
from qc import inline_site_for_restricted_browser, structural_checks

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--site', required=True)
    p.add_argument('--out', required=True)
    args=p.parse_args()
    site=Path(args.site).resolve()
    problems, _, _=structural_checks(site)
    if problems: raise SystemExit('Refusing portable export: '+'; '.join(problems))
    result=inline_site_for_restricted_browser(site)
    out=Path(args.out).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(result,encoding='utf-8')
    print(f'Portable private-preview HTML: {out} ({out.stat().st_size:,} bytes)')

if __name__=='__main__':main()
