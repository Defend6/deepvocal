"""Collect attributable Chinese source snapshots; data preparation only, no app code."""
from pathlib import Path
import concurrent.futures, datetime, hashlib, json, time, urllib.parse, urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'datasets/zh-corpus-20261103'
RAW = OUT / 'raw'
UA = 'DeepVocalResearchCorpus/2.0 (personal noncommercial research; source attribution retained)'
TITLES = ['声乐','歌唱','音域','音区','假声','头声','胸声','声部','视唱练耳','唱名法','首调唱名法','固定调唱名法','音高','音准','音程','半音','全音','音阶','调号','节奏','节拍','拍号','切分音','十二平均律','基频','谐波','音色','响度','分贝','削波','采样率','混响','声带','共振峰','颤音']

def fetch(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':UA}), timeout=35) as r:
                return r.read()
        except Exception:
            if attempt == 2: raise
            time.sleep(2 * (attempt + 1))

def api(params):
    return fetch('https://zh.wikipedia.org/w/api.php?' + urllib.parse.urlencode({'format':'json', **params}))

def one(title):
    data = api({'action':'query','titles':title,'redirects':1,'converttitles':1,'prop':'info|pageprops|revisions','inprop':'url','rvprop':'ids|timestamp','rvlimit':1})
    meta = json.loads(data)
    pages = list(meta['query']['pages'].values())
    page = pages[0]
    if 'missing' in page: return {'requested_title':title,'status':'missing'}
    if 'disambiguation' in page.get('pageprops',{}): return {'requested_title':title,'status':'disambiguation','page_id':page['pageid'],'title':page['title']}
    rev = page['revisions'][0]['revid']
    # Pin both rendered text and original markup to the same immutable revision.
    body = api({'action':'parse','oldid':rev,'prop':'wikitext|text|displaytitle|revid','disableeditsection':1})
    parsed = json.loads(body)['parse']
    folder = RAW / 'zhwiki' / str(page['pageid'])
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'metadata.json').write_bytes(data)
    (folder/'revision.json').write_bytes(body)
    (folder/'source.wiki').write_text(parsed['wikitext']['*'],encoding='utf8')
    (folder/'source.html').write_text(parsed['text']['*'],encoding='utf8')
    return {'requested_title':title,'status':'downloaded','page_id':page['pageid'],'title':page['title'],'revision_id':rev,'timestamp':page['revisions'][0]['timestamp'],'url':page['fullurl'],'permalink':f'https://zh.wikipedia.org/w/index.php?oldid={rev}','history_url':f'https://zh.wikipedia.org/w/index.php?curid={page["pageid"]}&action=history','raw_dir':folder.relative_to(OUT).as_posix()}

def main():
    RAW.mkdir(parents=True,exist_ok=True)
    (OUT/'scripts').mkdir(exist_ok=True)
    log=[]
    # Three requests in parallel, bounded retry, no rate-limit circumvention.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        tasks={pool.submit(one,t):t for t in TITLES}
        for future in concurrent.futures.as_completed(tasks):
            try: item=future.result()
            except Exception as e: item={'requested_title':tasks[future],'status':'failed','error':str(e)}
            log.append(item)
    log.sort(key=lambda x:TITLES.index(x['requested_title']))
    rights={}
    for key,url in {
        'siteinfo':'https://zh.wikipedia.org/w/api.php?action=query&format=json&meta=siteinfo&siprop=rightsinfo|general',
        'terms':'https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use',
        'cc_by_sa_4':'https://creativecommons.org/licenses/by-sa/4.0/legalcode.en',
        'opencpop_readme':'https://raw.githubusercontent.com/wenet-e2e/opencpop/master/README.md',
        'opencpop_site':'https://wenet-e2e.github.io/opencpop/',
    }.items():
        try:
            data=fetch(url); (RAW/(key+'.txt')).write_bytes(data)
            rights[key]={'url':url,'file':'raw/'+key+'.txt','sha256':hashlib.sha256(data).hexdigest(),'status':'downloaded'}
        except Exception as e: rights[key]={'url':url,'status':'failed','error':str(e)}
    (OUT/'collection-log.json').write_text(json.dumps({'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pages':log,'evidence':rights},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'downloaded':sum(x['status']=='downloaded' for x in log),'other':[x for x in log if x['status']!='downloaded'],'evidence':rights},ensure_ascii=True,indent=2))

if __name__ == '__main__': main()
