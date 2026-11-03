"""Retrieve only frozen public source files; never accepts agreements or runs downloaded code."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,tempfile,urllib.request,os
DATA=Path(__file__).resolve().parents[1]/'datasets'
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
 entries=json.loads((DATA/'retrieval-resources.json').read_text(encoding='utf-8'))['resources'];good=[];bad=[]
 for row in entries:
  target=(DATA/row['path']).resolve()
  if DATA.resolve() not in target.parents:raise ValueError('outside data directory')
  if target.is_file() and sha(target)==row['sha256']:good.append(row['path']);continue
  if args.verify_only:bad.append({'path':row['path'],'reason':'missing or hash mismatch'});continue
  target.parent.mkdir(parents=True,exist_ok=True)
  temp=target.with_name(target.name+'.fetching')
  try:
   curl=shutil.which('curl.exe') or shutil.which('curl')
   if curl:
    subprocess.run([curl,'--silent','--show-error','--location','--fail','--max-time','240','--max-filesize',str(max(row['bytes']+1024,1024*1024)),row['url'],'--output',str(temp)],check=True)
   else:
    with urllib.request.urlopen(urllib.request.Request(row['url'],headers={'User-Agent':'DeepVocal-DataPreparation/1.3'}),timeout=90) as response,temp.open('wb') as out:
     total=0
     while True:
      b=response.read(1024*1024)
      if not b:break
      total+=len(b)
      if total>row['bytes']+1024:raise ValueError('source grew beyond frozen size')
      out.write(b)
   if sha(temp)!=row['sha256']:raise ValueError('upstream content changed; preserve existing file and review new version')
   os.replace(temp,target);good.append(row['path'])
  except Exception as e:bad.append({'path':row['path'],'reason':str(e)})
  finally:
   if temp.exists():temp.unlink()
 print(json.dumps({'verified':len(good),'failed':bad,'network_used':not args.verify_only,'purpose':'frozen dataset files only, no model or app'},ensure_ascii=False,indent=2))
 return bool(bad)
if __name__=='__main__':raise SystemExit(main())
