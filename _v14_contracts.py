"""Machine-readable design contracts for the final documentation baseline."""
import json,re,copy,hashlib

def save(write,path,value):write(path,json.dumps(value,ensure_ascii=False,indent=2))
def obj(p,required=(),**extra):return dict(type='object',properties=p,required=list(required),additionalProperties=False,**extra)
def string(**kw):return dict(type='string',**kw)
def integer(**kw):return dict(type='integer',**kw)
def number(**kw):return dict(type='number',**kw)
def arr(x,**kw):return dict(type='array',items=x,**kw)
def ref(n):return {'$ref':'#/components/schemas/'+n}
UUID=string(format='uuid');BOOL={'type':'boolean'};TIME=string(format='date-time')

def update_contracts(write,root,paths,s):
    api=json.loads((root/'contracts/openapi.json').read_text(encoding='utf-8'))
    s=api['components']['schemas'];paths=api['paths'];api['info']['version']='1.4.0'
    api['info']['description']='DOCUMENTATION DESIGN ONLY. Research zh corpus, acappella, engineering scores. Singing disabled. No app implementation.'
    # General profiles and immutable business references.
    s['Profile']['properties']['version']=integer(minimum=1);s['Profile']['required'].append('version')
    s['ProfilePatch']=obj({**copy.deepcopy(s['Profile']['properties'])},['version'],minProperties=2)
    paths['/me/profile']['patch']['requestBody']['content']['application/json']['schema']=ref('ProfilePatch')
    s['AssessmentRequest']['properties'].update(task_kind=string(enum=['free_singing','long_tone','reference_song']),acappella_confirmed={'const':True,'type':'boolean'},reference_transpose_semitones=integer(minimum=-12,maximum=12))
    s['AssessmentRequest']['required']+=['task_kind','acappella_confirmed']
    s['AssessmentRequest']['allOf']=[
     {'if':{'properties':{'reference_kind':{'const':'none'}}},'then':{'not':{'required':['reference_asset_id']},'properties':{'task_kind':{'enum':['free_singing','long_tone']},'reference_transpose_semitones':{'const':0}}},'else':{'required':['reference_asset_id'],'properties':{'task_kind':{'const':'reference_song'}}}},
     {'if':{'properties':{'task_kind':{'const':'reference_song'}}},'then':{'properties':{'reference_kind':{'enum':['midi','dry_vocal']}}}}
    ]
    s['Metric']['properties']['unavailable_reason'].update(minLength=1)
    s['Metric']['allOf']=[{'if':{'properties':{'available':{'const':False}}},'then':{'properties':{'value':{'type':'null'},'unavailable_reason':string(minLength=1)}},'else':{'properties':{'value':number(),'unavailable_reason':{'type':'null'}}}}]
    s['ScoreDimension']=obj({'name':string(minLength=1),'value':{'type':['number','null'],'minimum':0,'maximum':100},'available':BOOL,'unavailable_reason':{'type':['string','null']},'coverage':{'type':['number','null'],'minimum':0,'maximum':1}},['name','value','available','unavailable_reason','coverage'],allOf=[{'if':{'properties':{'available':{'const':False}}},'then':{'properties':{'value':{'type':'null'},'unavailable_reason':string(minLength=1)}},'else':{'properties':{'value':number(minimum=0,maximum=100),'unavailable_reason':{'type':'null'}}}}])
    s['ScoreBundle']=obj({'profile':string(enum=['reference_song','long_tone','free_singing']),'ruleset_version':string(minLength=1),'available':BOOL,'total':{'type':['number','null'],'minimum':0,'maximum':100},'unavailable_reason':{'type':['string','null']},'dimensions':arr(ref('ScoreDimension')),'coverage':{'type':['number','null'],'minimum':0,'maximum':1},'label':{'const':'练习参考分','type':'string'},'professional_validation':{'const':False,'type':'boolean'}},['profile','ruleset_version','available','total','unavailable_reason','dimensions','coverage','label','professional_validation'],allOf=[{'if':{'properties':{'available':{'const':False}}},'then':{'properties':{'total':{'type':'null'},'unavailable_reason':string(minLength=1)}},'else':{'properties':{'total':number(minimum=0,maximum=100),'unavailable_reason':{'type':'null'}}}},{'if':{'properties':{'profile':{'const':'free_singing'}}},'then':{'properties':{'available':{'const':False}}}}])
    s['Report']['properties'].update(scores=ref('ScoreBundle'),reference_snapshot={'type':'object','additionalProperties':True},measurement_version=string(minLength=1))
    s['Report']['required']+=['scores','measurement_version']
    s['Report']['allOf']=[{'if':{'properties':{'quality':{'properties':{'status':{'const':'quality_failed'}}}}},'then':{'properties':{'scores':{'properties':{'available':{'const':False},'dimensions':{'maxItems':0}}},'metrics':{'maxItems':0}}}}]
    s['Capabilities']['properties'].update(enabled=BOOL,mode=string(enum=['disabled','mock','real']),backend_kind=string(enum=['disabled','mock','svs','svc']),max_seconds=integer(minimum=0))
    s['Capabilities']['required'].append('enabled')
    s['Capabilities']['allOf']=[{'if':{'properties':{'enabled':{'const':False}}},'then':{'properties':{'mode':{'const':'disabled'},'backend_kind':{'const':'disabled'},'max_seconds':{'const':0},'requires_training':{'const':False},'supports_lyrics':{'const':False},'supports_midi':{'const':False},'supports_dry_vocal':{'const':False},'supported_languages':{'maxItems':0},'model_revision':{'type':'null'}}}}]
    disabled={'adapter_id':'reserved-existing-service','enabled':False,'mode':'disabled','backend_kind':'disabled','supports_lyrics':False,'supports_midi':False,'supports_dry_vocal':False,'supported_languages':[],'max_seconds':0,'model_revision':None,'requires_training':False}
    save(write,'fixtures/singing-disabled-example.json',disabled)
    paths['/singing']['post']['responses'].pop('202',None)
    paths['/singing']['post']['responses']['501']={'description':'FEATURE_DISABLED. Authenticated placeholder; never creates jobs or audio.','content':{'application/json':{'schema':ref('Error')}}}
    paths['/singing']['post']['description']='Future integration only. Current feature flag false: return 501 FEATURE_DISABLED before charging, creating job or accepting voice samples.'
    paths['/singing/capabilities']['get']['responses']['200']['content']['application/json']['example']=disabled
    # Do not allow response-only fields to be written by clients.
    s['PlanItemInput']=copy.deepcopy(s['PlanItem']);s['PlanItemInput']['properties'].pop('completed');s['PlanItemInput']['required'].remove('completed')
    s['PlanPatch']['properties']['items']=arr(ref('PlanItemInput'))
    s['MemoryFactPatch']['properties']['version']=integer(minimum=1);s['MemoryFactPatch']['required'].append('version')
    s['MemoryFact']['properties']['version']=integer(minimum=1);s['MemoryFact']['required'].append('version')
    s['Accepted']['properties']['workflow_id']=UUID
    s['ChatResponse']['properties']['proposed_actions']=arr(obj({'action':string(enum=['assessment_then_planning','knowledge_then_planning']),'summary':string(maxLength=500),'missing_fields':arr(string())},['action','summary','missing_fields']))
    s['Workflow']=obj({'id':UUID,'status':string(enum=['CREATED','RUNNING','WAIT_JOB','WAIT_USER_INPUT','COMPLETED','FAILED','CANCELLED']),'version':integer(minimum=1),'session_id':UUID,'pending_fields':arr(string()),'job_ids':arr(UUID),'artifact_refs':arr(UUID),'error_code':{'type':['string','null']},'trace_id':string()},['id','status','version','session_id','pending_fields','job_ids','artifact_refs','error_code','trace_id'])
    s['WorkflowCreate']=obj({'session_id':UUID,'action':string(enum=['assessment_then_planning','knowledge_then_planning']),'asset_id':UUID,'assessment':ref('AssessmentRequest'),'question':string(minLength=1,maxLength=5000),'goal':string(minLength=1,maxLength=500),'minutes_per_day':integer(minimum=5,maximum=120),'days':integer(enum=[7,14]),'confirmed':{'type':'boolean','const':True}},['session_id','action','goal','minutes_per_day','days','confirmed'],allOf=[{'if':{'properties':{'action':{'const':'assessment_then_planning'}}},'then':{'required':['assessment'],'not':{'required':['question']}},'else':{'required':['question'],'not':{'required':['assessment']}}}])
    s['WorkflowCreate']['properties'].pop('asset_id')
    s['WorkflowWait']=obj({'workflow_id':UUID,'version':integer(minimum=1),'waiting':BOOL,'resume_token':{'type':['string','null'],'maxLength':256},'expires_at':{'type':['string','null'],'format':'date-time'},'pending_schema':{'type':'object','additionalProperties':True}},['workflow_id','version','waiting','resume_token','expires_at','pending_schema'])
    s['WorkflowResume']=obj({'version':integer(minimum=1),'resume_token':string(minLength=16,maxLength=256),'input':{'type':'object','maxProperties':12,'additionalProperties':True}},['version','resume_token','input'])
    purposes=['analysis','practice_storage','cross_session_memory','research_improvement','singing_reference']
    s['PurposeConsentRequest']=obj({'purpose':string(enum=purposes),'granted':BOOL,'text_version':string(minLength=1,maxLength=100)},['purpose','granted','text_version'])
    s['PurposeConsent']=obj({'id':UUID,'purpose':string(enum=purposes),'status':string(enum=['granted','refused','revoked']),'version':integer(minimum=1),'created_at':TIME,'revoked_at':{'type':['string','null'],'format':'date-time'}},['id','purpose','status','version','created_at','revoked_at'])
    s['PurposeConsentsPage']=obj({'items':arr(ref('PurposeConsent')),'next_cursor':{'type':['string','null']}},['items','next_cursor'])
    s['Retention']=obj({'raw_audio_days':integer(enum=[7,30,90,180]),'report_days':integer(enum=[7,30,90,180]),'technical_log_days':integer(const=30),'backup_max_days':integer(const=30),'version':integer(minimum=1)},['raw_audio_days','report_days','technical_log_days','backup_max_days','version'])
    s['RetentionPatch']=obj({'raw_audio_days':integer(enum=[7,30,90,180]),'report_days':integer(enum=[7,30,90,180]),'version':integer(minimum=1)},['version'],minProperties=2)
    s['Corpus']=obj({'version':string(minLength=1,maxLength=100),'language':string(const='zh'),'state':string(enum=['building','checked','active','retired','failed']),'revision':integer(minimum=1),'review_level':string(enum=['candidate','evidence_checked']),'expert_verified':{'type':'boolean','const':False}},['version','language','state','revision','review_level','expert_verified'])
    s['CorpusActivate']=obj({'expected_active_version':{'type':['string','null']},'expected_revision':integer(minimum=1)},['expected_active_version','expected_revision'])
    def endpoint(path,method,request,response,status=200,admin=False,paged=False):
        op={'operationId':method+'_'+re.sub('[^a-z0-9]+','_',path.lower()).strip('_'),'summary':'Design contract '+path,'description':'Server enforced ownership. '+('Requires admin scope. ' if admin else '')+'Idempotency and state checks apply.','responses':{str(status):{'description':'Design success','content':{'application/json':{'schema':ref(response)}}}}}
        if request:op['requestBody']={'required':True,'content':{'application/json':{'schema':ref(request)}}}
        op['parameters']=[{'name':v,'in':'path','required':True,'schema':UUID if v=='id' else string(minLength=1,maxLength=100)} for v in re.findall(r'\{(\w+)\}',path)]
        if paged:op['parameters']+=[{'name':'limit','in':'query','schema':integer(minimum=1,maximum=100)},{'name':'cursor','in':'query','schema':string(maxLength=2048)}]
        paths.setdefault(path,{})[method]=op
    endpoint('/workflows','post','WorkflowCreate','Workflow',202)
    endpoint('/workflows/{id}','get',None,'Workflow')
    endpoint('/workflows/{id}/wait','get',None,'WorkflowWait')
    endpoint('/workflows/{id}/resume','post','WorkflowResume','Workflow',202)
    endpoint('/consents','get',None,'PurposeConsentsPage',paged=True)
    endpoint('/consents','post','PurposeConsentRequest','PurposeConsent',201)
    endpoint('/consents/{id}','delete',None,'PurposeConsent')
    endpoint('/me/retention','get',None,'Retention')
    endpoint('/me/retention','patch','RetentionPatch','Retention')
    endpoint('/admin/corpora/{version}','get',None,'Corpus',admin=True)
    endpoint('/admin/corpora/{version}/activate','post','CorpusActivate','Corpus',admin=True)
    paths['/jobs/{id}/cancel']['post']['responses'].pop('202',None)
    paths['/jobs/{id}/cancel']['post']['responses']['200']={'description':'Current job snapshot; cancel request is not completion.','content':{'application/json':{'schema':ref('Job')}}}
    # Mark all mutating operations, including profile/plan/log/consent/resume, consistently.
    for path,methods in paths.items():
        for method,op in methods.items():
            if method in ['post','patch','delete']:
                pars=op.setdefault('parameters',[])
                if not any(x['name']=='Idempotency-Key' for x in pars):pars.append({'name':'Idempotency-Key','in':'header','required':True,'schema':string(minLength=8,maxLength=128)})
            for code in ['400','401','403','404','409','422','429','500','503']:
                op['responses'].setdefault(code,{'description':'Design error '+code,'content':{'application/json':{'schema':ref('Error')}}})
    save(write,'contracts/openapi.json',api)
    # Updated sample must pass the real conditional schema, not only a superficial subset.
    report=json.loads((root/'fixtures/report-example.json').read_text(encoding='utf-8'))
    report['measurement_version']='synthetic-only';report['scores']={'profile':'free_singing','ruleset_version':'engineering-v1','available':False,'total':None,'unavailable_reason':'REFERENCE_OR_DEFINED_TASK_REQUIRED','dimensions':[],'coverage':None,'label':'练习参考分','professional_validation':False}
    save(write,'fixtures/report-example.json',report)
    save(write,'contracts/scoring-rules.json',{'version':'engineering-v1','status':'DESIGN_NOT_PROFESSIONALLY_CALIBRATED','label':'练习参考分','professional_validation':False,'reference_song':{'pitch_scale_cents':200,'rhythm_scale_seconds':0.3,'pitch_weight':0.6,'rhythm_weight':0.4,'coverage_multiplier':True,'minimum_reference_coverage':0.8,'minimum_f0_coverage':0.8,'tempo_ratio_range':[0.8,1.25],'maximum_false_positive_voicing_ratio':0.2,'missing_dimension_policy':'total_unavailable_no_reweight','formula':'(0.6*pitch+0.4*rhythm)*coverage'},'long_tone':{'mad_scale_cents':100,'stability_weight':0.7,'sustain_weight':0.3,'minimum_seconds':3,'minimum_tracking_coverage':0.8,'formula':'0.7*stability+0.3*sustain'},'free_singing':{'total_available':False,'reason':'REFERENCE_OR_DEFINED_TASK_REQUIRED'},'llm_may_change_scores':False})
    save(write,'contracts/error-catalog.json',{'version':'1.4','codes':{code:status for status,codes in {400:['INVALID_REQUEST','CURSOR_INVALID'],401:['UNAUTHENTICATED'],403:['FORBIDDEN'],404:['RESOURCE_NOT_FOUND'],409:['STATE_CONFLICT','VERSION_CONFLICT','IDEMPOTENCY_CONFLICT'],413:['MEDIA_TOO_LARGE'],415:['MEDIA_UNSUPPORTED'],422:['VALIDATION_FAILED','REFERENCE_REQUIRED','AUDIO_QUALITY_LOW','CONSENT_REQUIRED'],429:['QUOTA_EXCEEDED'],501:['FEATURE_DISABLED'],503:['DEPENDENCY_UNAVAILABLE','UNKNOWN_PROVIDER_OUTCOME'],500:['INTERNAL_ERROR']}.items() for code in codes}})
    route=json.loads((root/'contracts/route-decision.schema.json').read_text(encoding='utf-8'))
    route['allOf']=[{'if':{'properties':{'decision':{'const':'clarify'}}},'then':{'properties':{'primary_intent':{'type':'null'}}},'else':{'properties':{'primary_intent':{'type':'string','enum':['knowledge','assessment','planning','singing']}}}}]
    save(write,'contracts/route-decision.schema.json',route)

