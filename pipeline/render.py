import json, os, sys, subprocess, shutil
from pathlib import Path
from multiprocessing import Pool
from common import ffmpeg_path

def work(a):
    page_uri, frames, fps, lo, hi = a
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={'width': 1080, 'height': 1920})
        pg.goto(page_uri); pg.wait_for_function('window.__ready===true')
        for i in range(lo, hi):
            pg.evaluate(f'render({i / fps})')
            pg.screenshot(path=os.path.join(frames, f'f{i:05d}.jpg'), type='jpeg', quality=94)
        br.close()
    return hi - lo

if __name__ == '__main__':
    D = sys.argv[1]; OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(D, 'video.mp4')
    here = Path(__file__).resolve().parent
    tl = json.load(open(os.path.join(D, 'timeline.json')))
    html = (here / 'template.html').read_text(encoding='utf8').replace('/*TL*/null', json.dumps(tl))
    page = Path(D).resolve() / 'page.html'; page.write_text(html, encoding='utf8')
    fps = tl['fps']; N = int(tl['duration'] * fps)
    frames = str(Path(D).resolve() / 'frames'); shutil.rmtree(frames, ignore_errors=True); os.makedirs(frames)
    step = (N + 1) // 2
    jobs = [(page.as_uri(), frames, fps, 0, step), (page.as_uri(), frames, fps, step, N)]
    with Pool(2) as pool: print('frames', sum(pool.map(work, jobs)))
    subprocess.run([ffmpeg_path(), '-y', '-loglevel', 'error', '-framerate', str(fps), '-i', os.path.join(frames, 'f%05d.jpg'),
                    '-i', os.path.join(D, 'audio.wav'), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '19', '-preset', 'veryfast',
                    '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', OUT], check=True)
    print('wrote', OUT)
