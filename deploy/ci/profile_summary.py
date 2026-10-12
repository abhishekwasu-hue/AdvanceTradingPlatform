"""LD-7 diagnostics: summarise a py-spy raw profile (`stack;frames count` lines) of the API processes: the share of
samples in each frame (inclusive, counted once per stack) for the application and the libraries that matter, and the
frames that spend the most time themselves. Stdlib only; prints to stdout."""
import collections
import re
import sys

KEEP = re.compile(r"\(app/|sqlalchemy/(orm|engine|pool)|asyncpg|starlette|fastapi|pydantic|uvicorn/protocols|bcrypt|json|httpx|"
                  r"pandas|logging|anyio|jwt|prometheus")


def frame_key(frame: str) -> str:
    name, _, where = frame.partition(" (")
    return f"{name} ({where.split(':')[0]}" + ")" if where else name


def main(path: str) -> None:
    inclusive: collections.Counter = collections.Counter()
    own: collections.Counter = collections.Counter()
    total = 0
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip()
        if not line or " " not in line:
            continue
        stack, count_text = line.rsplit(" ", 1)
        if not count_text.isdigit():
            continue
        count = int(count_text)
        total += count
        frames = [frame_key(f) for f in stack.split(";")]
        for key in set(frames):
            inclusive[key] += count
        own[frames[-1]] += count
    if not total:
        print("no samples")
        return
    print(f"samples: {total}")
    print("== inclusive (application and key libraries)")
    shown = 0
    for key, count in inclusive.most_common():
        if KEEP.search(key):
            print(f"{100 * count / total:6.1f}%  {key}")
            shown += 1
            if shown >= 80:
                break
    print("== self (top 40)")
    for key, count in own.most_common(40):
        print(f"{100 * count / total:6.1f}%  {key}")


if __name__ == "__main__":
    main(sys.argv[1])