def update_database(write,root):
    sql=(root/'database/schema.sql').read_text(encoding='utf-8')
    sql=sql.replace('-- DeepVocal candidate V1 schema. PostgreSQL, not a running migration.','-- DeepVocal V1.4 initial design schema. PostgreSQL. NOT an executed migration.')
    sql=sql.replace('id uuid PRIMARY KEY, subject text NOT NULL UNIQUE,','id uuid PRIMARY KEY, issuer text NOT NULL, subject text NOT NULL,')
    sql=sql.replace("created_at timestamptz NOT NULL DEFAULT now()\n);\nCREATE TABLE user_profiles", "created_at timestamptz NOT NULL DEFAULT now(), data_revision bigint NOT NULL DEFAULT 1, memory_revision bigint NOT NULL DEFAULT 1, UNIQUE(issuer,subject)\n);\nCREATE TABLE user_profiles",1)
    sql=sql.replace("updated_at timestamptz NOT NULL DEFAULT now()\n);\nCREATE TABLE sessions", "updated_at timestamptz NOT NULL DEFAULT now(), version integer NOT NULL DEFAULT 1 CHECK(version>=1)\n);\nCREATE TABLE sessions",1)
    sql=sql.replace("status text NOT NULL DEFAULT 'pending'", "object_version text, verified_at timestamptz, expires_at timestamptz,\n status text NOT NULL DEFAULT 'pending'",1)
    sql=sql.replace('cancel_requested boolean NOT NULL DEFAULT false, lease_until timestamptz,','cancel_requested boolean NOT NULL DEFAULT false, lease_until timestamptz, claim_token uuid, provider_request_id text,')
    sql=sql.replace("algorithm_version text NOT NULL, explanation_version text NOT NULL,", "scores jsonb NOT NULL DEFAULT '{}', measurement_version text NOT NULL DEFAULT 'unlocked',\n algorithm_version text NOT NULL, explanation_version text NOT NULL,")
    sql=sql.replace("status text NOT NULL CHECK(status IN ('active','completed','archived')), version integer NOT NULL DEFAULT 1,", "family_id uuid NOT NULL, revision integer NOT NULL DEFAULT 1 CHECK(revision>=1),\n status text NOT NULL CHECK(status IN ('active','completed','archived')), version integer NOT NULL DEFAULT 1 CHECK(version>=1),")
    sql=sql.replace("notes text NOT NULL DEFAULT '', FOREIGN KEY(item_id,owner_id)", "notes text NOT NULL DEFAULT '', UNIQUE(owner_id,item_id), FOREIGN KEY(item_id,owner_id)")
    sql=sql.replace("id uuid PRIMARY KEY, source_uri text NOT NULL, title text NOT NULL, license_note text NOT NULL,", "id uuid PRIMARY KEY, source_id uuid NOT NULL, language text NOT NULL DEFAULT 'zh' CHECK(language='zh'), source_uri text NOT NULL, title text NOT NULL, license_note text NOT NULL,")
    sql=sql.replace("content text NOT NULL, content_hash char(64) NOT NULL, scope text NOT NULL CHECK(scope IN ('public','private')),\n owner_id uuid REFERENCES users(id), UNIQUE(document_id,ordinal),\n CHECK((scope='private' AND owner_id IS NOT NULL) OR (scope='public' AND owner_id IS NULL))", "content text NOT NULL, content_hash char(64) NOT NULL, start_char integer NOT NULL CHECK(start_char>=0), end_char integer NOT NULL,\n UNIQUE(document_id,ordinal), CHECK(end_char>start_char)")
    sql=sql.replace("payload jsonb NOT NULL, occurred_at", "event_kind text NOT NULL, source_version bigint NOT NULL CHECK(source_version>=1), sequence_no bigint GENERATED ALWAYS AS IDENTITY,\n payload jsonb NOT NULL, occurred_at")
    sql=sql.replace('UNIQUE(owner_id,source_type,source_id)','UNIQUE(owner_id,source_type,source_id,event_kind,source_version)')
    sql=sql.replace("fact jsonb NOT NULL, status text", "semantic_key text NOT NULL, version integer NOT NULL DEFAULT 1 CHECK(version>=1),\n fact jsonb NOT NULL, status text")
    sql=sql.replace("FOREIGN KEY(source_event_id,owner_id) REFERENCES memory_events(id,owner_id)\n);", "UNIQUE(id,owner_id), FOREIGN KEY(source_event_id,owner_id) REFERENCES memory_events(id,owner_id)\n);")
    sql=sql.replace("summary jsonb NOT NULL, fact_ids", "input_high_watermark bigint NOT NULL DEFAULT 0, input_revision bigint NOT NULL DEFAULT 0,\n summary jsonb NOT NULL, fact_ids")
    sql=sql.replace("response jsonb, expires_at", "status text NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress','completed')), linked_job_id uuid, response_status integer, response jsonb, expires_at")
    # Additive tables before COMMIT; references are forward-resolved with ALTER where needed.
    sql=sql[:sql.index('COMMIT;')]+EXTRA_SQL+'\nCOMMIT;\n-- Service transactions must enforce owner/purpose/revision, same-corpus links, state transitions, lease fencing and aggregate minute limits.\n-- RLS is not enabled; every repository query MUST constrain trusted owner. Checkpointer internal tables are managed by its adapter, separately versioned.\n'
    save_sql=sql.replace(" );", ");")
    write('database/schema.sql',save_sql)
    tables=re.findall(r'CREATE TABLE (\w+)',save_sql)
    save(write,'contracts/database-model.json',{'version':'1.4','status':'DESIGN_NOT_EXECUTED','table_count':len(tables),'tables':tables,'checkpointer_tables':'adapter_managed_not_faked_here','rls_enabled':False,'service_invariants_required':True})

