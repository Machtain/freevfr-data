#!/usr/bin/env python3
"""Met à jour vac_georef.xml et la ligne <Georef> de data.xml pour un cycle AIRAC.

Usage :
    python update_georef.py [cycle] [--dry-run]

  cycle : cycle AIRAC au format yynn (ex. 2611). S'il est omis, on prend le
          cycle en vigueur à la date du jour.

Étapes :
  1. Lit airacs/aixm_<cycle>.xml et récupère l'attribut "effective" de <AIXM-Snapshot>
  2. En déduit le dossier eAIP (ex. eAIP_29_OCT_2026)
  3. Remplace tous les eAIP_DD_MMM_YYYY des attributs vac des <AD> de vac_georef.xml
  4. Met à jour cycle et crc32 (CRC32 du nouveau vac_georef.xml) dans <Georef> de data.xml
"""

import argparse
import datetime as dt
import os
import re
import sys
import zlib

from add_airac import AIRAC_PERIOD, AIRAC_REF, AIRACS_DIR, DATA_XML, ROOT, airac_cycle

GEOREF_XML = os.path.join(ROOT, "vac_georef.xml")
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

SNAPSHOT_EFFECTIVE_RE = re.compile(rb'<AIXM-Snapshot\b[^>]*\beffective="(\d{4}-\d{2}-\d{2})')
AD_VAC_RE = re.compile(r'(<AD\b[^>]*\bvac=")([^"]*)(")')
EAIP_RE = re.compile(r"eAIP_\d{2}_[A-Z]{3}_\d{4}")
GEOREF_RE = re.compile(r"<Georef\b[^>]*/>")


def current_cycle(today: dt.date) -> str:
    """Cycle AIRAC en vigueur à une date donnée."""
    n = (today - AIRAC_REF).days // AIRAC_PERIOD
    return airac_cycle(AIRAC_REF + dt.timedelta(days=n * AIRAC_PERIOD))


def read_effective(path: str) -> dt.date:
    with open(path, "rb") as f:
        m = SNAPSHOT_EFFECTIVE_RE.search(f.read(8192))
    if not m:
        sys.exit(f"Erreur : attribut effective de <AIXM-Snapshot> introuvable dans {path}.")
    return dt.date.fromisoformat(m.group(1).decode())


def eaip_name(date: dt.date) -> str:
    return f"eAIP_{date.day:02d}_{MONTHS[date.month - 1]}_{date.year}"


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path: str, text: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    p = argparse.ArgumentParser(description="Met à jour vac_georef.xml pour un cycle AIRAC.")
    p.add_argument("cycle", nargs="?", help="cycle AIRAC yynn (défaut : cycle en vigueur aujourd'hui)")
    p.add_argument("--dry-run", action="store_true", help="afficher sans rien écrire")
    args = p.parse_args()

    if args.cycle:
        if not re.fullmatch(r"\d{4}", args.cycle):
            sys.exit("Erreur : le cycle doit être au format yynn (ex. 2611).")
        cycle = args.cycle
    else:
        cycle = current_cycle(dt.date.today())
        print(f"Aucun cycle fourni, cycle en vigueur aujourd'hui : {cycle}")

    aixm = os.path.join(AIRACS_DIR, f"aixm_{cycle}.xml")
    if not os.path.exists(aixm):
        sys.exit(f"Erreur : {aixm} introuvable (lancer d'abord add_airac.py ?).")
    effective = read_effective(aixm)
    new_eaip = eaip_name(effective)
    print(f"Cycle {cycle} : effective {effective} → {new_eaip}")

    # --- vac_georef.xml ---
    georef = read_text(GEOREF_XML)
    old_names = {}
    count = 0

    def repl(m):
        nonlocal count
        url, n = EAIP_RE.subn(new_eaip, m.group(2))
        for old in EAIP_RE.findall(m.group(2)):
            old_names[old] = old_names.get(old, 0) + 1
        count += n
        return m.group(1) + url + m.group(3)

    georef = AD_VAC_RE.sub(repl, georef)
    if count == 0:
        sys.exit("Erreur : aucun eAIP_DD_MMM_YYYY trouvé dans les vac des <AD>.")
    print("vac_georef.xml : " + ", ".join(f"{n} × {o}" for o, n in sorted(old_names.items()))
          + f" → {new_eaip}")

    crc = f"{zlib.crc32(georef.encode('utf-8')) & 0xFFFFFFFF:08X}"

    # --- data.xml ---
    data = read_text(DATA_XML)
    m = GEOREF_RE.search(data)
    if not m:
        sys.exit("Erreur : ligne <Georef .../> introuvable dans data.xml.")
    line = m.group(0)
    for attr, value in (("cycle", cycle), ("crc32", crc)):
        if not re.search(rf'\b{attr}="[^"]*"', line):
            sys.exit(f"Erreur : attribut {attr} absent de la ligne <Georef>.")
        line = re.sub(rf'\b{attr}="[^"]*"', f'{attr}="{value}"', line)
    data = data[:m.start()] + line + data[m.end():]
    print(f"data.xml : {m.group(0)}\n        → {line}")

    if args.dry_run:
        print("[dry-run] rien n'a été écrit.")
        return
    write_text(GEOREF_XML, georef)
    write_text(DATA_XML, data)
    print("Terminé.")


if __name__ == "__main__":
    main()
