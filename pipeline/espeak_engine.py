import ctypes, struct, wave
import espeakng_loader as e
lib = ctypes.CDLL(e.get_library_path())
CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)
buf = []
def _cb(wav, n, ev):
    if wav and n > 0: buf.extend(wav[i] for i in range(n))
    return 0
cb = CB(_cb)
sr = lib.espeak_Initialize(1, 0, e.get_data_path().encode(), 0)
lib.espeak_SetSynthCallback(cb)
V = {"HER": ("en-us+f3", 160, 62), "HIM": ("en-us+m3", 150, 38)}
def say_line(text, who):
    voice, rate, pitch = V[who]; buf.clear()
    lib.espeak_SetVoiceByName(voice.encode()); lib.espeak_SetParameter(1, rate, 0); lib.espeak_SetParameter(3, pitch, 0)
    b = text.encode('utf8'); lib.espeak_Synth(b, len(b) + 1, 0, 1, 0, 1, None, None); lib.espeak_Synchronize()
    return list(buf), sr
def write(path, s, rate):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(struct.pack('<%dh' % len(s), *s))
