"""Download the Blender open movie Tears of Steel and its subtitles, build test videos with an embedded English track, and write a corpus file.

Every translation is of the same film, so the corpus has no "another episode" case: a translation of the same film is not a wrong match.
The "random times" case covers rejection.

usage: tears_of_steel.py FOLDER   then: torture.py FOLDER/work FOLDER/corpus.json
"""
import json
import os
import subprocess
import sys
import urllib.request

SOURCE = "https://download.blender.org/demo/movies/ToS"
TRANSLATIONS = (("fr-orig", "mkv"), ("fr-Goofy", "mp4"), ("fr-OMenor", "mkv"), ("JP", "mkv"), ("de", "mp4"), ("ru", "mkv"))

folder = os.path.abspath(sys.argv[1])
os.makedirs(folder, exist_ok=True)
for name in ["tears_of_steel_720p.mov"] + [f"subtitles/TOS-{language}.srt" for language in {"en", "fr-orig", "fr-Goofy", "fr-OMenor", "JP", "de", "ru"}]:
    target = os.path.join(folder, os.path.basename(name))
    if not os.path.exists(target):
        print("downloading", name, flush=True)
        urllib.request.urlretrieve(f"{SOURCE}/{name}", target)
for container, codec in (("mkv", "srt"), ("mp4", "mov_text")):
    video = os.path.join(folder, f"tears-of-steel.{container}")
    if not os.path.exists(video):
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", os.path.join(folder, "tears_of_steel_720p.mov"),
                        "-i", os.path.join(folder, "TOS-en.srt"), "-map", "0:v", "-map", "0:a", "-map", "1", "-c", "copy", "-c:s", codec,
                        "-metadata:s:s:0", "language=eng", video], check=True)
corpus = [{"name": f"TOS-{name}", "video": os.path.join(folder, f"tears-of-steel.{container}"), "truth": os.path.join(folder, f"TOS-{name}.srt")}
          for name, container in TRANSLATIONS]
with open(os.path.join(folder, "corpus.json"), "w") as handle:
    json.dump(corpus, handle, indent=1)
print(f"corpus written to {folder}/corpus.json ({len(corpus)} tracks)")
