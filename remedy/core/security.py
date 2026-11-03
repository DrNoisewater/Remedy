"""Security Center helpers: startup-persistence review with honest, lead-only heuristics.

Remedy is not an antivirus. Only Microsoft Defender (or the registered antivirus) gives verdicts; Remedy shows
those as "Confirmed by provider". Everything Remedy notices itself is a *lead* for a human to review:
unsigned does not mean malware, and an odd location does not mean malware. Nothing is quarantined, deleted or
killed automatically, and no file or hash ever leaves the PC.
"""
from __future__ import annotations

import hashlib
import os
import re

from .utils import IS_WINDOWS, as_list, powershell_json, ps_quote

USER_WRITABLE = re.compile(r"\\(appdata|temp|tmp|downloads|users\\public)\\", re.IGNORECASE)
TRUSTED_ROOTS = re.compile(r"^[a-z]:\\(windows|program files|program files \(x86\))\\", re.IGNORECASE)

LEVELS = {"provider": "Confirmed by provider", "review": "Needs review", "broken": "Broken entry",
          "none": "No concerns noticed"}


def assess_entry(path: str, sig_status: str, signer: str = "") -> tuple:
    """(level, explanation) for one startup entry. level in LEVELS. Never returns a malware verdict."""
    p = (path or "").strip()
    st = (sig_status or "").lower()
    if not p:
        return "review", "No program path could be read from this entry; look at the command it runs."
    if st == "missing":
        return "broken", "The file this entry starts doesn't exist. Usually left behind by an uninstalled app; " \
                         "safe to disable."
    signed = st == "valid"
    odd_place = bool(USER_WRITABLE.search(p + "\\")) and not TRUSTED_ROOTS.match(p)
    if st in ("hashmismatch", "nottrusted"):
        return "review", f"The file's signature is {sig_status}: it was changed after signing, or the signer isn't " \
                         "trusted. Worth scanning with Defender."
    if not signed and odd_place:
        return "review", "Unsigned and starts from a user folder (AppData/Temp/Downloads). Many legitimate apps do " \
                         "this too, so this is a lead to check, not a verdict. Scan it with Defender if unsure."
    if not signed:
        return "none", "Not digitally signed, but it runs from a normal program location. Common for small tools."
    return "none", f"Signed by {signer.split(',')[0].replace('CN=', '') if signer else 'a trusted publisher'}."


def signatures(paths: list) -> dict:
    """{path: (status, signer)} via Get-AuthenticodeSignature (Windows only; read-only)."""
    paths = [p for p in dict.fromkeys(paths) if p]
    if not IS_WINDOWS or not paths:
        return {}
    arr = ",".join(ps_quote(p) for p in paths[:200])
    script = (f"@({arr}) | ForEach-Object {{ $p=$_; if (Test-Path -LiteralPath $p -PathType Leaf) {{ "
              "$s = Get-AuthenticodeSignature -LiteralPath $p; [pscustomobject]@{Path=$p; Status=[string]$s.Status; "
              "Signer=$(if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' })} } else { "
              "[pscustomobject]@{Path=$p; Status='Missing'; Signer=''} } } | ConvertTo-Json -Compress")
    out = {}
    for row in as_list(powershell_json(script, timeout=180)):
        if isinstance(row, dict) and row.get("Path"):
            out[row["Path"]] = (str(row.get("Status") or ""), str(row.get("Signer") or ""))
    return out


def sha256(path: str, limit: int = 2 * 1024 ** 3) -> str:
    """Local hash for the technician's notes. Never sent anywhere."""
    if os.path.getsize(path) > limit:
        raise ValueError("File is larger than 2 GB")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def defender_scan_args(path: str) -> list:
    """Custom scan of one file with Microsoft Defender's command-line tool. Defender applies its own policy if it
    finds something (that's the supported remediation path)."""
    exe = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Windows Defender", "MpCmdRun.exe")
    return [exe, "-Scan", "-ScanType", "3", "-File", path]
