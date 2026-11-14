"""Render web/public/game/audio/lines.json to mp3s with ElevenLabs (skips files that exist).

uv run scripts/gen_game_audio.py --dry-run   # list what would be generated + char count
uv run scripts/gen_game_audio.py [ids...]    # generate (all missing, or only the given ids; given ids are regenerated)
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

AUDIO = Path(__file__).resolve().parent.parent / "web" / "public" / "game" / "audio"


def jobs(spec: dict) -> list[tuple[str, str, dict]]:
    out = [(line["id"], "tts", line) for line in spec["lines"]]
    out += [(fx["id"], "sfx", fx) for fx in spec["sfx"]]
    out += [(track["id"], "music", track) for track in spec["music"]]
    return out


def render(client, spec: dict, kind: str, item: dict):
    if kind == "tts":
        fx = spec["fx"].get(item.get("fx", ""))
        return client.text_to_speech.convert(
            text=item["text"], voice_id=spec["voices"][item["speaker"]], model_id="eleven_v4",
            output_format="mp3_44100_128", **({"audio_effects": fx} if fx else {}))
    if kind == "sfx":
        return client.text_to_sound_effects.convert(
            text=item["prompt"], model_id="eleven_text_to_sound_v3",
            duration_seconds=item.get("duration"), loop=item.get("loop", False))
    return client.music.compose(prompt=item["prompt"], music_length_ms=item["ms"], model_id="music_v2_5")


def main(argv: list[str]) -> None:
    dry = "--dry-run" in argv
    only = {a for a in argv if not a.startswith("--")}
    spec = json.loads((AUDIO / "lines.json").read_text())
    all_jobs = jobs(spec)
    if unknown := only - {j[0] for j in all_jobs}:
        sys.exit(f"unknown ids: {sorted(unknown)}")
    todo = [j for j in all_jobs if j[0] in only] if only else \
        [j for j in all_jobs if not (AUDIO / f"{j[0]}.mp3").exists()]
    chars = sum(len(i["text"]) for _, k, i in todo if k == "tts")
    print(f"{len(todo)} files: {sum(k == 'tts' for _, k, _ in todo)} tts ({chars} chars), "
          f"{sum(k == 'sfx' for _, k, _ in todo)} sfx, {sum(k == 'music' for _, k, _ in todo)} music")
    if dry:
        for jid, kind, _ in todo:
            print(f"  {kind:5} {jid}")
        return
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    from elevenlabs import ElevenLabs
    client = ElevenLabs()
    failed = []
    for jid, kind, item in todo:
        try:
            data = b"".join(render(client, spec, kind, item))
            if len(data) < 1024:  # an error body or empty stream must not become a "done" file
                raise ValueError(f"suspiciously small response ({len(data)} bytes)")
            tmp = AUDIO / f"{jid}.mp3.tmp"
            try:
                tmp.write_bytes(data)
                tmp.replace(AUDIO / f"{jid}.mp3")
            finally:
                tmp.unlink(missing_ok=True)  # atomic: a crash never leaves a truncated mp3 that later runs skip
            print(f"  ok   {jid} ({len(data) // 1024} KB)")
        except Exception as e:  # keep going; rerun regenerates only the missing ones
            failed.append(jid)
            print(f"  FAIL {jid}: {e}")
    if failed:
        sys.exit(f"{len(failed)} failed: {failed}")


if __name__ == "__main__":
    main(sys.argv[1:])
