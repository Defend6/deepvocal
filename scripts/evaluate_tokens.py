"""Compare recorded Skill injection counts, not billing cost or output quality."""
import argparse,json,statistics
from pathlib import Path

def evaluate(items):
    if not items: raise ValueError('empty input')
    ids=set(); eager=lazy=0; savings=[]
    for r in items:
        if not isinstance(r.get('id'),str) or not r['id'] or r['id'] in ids: raise ValueError('missing/duplicate id')
        ids.add(r['id']); a=r.get('eager_skill_tokens'); b=r.get('lazy_skill_tokens')
        if type(a) is not int or a<=0 or type(b) is not int or b<0: raise ValueError('counts must be integers, eager > 0 and lazy >= 0')
        eager+=a;lazy+=b;savings.append((a-b)/a)
    return dict(n=len(items),eager_skill_tokens=eager,lazy_skill_tokens=lazy,weighted_reduction=(eager-lazy)/eager,median_reduction=statistics.median(savings),min_reduction=min(savings),max_reduction=max(savings),synthetic=all(x.get('synthetic') is True for x in items),scope='Skill injection only; not total bill reduction')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);a=p.parse_args()
    try: report=evaluate([json.loads(s) for s in Path(a.input).read_text(encoding='utf-8').splitlines() if s.strip()])
    except (ValueError,KeyError,TypeError) as e: p.error(str(e))
    print(json.dumps(report,ensure_ascii=False,indent=2))
