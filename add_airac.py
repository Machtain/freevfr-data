#!/usr/bin/env python3
"""Ajoute un cycle AIRAC à FreeVFR-data à partir d'un export SIA.

Usage :
    python add_airac.py export_xml_bd_sia_XXXX.zip [--cycle 2612] [--force] [--dry-run]

Étapes :
  1. Trouve dans le zip le fichier AIXM4.5_all_FR_OM_yyyy-mm-dd.xml
  2. Déduit le cycle AIRAC (yynn, nn = numéro du cycle dans l'année, 01..13)
     depuis la date d'entrée en vigueur (cycles de 28 jours)
  3. L'extrait dans airacs/aixm_yynn.xml en calculant son CRC32
  4. Insère la ligne <Airac .../> dans data.xml (ordre décroissant des cycles)
"""

import argparse
import datetime as dt
import os
import re
import sys
import zipfile
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_XML = os.path.join(ROOT, "data.xml")
AIRACS_DIR = os.path.join(ROOT, "airacs")
DEFAULT_URL_BASE = "https://raw.githubusercontent.com/Machtain/freevfr-data/refs/heads/main/airacs/"

# Date de référence : AIRAC 2601 (22/01/2026). Tous les cycles sont espacés de 28 jours.
AIRAC_REF = dt.date(2026, 1, 22)
AIRAC_PERIOD = 28

MEMBER_RE = re.compile(r"(?:^|/)AIXM4\.5_all_FR_OM_(\d{4}-\d{2}-\d{2})\.xml$", re.IGNORECASE)
EFFECTIVE_RE = re.compile(rb'effective="(\d{4}-\d{2}-\d{2})')
AIRAC_LINE_RE = re.compile(r'^([ \t]*)<Airac\b[^>]*\bcycle="(\d{4})"[^>]*/>[ \t]*\r?\n?', re.MULTILINE)


