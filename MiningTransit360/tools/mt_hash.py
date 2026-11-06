# -*- coding: utf-8 -*-
"""
Empreinte MT360 (miroir exact de modSecurity.MT_Hash en VBA).

Algorithme : 4 voies FNV-1a 32 bits, 'rounds' tours, melange croise des voies.
Toutes les operations VBA sont faites en Double (< 2^53) pour eviter les
debordements de Long : ce fichier reproduit exactement la meme arithmetique.

ATTENTION : ce n'est pas une primitive cryptographique certifiee. Elle sert a
ne pas stocker les mots de passe en clair et a detecter la modification
silencieuse d'une facture validee. Voir docs/04_REGLES_DE_GESTION.md (securite).
"""

SEEDS = [2166136261.0, 3735928559.0, 305419896.0, 2882400001.0]
TWO32 = 4294967296.0


def fnv_step(h, b):
    lo = h - int(h / 256.0) * 256.0
    h = h - lo + (int(lo) ^ (b & 255))
    lo = h - int(h / 256.0) * 256.0
    h = lo * 16777216.0 + h * 403.0
    return h - int(h / TWO32) * TWO32


def to_hex8(h):
    hi = int(h / 65536.0)
    lo = int(h - hi * 65536.0)
    return ("%04X" % hi) + ("%04X" % lo)


def mt_hash(text, rounds=64):
    h = list(SEEDS)
    codes = [ord(c) for c in text]
    for r in range(1, rounds + 1):
        for i in range(4):
            x = h[i]
            for c in codes:
                x = fnv_step(x, c & 255)
                x = fnv_step(x, (c >> 8) & 255)
            x = fnv_step(x, (r + i) & 255)
            nxt = h[(i + 1) % 4]
            for k in range(4):
                x = fnv_step(x, int(nxt / (256.0 ** k)) & 255)
            h[i] = x
    return "".join(to_hex8(v) for v in h)


def password_hash(password, salt):
    return mt_hash(salt + ":" + password, 64)


def invoice_fingerprint(num, client_code, tonnage, ttc):
    # Representation canonique identique au VBA : tonnage a 3 decimales, TTC entier
    payload = "%s|%s|%.3f|%d" % (num, client_code, round(float(tonnage), 3), int(round(float(ttc))))
    return mt_hash(payload, 8)


if __name__ == "__main__":
    print(password_hash("Admin@2026", "S4LT"))
    print(invoice_fingerprint("FAC-2026-0001", "SMN", 12345.678, 740740680))
