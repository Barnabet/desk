"""A small DER (ASN.1) reader: enough to describe X.509 certificates, CSRs, CRLs and key files without a crypto library.

It never prints key material: private keys are reported by kind and size only. Standard library only.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import re
from typing import Any, Iterator

OIDS = {
    "2.5.4.3": "CN", "2.5.4.6": "C", "2.5.4.7": "L", "2.5.4.8": "ST", "2.5.4.10": "O", "2.5.4.11": "OU",
    "2.5.4.5": "serialNumber", "1.2.840.113549.1.9.1": "emailAddress", "0.9.2342.19200300.100.1.25": "DC",
    "1.2.840.113549.1.1.1": "RSA", "1.2.840.10045.2.1": "EC", "1.2.840.10040.4.1": "DSA", "1.3.101.112": "Ed25519",
    "1.3.101.113": "Ed448", "1.3.101.110": "X25519", "1.3.101.111": "X448", "1.2.840.113549.1.1.10": "RSASSA-PSS",
    "1.2.840.113549.1.1.4": "md5WithRSA", "1.2.840.113549.1.1.5": "sha1WithRSA", "1.2.840.113549.1.1.11": "sha256WithRSA",
    "1.2.840.113549.1.1.12": "sha384WithRSA", "1.2.840.113549.1.1.13": "sha512WithRSA", "1.2.840.10045.4.3.2": "ecdsa-with-SHA256",
    "1.2.840.10045.4.3.3": "ecdsa-with-SHA384", "1.2.840.10045.4.3.4": "ecdsa-with-SHA512", "1.2.840.10045.4.1": "ecdsa-with-SHA1",
    "1.2.840.10045.3.1.7": "P-256", "1.3.132.0.34": "P-384", "1.3.132.0.35": "P-521", "1.3.132.0.10": "secp256k1",
    "1.2.840.113549.1.5.13": "PBES2", "1.2.840.113549.1.12.1.3": "pbeWithSHAAnd3-KeyTripleDES-CBC",
    "1.2.840.113549.1.7.1": "pkcs7-data", "1.2.840.113549.1.7.2": "pkcs7-signedData", "1.2.840.113549.1.7.6": "pkcs7-encryptedData",
}

PRIVATE_PEM = re.compile(r"PRIVATE KEY|PRIVATE KEY BLOCK|OPENSSH PRIVATE|SSH2 ENCRYPTED PRIVATE")


class DerError(ValueError):
    pass


def tlv(buf: bytes, i: int) -> tuple[int, int, int]:
    """(tag, content start, content end) of the element at i."""
    if i + 2 > len(buf):
        raise DerError("truncated")
    tag = buf[i]
    i += 1
    if tag & 0x1F == 0x1F:  # high tag number form
        while i < len(buf) and buf[i] & 0x80:
            i += 1
        i += 1
    ln = buf[i]
    i += 1
    if ln & 0x80:
        n = ln & 0x7F
        if n == 0 or n > 4 or i + n > len(buf):
            raise DerError("bad length")
        ln = int.from_bytes(buf[i : i + n], "big")
        i += n
    if i + ln > len(buf):
        raise DerError("truncated content")
    return tag, i, i + ln


def children(buf: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    out = []
    i = start
    while i < end:
        t, s, e = tlv(buf, i)
        out.append((t, s, e))
        i = e
    return out


def oid(b: bytes) -> str:
    if not b:
        return ""
    parts = [b[0] // 40, b[0] % 40]
    v = 0
    for byte in b[1:]:
        v = (v << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(v)
            v = 0
    return ".".join(str(p) for p in parts)


def _str(b: bytes, tag: int) -> str:
    if tag == 0x1E:  # BMPString
        return b.decode("utf-16-be", "replace")
    return b.decode("utf-8", "replace")


def name(buf: bytes, s: int, e: int) -> str:
    parts = []
    for _t, rs, re_ in children(buf, s, e):  # RDN sets
        for _t2, as_, ae in children(buf, rs, re_):  # AttributeTypeAndValue
            kids = children(buf, as_, ae)
            if len(kids) < 2:
                continue
            ot, os_, oe = kids[0]
            vt, vs, ve = kids[1]
            key = OIDS.get(oid(buf[os_:oe]), oid(buf[os_:oe]))
            parts.append(f"{key}={_str(buf[vs:ve], vt)}")
    return ", ".join(parts)


def _time(tag: int, b: bytes) -> str:
    s = b.decode("ascii", "replace")
    try:
        if tag == 0x17:  # UTCTime YYMMDDHHMMSSZ
            yy = int(s[0:2])
            year = 2000 + yy if yy < 50 else 1900 + yy
            dt = _dt.datetime(year, int(s[2:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), int(s[10:12]) if len(s) > 12 else 0, tzinfo=_dt.timezone.utc)
        else:
            dt = _dt.datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), int(s[10:12]), int(s[12:14]) if len(s) > 14 else 0, tzinfo=_dt.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, IndexError):
        return s


def _int_bits(b: bytes) -> int:
    b = b.lstrip(b"\x00")
    return (len(b) - 1) * 8 + b[0].bit_length() if b else 0


def spki(buf: bytes, s: int, e: int) -> str:
    """'RSA 2048', 'EC P-256', 'Ed25519' from a SubjectPublicKeyInfo."""
    kids = children(buf, s, e)
    alg = children(buf, kids[0][1], kids[0][2])
    alg_name = OIDS.get(oid(buf[alg[0][1] : alg[0][2]]), oid(buf[alg[0][1] : alg[0][2]]))
    if alg_name == "RSA" and len(kids) > 1:
        try:
            bs = kids[1]
            inner = buf[bs[1] + 1 : bs[2]]  # skip the unused-bits byte
            _t, ss, se = tlv(inner, 0)
            n = children(inner, ss, se)[0]
            return f"RSA {_int_bits(inner[n[1]:n[2]])}"
        except (DerError, IndexError):
            return "RSA"
    if alg_name == "EC" and len(alg) > 1 and alg[1][0] == 0x06:
        return f"EC {OIDS.get(oid(buf[alg[1][1]:alg[1][2]]), oid(buf[alg[1][1]:alg[1][2]]))}"
    return alg_name


def certificate(buf: bytes, now: _dt.datetime | None = None) -> dict[str, Any]:
    """Subject, issuer, validity, key, SANs and CA flag of a DER certificate."""
    _t, s, e = tlv(buf, 0)
    top = children(buf, s, e)
    tbs = children(buf, top[0][1], top[0][2])
    i = 0
    if tbs[0][0] == 0xA0:  # [0] version
        i = 1
    serial = buf[tbs[i][1] : tbs[i][2]].hex()
    sig_alg = children(buf, tbs[i + 1][1], tbs[i + 1][2])
    issuer = name(buf, tbs[i + 2][1], tbs[i + 2][2])
    validity = children(buf, tbs[i + 3][1], tbs[i + 3][2])
    subject = name(buf, tbs[i + 4][1], tbs[i + 4][2])
    key = spki(buf, tbs[i + 5][1], tbs[i + 5][2])
    info: dict[str, Any] = {
        "subject": subject,
        "issuer": issuer,
        "not_before": _time(validity[0][0], buf[validity[0][1] : validity[0][2]]),
        "not_after": _time(validity[1][0], buf[validity[1][1] : validity[1][2]]),
        "key": key,
        "signature": OIDS.get(oid(buf[sig_alg[0][1] : sig_alg[0][2]]), oid(buf[sig_alg[0][1] : sig_alg[0][2]])),
        "serial": serial[:40],
        "self_signed": subject == issuer,
    }
    for t, es, ee in tbs[i + 6 :]:
        if t != 0xA3:
            continue
        _t2, ss, se = tlv(buf, es)
        for _t3, xs, xe in children(buf, ss, se):
            parts = children(buf, xs, xe)
            ext = oid(buf[parts[0][1] : parts[0][2]])
            val = parts[-1]
            if ext == "2.5.29.17":  # subjectAltName
                _t4, gs, ge = tlv(buf, val[1])
                names = []
                for gt, ns, ne in children(buf, gs, ge):
                    if gt in (0x82, 0x81, 0x86):
                        names.append(buf[ns:ne].decode("ascii", "replace"))
                    elif gt == 0x87:
                        raw = buf[ns:ne]
                        names.append(".".join(str(x) for x in raw) if len(raw) == 4 else raw.hex())
                info["san"] = names
            elif ext == "2.5.29.19":  # basicConstraints
                _t4, bs, be = tlv(buf, val[1])
                kids = children(buf, bs, be)
                info["ca"] = bool(kids and kids[0][0] == 0x01 and buf[kids[0][1]] != 0)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        na = _dt.datetime.strptime(info["not_after"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        nb = _dt.datetime.strptime(info["not_before"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        days = (na - now).days
        info["status"] = "expired" if na < now else "not yet valid" if nb > now else f"valid, {days} days left"
    except ValueError:
        pass
    return info


def classify_der(buf: bytes) -> tuple[str, dict[str, Any]] | None:
    """What a DER blob is: ('der-cert'|'der-key'|'pkcs12'|'csr'|'crl'|'der-rsa-key'|'der-public-key', details) or None."""
    try:
        _t, s, e = tlv(buf, 0)
        if _t != 0x30:
            return None
        top = children(buf, s, e)
    except DerError:
        return None
    if not top:
        return None
    tags = [t for t, _, _ in top]
    try:
        # PKCS#12: SEQUENCE { INTEGER 3, SEQUENCE { contentInfo }, ... }
        if tags[0] == 0x02 and buf[top[0][1] : top[0][2]] == b"\x03" and len(top) >= 2 and tags[1] == 0x30:
            return "pkcs12", {"note": "PKCS#12 bundle: usually a certificate plus its private key, password-protected"}
        # PKCS#8 private key: SEQUENCE { INTEGER 0|1, SEQUENCE { OID alg ... }, OCTET STRING }
        if tags[0] == 0x02 and len(top) >= 3 and tags[1] == 0x30 and tags[2] == 0x04:
            alg = children(buf, top[1][1], top[1][2])
            alg_name = OIDS.get(oid(buf[alg[0][1] : alg[0][2]]), "unknown algorithm")
            return "der-key", {"key": f"{alg_name} private key (PKCS#8)", "private_key": True}
        # PKCS#1 RSA private key: SEQUENCE { INTEGER 0, INTEGER n, INTEGER e, INTEGER d, ... } (9 integers)
        if len(top) >= 9 and all(t == 0x02 for t in tags[:9]):
            return "der-key", {"key": f"RSA {_int_bits(buf[top[1][1]:top[1][2]])} private key (PKCS#1)", "private_key": True}
        # Encrypted PKCS#8: SEQUENCE { SEQUENCE { OID pbes2 ... }, OCTET STRING }
        if len(top) == 2 and tags == [0x30, 0x04]:
            alg = children(buf, top[0][1], top[0][2])
            if alg and alg[0][0] == 0x06:
                return "der-key", {"key": "encrypted private key (PKCS#8, password-protected)", "private_key": True, "encrypted": True}
        # SubjectPublicKeyInfo: SEQUENCE { SEQUENCE { OID ... }, BIT STRING }
        if len(top) == 2 and tags == [0x30, 0x03]:
            return "der-cert", {"key": f"{spki(buf, s, e)} public key", "public_key": True}
        if tags[0] == 0x30 and len(top) >= 3:
            inner = children(buf, top[0][1], top[0][2])
            itags = [t for t, _, _ in inner]
            # Certificate: tbs starts with [0] version or INTEGER serial, then AlgorithmIdentifier, Name, Validity…
            if itags[:1] == [0xA0] or (len(itags) >= 6 and itags[0] == 0x02 and itags[1] == 0x30 and itags[3] == 0x30):
                try:
                    return "der-cert", certificate(buf)
                except (DerError, IndexError):
                    pass
            # CSR: CertificationRequestInfo { INTEGER 0, Name, SPKI, [0] attributes }
            if len(itags) >= 3 and itags[0] == 0x02 and itags[1] == 0x30 and itags[2] == 0x30:
                return "der-cert", {"kind": "certificate signing request", "subject": name(buf, inner[1][1], inner[1][2])}
            # CRL: TBSCertList { [INTEGER v2], AlgorithmIdentifier, Name issuer, Time thisUpdate, ... }
            j = 1 if itags and itags[0] == 0x02 else 0
            if len(itags) > j + 2 and itags[j] == 0x30 and itags[j + 1] == 0x30 and itags[j + 2] in (0x17, 0x18):
                return "der-cert", {"kind": "certificate revocation list", "issuer": name(buf, inner[j + 1][1], inner[j + 1][2])}
    except (DerError, IndexError):
        return None
    return None


_PEM_BEGIN = re.compile(r"-----BEGIN ([A-Z0-9 #]+)-----")


def pem_blocks(text: str) -> Iterator[tuple[str, str]]:
    """(label, body) of each PEM block. Linear time: a lazy "BEGIN (.*?) END" regex rescanned the rest of the text
    for every unclosed BEGIN line; here a label without an END is never looked for again."""
    pos = 0
    unclosed: set[str] = set()
    while True:
        m = _PEM_BEGIN.search(text, pos)
        if not m:
            return
        label = m.group(1)
        end = -1 if label in unclosed else text.find(f"-----END {label}-----", m.end())
        if end < 0:
            unclosed.add(label)
            pos = m.end()
            continue
        yield label, text[m.end() : end]
        pos = end + len(label) + 14


def pem_summary(text: str) -> dict[str, Any]:
    """Blocks in a PEM file: kinds, certificate subjects and expiry, private keys (never their content)."""
    blocks = []
    private = 0
    encrypted = 0
    for label, body in pem_blocks(text):
        kind = label.strip()
        entry: dict[str, Any] = {"kind": kind}
        if PRIVATE_PEM.search(kind):
            private += 1
            entry["private_key"] = True
            if "ENCRYPTED" in kind or "Proc-Type: 4,ENCRYPTED" in body:
                encrypted += 1
                entry["encrypted"] = True
        elif kind in ("CERTIFICATE", "TRUSTED CERTIFICATE", "X509 CERTIFICATE"):
            b64 = "".join(line for line in body.splitlines() if line and ":" not in line)
            try:
                der = base64.b64decode(b64, validate=False)
                entry.update(certificate(der))
            except (binascii.Error, DerError, IndexError, ValueError):
                entry["error"] = "could not parse"
        elif kind in ("PUBLIC KEY",):
            b64 = "".join(line for line in body.splitlines() if line and ":" not in line)
            try:
                der = base64.b64decode(b64)
                _t, s, e = tlv(der, 0)
                entry["key"] = spki(der, s, e)
            except (binascii.Error, DerError, IndexError, ValueError):
                pass
        blocks.append(entry)
    return {"blocks": blocks, "private_keys": private, "encrypted_private_keys": encrypted}
