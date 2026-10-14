"""Two-voice dialogue -> audio.wav + timeline.json
Engine: Microsoft Edge neural voices (natural, free). Falls back to offline espeak if unavailable."""
import json, sys, os, wave, struct, asyncio, subprocess, tempfile
from common import ffmpeg_path

# ---- change voices here (run `edge-tts --list-voices` to see all) ----
EDGE_VOICES = {"HER": ("en-US-JennyNeural", "+0%"), "HIM": ("en-US-AndrewNeural", "-4%")}

def read_wav(path):
    with wave.open(path) as w:
        sr = w.getframerate(); n = w.getnframes()
        return list(struct.unpack('<%dh' % n, w.readframes(n))), sr

def edge_line(text, who, tmp, i):
    mp3 = os.path.join(tmp, f'l{i}.mp3'); wav = os.path.join(tmp, f'l{i}.wav')
    if os.environ.get('FAKE_EDGE'):               # offline self-test of the decode path only
        import espeak_engine as es
        s, sr = es.say_line(text, who); es.write(wav.replace('.wav', '_src.wav'), s, sr)
        subprocess.run([ffmpeg_path(), '-y', '-loglevel', 'error', '-i', wav.replace('.wav', '_src.wav'), mp3], check=True)
    else:
        import edge_tts
        voice, rate = EDGE_VOICES[who]
        for attempt in range(4):
            try:
                asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(mp3)); break
            except Exception as e:
                if attempt == 3: raise
    subprocess.run([ffmpeg_path(), '-y', '-loglevel', 'error', '-i', mp3, '-ac', '1', '-ar', '24000', wav], check=True)
    return read_wav(wav)

# ---- offline natural voices (Piper). Models live in the "voices" folder next to this file ----
PIPER_VOICES = {"HER": "en_US-amy-medium", "HIM": "en_US-ryan-medium"}
HERE = os.path.dirname(os.path.abspath(__file__))

def piper_line(text, who, tmp, i):
    wav = os.path.join(tmp, f'p{i}.wav')
    model = os.path.join(HERE, 'voices', PIPER_VOICES[who] + '.onnx')
    subprocess.run([sys.executable, '-m', 'piper', '--model', model, '--output_file', wav],
                   input=text.encode('utf8'), check=True)
    return read_wav(wav)

def get_line(text, who, tmp, i, engine):
    if engine == 'piper':
        return piper_line(text, who, tmp, i)
    if engine == 'edge':
        return edge_line(text, who, tmp, i)
    import espeak_engine as es
    return es.say_line(text, who)

def write(path, samples, sr):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(struct.pack('<%dh' % len(samples), *samples))

if __name__ == '__main__':
    script = json.load(open(sys.argv[1])); out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    engine = os.environ.get('ENGINE', 'edge')
    if engine == 'edge':
      try:
        import edge_tts  # noqa
      except ImportError:
        engine = 'espeak'
    if os.environ.get('FAKE_EDGE'): engine = 'edge'
    if os.environ.get('FORCE_ESPEAK'): engine = 'espeak'
    print('voice engine:', engine)
    tmp = tempfile.mkdtemp()
    sr = None; samples = []; timeline = []; t = 0.0
    for i, ln in enumerate(script["lines"]):
        s, lsr = get_line(ln["text"], ln["who"], tmp, i, engine)
        if sr is None:
            sr = lsr; samples += [0] * int(sr * 0.5); t = 0.5
        dur = len(s) / sr
        timeline.append({"who": ln["who"], "text": ln["text"], "start": round(t, 3), "end": round(t + dur, 3), "cta": bool(ln.get("cta"))})
        samples += s
        gap = int(sr * ln.get("pause", 0.45)); samples += [0] * gap
        t += dur + gap / sr
    samples += [0] * int(sr * 0.6)
    write(os.path.join(out, "audio.wav"), samples, sr)
    json.dump({"duration": round(len(samples) / sr, 3), "hook": script["hook"], "cta": script["cta"],
               "cta_keyword": script.get("cta_keyword", ""), "cta_card": script.get("cta_card", ""), "cta_sub": script.get("cta_sub", ""), "lines": timeline}, open(os.path.join(out, "timeline.json"), "w"), indent=1)
    print('duration', round(len(samples) / sr, 2))
