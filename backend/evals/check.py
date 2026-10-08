"""CI-friendly comparison: 0 pass, 1 regression, 2 incomparable/invalid."""
import argparse
import json
from pathlib import Path
from .analysis import compare

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',required=True);p.add_argument('--current',required=True)
    p.add_argument('--margin',type=float,default=.05);args=p.parse_args()
    if not 0<=args.margin<=1:p.error('margin must be 0-1')
    try:
        old=json.loads(Path(args.baseline).read_text(encoding='utf8'));new=json.loads(Path(args.current).read_text(encoding='utf8'))
        result=compare(old.get('report',old),new.get('report',new),args.margin)
        print(json.dumps(result,indent=2))
        raise SystemExit(2 if not result['comparable'] or not result['profiles'] else 1 if result['regression'] else 0)
    except (ValueError,KeyError,OSError) as exc:
        print(str(exc));raise SystemExit(2)

if __name__=='__main__':main()
