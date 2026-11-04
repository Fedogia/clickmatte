# -*- coding: utf-8 -*-
"""Lettura/scrittura video con ffmpeg: informazioni, decodifica, ProRes, anteprima per l'interfaccia."""

import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile

from .eventi import ErroreMotore


MATRICE = {"bt709": "bt709", "bt2020nc": "bt2020", "bt2020c": "bt2020", "smpte170m": "smpte170m",
           "bt470bg": "bt470", "smpte240m": "smpte240m", "fcc": "fcc"}


def controlla_ffmpeg():
    for exe in ("ffmpeg", "ffprobe"):
        if not shutil.which(exe):
            raise ErroreMotore(f"Manca {exe}. Installa con:  brew install ffmpeg")


def info_video(path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=width,height,r_frame_rate,avg_frame_rate,pix_fmt,bits_per_raw_sample,color_space,"
           "color_transfer,color_primaries,color_range:stream_side_data=rotation:stream_tags=rotate"
           ":format=duration", "-of", "json", path]
    d = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    s = d["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    rot = 0
    for sd in s.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if "tags" in s and "rotate" in s["tags"]:
        rot = int(float(s["tags"]["rotate"]))
    if abs(rot) % 180 == 90:
        w, h = h, w
    fps_txt = s.get("r_frame_rate") or s.get("avg_frame_rate") or "25/1"
    num, den = fps_txt.split("/")
    fps = float(num) / float(den or 1)
    dur = float(d.get("format", {}).get("duration") or s.get("duration") or 0)

    pf = s.get("pix_fmt", "")
    try:
        bit = int(s.get("bits_per_raw_sample") or 0)
    except ValueError:
        bit = 0
    if not bit:
        bit = 10 if any(x in pf for x in ("10", "12", "16", "p010", "p016")) else 8

    cs = s.get("color_space") or "bt709"
    if cs not in MATRICE:
        cs = "bt709"
    cp = s.get("color_primaries") or ""
    if cp in ("", "unknown", "unspecified", "reserved"):
        cp = "bt2020" if cs.startswith("bt2020") else "bt709"
    ct = s.get("color_transfer") or ""
    if ct in ("", "unknown", "unspecified", "reserved"):
        ct = "bt709"
    rng = "pc" if s.get("color_range") in ("pc", "jpeg", "full") else "tv"
    colore = {"m": MATRICE[cs], "cs": cs, "cp": cp, "ct": ct, "range": rng}
    return w, h, fps_txt, fps, dur, bit, colore


def lettore_frame(path, f0, n, fps_txt, w, h, colore, bit16=False, scala=False):
    """Decodifica esattamente i frame f0 .. f0+n-1 (n = 0: fino alla fine) in RGB grezzo, 8 o 16 bit.

    "Frame k" e' sempre lo stesso, da qualunque punto si cominci a leggere: la griglia a frame rate
    costante e' ancorata ai tempi originali del file (-copyts + filtro fps), non al punto di partenza.
    Serve per i video dei telefoni, i cui tempi non sono perfettamente regolari: senza questo, leggere
    da meta' poteva duplicare o saltare un frame rispetto alla lettura dall'inizio.
    """
    num, den = fps_txt.split("/")
    fps = float(num) / float(den or 1)
    cmd = ["ffmpeg", "-v", "error", "-noaccurate_seek"]
    if f0 > 3:
        cmd += ["-ss", f"{(f0 - 3) / fps:.6f}"]
    s0 = inizio_stream(path)
    trim = f"trim=start={s0 + (f0 - 0.5) / fps:.6f}"
    if n > 0:
        trim += f":end={s0 + (f0 + n - 0.5) / fps:.6f}"
    dim = f"{w}:{h}:" if scala else ""
    cmd += ["-copyts", "-i", path, "-an", "-sn", "-dn", "-vf",
            f"fps=fps={fps_txt}:round=near,{trim},scale={dim}in_color_matrix={colore['m']}:in_range={colore['range']}",
            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb48le" if bit16 else "rgb24", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * (6 if bit16 else 3) * 2)


@functools.lru_cache(maxsize=64)
def inizio_stream(path):
    """Tempo (s) del primo frame nel file: la griglia dei frame parte da qui."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time",
                        "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    try:
        return float(r.splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def lettore(path, inizio, durata, w, h, colore, bit16=False, scala=False):
    """(vecchio modo, per tempi in secondi) Decodifica il tratto in RGB grezzo con la matrice colore giusta."""
    cmd = ["ffmpeg", "-v", "error"]
    if inizio > 0:
        cmd += ["-ss", f"{inizio:.4f}"]
    cmd += ["-i", path]
    if durata > 0:
        cmd += ["-t", f"{durata:.4f}"]
    dim = f"{w}:{h}:" if scala else ""
    cmd += ["-an", "-vf", f"scale={dim}in_color_matrix={colore['m']}:in_range={colore['range']}",
            "-f", "rawvideo", "-pix_fmt", "rgb48le" if bit16 else "rgb24", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * (6 if bit16 else 3) * 2)


def prova_prores_hw():
    """Controlla se l'encoder ProRes hardware (VideoToolbox) funziona, anche con l'alfa."""
    enc = {}
    if sys.platform != "darwin":
        return enc
    lista = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "prores_videotoolbox" not in lista:
        return enc
    tmp = tempfile.mkdtemp(prefix="scontorno_hw_")
    try:
        for kind, prof, src in (("base", "hq", "color=c=red:s=256x144:d=0.2"),
                                ("alpha", "4444", "color=c=red@0.5:s=256x144:d=0.2,format=rgba")):
            out = os.path.join(tmp, f"{kind}.mov")
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-frames:v", "3",
                                "-c:v", "prores_videotoolbox", "-profile:v", prof, out], capture_output=True)
            if r.returncode != 0 or not os.path.isfile(out):
                continue
            pf = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=pix_fmt", "-of", "csv=p=0", out], capture_output=True, text=True).stdout
            if kind == "alpha" and "a" not in pf.replace("yuv", ""):
                continue  # niente canale alfa: resta su prores_ks
            enc[kind] = ["-c:v", "prores_videotoolbox", "-profile:v", prof]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return enc


def scrittore(out, w, h, fps_txt, kind, colore, bit16=False, hw=None):
    hw = hw or {}
    if kind == "alpha":
        pix_in = "rgba64le" if bit16 else "rgba"
        enc = hw.get("alpha") or ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                                  "-alpha_bits", "16", "-vendor", "apl0"]
    elif kind == "base":
        pix_in = "rgb48le" if bit16 else "rgb24"
        enc = hw.get("base") or ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
                                 "-vendor", "apl0"]
    else:  # anteprima (gia' ridotta a 1080p)
        pix_in = "rgb24"
        enc = ["-c:v", "libx264", "-crf", "20", "-g", "1", "-pix_fmt", "yuv420p"]   # tutto chiave: salto preciso
    if kind == "prev":
        vf = "scale=out_color_matrix=bt709:out_range=tv"
        tag = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    else:
        vf = f"scale=out_color_matrix={colore['m']}:out_range=tv"
        tag = ["-colorspace", colore["cs"], "-color_primaries", colore["cp"], "-color_trc", colore["ct"],
               "-color_range", "tv"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix_in, "-s", f"{w}x{h}",
           "-r", fps_txt, "-i", "-", "-vf", vf] + enc + tag + [out]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)