EXTRA_SQL=r'''
CREATE TABLE workflow_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), session_id uuid NOT NULL,
 root_run_id uuid NOT NULL, parent_run_id uuid, status text NOT NULL CHECK(status IN ('CREATED','RUNNING','WAIT_JOB','WAIT_USER_INPUT','COMPLETED','FAILED','CANCELLED')),
 action_kind text NOT NULL, action_snapshot jsonb NOT NULL, version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 checkpoint_ref text, current_node text, tool_attempts integer NOT NULL DEFAULT 0 CHECK(tool_attempts>=0),
 business_steps integer NOT NULL DEFAULT 0 CHECK(business_steps>=0), resumes integer NOT NULL DEFAULT 0 CHECK(resumes>=0),
 expires_at timestamptz NOT NULL, trace_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id), FOREIGN KEY(session_id,owner_id) REFERENCES sessions(id,owner_id),
 FOREIGN KEY(parent_run_id,owner_id) REFERENCES workflow_runs(id,owner_id),
 FOREIGN KEY(root_run_id,owner_id) REFERENCES workflow_runs(id,owner_id)
);
CREATE TABLE workflow_steps (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), run_id uuid NOT NULL, step_key text NOT NULL,
 agent text NOT NULL CHECK(agent IN ('knowledge','assessment','planning','singing')),
 status text NOT NULL CHECK(status IN ('pending','running','waiting','succeeded','failed','cancelled')),
 attempt integer NOT NULL DEFAULT 0 CHECK(attempt>=0), effect_key text NOT NULL, input_snapshot jsonb NOT NULL,
 output_refs jsonb NOT NULL DEFAULT '[]', error_code text, version integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(run_id,step_key), UNIQUE(owner_id,effect_key), FOREIGN KEY(run_id,owner_id) REFERENCES workflow_runs(id,owner_id)
);
CREATE TABLE workflow_waits (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), run_id uuid NOT NULL,
 wait_kind text NOT NULL CHECK(wait_kind IN ('job','user_input')), job_id uuid, pending_schema jsonb NOT NULL,
 resume_token_hash char(64), version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 consumed_at timestamptz, consumed_request_key text, expires_at timestamptz NOT NULL,
 CHECK((wait_kind='job' AND job_id IS NOT NULL AND resume_token_hash IS NULL) OR (wait_kind='user_input' AND job_id IS NULL AND resume_token_hash IS NOT NULL)),
 FOREIGN KEY(run_id,owner_id) REFERENCES workflow_runs(id,owner_id), FOREIGN KEY(job_id,owner_id) REFERENCES jobs(id,owner_id)
);
CREATE UNIQUE INDEX workflow_one_wait ON workflow_waits(run_id) WHERE consumed_at IS NULL;
CREATE TABLE job_attempts (
 job_id uuid NOT NULL REFERENCES jobs(id), attempt integer NOT NULL CHECK(attempt>=1), claim_token uuid NOT NULL UNIQUE,
 worker_id text NOT NULL, started_at timestamptz NOT NULL, lease_until timestamptz NOT NULL,
 finished_at timestamptz, status text NOT NULL CHECK(status IN ('running','succeeded','failed','expired','cancelled')),
 temporary_object_prefix text, provider_request_id text, error_code text, PRIMARY KEY(job_id,attempt)
);
ALTER TABLE jobs ADD COLUMN workflow_id uuid;
ALTER TABLE jobs ADD FOREIGN KEY(workflow_id,owner_id) REFERENCES workflow_runs(id,owner_id);
CREATE TABLE consent_grants (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 purpose text NOT NULL CHECK(purpose IN ('analysis','practice_storage','cross_session_memory','research_improvement','singing_reference')),
 status text NOT NULL CHECK(status IN ('granted','refused','revoked')), text_version text NOT NULL,
 version integer NOT NULL DEFAULT 1 CHECK(version>=1), created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
 UNIQUE(id,owner_id), CHECK((status='revoked' AND revoked_at IS NOT NULL) OR (status<>'revoked' AND revoked_at IS NULL))
);
CREATE UNIQUE INDEX consent_one_active_purpose ON consent_grants(owner_id,purpose) WHERE status='granted';
CREATE TABLE retention_preferences (
 owner_id uuid PRIMARY KEY REFERENCES users(id), raw_audio_days integer NOT NULL DEFAULT 30 CHECK(raw_audio_days IN (7,30,90,180)),
 report_days integer NOT NULL DEFAULT 180 CHECK(report_days IN (7,30,90,180)), technical_log_days integer NOT NULL DEFAULT 30 CHECK(technical_log_days=30),
 backup_max_days integer NOT NULL DEFAULT 30 CHECK(backup_max_days=30), version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_sources (
 id uuid PRIMARY KEY, source_uri text NOT NULL, title text NOT NULL, author text NOT NULL, language text NOT NULL CHECK(language='zh'),
 license_id text NOT NULL, license_evidence jsonb NOT NULL, allowed_purposes jsonb NOT NULL,
 snapshot_hash char(64) NOT NULL, retrieved_at timestamptz NOT NULL, withdrawn_at timestamptz,
 CHECK(jsonb_typeof(license_evidence)='object'), CHECK(jsonb_typeof(allowed_purposes)='array')
);
CREATE TABLE corpus_versions (
 version text PRIMARY KEY, owner_id uuid REFERENCES users(id), channel text NOT NULL DEFAULT 'zh_research',
 scope text NOT NULL CHECK(scope IN ('public','private')), language text NOT NULL CHECK(language='zh'),
 state text NOT NULL CHECK(state IN ('building','checked','active','retired','failed')),
 revision integer NOT NULL DEFAULT 1 CHECK(revision>=1), review_level text NOT NULL CHECK(review_level IN ('candidate','evidence_checked')),
 expert_verified boolean NOT NULL DEFAULT false CHECK(expert_verified=false), manifest jsonb NOT NULL,
 vector_projection_ready boolean NOT NULL DEFAULT false, graph_projection_ready boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), activated_at timestamptz,
 CHECK((scope='private' AND owner_id IS NOT NULL) OR (scope='public' AND owner_id IS NULL)),
 CHECK(state<>'active' OR (review_level='evidence_checked' AND vector_projection_ready AND graph_projection_ready))
);
CREATE UNIQUE INDEX active_public_corpus ON corpus_versions(channel) WHERE state='active' AND owner_id IS NULL;
CREATE UNIQUE INDEX active_private_corpus ON corpus_versions(owner_id,channel) WHERE state='active' AND owner_id IS NOT NULL;
ALTER TABLE knowledge_documents ADD FOREIGN KEY(source_id) REFERENCES knowledge_sources(id);
ALTER TABLE knowledge_documents ADD FOREIGN KEY(corpus_version) REFERENCES corpus_versions(version);
CREATE TABLE corpus_reviews (
 id uuid PRIMARY KEY, corpus_version text NOT NULL REFERENCES corpus_versions(version), resource_id uuid NOT NULL,
 resource_kind text NOT NULL CHECK(resource_kind IN ('source','document','chunk','entity','relation','exercise')),
 decision text NOT NULL CHECK(decision IN ('evidence_checked','reject','needs_revision')),
 verifier_revision text NOT NULL, evidence jsonb NOT NULL, limitations jsonb NOT NULL DEFAULT '[]',
 reviewer_subject text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE seed_id_mappings (
 seed_version text NOT NULL, resource_kind text NOT NULL, seed_id text NOT NULL, production_id uuid NOT NULL,
 source_hash char(64) NOT NULL, namespace_uuid uuid NOT NULL, PRIMARY KEY(seed_version,resource_kind,seed_id), UNIQUE(resource_kind,production_id)
);
CREATE TABLE graph_entities (
 id uuid PRIMARY KEY, corpus_version text NOT NULL REFERENCES corpus_versions(version),
 entity_type text NOT NULL CHECK(entity_type IN ('VocalConcept','Technique','AudioIssue','Exercise','SongSegment','PracticeRecord')),
 canonical_label text NOT NULL, aliases jsonb NOT NULL DEFAULT '[]', payload jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('candidate','evidence_checked','retired')), UNIQUE(id,corpus_version)
);
CREATE TABLE chunk_entity_links (
 corpus_version text NOT NULL REFERENCES corpus_versions(version), chunk_id uuid NOT NULL REFERENCES knowledge_chunks(id), entity_id uuid NOT NULL,
 start_char integer NOT NULL CHECK(start_char>=0), end_char integer NOT NULL, evidence_text text NOT NULL,
 link_confidence double precision CHECK(link_confidence>=0 AND link_confidence<=1), PRIMARY KEY(chunk_id,entity_id,start_char),
 CHECK(end_char>start_char), FOREIGN KEY(entity_id,corpus_version) REFERENCES graph_entities(id,corpus_version)
);
CREATE TABLE graph_relations (
 id uuid PRIMARY KEY, corpus_version text NOT NULL REFERENCES corpus_versions(version), subject_id uuid NOT NULL, object_id uuid NOT NULL,
 predicate text NOT NULL CHECK(predicate IN ('RELATED_TO','REQUIRES','ADDRESSES','ILLUSTRATES','SUPPORTED_BY','OBSERVED_IN')),
 source_chunk_id uuid NOT NULL REFERENCES knowledge_chunks(id), start_char integer NOT NULL CHECK(start_char>=0), end_char integer NOT NULL,
 conditions jsonb NOT NULL DEFAULT '{}', review_level text NOT NULL CHECK(review_level='evidence_checked'),
 retired_at timestamptz, CHECK(end_char>start_char),
 FOREIGN KEY(subject_id,corpus_version) REFERENCES graph_entities(id,corpus_version),
 FOREIGN KEY(object_id,corpus_version) REFERENCES graph_entities(id,corpus_version)
);
CREATE TABLE memory_fact_sources (
 owner_id uuid NOT NULL REFERENCES users(id), fact_id uuid NOT NULL, event_id uuid NOT NULL, PRIMARY KEY(fact_id,event_id),
 FOREIGN KEY(fact_id,owner_id) REFERENCES memory_facts(id,owner_id), FOREIGN KEY(event_id,owner_id) REFERENCES memory_events(id,owner_id)
);
CREATE TABLE memory_profile_sources (
 owner_id uuid NOT NULL REFERENCES memory_profiles(owner_id), fact_id uuid NOT NULL, profile_version integer NOT NULL CHECK(profile_version>=1),
 PRIMARY KEY(owner_id,profile_version,fact_id), FOREIGN KEY(fact_id,owner_id) REFERENCES memory_facts(id,owner_id)
);
CREATE TABLE memory_compaction_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), input_high_watermark bigint NOT NULL CHECK(input_high_watermark>=0),
 expected_revision bigint NOT NULL CHECK(expected_revision>=1), consent_revision bigint NOT NULL,
 status text NOT NULL CHECK(status IN ('queued','running','committed','stale','failed')),
 extractor_revision text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(owner_id,input_high_watermark,expected_revision)
);
CREATE TABLE deletion_ledger (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), resource_kind text NOT NULL, resource_id uuid,
 semantic_key_hash char(64) NOT NULL, data_revision bigint NOT NULL CHECK(data_revision>=1),
 created_at timestamptz NOT NULL DEFAULT now(), purge_after timestamptz NOT NULL, UNIQUE(owner_id,resource_kind,semantic_key_hash)
);
CREATE TABLE event_consumptions (
 consumer text NOT NULL, event_id uuid NOT NULL REFERENCES outbox_events(id), consumed_at timestamptz NOT NULL DEFAULT now(),
 result_ref uuid, PRIMARY KEY(consumer,event_id)
);
CREATE INDEX workflow_owner_status ON workflow_runs(owner_id,status,created_at);
CREATE INDEX wait_job_pending ON workflow_waits(job_id) WHERE consumed_at IS NULL;
CREATE INDEX memory_events_owner_sequence ON memory_events(owner_id,sequence_no);
CREATE INDEX deletion_owner_revision ON deletion_ledger(owner_id,data_revision);
'''

