"""
scripts/secret_scan.py - lightweight secret scanner with no external dependencies.
- Scans code and docs for secret-like patterns while masking values.
- Verifies local environment files are ignored and an example file exists.
- Exits with code 1 when a secret-like value is found.
Run: python scripts/secret_scan.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", "artifacts", "archive", ".venv", "venv", ".pytest_cache", "node_modules"}
# Skip local environment files as content, but verify they are ignored.
SKIP_FILES = {".env"}
SCAN_EXT = {".py", ".yaml", ".yml", ".md", ".json", ".txt", ".js", ".html", ".ini", ".cfg", ".toml", ".env.example"}

PATTERNS = [
    ("password assignment", re.compile(r"(password|passwd|pwd)\s*[:=]\s*['\"]?([^\s'\"]{4,})", re.I)),
    ("generic secret/api key", re.compile(r"(api[_-]?key|secret|token|access[_-]?key)\s*[:=]\s*['\"]?([A-Za-z0-9/\+=_\-]{12,})", re.I)),
    ("postgres URL with creds", re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:\s]+:([^@\s]+)@", re.I)),
    ("AWS access key", re.compile(r"(AKIA[0-9A-Z]{16})")),
    ("OpenAI-style key", re.compile(r"(sk-[A-Za-z0-9]{20,})")),
]
# Placeholder values are allowed.
ALLOW = re.compile(r"your[_-]|example|placeholder|__set_in|changeme|xxxx|<.*>|env\.|os\.environ|getenv|\$\{", re.I)


def mask(v):
    v = str(v)
    return v[:2] + "***" + v[-1:] if len(v) > 4 else "***"


def scan_file(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                if "secret_scan" in line or "PATTERNS" in line:
                    continue
                for name, rx in PATTERNS:
                    m = rx.search(line)
                    if m:
                        val = m.group(m.lastindex)
                        # Ignore ordinary identifier/function assignments such as
                        # ``token = _require_token(...)``. Secret findings must be
                        # literal-looking values, not variable names.
                        value_prefix = line[:m.start(m.lastindex)].rstrip()
                        quoted_literal = value_prefix.endswith(("'", '"'))
                        if not quoted_literal and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", val):
                            continue
                        if ALLOW.search(line):
                            continue
                        out.append((i, name, mask(val)))
    except Exception:
        pass
    return out


findings = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in SKIP_DIRS]
    for f in fn:
        if f in SKIP_FILES:
            continue
        ext = os.path.splitext(f)[1]
        if ext not in SCAN_EXT and f != ".env.example":
            continue
        p = os.path.join(dp, f)
        for (ln, name, masked) in scan_file(p):
            rel = os.path.relpath(p, ROOT)
            findings.append((rel, ln, name, masked))

# Hygiene checks.
gi = os.path.join(ROOT, ".gitignore")
env_ignored = os.path.exists(gi) and ".env" in open(gi, encoding="utf-8").read()
env_example = os.path.exists(os.path.join(ROOT, ".env.example"))
env_present = os.path.exists(os.path.join(ROOT, ".env"))

print("=== SECRET SCAN ===")
print(f".gitignore covers .env : {'YES' if env_ignored else 'NO (!)'}")
print(f".env.example present    : {'YES' if env_example else 'NO (!)'}")
print(f".env present (local)    : {'YES (ensure gitignored, never commit)' if env_present else 'no'}")
if findings:
    print(f"\n[!] potential secrets in scanned files ({len(findings)}):")
    for rel, ln, name, masked in findings:
        print(f"    {rel}:{ln}  [{name}]  value={masked}")
else:
    print("\n[OK] no hardcoded secrets found in scanned code/docs.")

problems = bool(findings) or (not env_ignored) or (not env_example)
print("\nRESULT:", "FAIL" if problems else "PASS")
sys.exit(1 if problems else 0)  # Also fails when ignore/example hygiene is missing.
