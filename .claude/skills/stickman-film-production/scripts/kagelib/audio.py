"""Audio production: voice generation, procedural sound design, stems and mix.

Voice providers are adapters. The bundled adapter drives the macOS `say`
command (offline system voices). Unsupported delivery controls are reported,
never silently claimed. All synthesized SFX/music is generated here from
deterministic seeds, so provenance and licensing are unambiguous.
"""
from __future__ import annotations

import array
import json
import math
import platform
import random
import re
import shutil
import tempfile
import wave
from pathlib import Path

from .util import KageError, ffmpeg_filter_script_args, log, require_tool, run, sha256_json

SR = 48000

# ---------------------------------------------------------------- WAV helpers


def write_wav(path: Path, samples, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = array.array("h", (max(-32767, min(32767, int(s * 32767))) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav_mono(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        if sw != 2:
            raise KageError(f"{path}: expected 16-bit PCM")
        data = array.array("h")
        data.frombytes(w.readframes(n))
    if ch > 1:
        data = data[::ch]
    return [s / 32768.0 for s in data], sr


def envelope(samples: list[float], sr: int, fps: float) -> list[float]:
    hop = sr / fps
    out = []
    i = 0
    while int(i) < len(samples):
        seg = samples[int(i):int(i + hop)]
        out.append(round(math.sqrt(sum(s * s for s in seg) / max(1, len(seg))), 5))
        i += hop
    return out


def estimate_f0(samples: list[float], sr: int) -> float | None:
    """Median fundamental frequency (Hz) of voiced frames via autocorrelation."""
    step = max(1, sr // 8000)
    x = samples[::step]
    fs = sr / step
    frame, lag_min, lag_max = int(0.04 * fs), int(fs / 400), int(fs / 70)
    f0s = []
    for start in range(0, len(x) - frame - lag_max, frame):
        seg = x[start:start + frame + lag_max]
        energy = sum(s * s for s in seg[:frame])
        if energy / frame < 1e-4:
            continue
        corr = [sum(seg[i] * seg[i + lag] for i in range(0, frame, 2)) for lag in range(lag_min, lag_max)]
        peak = max(corr)
        if peak <= 0.3 * energy / 2:
            continue
        # Earliest strong local peak = the fundamental period. Taking the global
        # maximum instead picks multiples of the period (sub-harmonic errors).
        best_lag = 0
        for k in range(1, len(corr) - 1):
            if corr[k] >= 0.85 * peak and corr[k] >= corr[k - 1] and corr[k] >= corr[k + 1]:
                best_lag = k + lag_min
                break
        if best_lag:
            f0s.append(fs / best_lag)
    if not f0s:
        return None
    f0s.sort()
    return round(f0s[len(f0s) // 2], 1)


# ---------------------------------------------------------------- voice adapter

def say_available() -> bool:
    return platform.system() == "Darwin" and shutil.which("say") is not None


def provider_capabilities(provider: str) -> dict:
    if provider == "macos_say":
        return {"pitch": True, "rate": True, "volume": True, "pauses": True, "emotion": False, "whisper": False,
                "word_alignment": False, "phoneme_alignment": False,
                "notes": "Emotion is approximated with rate/volume only; no alignment output (viseme mode unavailable)."}
    return {"available": False}


def _say_text(line: dict, voice: dict) -> tuple[str, int, list[str]]:
    """Build `say` input with embedded commands; returns text, rate, approximations."""
    notes = []
    text = line["text"]
    for word, spoken in voice.get("pronunciations", {}).items():
        text = re.sub(rf"\b{re.escape(word)}\b", spoken, text)
    delivery = line.get("delivery", {})
    for word, ms in delivery.get("pauses_ms", {}).get("after_word", {}).items():
        spoken_word = word
        for w, sp in voice.get("pronunciations", {}).items():
            spoken_word = re.sub(rf"\b{re.escape(w)}\b", sp, spoken_word)
        if spoken_word in text:
            text = text.replace(spoken_word, f"{spoken_word} [[slnc {int(ms)}]]", 1)
        else:
            notes.append(f"pause after '{word}' not applied (word not found)")
    rate = int(voice.get("rate_wpm", 175))
    pace = delivery.get("pace", "normal")
    rate = int(rate * {"slow": 0.9, "fast": 1.12}.get(pace, 1.0))
    prefix = ""
    pbas = (voice.get("pitch") or {}).get("pbas")
    if pbas is not None:
        prefix += f"[[pbas {int(pbas)}]] "
    vol = {"soft": 0.75, "loud": 1.0, "whisper": 0.55, "shout": 1.0}.get(delivery.get("volume", "normal"))
    if vol is not None:
        prefix += f"[[volm {vol}]] "
    if delivery.get("volume") in ("whisper", "shout"):
        notes.append(f"'{delivery['volume']}' approximated by volume only (provider has no style control)")
    if delivery.get("emotion"):
        notes.append(f"emotion '{delivery['emotion']}' not supported by provider; approximated via pace/volume")
    return prefix + text, rate, notes


def line_cache_key(line: dict, voice: dict) -> str:
    return sha256_json({"text": line["text"], "delivery": line.get("delivery", {}), "voice": {k: v for k, v in voice.items() if k not in ("status",)},
                        "provider_os": platform.mac_ver()[0] or platform.platform()})


def generate_line(line: dict, voice: dict, out_wav: Path, fps: float) -> dict:
    """Generate one dialogue line. Returns the generation record (duration, envelope, f0, notes)."""
    if voice["provider"] != "macos_say":
        raise KageError(f"{line['id']}: voice provider '{voice['provider']}' has no adapter in this installation")
    if not say_available():
        raise KageError(f"{line['id']}: macOS 'say' is not available on this machine; supply recorded audio or another provider")
    text, rate, notes = _say_text(line, voice)
    ffmpeg = require_tool("ffmpeg")
    with tempfile.TemporaryDirectory() as td:
        aiff = Path(td) / "line.aiff"
        run(["say", "-v", voice["provider_voice"], "-r", str(rate), "-o", str(aiff), text], timeout=120)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        # Trim leading/trailing silence so timing reflects the actual speech.
        run([ffmpeg, "-v", "error", "-y", "-i", str(aiff), "-af",
             "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse",
             "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)], timeout=120)
    samples, sr = read_wav_mono(out_wav)
    if len(samples) < sr * 0.2:
        raise KageError(f"{line['id']}: generated audio is shorter than 0.2 s (provider failure?)")
    peak = max(abs(s) for s in samples)
    return {
        "line": line["id"], "voice": voice["id"], "provider": voice["provider"], "provider_voice": voice["provider_voice"],
        "file": out_wav.name, "duration_s": round(len(samples) / sr, 4), "sample_rate": sr,
        "peak": round(peak, 4), "envelope": envelope(samples, sr, fps), "f0_hz": estimate_f0(samples, sr),
        "rendered_text": text, "rate_wpm": rate, "approximations": notes,
        "alignment": None, "cache_key": line_cache_key(line, voice), "status": "generated",
    }


# ---------------------------------------------------------------- procedural synthesis

def _lowpass(x: list[float], alpha: float) -> list[float]:
    y, acc = [], 0.0
    for s in x:
        acc += alpha * (s - acc)
        y.append(acc)
    return y


def synth(preset: str, duration: float | None = None, seed: int = 7, params: dict | None = None) -> list[float]:
    params = params or {}
    rng = random.Random(f"{preset}:{seed}")
    if preset == "footstep":
        n = int(0.16 * SR)
        noise = _lowpass([rng.uniform(-1, 1) for _ in range(n)], 0.08)
        return [0.9 * noise[i] * math.exp(-i / SR * 38) + 0.5 * math.sin(2 * math.pi * 85 * i / SR) * math.exp(-i / SR * 45) for i in range(n)]
    if preset == "seat_creak":
        n = int(0.4 * SR)
        out = []
        ph = 0.0
        for i in range(n):
            t = i / SR
            f = 170 + 40 * math.sin(2 * math.pi * 9 * t)
            ph += f / SR
            saw = 2 * (ph % 1.0) - 1
            env = math.sin(math.pi * t / 0.4) ** 2
            out.append(0.25 * saw * env)
        return _lowpass(out, 0.25)
    if preset == "whistle":
        d = 1.6
        out = []
        for i in range(int(d * SR)):
            t = i / SR
            env = min(1.0, t / 0.08) * min(1.0, (d - t) / 0.35)
            vib = 1 + 0.004 * math.sin(2 * math.pi * 5.5 * t)
            s = sum(a * math.sin(2 * math.pi * f * vib * t) for f, a in ((587, 0.5), (740, 0.4), (1174, 0.12), (1480, 0.08)))
            out.append(0.55 * s * env)
        return out
    if preset == "birds":
        d = duration or params.get("duration_s", 3.0)
        out = [0.0] * int(d * SR)
        t = 0.1
        while t < d - 0.2:
            f0 = rng.uniform(2800, 3600)
            length = rng.uniform(0.05, 0.09)
            start = int(t * SR)
            for i in range(int(length * SR)):
                u = i / (length * SR)
                f = f0 + 1600 * u
                out[start + i] += 0.35 * math.sin(2 * math.pi * f * i / SR) * math.sin(math.pi * u)
            t += rng.uniform(0.12, 0.45)
        return out
    if preset in ("train_interior", "train_exterior"):
        if not duration:
            raise KageError(f"synth preset {preset} needs a duration")
        exterior = preset == "train_exterior"
        n = int(duration * SR)
        brown, acc = [], 0.0
        for _ in range(n):
            acc = 0.995 * acc + 0.06 * rng.uniform(-1, 1)
            brown.append(acc)
        rumble = _lowpass(brown, 0.02 if not exterior else 0.05)
        period = 0.45 if exterior else 0.9
        out = []
        for i in range(n):
            t = i / SR
            ph = t % period
            clack = 0.0
            for off in (0.0, 0.16):
                dt = ph - off
                if 0 <= dt < 0.06:
                    clack += math.exp(-dt * 70) * math.sin(2 * math.pi * 210 * dt)
            s = 1.6 * rumble[i] + 0.35 * clack + 0.12 * math.sin(2 * math.pi * 48 * t) * (0.6 + 0.4 * math.sin(2 * math.pi * 0.7 * t))
            if exterior:
                s += 0.5 * brown[i]
            out.append(0.6 * s)
        return out
    if preset == "lullaby":
        if not duration:
            raise KageError("lullaby needs a duration")
        bpm = params.get("tempo_bpm", 66)
        beat = 60.0 / bpm
        n = int(duration * SR)
        out = [0.0] * n
        # C - Am - F - G, one chord per bar of 4 beats, arpeggiated in eighths.
        chords = [(261.63, 329.63, 392.0), (220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66)]
        melody = [659.25, 587.33, 523.25, 587.33, 523.25, 440.0, 392.0, 440.0]

        def note(t0: float, f: float, amp: float, decay: float, length: float):
            s0 = int(t0 * SR)
            for i in range(int(length * SR)):
                k = s0 + i
                if k >= n:
                    break
                t = i / SR
                env = min(1.0, t / 0.01) * math.exp(-t * decay)
                out[k] += amp * env * (math.sin(2 * math.pi * f * t) + 0.25 * math.sin(4 * math.pi * f * t))

        bar = 0
        t = 0.0
        while t < duration:
            chord = chords[bar % 4]
            note(t, chord[0] / 2, 0.22, 1.2, 4 * beat)
            for e in range(8):
                note(t + e * beat / 2, chord[e % 3] * (2 if e >= 4 else 1), 0.08, 3.0, 1.2)
            if bar % 2 == 0:
                for q in range(4):
                    note(t + q * beat, melody[(bar // 2 * 4 + q) % len(melody)], 0.1, 1.6, beat * 1.4)
            t += 4 * beat
            bar += 1
        peak = max(abs(s) for s in out) or 1.0
        return [0.7 * s / peak for s in out]
    raise KageError(f"unknown synth preset '{preset}'")


def asset_audio(asset_id: str, asset: dict, out_dir: Path, duration: float | None = None) -> tuple[Path, dict]:
    """Render (or reuse) an audio asset; returns its path and provenance record."""
    src = asset["source"]
    if src["type"] == "file":
        path = Path(src["path"])
        if not path.exists():
            raise KageError(f"{asset_id}: source file {path} is missing")
        return path, {"asset": asset_id, "type": "file", "license": asset.get("license", "UNSPECIFIED")}
    if src["type"] != "synth":
        raise KageError(f"{asset_id}: unsupported audio source '{src['type']}'")
    preset = src["preset"]
    needs_duration = preset in ("train_interior", "train_exterior", "lullaby")
    d = round(duration, 3) if needs_duration else src.get("duration_s")
    key = sha256_json({"preset": preset, "duration": d, "params": src})[:16]
    path = out_dir / f"{asset_id}_{key}.wav"
    if not path.exists():
        log(f"synthesizing {asset_id} ({preset}{f', {d:.2f}s' if d else ''})")
        write_wav(path, synth(preset, d, params=src))
    return path, {"asset": asset_id, "type": "synth", "preset": preset, "license": asset.get("license"), "file": path.name}


# ---------------------------------------------------------------- mixing

def _db(x: float) -> float:
    return 10 ** (x / 20.0)


def build_stem(clips: list[tuple[Path, float, dict]], total: float, out: Path) -> None:
    """clips: (file, start_s, opts{gain_db, fade_in_s, fade_out_s, duration_s})."""
    ffmpeg = require_tool("ffmpeg")
    if not clips:
        run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{total:.6f}", "-c:a", "pcm_s16le", str(out)])
        return
    args = [ffmpeg, "-v", "error", "-y"]
    chains = []
    for i, (path, start, opts) in enumerate(clips):
        args += ["-i", str(path)]
        f = [f"aresample={SR}", "aformat=channel_layouts=mono"]
        if opts.get("duration_s"):
            f.append(f"atrim=0:{opts['duration_s']:.6f}")
        if opts.get("fade_in_s"):
            f.append(f"afade=t=in:st=0:d={opts['fade_in_s']}")
        if opts.get("fade_out_s") and opts.get("duration_s"):
            f.append(f"afade=t=out:st={max(0.0, opts['duration_s'] - opts['fade_out_s']):.6f}:d={opts['fade_out_s']}")
        f.append(f"volume={opts.get('gain_db', 0)}dB")
        delay = max(0, int(round(start * 1000)))
        f.append(f"adelay={delay}:all=1")
        chains.append(f"[{i}:a]{','.join(f)}[c{i}]")
    mix = "".join(f"[c{i}]" for i in range(len(clips)))
    graph = ";".join(chains) + f";{mix}amix=inputs={len(clips)}:normalize=0:duration=longest,apad=whole_dur={total:.6f},atrim=0:{total:.6f}[out]"
    graph_file = out.with_suffix(".graph.txt")
    graph_file.write_text(graph)
    # -t is a hard stop: the stem always ends exactly at the timeline duration.
    run(args + ffmpeg_filter_script_args(graph_file) + ["-map", "[out]", "-t", f"{total:.6f}", "-c:a", "pcm_s16le", "-ar", str(SR), str(out)],
        timeout=300)


def loudness(path: Path) -> dict:
    """EBU R128 integrated loudness (LUFS), true peak (dBTP) and LRA via loudnorm analysis."""
    ffmpeg = require_tool("ffmpeg")
    cp = run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"], timeout=600)
    text = cp.stderr.decode("utf-8", "replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not m:
        raise KageError(f"could not measure loudness of {path}")
    d = json.loads(m.group(0))
    return {"integrated_lufs": float(d["input_i"]), "true_peak_dbtp": float(d["input_tp"]), "lra_lu": float(d["input_lra"]),
            "threshold": float(d["input_thresh"]), "offset": float(d["target_offset"])}


def master_mix(stems: dict[str, Path], music_duck: dict | None, out: Path, target_lufs: float, target_tp: float,
               total: float) -> dict:
    """Combine stems (music ducked under dialogue), normalise loudness (two-pass), stereo 48 kHz."""
    ffmpeg = require_tool("ffmpeg")
    pre = out.with_name(out.stem + "_premaster.wav")
    order = ["dialogue", "sfx", "ambience", "music"]
    args = [ffmpeg, "-v", "error", "-y"]
    for name in order:
        args += ["-i", str(stems[name])]
    duck = music_duck or {}
    ratio = max(1.5, 1 + duck.get("db", 6))
    ducked = out.with_name("music_ducked.wav")
    graph = ("[0:a]asplit=2[dlg][key];"
             f"[3:a][key]sidechaincompress=threshold=0.008:ratio={ratio:.2f}:attack=30:release=450:makeup=1,asplit=2[mus][musq];"
             "[dlg][1:a][2:a][mus]amix=inputs=4:normalize=0:duration=longest,"
             f"atrim=0:{total:.6f},pan=stereo|c0=c0|c1=c0[out]")
    # The ducked music is also written on its own so QA can measure the ducking.
    run(args + ["-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s24le", "-ar", str(SR), str(pre),
                "-map", "[musq]", "-c:a", "pcm_s16le", "-ar", str(SR), str(ducked)], timeout=600)
    m = loudness(pre)
    # Gain to target, then a transparent peak limiter so linear normalisation can
    # reach the target without breaking the true-peak ceiling.
    gain = target_lufs - m["integrated_lufs"]
    limited = out.with_name(out.stem + "_limited.wav")
    ceiling = 10 ** ((target_tp - 1.0) / 20)
    run([ffmpeg, "-v", "error", "-y", "-i", str(pre), "-af",
         f"volume={gain:.3f}dB,alimiter=limit={ceiling:.5f}:attack=3:release=60:level=false,atrim=0:{total:.6f}",
         "-c:a", "pcm_s24le", "-ar", str(SR), str(limited)], timeout=600)
    m2 = loudness(limited)
    run([ffmpeg, "-v", "error", "-y", "-i", str(limited), "-af",
         f"loudnorm=I={target_lufs}:TP={target_tp}:LRA=11:measured_I={m2['integrated_lufs']}:measured_TP={m2['true_peak_dbtp']}:"
         f"measured_LRA={m2['lra_lu']}:measured_thresh={m2['threshold']}:offset={m2['offset']}:linear=true,"
         f"aresample={SR},atrim=0:{total:.6f}",
         "-c:a", "pcm_s24le", "-ar", str(SR), str(out)], timeout=600)
    final = loudness(out)
    return {"premaster": m, "limited": m2, "applied_gain_db": round(gain, 2), "master": final, "ducking_ratio": round(ratio, 2)}


def write_srt(subtitles: list[dict], path: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    lines = []
    for i, s in enumerate(subtitles, 1):
        lines += [str(i), f"{ts(s['start_s'])} --> {ts(s['end_s'])}", s["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def rms_window(path: Path, start: float, end: float) -> float:
    samples, sr = read_wav_mono(path)
    seg = samples[int(start * sr):int(end * sr)]
    return math.sqrt(sum(s * s for s in seg) / max(1, len(seg)))
