"""Check collected evidence and local review DB; never validates musical correctness."""
from pathlib import Path
import hashlib,json,sqlite3,zipfile
ROOT=Path(__file__).resolve().parents[1]/'datasets'
def rows(name):return [json.loads(s) for s in (ROOT/name).read_text(encoding='utf-8').splitlines() if s.strip()]
def main():
 checks=[]
 def check(ok,label):
  if not ok:raise ValueError(label)
  checks.append(label)
 summary=json.loads((ROOT/'SUMMARY.json').read_text(encoding='utf-8'))
 sources=json.loads((ROOT/'source-registry.json').read_text(encoding='utf-8'))['sources'];sids={r['source_id'] for r in sources}
 docs=rows('normalized/documents.jsonl');dmap={r['document_id']:r for r in docs}
 chunks=rows('normalized/chunks.jsonl');cmap={r['chunk_id']:r for r in chunks}
 entities=rows('normalized/entities.jsonl');eids={e['entity_id'] for e in entities}
 check(len(dmap)==len(docs) and len(cmap)==len(chunks),'unique document and chunk ids')
 check(all(d['source_id'] in sids for d in docs),'document provenance')
 check(all(c['text']==dmap[c['document_id']]['text'][c['start_char']:c['end_char']] for c in chunks),'exact source spans')
 check(all(hashlib.sha256(c['text'].encode()).hexdigest()==c['text_sha256'] for c in chunks),'chunk hashes')
 check(all(not c['production_enabled'] and c['review_status']=='NEEDS_REVIEW' for c in chunks),'unreviewed corpus cannot activate')
 mentions=rows('normalized/mentions.jsonl');edges=rows('normalized/relations_candidate.jsonl')
 check(all(m['entity_id'] in eids and m['chunk_id'] in cmap and m['evidence_text']==cmap[m['chunk_id']]['text'][m['start_char']:m['end_char']] for m in mentions),'mention refs and spans')
 check(all(e['subject_id'] in eids and e['object_id'] in eids and e['source_chunk_id'] in cmap and e['predicate']=='CO_OCCURS_WITH' and not e['production_enabled'] for e in edges),'candidate edges cannot imply causes')
 for m in rows('raw-manifest.jsonl'):
  p=(ROOT/m['path']).resolve()
  check(ROOT.resolve() in p.parents and p.is_file() and p.stat().st_size==m['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==m['sha256'],'raw integrity '+m['path'])
 audio=rows('normalized/audio_inventory.jsonl');notes=rows('normalized/note_annotations.jsonl');amap={a['clip_id']:a for a in audio}
 check(len(audio)==40 and all(a['f0_frames']>0 for a in audio),'40 real audio clips with F0 annotations')
 check(all(n['clip_id'] in amap and n['pitch_hz']>0 and n['duration_seconds']>0 and n['start_seconds']>=0 and n['start_seconds']<amap[n['clip_id']]['duration_seconds']+.1 for n in notes),'note annotation ranges and references')
 with zipfile.ZipFile(ROOT/'raw/vocadito/vocadito.zip') as z:
  check(z.testzip() is None,'upstream archive CRC')
  check(all(hashlib.sha256(z.read(a['archive_member'])).hexdigest()==a['sha256'] for a in audio),'audio member hashes')
 scores=rows('normalized/score_segments.jsonl')
 check(len(scores)==16 and all(s['note_count']==len(s['note_events']) and s['time_basis']=='score_quarter_notes_not_seconds' for s in scores),'score segments retain symbolic time')
 check(all((ROOT/s['source_path']).is_file() for s in scores),'score source files')
 check(summary['user_practice_record_count']==0 and not summary['embeddings_computed'] and not summary['expert_review_completed'],'no fabricated users or completed model work')
 db=sqlite3.connect('file:'+str(ROOT/'normalized/graphrag-seed.sqlite3')+'?mode=ro',uri=True)
 check(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','SQLite integrity')
 check(db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]==len(chunks),'SQLite and JSONL chunk counts')
 check(db.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'breath'").fetchone()[0]>0,'offline lexical index functional')
 db.close()
 result={'status':'PASS','checks_passed':len(checks),'documents':len(docs),'chunks':len(chunks),'audio_clips':len(audio),'score_segments':len(scores),'note_annotations':len(notes),'expert_review':False,'production_import':False}
 print(json.dumps(result,ensure_ascii=False,indent=2));return result
if __name__=='__main__':main()
