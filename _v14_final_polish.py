"""Final cross-document corrections; artifact authoring only."""
import json,re,hashlib,html

def polish(d,write,root):
    p=d['02_产品需求文档PRD.md']
    p=p.replace('完整闭环增加参考歌声：','未来接入已有歌声服务后扩展闭环（当前禁用）：')
    p=p.replace('| 参考歌声 | 授权、样本、歌词、旋律、调号、BPM | 排队状态、试听、参数、加入曲目 | 撤销授权、中途失败、模型不支持时阻断 |','| 歌声服务接入 | 仅查看当前能力 | 显示 disabled 和未来接入说明 | 当前不上传音色、不创建生成任务 |')
    p=p.replace('最后引入真实全曲测评、声音生成、自动候选 Skill。','再完成真实全曲测评和自动候选 Skill。已有声音生成服务留到后续独立接入。')
    p=p.replace('真实歌声生成和效果指标单独验收，不因主流程完成而自动通过。','当前验收 disabled 接口和无副作用阻断；真实歌声接入在后续任务验收，独立效果指标由用户另行评测。')
    p=p.replace('首期目标用户、唱法、语料语言和项目用途由 19 章确认；未确认前当前页面与故事以成人中文学习者为候选背景。','目标已由 19 章答复确定：本人非商业研究，成人初学者/爱好者，中文流行演唱与中文资料。')
    p=p.replace('| BR-05 | 本人授权、声音样本、旋律和模型能力缺一时不提交真实生成 |','| BR-05 | 当前生成禁用，不接收音色训练或创建生成任务；后续集成另验授权与能力 |')
    p=p.replace('| BR-06 | 生成参考→用户新模唱→测评→计划必须保留不同资产与任务依赖 |','| BR-06 | 未来生成参考→用户新模唱→测评→计划保留不同资产与任务依赖，当前仅预留 |')
    d['02_产品需求文档PRD.md']=p
    key='15_GitHub相似项目调研.md';p=d[key]
    p=p.replace('生成在 DiffSinger 型 SVS 和 RVC 型 SVC 中选一条最小路线。','生成模块按 D-014 禁用，DiffSinger/RVC 仅作为理解未来已有服务输入的研究参考，不要求当前选型或试接。')
    p=p.replace('为每个候选建立独立试验目录，','当前仅对所需编排、检索、声学候选建立独立试验目录，')
    d[key]=p
    # Correct paths, physical schema claims and stable wait token semantics.
    d['06_数据库设计文档.md']=d['06_数据库设计文档.md'].replace('diagrams/core-entities.svg','diagrams/entities.svg').replace('顶层对象/数组有 CHECK','顶层对象/数组按 DDL 所列 CHECK 和服务 schema 校验共同约束')
    d['07_接口契约与错误码.md']=d['07_接口契约与错误码.md'].replace('Bear\u00ader','Bearer').replace('只有 WAIT_USER_INPUT 才提供新短期令牌。','只有 WAIT_USER_INPUT 才返回当前短期令牌；GET 不轮换令牌或改变 version。')
    d['07_接口契约与错误码.md']=d['07_接口契约与错误码.md'].replace('维度有 unit、覆盖、profile、ruleset_version','原始测量有 unit，评分为 0～100 分，包含覆盖、profile、ruleset_version')
    d['05_详细设计文档.md']+='''

## 等待令牌的只读签发

进入 WAIT_USER_INPUT 的事务固定 wait_id、run version、到期时间和 token_key_version。用服务端专用 HMAC 密钥对 owner/run/wait/version/expiry 做规范串联，派生不可预测令牌，库中只保存 SHA256；同一等待重复 GET 返回同一令牌，不写状态或轮换版本。密钥不放 checkpoint；轮换时保留未过期 key version，丢失密钥则明确结束该等待并由用户重新确认动作，不能降级为无令牌恢复。恢复事务锁 wait/run，恒定时间核对哈希及 owner/version/expiry、consume 标记，业务推进与 outbox 同事务；重复同幂等键返回第一次响应，换键重放旧 token 返回409。

## 付费调用的费用事务

provider_budgets 按 owner/provider/currency/UTC 计费日唯一；provider_reservations 按 owner/effect_key 唯一。启用前必须选供应商并设置日硬上限和单调用 max_cost_minor；金额使用整数最小币种，所有请求（含 embedding、rerank、分类、压缩和重试）共享同一限额主账。先按预算行加锁，验证 spent+reserved+本次上限<=limit，再预留并记录 effect_key 后外呼。并发请求不能各读一次余额后无锁扣款。

已确认成功则原子释放预留并结算实际金额；确认未受理才释放。超时未知收单记 unknown，继续占用额度，只能用供应商请求ID查询或由维护者核对，不因租约到期直接退款重试。回调/查询也按 reservation version 幂等；币种不同不直接相加。若供应商不能承诺单调用上限，需先限制输入输出并核对其计费边界，否则不启用该适配器。硬限额是应用对新请求的准入限制，不能宣称能撤回已发生的供应商收费。
'''
    d['06_数据库设计文档.md']+='''

## 费用和评分存储补齐

provider_budgets/provider_reservations 是付费并发准入主账，分别保留日上限、已结算/预留金额及一次外呼的 effect_key、请求ID、unknown状态和version。锁预算行后才预留；未知收单不释放预留。它们不存银行卡或供应商密钥。Report 的 scores/quality/reference_snapshot 为对象、metrics/limitations 为数组；服务必须校验完整契约后写入，不能靠空对象默认绕过必填评分字段。reference_snapshot 保存参考哈希、移调、对齐参数和配置版本，保证之后复算不随原参考改变。

计划 family_id+revision 唯一；修改生成新修订时，已完成 item 保留在历史修订、日志不复制，未完成 item 新建 ID 并带来源映射。活动计划视图联合本 family 历史已完成记录，不能以复用 item ID 同时归属两个 plan。所有修订操作锁 family，防重复 revision 或双活动版本。
'''
    d['24_综合评分与多维测评规则.md']+='''

## 单位、分段与异常值补充

报告原始 F0 用 Hz，音高误差和 MAD 用 cents，时间内部整数毫秒，节奏公式换算为秒后运算；不能把120ms直接代入0.3秒分母。F0<=0、非有限值和不可跟踪帧不进入对数；JSON禁止NaN/Infinity。全曲按原始时间轴汇总加权误差后计算总分，不对片段分数取简单平均。参考移调固定后再算误差；未经用户选择不得自动移调/折叠八度。规则中 max(0,...) 已处理超尺度误差，负误差/负时长是无效输入，不能变成超过100分。

reference_song 默认只允许全局起点偏移与有界全局速度变化；记录 offset_ms/tempo_ratio，局部匹配只解决音符关联。伴随速度变动很大导致不可靠时返回不可评分，不以无限DTW把节奏问题抹平。long_tone 任务目标时长必须由提交前用户任务配置确定，不能分析后用最长音段本身作分母；本版以 goal_duration_ms（3000～120000）提交。有效持续时长取整个目标窗口最长连续有效发声，跟踪短空洞的容忍阈值随 measurement_version 锁定。
'''
    d['26_参考资料采纳与最终交接.md']+='''

## 来源定位口径

P0001 等是原 DOCX 段落位置，提取保留空段造成的编号间隔，77 是有文字的段落数而非最大段落编号。图片以原 DOCX word/media/imageN.* 定位；提取文本和文件 SHA256 用于追溯。资料中的第一人称“已完成”仅作为参考陈述，未写成本项目已实现证据。
'''
    # Machine contracts: add the exact long-tone target and tighten input/output relations.
    api=json.loads((root/'contracts/openapi.json').read_text(encoding='utf-8'));s=api['components']['schemas']
    req=s['AssessmentRequest'];req['properties']['goal_duration_ms']={'type':'integer','minimum':3000,'maximum':120000}
    req['allOf'].append({'if':{'properties':{'task_kind':{'const':'long_tone'}}},'then':{'required':['goal_duration_ms']},'else':{'not':{'required':['goal_duration_ms']}}})
    s['ScoreDimension']['properties']['unit']={'type':'string','const':'score_0_100'};s['ScoreDimension']['required'].append('unit')
    for name,typ in [('family_id',{'type':'string','format':'uuid'}),('revision',{'type':'integer','minimum':1})]:
        s['Plan']['properties'][name]=typ;s['Plan']['required'].append(name)
    api['paths']['/plans/{id}']['patch']['description']='Creates next family revision with a new plan id; returns new Plan. Old completed items/logs remain historical; item ids in request identify original uncompleted items, server assigns new revision item ids. Server validates owner, family lock, version and day/minute constraints.'
    for profile,names in [('reference_song',['pitch','rhythm']),('long_tone',['stability','sustain'])]:
        s['ScoreBundle']['allOf'].append({'if':{'properties':{'profile':{'const':profile},'available':{'const':True}}},'then':{'properties':{'coverage':{'type':'number','minimum':0.8,'maximum':1},'dimensions':{'minItems':2,'allOf':[{'contains':{'properties':{'name':{'const':name},'available':{'const':True}},'required':['name','available']},'minContains':1,'maxContains':1} for name in names]}}}})
    api['paths']['/workflows/{id}/wait']['get']['description']+=' Read-only, returns stable token for current unexpired wait; never rotates state on GET.'
    api['paths']['/uploads']['post']['responses']['501']={'description':'FEATURE_DISABLED for voice_sample/melody/generated_reference while singing disabled; assessment reference uploads use purpose=reference.','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}}
    # Every externally advertised error has the same Error schema; custom codes remain service responsibilities.
    for methods in api['paths'].values():
        for method,op in methods.items():
            if method not in ['get','post','patch','delete','put']:continue
            if method in ['post','patch','put']:
                for code in ['413','415']:
                    op['responses'].setdefault(code,{'description':'Input limit or format rejected','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}})
    write('contracts/openapi.json',json.dumps(api,ensure_ascii=False,indent=2))
    sql=(root/'database/schema.sql').read_text(encoding='utf-8')
    sql=sql.replace("scores jsonb NOT NULL DEFAULT '{}', measurement_version text NOT NULL DEFAULT 'unlocked',","scores jsonb NOT NULL CHECK(jsonb_typeof(scores)='object'), measurement_version text NOT NULL,\n reference_snapshot jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(reference_snapshot)='object'), expires_at timestamptz,")
    sql=sql.replace('quality jsonb NOT NULL, metrics jsonb NOT NULL,',"quality jsonb NOT NULL CHECK(jsonb_typeof(quality)='object'), metrics jsonb NOT NULL CHECK(jsonb_typeof(metrics)='array'),")
    sql=sql.replace('resume_token_hash char(64) NOT NULL UNIQUE,','resume_token_hash char(64) NOT NULL UNIQUE, token_key_version text NOT NULL,')
    # Existing spelling may differ: the validator ensures the token field is covered.
    if 'token_key_version' not in sql:
        sql=sql.replace('token_hash char(64) NOT NULL UNIQUE,','token_hash char(64) NOT NULL UNIQUE, token_key_version text NOT NULL,')
    if 'token_key_version' not in sql:
        sql=sql.replace('resume_token_hash char(64),','resume_token_hash char(64), token_key_version text,')
    sql=sql.replace("wait_kind='user_input' AND job_id IS NULL AND resume_token_hash IS NOT NULL)","wait_kind='user_input' AND job_id IS NULL AND resume_token_hash IS NOT NULL AND token_key_version IS NOT NULL)")
    sql=sql.replace('COMMIT;',BUDGET_SQL+'\nCOMMIT;')
    write('database/schema.sql',sql)
    model=json.loads((root/'contracts/database-model.json').read_text(encoding='utf-8'));model['tables']=re.findall(r'CREATE TABLE (\w+)',sql);model['table_count']=len(model['tables'])
    write('contracts/database-model.json',json.dumps(model,ensure_ascii=False,indent=2))
    # Source integrity is independent of the changing generated questionnaire.
    path=root/'sources/user-inputs/面经_来源记录.json';meta=json.loads(path.read_text(encoding='utf-8'))
    meta['original_answers_sha256']=hashlib.sha256((root/'sources/user-inputs/待确定问题_用户原始答卷.md').read_bytes()).hexdigest()
    write('sources/user-inputs/面经_来源记录.json',json.dumps(meta,ensure_ascii=False,indent=2))
    write('configs/gitignore.example', '''# Future code repository template: copy to project .gitignore and review tracked files.
.env
.env.*
!.env.example
!configs/.env.example
**/__pycache__/
*.pyc
/node_modules/
/.venv/
/logs/
/data/
/models/
/backups/
/datasets/raw/
/datasets/normalized/
/sources/user-inputs/
/_qa/
*.sqlite3
*.dump
*.pem
*.key
# Data licensing and document de-identification require a separate publish whitelist.
''')
    # Update diagrams in place and preserve a small, readable entity overview.
    path=root/'diagrams/system.svg';svg=path.read_text(encoding='utf-8').replace('GPU Worker','已有服务接入预留').replace('授权核验与真实模型','disabled · 后续集成');write('diagrams/system.svg',svg)
    path=root/'diagrams/entities.svg';svg=path.read_text(encoding='utf-8').replace('voice_consents','consent_grants').replace('singing_requests','workflow_runs/steps/waits').replace('任务 样本 旋律 授权','等待 版本 幂等 恢复').replace('授权引用','用途记录').replace('owner_id 与授权状态','owner_id 与用途状态');write('diagrams/entities.svg',svg)
    # Prototype additions are explicitly synthetic states, with no application service.
    p=(root/'prototype/index.html').read_text(encoding='utf-8')
    p=p.replace('完整学习闭环：参考歌声 → 用户模唱 → 分段报告 → 练习计划。','当前闭环：中文问答 → 本人清唱 → 分段报告 → 练习计划；歌声服务后续接入。')
    p=p.replace('<label for="mode">测评模式</label>','<label class="inline-label"><input id="acappella" type="checkbox"><span>我确认本次是本人清唱（演示）</span></label><label for="taskKind">练习任务类型</label><select id="taskKind"><option value="free_singing">自由演唱</option><option value="long_tone">单一长音任务</option><option value="reference_song">有参考歌曲/片段</option></select><label for="goalDuration">长音目标秒数</label><input id="goalDuration" type="number" min="3" max="120" value="5"><label for="mode">测评模式</label>')
    p=p.replace("function analyze(){","function analyze(){if(!state.assessmentFile)return notify('请先选择音频。');if(!document.getElementById('acappella').checked)return notify('请确认本次为本人清唱。');const task=document.getElementById('taskKind').value;const mode=document.getElementById('mode').value;if((task==='reference_song')!==(mode==='reference'))return notify('有参考歌曲任务须选择有参考模式；其他任务使用无参考模式。');if(task==='long_tone'&&!(Number(document.getElementById('goalDuration').value)>=3&&Number(document.getElementById('goalDuration').value)<=120))return notify('长音目标需为3至120秒。');")
    p=p.replace('<details><summary>分段建议展示</summary>','<div class="notice"><strong>练习参考分 · engineering-v1</strong><p>本演示未计算多维分或综合分，显示“不可用”，不能将空值显示为0分。自由演唱缺参考或任务定义时不提供音准节奏总分。</p><a href="../index.html#doc24">查看多维评分规则与算例</a></div><details><summary>分段建议展示</summary>')
    p=p.replace('<label for="rawRetention">','<label class="inline-label"><input id="storageConsent" type="checkbox"><span>允许保存练习记录</span></label><label for="rawRetention">')
    p=p.replace("${state.generated?'Mock 完成 无真实产物':'暂无'}","接口预留 · disabled")
    p=p.replace('V1 设计评审版','V1.4 设计评审版')
    write('prototype/index.html',p)

BUDGET_SQL='''
CREATE TABLE provider_budgets (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), provider text NOT NULL,
 currency char(3) NOT NULL, period_day date NOT NULL, limit_minor bigint NOT NULL CHECK(limit_minor>=0),
 reserved_minor bigint NOT NULL DEFAULT 0 CHECK(reserved_minor>=0), spent_minor bigint NOT NULL DEFAULT 0 CHECK(spent_minor>=0),
 version integer NOT NULL DEFAULT 1 CHECK(version>=1), UNIQUE(owner_id,provider,currency,period_day), UNIQUE(id,owner_id),
 CHECK(reserved_minor+spent_minor<=limit_minor)
);
CREATE TABLE provider_reservations (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), budget_id uuid NOT NULL, effect_key text NOT NULL,
 max_cost_minor bigint NOT NULL CHECK(max_cost_minor>=0), actual_cost_minor bigint CHECK(actual_cost_minor>=0 AND actual_cost_minor<=max_cost_minor),
 state text NOT NULL CHECK(state IN ('reserved','submitted','unknown','settled','released')),
 provider_request_id text, version integer NOT NULL DEFAULT 1 CHECK(version>=1), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,effect_key), FOREIGN KEY(budget_id,owner_id) REFERENCES provider_budgets(id,owner_id),
 CHECK((state='settled' AND actual_cost_minor IS NOT NULL) OR (state<>'settled' AND actual_cost_minor IS NULL))
);
ALTER TABLE practice_plans ADD UNIQUE(owner_id,family_id,revision);
CREATE UNIQUE INDEX plan_family_one_active ON practice_plans(owner_id,family_id) WHERE status='active';
'''
