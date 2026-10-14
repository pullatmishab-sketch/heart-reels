"""Google Sheet (or CSV) -> one MP4 per row. Always-on mode keeps going until the sheet has no pending rows,
then keeps watching for new rows. Shows created / pending / failed in the sheet (status column).
Usage:  python batch.py              (one pass over sheet_url.txt)
        python batch.py --watch 2    (never stops: re-check the sheet every 2 minutes)
        python batch.py rows.csv     (local csv)"""
import csv, io, json, os, re, subprocess, sys, time, traceback, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'videos'); WORK = os.path.join(HERE, 'work')
try:
    import github_upload as gh
except Exception:
    gh = None
MAX_TRIES = 3; UP_TRIES = 5
tries = {}; up_tries = {}; last_pushed = {}; pages_ok = [False]

def sheet_gid(src):
    g = re.search(r'gid=(\d+)', src); return g.group(1) if g else '0'

def load_rows(src):
    if os.path.exists(src):
        text = open(src, encoding='utf-8-sig').read()
    else:
        m = re.search(r'/d/([\w-]+)', src)
        url = f'https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={sheet_gid(src)}' if m else src
        text = urllib.request.urlopen(url, timeout=60).read().decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(text)))

def to_script(r):
    lines = []
    for ln in (r.get('dialogue') or '').replace('\r', '').split('\n'):
        m = re.match(r'\s*(HER|HIM)\s*:\s*(.+)', ln, re.I)
        if m: lines.append({"who": m.group(1).upper(), "text": m.group(2).strip()})
    if not lines: raise ValueError('no dialogue lines (use "HER: ..." / "HIM: ..." one per line)')
    lines[-1]["pause"] = 0.9
    kw = (r.get('cta_keyword') or '').strip().upper()
    voice = (r.get('cta_voice') or '').strip() or (f"Comment {kw.lower()}, and we'll send you the guide." if kw else (r.get('cta_card') or '').strip())
    card = (r.get('cta_card') or '').strip()
    if voice: lines.append({"who": "HER", "text": voice, "cta": True, "pause": 0.2})
    return {"hook": (r.get('hook') or '').replace('\\n', '\n'), "cta": voice, "cta_keyword": kw,
            "cta_card": card or voice, "cta_sub": (r.get('cta_sub') or '').strip(), "lines": lines}

# ---- status sync to the sheet (optional: needs status_webapp.txt, see SETUP_STATUS.txt) ----
def webapp():
    try:
        p = open(os.path.join(HERE, 'status_webapp.txt'), encoding='utf-8').read().split('\n')
        return p[0].strip(), (p[1].strip() if len(p) > 1 else '')
    except Exception:
        return '', ''

def push(updates, gid):
    """updates: {id: status}. Only sends changes. Never raises."""
    url, token = webapp()
    ch = [{"id": k, "status": v, "time": time.strftime('%Y-%m-%d %H:%M')} for k, v in updates.items() if last_pushed.get(k) != v]
    if not ch: return
    if url:
        try:
            body = json.dumps({"token": token, "gid": gid, "updates": ch}).encode('utf-8')
            req = urllib.request.Request(url, data=body, headers={'Content-Type': 'text/plain;charset=utf-8'})
            urllib.request.urlopen(req, timeout=90).read()
        except Exception as e:
            # Apps Script answers POST with a redirect; the write has already happened, so only real failures matter
            if 'HTTP Error 4' in str(e) and '302' not in str(e):
                print('(status sync problem:', e, ')', flush=True); return
    for c in ch: last_pushed[c["id"]] = c["status"]

