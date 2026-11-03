"""Validate the documentation bundle without installing packages or running services."""
from pathlib import Path
import csv, hashlib, importlib.util, json, math, re, sys

ROOT=Path(__file__).resolve().parents[1]
failures=[]; checks=[]
def check(condition,label):
    (checks if condition else failures).append(label)

def get_json(name):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))

def load_module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def schema_check(value,schema,root,path='$'):
    """Subset used by bundled examples; not a full OpenAPI or JSON Schema validator."""
    if '$ref' in schema:
        target=root
        for part in schema['$ref'][2:].split('/'): target=target[part]
        return schema_check(value,target,root,path)
    for child in schema.get('allOf',[]):schema_check(value,child,root,path)
    if 'if' in schema:
        try:schema_check(value,schema['if'],root,path);condition=True
        except ValueError:condition=False
        schema_check(value,schema.get('then' if condition else 'else',{}),root,path)
    if 'not' in schema:
        try:schema_check(value,schema['not'],root,path)
        except ValueError:pass
        else:raise ValueError(path+' prohibited condition')
    if 'anyOf' in schema:
        for child in schema['anyOf']:
            try: schema_check(value,child,root,path);return
            except ValueError: pass
        raise ValueError(path+' no anyOf branch matched')
    t=schema.get('type')
    allowed=t if isinstance(t,list) else [t]
    actual='null' if value is None else 'boolean' if type(value) is bool else 'integer' if type(value) is int else 'number' if type(value) is float else 'string' if isinstance(value,str) else 'object' if isinstance(value,dict) else 'array' if isinstance(value,list) else 'unknown'
    if t is not None and actual not in allowed and not(actual=='integer' and 'number' in allowed): raise ValueError(path+' invalid type '+actual)
    if 'enum' in schema and value not in schema['enum']: raise ValueError(path+' invalid enum')
    if 'const' in schema and value != schema['const']: raise ValueError(path+' invalid const')
    if isinstance(value,dict):
        if not set(schema.get('required',[])).issubset(value): raise ValueError(path+' missing required')
        if len(value)<schema.get('minProperties',0) or len(value)>schema.get('maxProperties',10**10):raise ValueError(path+' invalid property count')
        props=schema.get('properties',{})
        if schema.get('additionalProperties') is False and set(value)-set(props): raise ValueError(path+' unknown property')
        for k,v in value.items():
            if k in props: schema_check(v,props[k],root,path+'.'+k)
    if isinstance(value,list):
        if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',10**10):raise ValueError(path+' invalid item count')
        if schema.get('uniqueItems') and len({json.dumps(v,sort_keys=True) for v in value})!=len(value):raise ValueError(path+' duplicate items')
        if 'contains' in schema:
            matches=0
            for v in value:
                try:schema_check(v,schema['contains'],root,path);matches+=1
                except ValueError:pass
            if not(schema.get('minContains',1)<=matches<=schema.get('maxContains',10**10)):raise ValueError(path+' contains constraint')
        for i,v in enumerate(value): schema_check(v,schema.get('items',{}),root,path+f'[{i}]')
    if isinstance(value,str):
        if len(value)<schema.get('minLength',0) or len(value)>schema.get('maxLength',10**10): raise ValueError(path+' invalid length')
        if 'pattern' in schema and not re.search(schema['pattern'],value): raise ValueError(path+' pattern mismatch')
        if schema.get('format')=='uuid' and not re.fullmatch('[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',value):raise ValueError(path+' invalid uuid')
    if type(value) in (int,float):
        if not math.isfinite(value):raise ValueError(path+' nonfinite number')
        if value<schema.get('minimum',-math.inf) or value>schema.get('maximum',math.inf): raise ValueError(path+' out of bounds')

