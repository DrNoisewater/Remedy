"""Writes version_info.txt (Windows file details for Remedy.exe) from APP_VERSION in remedy/core/utils.py.

Task Manager and Explorer show these details, so the process is listed as "Remedy" rather than "Remedy.exe".
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
src = (ROOT / "remedy" / "core" / "utils.py").read_text(encoding="utf-8")
version = re.search(r'APP_VERSION\s*=\s*"([\d.]+)"', src).group(1)
publisher = re.search(r'APP_PUBLISHER\s*=\s*"([^"]+)"', src).group(1)
parts = [int(x) for x in version.split(".")] + [0, 0, 0]
tup = tuple(parts[:4])

TEMPLATE = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tup}, prodvers={tup}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{publisher}'),
      StringStruct('FileDescription', 'Remedy'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Remedy'),
      StringStruct('LegalCopyright', '\\u00a9 2026 {publisher}'),
      StringStruct('OriginalFilename', 'Remedy.exe'),
      StringStruct('ProductName', 'Remedy'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
(ROOT / "version_info.txt").write_text(TEMPLATE, encoding="utf-8")
print(f"version_info.txt written for Remedy {version}")