def write_local(state):
    with open(os.path.join(HERE, 'status.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['id', 'status'])
        for k, v in state.items(): w.writerow([k, v])

def valid_mp4(p):
    try:
        n = os.path.getsize(p)
        if n < 300_000: return False
        with open(p, 'rb') as f:
            head = f.read(65536); f.seek(max(0, n - 65536)); tail = f.read()
        return b'moov' in head or b'moov' in tail
    except Exception:
        return False

def publish_phase(rows, state, gid):
    """created -> upload to GitHub -> wait until live -> mark ready (Make then posts it to Instagram). Also cleans up."""
    if not (gh and gh.configured()): return 0
    try:
        if not pages_ok[0]: gh.ensure_pages(); pages_ok[0] = True
    except Exception as e:
        print('!! github setup problem:', e, flush=True); return 1
    st = gh.load_state(); files = st['files']; waiting = 0
    for vid, r in rows:
        if state[vid] != 'created': continue
        mp4 = os.path.join(OUT, vid + '.mp4'); url = gh.public_url(f'videos/{vid}.mp4')
        if vid not in files and not valid_mp4(mp4):
            print('!! broken/partial video, will remake:', vid, flush=True)
            os.makedirs(os.path.join(HERE, '_bad'), exist_ok=True)
            os.replace(mp4, os.path.join(HERE, '_bad', vid + '_' + str(int(time.time())) + '.mp4')); state[vid] = 'pending'; waiting += 1; continue
        try:
            if vid not in files:
                print('==> uploading', vid, 'to GitHub', flush=True)
                gh.upload(mp4, f'videos/{vid}.mp4'); files[vid] = {'t': time.time()}; gh.save_state(st)
            if gh.is_live(url):
                state[vid] = 'ready'; push({vid: 'ready'}, gid); print('==> ready for Instagram:', vid, flush=True)
            else:
                waiting += 1
        except Exception as e:
            up_tries[vid] = up_tries.get(vid, 0) + 1
            print('!! upload failed', vid, f'({up_tries[vid]}/{UP_TRIES})', e, flush=True)
            if up_tries[vid] >= UP_TRIES: state[vid] = 'failed'; push({vid: 'failed'}, gid)
            else: waiting += 1
    # cleanup: remove videos Instagram already has, so the repo never fills up
    try:
        for vid in list(files):
            if state.get(vid) == 'posted' and time.time() - files[vid]['t'] > 6 * 3600:
                gh.delete(f'videos/{vid}.mp4'); del files[vid]; gh.save_state(st); print('cleaned', vid, flush=True)
        if time.time() - st.get('last_squash', 0) > 7 * 86400:
            gh.squash_history(); st['last_squash'] = time.time(); gh.save_state(st); print('repo history squashed', flush=True)
    except Exception as e:
        print('(cleanup skipped:', e, ')', flush=True)
    return waiting

def run(src):
    if os.path.exists(os.path.join(HERE, 'github_render.txt')): return 0   # GitHub Actions renders now; this PC stays idle
    os.makedirs(OUT, exist_ok=True); made = 0; gid = sheet_gid(src)
    rows = []
    for r in load_rows(src):
        vid = re.sub(r'[^\w-]', '_', (r.get('id') or '').strip())
        if vid: rows.append((vid, r))
    state = {}; from_sheet = set()
    for vid, r in rows:
        sheet_status = (r.get('status') or '').strip().lower()
        if sheet_status in ('ready', 'posting', 'posted'): state[vid] = sheet_status; from_sheet.add(vid)
        elif os.path.exists(os.path.join(OUT, vid + '.mp4')): state[vid] = 'created'
        elif sheet_status == 'skip': state[vid] = 'skip'
        elif tries.get(vid, 0) >= MAX_TRIES: state[vid] = 'failed'
        else: state[vid] = 'pending'
    push({k: v for k, v in state.items() if v != 'skip' and k not in from_sheet}, gid); write_local(state)
    todo = [(v, r) for v, r in rows if state[v] == 'pending']
    cnt = lambda s: sum(x == s for x in state.values())
    print(f"sheet: {cnt('posted')} posted, {cnt('ready')} ready, {cnt('created')} created, {len(todo)} pending, {cnt('failed')} failed", flush=True)
    for vid, r in todo:
        mp4 = os.path.join(OUT, vid + '.mp4')
        print('==> making', vid, flush=True)
        push({vid: 'making'}, gid)
        try:
            tries[vid] = tries.get(vid, 0) + 1
            sc = to_script(r); d = os.path.join(WORK, vid); os.makedirs(d, exist_ok=True)
            sj = os.path.join(d, 'script.json'); json.dump(sc, open(sj, 'w'))
            py = sys.executable
            subprocess.run([py, os.path.join(HERE, 'tts.py'), sj, d], check=True)
            subprocess.run([py, os.path.join(HERE, 'env.py'), d], check=True)
            tmp = os.path.join(OUT, vid + '.part.mp4')
            subprocess.run([py, os.path.join(HERE, 'render.py'), d, tmp], check=True)
            os.replace(tmp, mp4)
            open(os.path.join(OUT, vid + '_caption.txt'), 'w', encoding='utf-8').write((r.get('caption') or '').strip())
            made += 1; state[vid] = 'created'
        except Exception as e:
            state[vid] = 'failed' if tries[vid] >= MAX_TRIES else 'pending'
            print('!! failed', vid, f'(try {tries[vid]}/{MAX_TRIES})', e, flush=True)
            if os.path.exists(mp4) and os.path.getsize(mp4) == 0: os.remove(mp4)
        push({vid: state[vid]}, gid); write_local(state)
        if state[vid] == 'created': publish_phase(rows, state, gid)   # upload each video as soon as it is made
    waiting = publish_phase(rows, state, gid); write_local(state)
    left = sum(s == 'pending' for s in state.values()) + waiting
    print(f'done, {made} new video(s) in {OUT}. still to do: {left}', flush=True)
    return left

if __name__ == '__main__':
    a = sys.argv[1:]; watch = None
    if '--watch' in a:
        i = a.index('--watch'); watch = float(a[i + 1]) if len(a) > i + 1 else 2; del a[i:i + 2]
    src = a[0] if a else open(os.path.join(HERE, 'sheet_url.txt')).read().strip()
    def _sig(): return tuple(os.path.getmtime(os.path.join(HERE, f)) for f in ('batch.py', 'github_upload.py', 'sync_code.py', 'gh_render.py', 'template.html') if os.path.exists(os.path.join(HERE, f)))
    sig0 = _sig()
    try:
        import sync_code; sync_code.run()
    except Exception as e:
        print('(code sync problem:', e, ')', flush=True)
    while True:
        if watch and _sig() != sig0:
            print('code updated, restarting...', flush=True); os.execv(sys.executable, [sys.executable] + sys.argv)
        try:
            left = run(src)
        except Exception:
            if not watch: raise
            traceback.print_exc(); left = 1
        if not watch: break
        if left == 0: print(f'all rows done. waiting {watch} min for new rows...', flush=True)
        time.sleep(watch * 60 if left == 0 else 20)