def update_prototype(write,root):
    p=root/'prototype/index.html';text=p.read_text(encoding='utf-8')
    start=text.index("if(state.page==='singing')")
    end=text.index("if(state.page===",start+10)
    text=text[:start]+'''if(state.page==='singing')s=intro('歌声服务接入','当前仅保留接口，已有服务后续接入。')+`<section class="card"><span class="pill">disabled · 当前未接入</span><h2>本期不开发真实歌声生成</h2><p>四类 Agent 中保留 AI Singing 的能力查询与接入边界。生成、音色训练和 GPU 部署由后续任务接入你已有的实现。</p><p>当前不会创建生成任务、上传音色、产生费用或提供模拟成功音频。</p><details><summary>查看未来接入要求</summary><p>需核对服务协议、SVC/SVS 输入、本人用途授权、撤销机制、版本和产物引用。旋律参考是描述音符高低及时间的模板。</p></details></section>`;
''' +text[end:]
    text=text.replace("'参考歌声'","'歌声服务接入'")
    # Guard even calls made outside the visible UI. No simulation of current generation success.
    start=text.index('function generate()');end=text.index('\nfunction ',start+10)
    text=text[:start]+"function generate(){return notify('FEATURE_DISABLED：当前只保留歌声接入接口，不创建任务。')}"+text[end:]
    text=text.replace('选择本人录音', '选择本人清唱录音')
    text=text.replace('<label for="audio">', '<div class="notice">只接收清唱。分数为研究级练习参考，未经过专业评级校准。</div><label for="audio">')
    # These are nonpersistent design controls, clearly labeled as prototype-only.
    marker="if(state.page==='admin')"
    insert='''if(state.page==='memory')s+=`<section class="card"><h2>用途和保存期限 演示控件</h2><p>各用途独立，不因分析录音自动同意长期记忆或训练。</p><label class="inline-label"><input type="checkbox" id="analysisConsent"><span>允许本次录音分析</span></label><label class="inline-label"><input type="checkbox" id="memoryConsent"><span>允许跨会话练习记忆</span></label><label for="rawRetention">原音频保存天数</label><select id="rawRetention"><option>7</option><option selected>30</option><option>90</option><option>180</option></select><label for="reportRetention">报告保存天数</label><select id="reportRetention"><option>7</option><option>30</option><option>90</option><option selected>180</option></select><p class="callout">默认值是本项目工程选择，不是通用标准。原型不提交或持久保存设置。</p></section>`;
'''
    text=text.replace(marker,insert+marker)
    write('prototype/index.html',text)
