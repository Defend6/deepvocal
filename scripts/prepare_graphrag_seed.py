"""Build a local evidence-preparation database. No embeddings, model calls or app services."""
from pathlib import Path
from html.parser import HTMLParser
from fractions import Fraction
import csv,hashlib,html,io,itertools,json,re,sqlite3,wave,zipfile,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'datasets'
RAW=DATA/'raw'
OUT=DATA/'normalized'
OUT.mkdir(parents=True,exist_ok=True)
VERSION='seed-20261103-v1'
def digest(b):return hashlib.sha256(b).hexdigest()
def ident(prefix,value):return prefix+'_'+digest(value.encode('utf-8'))[:20]
def save(name,value):
 p=DATA/name;p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def jsonl(name,rows):
 p=DATA/name;p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
class Plain(HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.skip=0
 def handle_starttag(self,tag,attrs):
  if tag in ['script','style','iframe']:self.skip+=1
  if tag in ['p','h1','h2','h3','h4','li','br','div','section']:self.parts.append('\n')
 def handle_endtag(self,tag):
  if tag in ['script','style','iframe'] and self.skip:self.skip-=1
  if tag in ['p','h1','h2','h3','h4','li','div','section']:self.parts.append('\n')
 def handle_data(self,data):
  if not self.skip:self.parts.append(data)
def plain(text):
 p=Plain();p.feed(text)
 return '\n'.join(re.sub(r'[ \t\xa0]+',' ',s).strip() for s in ''.join(p.parts).splitlines() if s.strip())
def clean_md(text):
 text=re.sub(r'\A---.*?---\s*','',text,flags=re.S)
 text=re.sub(r'!\[[^\]]*\](?:\([^)]*\)|\[[^\]]*\])','',text)
 text=re.sub(r'^\[[^\]]+\]:.*$','',text,flags=re.M)
 text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',text)
 text=re.sub(r'\[([^\]]+)\]\[[^\]]*\]',r'\1',text)
 return plain(text)

def main():
 documents=[];chunks=[];sources=[];scores=[];audio=[];notes=[]
 def source(sid,title,url,license_id,tier,author,proof,uses):
  sources.append(dict(source_id=sid,title=title,url=url,license=license_id,license_partition=tier,author=author,license_evidence=proof,retrieved_on='2026-11-03',allowed_purpose=uses,production_enabled=False,review_status='NEEDS_REVIEW'))
  if sid in ['omt','lieder']:
   tree=json.loads((RAW/sid/'tree.json').read_text(encoding='utf-8'))
   sources[-1]['upstream_snapshot']={'kind':'git_tree','sha':tree['sha'],'evidence':'raw/'+sid+'/tree.json'}
  elif sid=='vocadito':
   sources[-1]['upstream_snapshot']={'kind':'zenodo_record','id':'5578807','evidence':'raw/vocadito/vocadito.json'}
 source('omt','Open Music Theory, first edition','https://github.com/openmusictheory/openmusictheory.github.io','CC-BY-SA-4.0','sharealike','Kris Shaffer, Bryn Hughes, Brian Moseley; editors and contributors','raw/omt/_includes/footer.html',['music_theory_retrieval'])
 source('vocal-textbook','Vocal Techniques for the Instrumentalist, second edition','https://www.fulcrum.org/concern/monographs/08612s169','CC-BY-NC-4.0','noncommercial','Amy Rosine (author); Sandra Mosteller (editor)','raw/vocal-textbook/book.epub::EPUB/copyright.xhtml',['noncommercial_vocal_pedagogy_retrieval'])
 source('nidcd','NIDCD voice information','https://www.nidcd.nih.gov/health/taking-care-your-voice','PUBLIC-DOMAIN-US-GOV','public_domain_policy','National Institute on Deafness and Other Communication Disorders','raw/nidcd/nidcd_policy.html.txt',['general_voice_information','health_boundary_education'])
 source('vocadito','vocadito: A dataset of solo vocals with f0, note, and lyric annotations','https://zenodo.org/records/5578807','CC-BY-4.0','attribution','Rachel Bittner, Katherine Pasalo, Juan José Bosch, Gabriel Meseguer-Brocal, David Rubinstein','raw/vocadito/vocadito.json',['f0_and_note_evaluation_not_voice_cloning'])
 source('lieder','OpenScore Lieder selected Schubert scores','https://github.com/OpenScore/Lieder','CC0-1.0','cc0','OpenScore contributors; Franz Schubert (composition)','raw/lieder/LICENSE.txt',['symbolic_score_structure','reference_note_parsing'])
 def document(sid,locator,title,text,categories):
  if len(text.strip())<100:return
  did=ident('doc',sid+'|'+locator)
  row=dict(document_id=did,source_id=sid,source_locator=locator,title=title,language='en',text=text,text_sha256=digest(text.encode()),categories=categories,corpus_version=VERSION,review_status='NEEDS_REVIEW',production_enabled=False)
  documents.append(row)
  start=0;ordinal=0
  while start<len(text):
   end=min(start+1400,len(text))
   if end<len(text):
    boundary=text.rfind('\n',start+700,end)
    if boundary>start:end=boundary
   fragment=text[start:end]
   chunks.append(dict(chunk_id=ident('chunk',did+'|'+str(start)+'|'+digest(fragment.encode())),document_id=did,source_id=sid,ordinal=ordinal,start_char=start,end_char=end,text=fragment,text_sha256=digest(fragment.encode()),language='en',categories=categories,license_partition=next(s['license_partition'] for s in sources if s['source_id']==sid),review_status='NEEDS_REVIEW',production_enabled=False,corpus_version=VERSION))
   if end==len(text):break
   start=max(start+1,end-180);ordinal+=1
 for p in sorted((RAW/'omt').glob('*.md')):
  if p.name in ['README.md','about.md']:continue
  raw=p.read_text(encoding='utf-8');title=re.search(r'^title:\s*(.*)$',raw,re.M)
  document('omt',p.relative_to(RAW).as_posix(),title.group(1).strip() if title else p.stem,clean_md(raw),['VocalConcept'])
 with zipfile.ZipFile(RAW/'vocal-textbook/book.epub') as z:
  assert z.testzip() is None
  for name in sorted(z.namelist()):
   if not name.endswith('.xhtml') or not any(t in name for t in ['chapter-','appendix-i-vocal-exercises','appendix-ii-vocal-health']):continue
   xml=z.read(name).decode('utf-8');body=re.search(r'<body[^>]*>(.*?)</body>',xml,re.S)
   text=plain(body.group(1) if body else xml)
   document('vocal-textbook','vocal-textbook/book.epub::'+name,text.splitlines()[0][:160],text,['VocalConcept','Technique','AudioIssue','Exercise'])
 for p in sorted((RAW/'nidcd').glob('*')):
  if 'policy' in p.name or p.suffix=='.pdf' or 'spasmodic' in p.name:continue
  raw=p.read_text(encoding='utf-8');article=re.search(r'<article[^>]*>(.*?)</article>',raw,re.S)
  if not article:continue
  body=article.group(1);body=re.sub(r'<div class="imageContainerRight">.*?</div>','',body,flags=re.S)
  text=plain(body);title=re.search(r'<h1[^>]*>(.*?)</h1>',raw,re.S)
  document('nidcd',p.relative_to(RAW).as_posix(),plain(title.group(1)) if title else p.stem,text,['VocalConcept','AudioIssue'])

 # Term aliases are project-authored navigation labels, not expert-validated definitions.
 term_groups={
 'VocalConcept':[('音高','pitch'),('基频','fundamental frequency|f0'),('音色','timbre|tone quality'),('响度','loudness|volume'),('声带','vocal fold|vocal folds|vocal cords'),('喉','larynx'),('共鸣','resonance'),('呼吸','breathing|breath|respiration'),('发声','phonation'),('音域','range|vocal range'),('声区','register|registers'),('头声','head voice'),('胸声','chest voice'),('假声','falsetto'),('横膈膜','diaphragm'),('元音','vowel|vowels'),('辅音','consonant|consonants'),('音阶','scale|scales'),('音程','interval|intervals'),('八度','octave|octaves'),('节拍','meter|metre'),('节奏','rhythm|rhythmic'),('调号','key signature|key signatures'),('移调','transposition|transpose'),('乐句','phrase|phrasing'),('切分','syncopation'),('三和弦','triad|triads'),('音名','pitch class|pitch-class'),('视唱','sight singing|sight-singing'),('唱名','solfege|solfège')],
 'Technique':[('连音','legato'),('断音','staccato'),('颤音','vibrato'),('滑音','glissando|portamento'),('咬字','diction|articulation'),('姿态','posture|alignment'),('气流控制','breath control|airflow'),('起音','onset|attack'),('渐强','crescendo'),('渐弱','decrescendo|diminuendo')],
 'AudioIssue':[('嘶哑','hoarseness|hoarse'),('气声','breathiness|breathy'),('嗓音疲劳','vocal fatigue'),('音高偏差','intonation|off-pitch'),('噪声','noise'),('声音紧张','tension|strain'),('失真','distortion'),('声带问题','voice problem|voice disorder')],
 'Exercise':[('哼鸣练习','humming|hum'),('唇颤音练习','lip trill|lip trills'),('热身练习','warm-up|warm up|vocalises'),('呼吸练习','breathing exercise|breathing exercises'),('音阶练习','scale exercise|scales'),('元音练习','vowel exercise|vowels')]
 }
 glossary=[];mentions=[];edges=[]
 for kind,terms in term_groups.items():
  for zh,aliases in terms:
   glossary.append(dict(entity_id=ident('entity',kind+'|'+zh),entity_type=kind,label_zh=zh,aliases_en=aliases.split('|'),definition=None,label_origin='PROJECT_AUTHORED_ALIAS_NOT_DEFINITION',review_status='NEEDS_REVIEW'))
 for c in chunks:
  matched=[]
  for e in glossary:
   pattern=r'\b(?:'+'|'.join(re.escape(a) for a in e['aliases_en'])+r')\b'
   hits=list(re.finditer(pattern,c['text'],re.I))
   for hit in hits[:3]:
    mentions.append(dict(mention_id=ident('mention',c['chunk_id']+'|'+e['entity_id']+'|'+str(hit.start())),entity_id=e['entity_id'],chunk_id=c['chunk_id'],start_char=hit.start(),end_char=hit.end(),evidence_text=c['text'][hit.start():hit.end()],extraction_method='bilingual_alias_regex_v1',review_status='NEEDS_REVIEW'))
   if hits:matched.append(e['entity_id'])
  for a,b in itertools.combinations(sorted(set(matched))[:8],2):
   edges.append(dict(edge_id=ident('edge',a+'|'+b+'|'+c['chunk_id']),subject_id=a,predicate='CO_OCCURS_WITH',object_id=b,source_chunk_id=c['chunk_id'],semantics='same_chunk_mention_only_not_causal_or_clinical',review_status='NEEDS_REVIEW',production_enabled=False))
 mentioned={m['entity_id'] for m in mentions}
 entities=[e for e in glossary if e['entity_id'] in mentioned]
 exercises=[]
 for c in chunks:
  if c['source_id']=='vocal-textbook' and re.search(r'\b(exercise|exercises|warm.up|humming|lip trill)\b',c['text'],re.I):
   exercises.append(dict(candidate_id=ident('exercise',c['chunk_id']),source_chunk_id=c['chunk_id'],evidence_text=c['text'],duration_minutes=None,intensity=None,contraindications=None,stop_rule=None,review_status='REQUIRES_VOCAL_EDUCATOR_REVIEW',production_enabled=False))

 # Parse symbolic vocal notes; quarter-note offsets are score coordinates, never seconds.
 step_pc={'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}
 for p in sorted((RAW/'lieder').rglob('*.mxl')):
  with zipfile.ZipFile(p) as z:
   container=ET.fromstring(z.read('META-INF/container.xml'))
   rf=next(x for x in container.iter() if x.tag.endswith('rootfile'))
   score=ET.fromstring(z.read(rf.attrib['full-path']))
  names={x.attrib['id']:x.findtext('part-name','') for x in score.findall('./part-list/score-part')}
  parts=score.findall('part');vocal=[x for x in parts if re.search(r'voice|vocal|sing|sopran|tenor|bariton',names.get(x.attrib['id'],''),re.I)]
  part=vocal[0] if vocal else parts[0];divisions=1;offset=Fraction(0);measures=[]
  for measure in part.findall('measure'):
   d=measure.findtext('attributes/divisions')
   if d:divisions=int(d)
   cursor=Fraction(0);extent=Fraction(0);events=[];previous=Fraction(0)
   for child in measure:
    duration=Fraction(int(child.findtext('duration','0')),divisions)
    if child.tag=='backup':cursor-=duration
    elif child.tag=='forward':cursor+=duration;extent=max(extent,cursor)
    elif child.tag=='note':
     chord=child.find('chord') is not None;onset=previous if chord else cursor
     pitch=child.find('pitch')
     if pitch is not None:
      step=pitch.findtext('step');octave=int(pitch.findtext('octave'));alter=float(pitch.findtext('alter','0'))
      events.append(dict(onset_quarter=float(offset+onset),duration_quarter=float(duration),written_midi=12*(octave+1)+step_pc[step]+alter,step=step,octave=octave,alter=alter,ties=[t.attrib.get('type') for t in child.findall('tie')]))
     previous=onset
     if not chord:cursor+=duration
     extent=max(extent,cursor)
   if events:measures.append(dict(measure=measure.attrib.get('number'),events=events,offset_quarter=float(offset)))
   offset+=max(extent,Fraction(0))
  picked=measures[:8]
  for i in range(0,len(picked),4):
   group=picked[i:i+4];ev=[e for m in group for e in m['events']]
   scores.append(dict(segment_id=ident('scoreseg',p.relative_to(RAW).as_posix()+'|'+str(i)),source_id='lieder',source_path=p.relative_to(DATA).as_posix(),title=p.parent.name,composer='Franz Schubert',part_id=part.attrib['id'],part_name=names.get(part.attrib['id'],''),voice_part_inferred=not bool(vocal),measure_start=group[0]['measure'],measure_end=group[-1]['measure'],note_events=ev,note_count=len(ev),pitch_min=min(e['written_midi'] for e in ev),pitch_max=max(e['written_midi'] for e in ev),time_basis='score_quarter_notes_not_seconds',repeat_policy='not_unfolded',tempo_to_seconds_status='NOT_COMPUTED',review_status='NEEDS_SCORE_REVIEW',production_enabled=False))

 archive=RAW/'vocadito/vocadito.zip';metadata=json.loads((RAW/'vocadito/vocadito.json').read_text())
 expected=metadata['files'][0]['checksum'].split(':',1)[1]
 assert hashlib.md5(archive.read_bytes()).hexdigest()==expected,'vocadito upstream checksum mismatch'
 with zipfile.ZipFile(archive) as z:
  assert z.testzip() is None
  members=[n for n in z.namelist() if not n.startswith('__MACOSX') and not n.endswith('/')]
  for name in sorted(n for n in members if n.endswith('.wav')):
   clip=Path(name).stem;blob=z.read(name)
   with wave.open(io.BytesIO(blob)) as w:params=dict(sample_rate=w.getframerate(),channels=w.getnchannels(),sample_width_bytes=w.getsampwidth(),frames=w.getnframes(),duration_seconds=w.getnframes()/w.getframerate())
   prefix='Annotations/F0/'+clip+'_f0.csv';f0rows=[]
   if prefix in members:f0rows=list(csv.reader(io.StringIO(z.read(prefix).decode('utf-8-sig'))))
   else:
    matches=[m for m in members if m.startswith('Annotations/F0/'+clip+'_')]
    if matches:prefix=matches[0];f0rows=list(csv.reader(io.StringIO(z.read(prefix).decode('utf-8-sig'))))
   parsed_f0=[(float(r[0]),float(r[1])) for r in f0rows if r]
   assert all(t>=0 and f>=0 for t,f in parsed_f0)
   row=dict(clip_id=clip,source_id='vocadito',archive_path='raw/vocadito/vocadito.zip',archive_member=name,sha256=digest(blob),**params,f0_annotation_member=prefix if parsed_f0 else None,f0_frames=len(parsed_f0),unvoiced_frames=sum(f==0 for _,f in parsed_f0),f0_zero_policy='0 means unvoiced; consumers must map to null',voice_clone_permission='NOT_ESTABLISHED',vocal_problem_labels=False,production_enabled=False)
   audio.append(row)
   for annotator in ['A1','A2']:
    path='Annotations/Notes/'+clip+'_notes'+annotator+'.csv'
    if path not in members:continue
    for n,r in enumerate(csv.reader(io.StringIO(z.read(path).decode('utf-8-sig')))):
     if not r:continue
     start,hz,duration=map(float,r[:3]);assert start>=0 and hz>0 and duration>0
     notes.append(dict(note_id=ident('note',clip+'|'+annotator+'|'+str(n)),clip_id=clip,annotator=annotator,start_seconds=start,pitch_hz=hz,duration_seconds=duration,source_archive_member=path))

 for name,rows in [('documents',documents),('chunks',chunks),('entities',entities),('mentions',mentions),('relations_candidate',edges),('exercise_candidates',exercises),('score_segments',scores),('audio_inventory',audio),('note_annotations',notes)]:jsonl('normalized/'+name+'.jsonl',rows)
 save('source-registry.json',{'corpus_version':VERSION,'sources':sources})
 save('normalized/bilingual-glossary.json',{'status':'PROJECT_AUTHORED_ALIAS_MAP_NEEDS_REVIEW','terms':glossary})
 manifest=[dict(path=p.relative_to(DATA).as_posix(),bytes=p.stat().st_size,sha256=digest(p.read_bytes())) for p in sorted(RAW.rglob('*')) if p.is_file()]
 jsonl('raw-manifest.jsonl',manifest)

 # A portable review database, separate from the proposed production PostgreSQL schema.
 db=sqlite3.connect(OUT/'graphrag-seed.sqlite3')
 for name in ['sources','documents','chunks','entities','mentions','relations_candidate','exercise_candidates','score_segments','audio_inventory','note_annotations','chunks_fts']:db.execute('DROP TABLE IF EXISTS '+name)
 for table,rows,key in [('sources',sources,'source_id'),('documents',documents,'document_id'),('chunks',chunks,'chunk_id'),('entities',entities,'entity_id'),('mentions',mentions,'mention_id'),('relations_candidate',edges,'edge_id'),('exercise_candidates',exercises,'candidate_id'),('score_segments',scores,'segment_id'),('audio_inventory',audio,'clip_id'),('note_annotations',notes,'note_id')]:
  db.execute('CREATE TABLE '+table+' (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)')
  db.executemany('INSERT INTO '+table+' VALUES (?,?)',[(r[key],json.dumps(r,ensure_ascii=False)) for r in rows])
 db.execute('CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, text, tokenize="unicode61")')
 db.executemany('INSERT INTO chunks_fts VALUES (?,?)',[(r['chunk_id'],r['text']) for r in chunks]);db.commit()
 assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 db.close()
 summary=dict(version=VERSION,status='COLLECTED_PREPARATION_ONLY_NOT_PRODUCTION',document_count=len(documents),chunk_count=len(chunks),entity_count=len(entities),mention_count=len(mentions),candidate_relation_count=len(edges),exercise_candidate_count=len(exercises),score_count=len(list((RAW/'lieder').rglob('*.mxl'))),score_segment_count=len(scores),audio_clip_count=len(audio),audio_duration_seconds=sum(r['duration_seconds'] for r in audio),note_annotation_count=len(notes),source_count=len(sources),raw_file_count=len(manifest),raw_bytes=sum(r['bytes'] for r in manifest),user_practice_record_count=0,embeddings_computed=False,neo4j_loaded=False,milvus_loaded=False,expert_review_completed=False)
 save('SUMMARY.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
