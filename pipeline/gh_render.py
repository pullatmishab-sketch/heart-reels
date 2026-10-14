"""Runs inside GitHub Actions. Sheet -> render pending rows -> commit mp4 to this repo -> wait for Pages -> mark 'ready'.
Make.com then posts 'ready' rows to Instagram. Config comes from the repo secret PIPELINE_CONFIG (3 lines):
  1) Google Sheet link   2) status web app URL   3) status token"""
import json, os, re, subprocess, sys, time, shutil, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import batch as B

VID = os.path.join(ROOT, 'videos'); WORK = '/tmp/work'; os.makedirs(VID, exist_ok=True)
BUDGET = int(os.environ.get('RENDER_BUDGET_MIN', '40')) * 60
cfg = [l.strip() for l in os.environ['PIPELINE_CONFIG'].strip().splitlines() if l.strip()]
SHEET, WEBAPP, TOKEN = cfg[0], cfg[1], cfg[2]
open(os.path.join(HERE, 'status_webapp.txt'), 'w').write(WEBAPP + '\n' + TOKEN + '\n')
OWNER, REPO = os.environ['GITHUB_REPOSITORY'].split('/')
BASE = f'https://{OWNER.lower()}.github.io/{REPO}'
STATE_F = os.path.join(ROOT, 'state.json'); T0 = time.time(); MAX_TRIES = 3
state = json.load(open(STATE_F)) if os.path.exists(STATE_F) else {'tries': {}}

def git(*a, check=True):
    r = subprocess.run(['git'] + list(a), cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode: raise RuntimeError('git ' + ' '.join(a) + ': ' + (r.stderr or r.stdout)[-400:])
    return r

def commit_push(msg):
    json.dump(state, open(STATE_F, 'w'))
    git('add', '-A', 'videos', 'state.json')
    if not git('status', '--porcelain').stdout.strip(): return
    git('commit', '-m', msg)
    for i in range(6):
        if git('push', check=False).returncode == 0: return
        git('pull', '--rebase', '-X', 'theirs', check=False); time.sleep(3 + i * 3)
    raise RuntimeError('git push kept failing')

def is_live(vid):
    try:
        req = urllib.request.Request(f'{BASE}/videos/{vid}.mp4?v={int(time.time())}', method='HEAD')
        r = urllib.request.urlopen(req, timeout=30)
        return r.status == 200 and 'video' in (r.headers.get('Content-Type') or '')
    except Exception:
        return False

def wait_live(vid, secs=600):
    t = time.time()
    while time.time() - t < secs:
        if is_live(vid): return True
        time.sleep(15)
    return False

gid = B.sheet_gid(SHEET)
rows = []
for r in B.load_rows(SHEET):
    vid = re.sub(r'[^\w-]', '_', (r.get('id') or '').strip())
    if vid: rows.append((vid, r))
have = lambda v: os.path.exists(os.path.join(VID, v + '.mp4'))
st = {v: (r.get('status') or '').strip().lower() for v, r in rows}

# 1) videos Instagram already has are no longer needed -> keep the repo small
gone = [v for v, _ in rows if st[v] == 'posted' and have(v)]
for v in gone: os.remove(os.path.join(VID, v + '.mp4'))
if gone: commit_push(f'clean {len(gone)} posted video(s)'); print('cleaned', gone, flush=True)

# 2) decide what to do
todo, check = [], []
for v, r in rows:
    s = st[v]
    if s in ('posting', 'posted', 'skip'): continue
    if s == 'ready':
        if not have(v): todo.append((v, r))          # marked ready but file is gone -> make it again
        continue
    if have(v): check.append(v)
    elif s == 'failed': continue                      # set the status back to "pending" in the sheet to retry
    else: todo.append((v, r))
print(f'to render: {len(todo)}, to verify: {len(check)}', flush=True)

for v in check:
    if is_live(v): B.push({v: 'ready'}, gid); print('ready:', v, flush=True)

# 3) render loop
for v, r in todo:
    if time.time() - T0 > BUDGET: print('time budget used, next run continues', flush=True); break
    if state['tries'].get(v, 0) >= MAX_TRIES:
        B.push({v: 'failed'}, gid); continue
    print('==> making', v, flush=True); B.push({v: 'making'}, gid)
    state['tries'][v] = state['tries'].get(v, 0) + 1
    try:
        d = os.path.join(WORK, v); shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
        sj = os.path.join(d, 'script.json'); json.dump(B.to_script(r), open(sj, 'w'))
        py = sys.executable
        subprocess.run([py, os.path.join(HERE, 'tts.py'), sj, d], check=True)
        subprocess.run([py, os.path.join(HERE, 'env.py'), d], check=True)
        tmp = os.path.join(d, 'out.mp4')
        subprocess.run([py, os.path.join(HERE, 'render.py'), d, tmp], check=True)
        if not B.valid_mp4(tmp): raise RuntimeError('rendered file looks broken')
        shutil.copy(tmp, os.path.join(VID, v + '.mp4')); shutil.rmtree(d, ignore_errors=True)
        B.push({v: 'created'}, gid)
        commit_push('add ' + v)
        if wait_live(v): B.push({v: 'ready'}, gid); print('==> ready for Instagram:', v, flush=True)
        else: print('(not live yet, next run will mark it ready)', v, flush=True)
    except Exception as e:
        print('!! failed', v, e, flush=True)
        B.push({v: 'failed' if state['tries'][v] >= MAX_TRIES else 'pending'}, gid)
        try: commit_push('state')
        except Exception: pass

# 4) keep git history small: squash when it grows
try:
    if int(git('rev-list', '--count', 'HEAD').stdout.strip()) > 150:
        git('checkout', '--orphan', 'squashed'); git('add', '-A'); git('commit', '-m', 'squash history')
        git('branch', '-M', 'main'); git('push', '-f', 'origin', 'main'); print('history squashed', flush=True)
except Exception as e:
    print('(squash skipped:', e, ')', flush=True)
print('done', flush=True)
