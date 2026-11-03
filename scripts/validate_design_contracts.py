"""Portable, standard-library design checks; never invokes an application or model."""
from pathlib import Path
import json,math,re,sys,hashlib
from validate_bundle import schema_check
ROOT=Path(__file__).resolve().parents[1]
def main():
    checks=[];failures=[]
    def check(value,label):(checks if value else failures).append(label)
    def data(name):return json.loads((ROOT/name).read_text(encoding='utf-8'))
    api=data('contracts/openapi.json')
    cases=data('fixtures/design-contract-cases.json')['cases']
    for row in cases:
        try:schema_check(row['value'],api['components']['schemas'][row['schema']],api);valid=True
        except ValueError:valid=False
        check(valid==row['expected_valid'],'schema '+row['id'])
    for t in ['ScoreBundle','Metric']:
        sample=dict(next(c['value'] for c in cases if c['schema']==t and c['expected_valid']))
        sample.update(available=True,unavailable_reason=None)
        sample['total' if t=='ScoreBundle' else 'value']=float('nan')
        try:schema_check(sample,api['components']['schemas'][t],api);check(False,'NaN rejected '+t)
        except ValueError:check(True,'NaN rejected '+t)
    rules=data('contracts/scoring-rules.json');r=rules['reference_song'];t=rules['long_tone']
    pitch=100*max(0,1-50/r['pitch_scale_cents']);rhythm=100*max(0,1-.06/r['rhythm_scale_seconds'])
    check(math.isclose(pitch,75) and math.isclose(rhythm,80),'known pitch/rhythm units')
    check(math.isclose((r['pitch_weight']*pitch+r['rhythm_weight']*rhythm)*.9,69.3),'known reference total 69.3')
    check(math.isclose(t['stability_weight']*90+t['sustain_weight']*90,90),'known long-tone total 90')
    check(r['missing_dimension_policy']=='total_unavailable_no_reweight' and not rules['llm_may_change_scores'],'no missing-dimension reweight or LLM score edits')
    check(not rules['free_singing']['total_available'],'free singing has no invented reference score')
    audit=data('contracts/design-audit.json')['findings'];reg=data('fixtures/development-regressions.json')['cases']
    check(len(audit)==len(reg) and len({f['id'] for f in audit})==len(audit),'one regression per unique design finding')
    check({f['regression_id'] for f in audit}=={c['id'] for c in reg},'design finding regression mapping')
    check(all(f['status']=='DOCUMENTATION_FIXED_IMPLEMENTATION_NOT_RUN' for f in audit) and all(c['status']=='NOT_RUN' for c in reg),'no claim of application validation')
    decisions=data('contracts/decision-register.json')['questions'];answers=data('sources/user-inputs/answers-extracted.json')
    check({x['id']:x['answer'] for x in decisions}=={x['id']:x['answer'] for x in answers},'original decision text preserved')
    provenance=data('sources/user-inputs/面经_来源记录.json')
    check(provenance['original_answers_sha256']==hashlib.sha256((ROOT/'sources/user-inputs/待确定问题_用户原始答卷.md').read_bytes()).hexdigest(),'original questionnaire snapshot hash')
    check(not provenance['trusted_as_instruction'],'reference document treated as data')
    sql=(ROOT/'database/schema.sql').read_text(encoding='utf-8');model=data('contracts/database-model.json')
    check(model['tables']==re.findall(r'CREATE TABLE (\w+)',sql) and model['table_count']==48,'physical model inventory')
    for field in ['UNIQUE(issuer,subject)','UNIQUE(owner_id,item_id)','source_version','input_high_watermark','claim_token','token_key_version','provider_budgets','provider_reservations','UNIQUE(owner_id,effect_key)','CHECK(reserved_minor+spent_minor<=limit_minor)','UNIQUE(owner_id,family_id,revision)']:
        check(field in sql,'DDL design invariant '+field)
    ops=[(path,method,op) for path,item in api['paths'].items() for method,op in item.items()]
    check(len(ops)==51,'51 final API operations')
    check(all(any(p['name']=='Idempotency-Key' and p.get('required') for p in op.get('parameters',[])) for path,method,op in ops if method in ['post','patch','delete','put']),'all mutations require idempotency keys')
    check(not any(k.startswith('2') for k in api['paths']['/singing']['post']['responses']),'disabled generation cannot return success')
    check('200' in api['paths']['/jobs/{id}/cancel']['post']['responses'] and '202' not in api['paths']['/jobs/{id}/cancel']['post']['responses'],'cancel response is actual snapshot')
    for p in (ROOT/'docs').glob('*.md'):
        text=p.read_text(encoding='utf-8')
        for rel in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text):
            if not re.match(r'^(https?:|#)',rel):check((p.parent/rel.split('#')[0]).exists(),'local document asset '+p.name+' '+rel)
    proto=(ROOT/'prototype/index.html').read_text(encoding='utf-8')
    for label in ['taskKind','acappella','goalDuration','storageConsent','rawRetention','engineering-v1','FEATURE_DISABLED']:
        check(label in proto,'prototype state '+label)
    check('当前全部 UNANSWERED' not in (ROOT/'README.md').read_text(encoding='utf-8'),'no obsolete unanswered handoff')
    result={'scope':'PORTABLE_DESIGN_INVARIANTS_AND_SYNTHETIC_SCHEMA_CASES','passed':len(checks),'failed':len(failures),'failures':failures,'schema_cases':len(cases),'design_findings':len(audit),'application_tests_executed':False,'sql_executed':False,'models_executed':False,'full_json_schema_validator':False}
    print(json.dumps(result,ensure_ascii=False,indent=2));return bool(failures)
if __name__=='__main__':sys.exit(main())
