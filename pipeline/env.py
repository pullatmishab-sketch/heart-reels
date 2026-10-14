import wave, json, sys, struct, math
d = sys.argv[1]
w = wave.open(d + '/audio.wav'); sr = w.getframerate(); n = w.getnframes()
s = struct.unpack('<%dh' % n, w.readframes(n))
FPS = 30; win = sr // FPS
env = []
for i in range(0, n, win):
    c = s[i:i + win]
    env.append(math.sqrt(sum(x * x for x in c) / max(1, len(c))) / 32768)
m = max(env) or 1
env = [round(min(1, (e / m) ** 0.7), 3) for e in env]
t = json.load(open(d + '/timeline.json'))
t['env'] = env; t['fps'] = FPS
json.dump(t, open(d + '/timeline.json', 'w'))
print('frames', len(env), 'dur', t['duration'])
