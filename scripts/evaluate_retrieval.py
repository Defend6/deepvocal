"""Offline retrieval metrics. Standard library only; no model or network calls."""
import argparse, json, math
from pathlib import Path

def rows(path):
    result = {}
    for line_no, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip(): continue
        item = json.loads(line)
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
            raise ValueError(f'{path}:{line_no}: valid string id required')
        if item['id'] in result: raise ValueError(f'duplicate id: {item["id"]}')
        result[item['id']] = item
    if not result: raise ValueError('dataset cannot be empty')
    return result

def ids(item, key, nonempty=False):
    values = item.get(key)
    if not isinstance(values, list) or any(not isinstance(v,str) or not v for v in values):
        raise ValueError(f'{item.get("id")}: {key} must be a list of nonempty strings')
    values = list(dict.fromkeys(values))
    if nonempty and not values: raise ValueError('gold relevant set cannot be empty')
    return values

def evaluate(gold, predictions, k=5):
    if k < 1: raise ValueError('k must be positive')
    if set(gold) != set(predictions):
        raise ValueError(f'id mismatch missing={sorted(set(gold)-set(predictions))} extra={sorted(set(predictions)-set(gold))}')
    details=[]
    for qid,item in gold.items():
        correct=set(ids(item,'gold_chunk_ids',True))
        retrieved=ids(predictions[qid],'retrieved_chunk_ids')[:k]
        hits=[i+1 for i,cid in enumerate(retrieved) if cid in correct]
        details.append(dict(id=qid,hit=int(bool(hits)),recall=len(correct.intersection(retrieved))/len(correct),reciprocal_rank=1/min(hits) if hits else 0,retrieved=retrieved,gold=sorted(correct)))
    n=len(details); p=sum(d['hit'] for d in details)/n; z=1.96
    center=(p+z*z/(2*n))/(1+z*z/n)
    spread=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return dict(n=n,k=k,hit_at_k=p,recall_at_k=sum(d['recall'] for d in details)/n,mrr_at_k=sum(d['reciprocal_rank'] for d in details)/n,hit_wilson95=[center-spread,center+spread],synthetic=all(x.get('synthetic') is True for x in gold.values()),per_query=details)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--gold',required=True);parser.add_argument('--pred',required=True);parser.add_argument('--k',type=int,default=5);parser.add_argument('--out');args=parser.parse_args()
    try: report=evaluate(rows(args.gold),rows(args.pred),args.k)
    except (ValueError,KeyError,TypeError) as e: parser.error(str(e))
    output=json.dumps(report,ensure_ascii=False,indent=2)
    if args.out: Path(args.out).write_text(output+'\n',encoding='utf-8')
    print(output)
