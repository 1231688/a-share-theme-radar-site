from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUTPUT = ROOT / "site-bundle.zip"
ALLOWED_SUFFIXES = {
    ".html", ".css", ".js", ".json", ".svg", ".png", ".jpg", ".jpeg",
    ".webp", ".woff", ".woff2", ".ico", ".txt",
}
EXCLUDED_NAMES = {".env", ".npmrc", ".git", "node_modules", "__pycache__"}

if not (DIST / "index.html").is_file():
    raise SystemExit("dist/index.html 不存在。")

with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED) as archive:
    for path in sorted(DIST.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"不允许符号链接：{path}")
        if not path.is_file():
            continue
        relative = path.relative_to(DIST)
        if any(part in EXCLUDED_NAMES or part.startswith(".env") for part in relative.parts):
            raise RuntimeError(f"Bundle 含开发或敏感文件：{relative}")
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise RuntimeError(f"Bundle 含非生产文件：{relative}")
        archive.write(path, relative.as_posix())

with ZipFile(OUTPUT) as archive:
    names = archive.namelist()
    assert "index.html" in names
    assert all("\\" not in name for name in names)
    assert OUTPUT.stat().st_size <= 64 * 1024 * 1024
    print(f"archive bytes: {OUTPUT.stat().st_size}")
    print(f"entries: {len(names)}")
