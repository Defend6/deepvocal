"""Offline tool-miscall evaluation; synthetic fixtures do not establish real gains."""
from pathlib import Path
import argparse,json

def evaluate(records):
    if not records:raise ValueError('empty records')
    groups={}
    for r in records:
        cfg=r.get('configuration')
        if cfg not in ('single_agent_baseline','langgraph_multiagent'):raise ValueError('invalid configuration')
        group=groups.setdefault(cfg,{})
        if not isinstance(r.get('id'),str) or not r['id'] or r['id'] in group:raise ValueError('missing or duplicate id in configuration')
        if type(r.get('task_pass')) is not bool or type(r.get('clarification')) is not bool:raise ValueError('explicit boolean labels required')
        if not isinstance(r.get('allowed_tools'),list) or any(not isinstance(t,str) for t in r['allowed_tools']):raise ValueError('allowed_tools required')
        if not isinstance(r.get('tool_calls'),list):raise ValueError('tool_calls required')
        for call in r['tool_calls']:
            if not isinstance(call.get('tool'),str) or not call['tool']:raise ValueError('tool name required')
            if any(type(call.get(k)) is not bool for k in ('params_valid','resource_allowed','preconditions_met','executed')):raise ValueError('tool policy labels must be booleans')
        group[r['id']]=r
    if len(groups)==2:
        a,b=groups.values()
        if set(a)!=set(b):raise ValueError('paired configurations must have identical request ids')
        for qid in a:
            for key in ('expected_agents','allowed_tools','should_clarify'):
                if a[qid].get(key)!=b[qid].get(key):raise ValueError('ground truth differs across configurations')
    result={}
    for cfg,group in groups.items():
        details=[];bad_requests=bad_calls=total_calls=invalid_executed=passed=clarified=0
        for r in group.values():
            wrong=0
            for call in r['tool_calls']:
                bad=call['tool'] not in r['allowed_tools'] or not all(call[k] for k in ('params_valid','resource_allowed','preconditions_met'))
                wrong+=int(bad);invalid_executed+=int(bad and call['executed'])
            total_calls+=len(r['tool_calls']);bad_calls+=wrong;bad_requests+=int(wrong>0)
            passed+=int(r['task_pass']);clarified+=int(r['clarification'])
            details.append({'id':r['id'],'wrong_tool_attempts':wrong,'task_pass':r['task_pass'],'clarification':r['clarification']})
        n=len(group)
        result[cfg]={'n':n,'request_miscall_rate':bad_requests/n,'tool_miscall_rate':bad_calls/total_calls if total_calls else None,'wrong_tool_attempts':bad_calls,'total_tool_attempts':total_calls,'invalid_executed_calls':invalid_executed,'task_completion_rate':passed/n,'clarification_rate':clarified/n,'synthetic':all(x.get('synthetic') is True for x in group.values()),'per_query':details}
    return {'scope':'offline annotated traces; blocked attempts count as mistakes','configurations':result}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);a=p.parse_args()
    try:out=evaluate([json.loads(s) for s in Path(a.input).read_text(encoding='utf-8').splitlines() if s.strip()])
    except (ValueError,KeyError,TypeError) as e:p.error(str(e))
    print(json.dumps(out,ensure_ascii=False,indent=2))