def main():
    docs=sorted((ROOT/'docs').glob('*.md'))
    check(len(docs)==27,'27 份需求设计文档齐全')
    for p in docs:
        text=p.read_text(encoding='utf-8')
        check(text.startswith('# '),p.name+' 有明确标题')
        check(len(text)>600,p.name+' 含实质内容')
        check(text.count('```')%2==0,p.name+' 代码围栏闭合')
    for name in ['README.md','index.html','prototype/index.html','database/schema.sql','contracts/openapi.json','contracts/requirements.json','contracts/skill.schema.json','configs/.env.example','configs/repro-manifest.template.json','sources/github-snapshot.json']:
        check((ROOT/name).is_file(),'存在 '+name)
    for p in ROOT.rglob('*.json'):
        if '_qa' in p.relative_to(ROOT).parts: continue
        try:json.loads(p.read_text(encoding='utf-8'));check(True,'JSON 可解析 '+str(p.relative_to(ROOT)))
        except ValueError: check(False,'JSON 无效 '+str(p.relative_to(ROOT)))
    reqs=get_json('contracts/requirements.json')
    decisions=get_json('contracts/decision-register.json')['questions']
    check([q['id'] for q in decisions]==[f'D-{i:03}' for i in range(1,41)],'40 项待确定问题编号完整且唯一')
    check(all(q['status']!='UNANSWERED' and q['implementation_rule'] and q['basis']==('USER_ANSWER' if q['answer'] else 'ENGINEERING_DEFAULT_NOT_USER_ANSWER') for q in decisions),'答案、推迟选择及工程默认明确区分')
    questionnaire=(ROOT/'docs/19_待确定问题相关文档_请填写.md').read_text(encoding='utf-8')
    check(all(q['id'] in questionnaire for q in decisions) and (ROOT/'sources/user-inputs/待确定问题_用户原始答卷.md').is_file(),'决策汇总包含全部编号且保留用户原始答卷')
    data=get_json('datasets/SUMMARY.json')
    source_rows=get_json('datasets/source-registry.json')['sources']
    check(len(source_rows)==data['source_count'] and all(not x['production_enabled'] for x in source_rows),'来源数与统计一致且未生产激活')
    for filename,key in [('documents','document_count'),('chunks','chunk_count'),('entities','entity_count'),('mentions','mention_count'),('relations_candidate','candidate_relation_count'),('exercise_candidates','exercise_candidate_count'),('score_segments','score_segment_count'),('audio_inventory','audio_clip_count'),('note_annotations','note_annotation_count')]:
        count=sum(bool(s.strip()) for s in (ROOT/f'datasets/normalized/{filename}.jsonl').read_text(encoding='utf-8').splitlines())
        check(count==data[key],'真实数据统计 '+filename)
    check(data['user_practice_record_count']==0 and all(not data[k] for k in ['embeddings_computed','neo4j_loaded','milvus_loaded','expert_review_completed']),'没有虚构私人记录、模型入库或专家审核')
    check((ROOT/'datasets/normalized/graphrag-seed.sqlite3').is_file(),'已交付实际 SQLite 数据准备库')
    ids=[x['id'] for x in reqs['features']+reqs['non_functional']]
    check(len(ids)==58 and len(set(ids))==len(ids),'46 FR 与 12 NFR 编号唯一')
    with (ROOT/'requirements-traceability.csv').open(encoding='utf-8-sig',newline='') as f:trace=list(csv.DictReader(f))
    check({r['id'] for r in trace}=={r['id'] for r in reqs['features']},'CSV 覆盖全部 FR')
    check(all(r['implementation_status']=='NOT_IMPLEMENTED' and r['test_status']=='NOT_RUN' for r in trace),'追踪表没有虚构实现和验收状态')
    srs=(ROOT/'docs/03_软件需求规格说明书SRS.md').read_text(encoding='utf-8')
    check(all(x in srs for x in ids),'SRS 覆盖全部需求编号')
    acceptance=get_json('fixtures/acceptance-run-template.json')
    tests=[r[k] for r in reqs['features'] for k in ['test_positive','test_negative']]
    check(len(tests)==len(set(tests))==92,'92 条正反向验收编号唯一')
    check(set(tests)=={t['id'] for t in acceptance['test_cases']},'验收模板覆盖全部 FR 正反向用例')
    check(all(t['status']=='NOT_RUN' for t in acceptance['test_cases']),'真实系统验收保持 NOT_RUN')
    api=get_json('contracts/openapi.json');schemas=api['components']['schemas'];ops=[]
    def refs(value):
        if isinstance(value,dict):
            if '$ref' in value:
                target=api
                try:
                    for part in value['$ref'][2:].split('/'):target=target[part]
                except (KeyError,TypeError): failures.append('无效引用 '+value['$ref'])
            if 'required' in value and isinstance(value['required'],list) and 'properties' in value and value.get('additionalProperties') is False:
                check(set(value['required']).issubset(value['properties']),'required 字段存在于 properties')
            for child in value.values():refs(child)
        elif isinstance(value,list):
            for child in value:refs(child)
    refs(api)
    for path,item in api['paths'].items():
        for method,op in item.items():
            ops.append(op['operationId'])
            check(all(any(p['name']==v and p['in']=='path' and p['required'] for p in op.get('parameters',[])) for v in re.findall(r'\{(\w+)\}',path)),'路径参数 '+method+' '+path)
            if path.startswith('/admin/'):
                check('admin scope' in op.get('description',''),'管理权限说明 '+path)
    check(len(set(ops))==len(ops),'OpenAPI operationId 唯一')
    check(api['openapi']=='3.1.0','OpenAPI 版本为 3.1.0')
    try:
        schema_check(get_json('fixtures/report-example.json'),schemas['Report'],api)
        schema=get_json('contracts/skill.schema.json');skill=get_json('fixtures/skill-example.json')
        schema_check(skill,schema,schema)
        check(True,'报告和 Skill 样例符合所使用的 schema 子集')
        check(skill['content_hash']==hashlib.sha256((ROOT/'fixtures/skill-body.txt').read_bytes()).hexdigest(),'Skill 内容哈希与实际文件一致')
    except ValueError as e:check(False,'样例校验 '+str(e))
    multi=get_json('configs/multiagent.template.json')
    check(set(multi['agents'])=={'knowledge','assessment','planning','singing'} and multi['specialist_count']==4,'恰好四个业务 Agent 子图')
    check(multi['capability_mapping']['recording_diagnosis']==multi['capability_mapping']['full_song_assessment']=='assessment','录音诊断和全曲均归属 Song Assessment')
    check('singing.submit' not in multi['agents']['knowledge']['tools'],'知识 Agent 未获得生成工具')
    decision=get_json('fixtures/route-decision-example.json')
    schema_check(decision,get_json('contracts/route-decision.schema.json'),{})
    check(decision['primary_intent'] in decision['requested_intents'],'路由主意图属于请求意图集合')
    route_records=[json.loads(s) for s in (ROOT/'fixtures/routing-traces.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    for record in route_records:schema_check(record,get_json('contracts/routing-eval.schema.json'),{})
    routing=load_module('evaluate_routing').evaluate(route_records)['configurations']
    check(math.isclose(routing['single_agent_baseline']['request_miscall_rate'],2/3) and routing['langgraph_multiagent']['request_miscall_rate']==0,'合成路由轨迹误调用公式及阻断尝试计数')
    check(all(r['invalid_executed_calls']==0 for r in routing.values()),'合成轨迹区分误调用尝试与非法执行')
    for p in [ROOT/'README.md',ROOT/'index.html',ROOT/'prototype/index.html']:
        raw=p.read_text(encoding='utf-8')
        links=re.findall(r'href="([^"]+)"',raw) if p.suffix=='.html' else re.findall(r'\]\(([^)]+)\)',raw)
        for link in links:
            if re.match(r'^(https?:|mailto:|#)',link):continue
            check((p.parent/link.split('#')[0]).exists(),'本地链接 '+str(p.relative_to(ROOT))+' → '+link)
    index=(ROOT/'index.html').read_text(encoding='utf-8')
    for source in re.findall(r'<img[^>]+src="([^"]+)"',index):
        check((ROOT/source).is_file(),'文档架构图存在 '+source)
    check(all(f'id="doc{i:02}"' in index for i in range(len(docs))),'总览包含全部文档锚点')
    check('演示数据' in (ROOT/'prototype/index.html').read_text(encoding='utf-8'),'原型显式标示演示数据')
    lock=get_json('configs/repro-manifest.template.json')
    check(lock['status']=='design_only' and lock['code_commit'] is None,'未锁环境没有冒充可严格复现')
    sql=(ROOT/'database/schema.sql').read_text(encoding='utf-8')
    tables=re.findall(r'CREATE TABLE (\w+)',sql)
    check(len(tables)==len(set(tables)) and len(tables)>=27,'候选表名唯一且覆盖核心模型')
    check(sql.strip().startswith('--') and 'BEGIN;' in sql and 'COMMIT;' in sql,'DDL 有事务边界')
    check(all(t in tables for t in re.findall(r'REFERENCES (\w+)',sql)),'DDL 外键目标表均已声明')
    retrieval=load_module('evaluate_retrieval');gold=retrieval.rows(ROOT/'fixtures/retrieval_gold.jsonl')
    vector=retrieval.evaluate(gold,retrieval.rows(ROOT/'fixtures/predictions-vector.jsonl'))
    graph=retrieval.evaluate(gold,retrieval.rows(ROOT/'fixtures/predictions-graph.jsonl'))
    check(vector['n']==6 and vector['hit_at_k']==0.5 and vector['mrr_at_k']==0.5,'合成检索样例公式 Vector')
    check(graph['n']==6 and math.isclose(graph['hit_at_k'],5/6) and math.isclose(graph['mrr_at_k'],5/12),'合成检索样例公式 Graph')
    try:retrieval.evaluate(gold,{});check(False,'缺失预测应拒绝')
    except ValueError:check(True,'缺失预测被拒绝')
    token=load_module('evaluate_tokens');counts=[json.loads(s) for s in (ROOT/'fixtures/token_counts.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    reduction=token.evaluate(counts)
    check(math.isclose(reduction['weighted_reduction'],1700/3000),'合成 Token 样例加权公式正确')
    try:token.evaluate([dict(id='bad',eager_skill_tokens=0,lazy_skill_tokens=1)]);check(False,'零分母应拒绝')
    except ValueError:check(True,'Token 零分母被拒绝')
    integrity=ROOT/'MANIFEST.sha256'
    if integrity.is_file() and '--skip-integrity' not in sys.argv:
        valid=True
        for line in integrity.read_text(encoding='utf-8').splitlines():
            digest,relative=line.split('  ',1);target=(ROOT/relative).resolve()
            if ROOT not in target.parents or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:valid=False
        check(valid,'交付文件 SHA256 完整性校验')
    result=dict(scope='documentation_data_counts_and_synthetic_formula_checks_only',passed=len(checks),failed=len(failures),failures=failures,documents=len(docs),functional_requirements=len(reqs['features']),nonfunctional_requirements=len(reqs['non_functional']),api_operations=len(ops),tables=len(tables),acceptance_cases=len(tests),integrity_checked=integrity.is_file() and '--skip-integrity' not in sys.argv,sql_executed=False,models_executed=False,application_tests_executed=False)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if failures else 0

if __name__=='__main__':sys.exit(main())