def airac_cycle(date: dt.date) -> str:
    """Retourne le cycle 'yynn' d'une date d'entrée en vigueur AIRAC."""
    delta = (date - AIRAC_REF).days
    if delta % AIRAC_PERIOD != 0:
        raise ValueError(f"{date} n'est pas une date d'entrée en vigueur AIRAC (pas un multiple de 28 jours depuis {AIRAC_REF})")
    # Premier cycle de l'année = première date AIRAC >= 1er janvier
    jan1 = dt.date(date.year, 1, 1)
    k = -((AIRAC_REF - jan1).days // AIRAC_PERIOD)  # ceil((jan1 - ref) / 28)
    first = AIRAC_REF + dt.timedelta(days=k * AIRAC_PERIOD)
    number = (date - first).days // AIRAC_PERIOD + 1
    return f"{date.year % 100:02d}{number:02d}"


def find_member(zf: zipfile.ZipFile):
    matches = [(info, MEMBER_RE.search(info.filename)) for info in zf.infolist()]
    matches = [(info, m) for info, m in matches if m]
    if not matches:
        sys.exit("Erreur : aucun fichier AIXM4.5_all_FR_OM_yyyy-mm-dd.xml dans le zip.")
    if len(matches) > 1:
        sys.exit("Erreur : plusieurs fichiers AIXM trouvés : " + ", ".join(i.filename for i, _ in matches))
    info, m = matches[0]
    return info, dt.date.fromisoformat(m.group(1))


def read_effective(zf: zipfile.ZipFile, info):
    with zf.open(info) as f:
        m = EFFECTIVE_RE.search(f.read(4096))
    return dt.date.fromisoformat(m.group(1).decode()) if m else None


def extract_with_crc(zf: zipfile.ZipFile, info, dest: str) -> str:
    crc = 0
    tmp = dest + ".tmp"
    with zf.open(info) as src, open(tmp, "wb") as dst:
        while chunk := src.read(1 << 20):
            crc = zlib.crc32(chunk, crc)
            dst.write(chunk)
    os.replace(tmp, dest)
    return f"{crc & 0xFFFFFFFF:08X}"


def existing_airac_line(cycle: str):
    """Retourne la ligne <Airac> du cycle si elle existe déjà dans data.xml, sinon None."""
    with open(DATA_XML, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    start, end = text.find("<Airacs>"), text.find("</Airacs>")
    if start < 0 or end < 0:
        sys.exit("Erreur : bloc <Airacs> introuvable dans data.xml.")
    for m in AIRAC_LINE_RE.finditer(text, start, end):
        if m.group(2) == cycle:
            return m.group(0).strip()
    return None


def update_data_xml(cycle: str, crc: str, force: bool, dry_run: bool):
    with open(DATA_XML, "r", encoding="utf-8", newline="") as f:
        text = f.read()

    start = text.find("<Airacs>")
    end = text.find("</Airacs>")
    if start < 0 or end < 0:
        sys.exit("Erreur : bloc <Airacs> introuvable dans data.xml.")

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = list(AIRAC_LINE_RE.finditer(text, start, end))

    # Indentation et base d'URL reprises des lignes existantes
    indent = lines[0].group(1) if lines else "        "
    url_base = DEFAULT_URL_BASE
    for m in lines:
        u = re.search(r'aixm="([^"]*/)aixm_\d{4}\.xml"', m.group(0))
        if u:
            url_base = u.group(1)
            break

    new_line = (f'{indent}<Airac cycle="{cycle}" status="active" crc32="{crc}" '
                f'aixm="{url_base}aixm_{cycle}.xml"/>{newline}')

    existing = next((m for m in lines if m.group(2) == cycle), None)
    if existing:
        if not force:
            sys.exit(f"Erreur : le cycle {cycle} existe déjà dans data.xml (utiliser --force pour le remplacer).")
        pos, stop = existing.start(), existing.end()
    else:
        older = next((m for m in lines if int(m.group(2)) < int(cycle)), None)
        if older:
            pos = stop = older.start()
        else:
            # Plus ancien que tous : avant </Airacs> (au début de sa ligne)
            pos = stop = text.rfind("\n", 0, end) + 1

    text = text[:pos] + new_line + text[stop:]
    print(("[dry-run] " if dry_run else "") + "data.xml : " + new_line.strip())
    if not dry_run:
        tmp = DATA_XML + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, DATA_XML)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    p =argparse.ArgumentParser(description="Ajoute un AIRAC SIA à FreeVFR-data.")
    p.add_argument("zip", help="fichier export_xml_bd_sia_*.zip")
    p.add_argument("--cycle", help="forcer le cycle (yynn) au lieu de le calculer")
    p.add_argument("--force", action="store_true", help="écraser un cycle/fichier déjà présent")
    p.add_argument("--dry-run", action="store_true", help="afficher sans rien écrire")
    args = p.parse_args()

    with zipfile.ZipFile(args.zip) as zf:
        info, file_date = find_member(zf)
        print(f"Fichier trouvé : {info.filename} ({info.file_size / 1e6:.1f} Mo)")

        effective = read_effective(zf, info)
        if effective and effective != file_date:
            print(f"Attention : date du nom ({file_date}) ≠ attribut effective ({effective}), on utilise effective.")
        date = effective or file_date

        if args.cycle:
            if not re.fullmatch(r"\d{4}", args.cycle):
                sys.exit("Erreur : --cycle doit être au format yynn (ex. 2612).")
            cycle = args.cycle
        else:
            try:
                cycle = airac_cycle(date)
            except ValueError as e:
                sys.exit(f"Erreur : {e}. Utiliser --cycle pour le préciser.")
        print(f"Entrée en vigueur {date} → cycle AIRAC {cycle}")

        # Vérification avant toute écriture : la ligne du cycle est-elle déjà dans data.xml ?
        existing = existing_airac_line(cycle)
        if existing:
            if not args.force:
                sys.exit(f"Erreur : le cycle {cycle} est déjà présent dans data.xml :\n  {existing}\n"
                         "Utiliser --force pour le réécrire.")
            print(f"Cycle {cycle} déjà présent, réécriture (--force) :\n  {existing}")

        dest = os.path.join(AIRACS_DIR, f"aixm_{cycle}.xml")
        if os.path.exists(dest) and not existing:
            print(f"Note : {dest} existe déjà mais n'est pas référencé dans data.xml, il sera écrasé.")

        if args.dry_run:
            with zf.open(info) as src:
                crc = 0
                while chunk := src.read(1 << 20):
                    crc = zlib.crc32(chunk, crc)
            crc = f"{crc & 0xFFFFFFFF:08X}"
            print(f"[dry-run] extraction vers {dest} (CRC32 {crc})")
        else:
            os.makedirs(AIRACS_DIR, exist_ok=True)
            crc = extract_with_crc(zf, info, dest)
            print(f"Extrait vers {dest} (CRC32 {crc})")

    update_data_xml(cycle, crc, args.force, args.dry_run)
    print("Terminé.")


if __name__ == "__main__":
    main()
