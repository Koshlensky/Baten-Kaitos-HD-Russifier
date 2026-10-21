import re
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8")
for game in ("bk1", "bk2"):
    data = open(f"work/re/{game}/global-metadata.dat", "rb").read()
    hdr = struct.unpack_from("<64I", data, 0)
    # (offset, size) пары заголовка метаданных v29: 18-я/19-я — fieldAndParameterDefaultValueData
    lo, size = hdr[18], hdr[19]
    blob = data[lo:lo + size]
    markers = re.findall(rb"@@([A-Za-z0-9_]+)@@", blob)
    prefixes = {}
    for m in markers:
        p = m.split(b"_")[0].decode()
        prefixes[p] = prefixes.get(p, 0) + 1
    top = sorted(prefixes.items(), key=lambda x: -x[1])[:12]
    print(game, "default-value blob", size, "markers", len(markers), top)
    for probe in (b"@@EV_0600@@", b"@@SCR_EVE08_2_2936@@", b"@@MINTAKA_N_101@@"):
        i = blob.find(probe)
        if i >= 0:
            print("   ", probe.decode(), "found; preceding bytes:", blob[i - 24:i].hex(" "))
