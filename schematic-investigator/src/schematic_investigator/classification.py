"""Conservative text categories; every category remains an unverified candidate."""
import re

TOKEN = re.compile(r"[A-Za-z0-9_+./#:-]+")
REFDES = re.compile(r"(?:U|R|C|L|D|Q|J|P|TP|FB|RN|SW|Y|X|CN)\d+[A-Z]?", re.I)


def classify_token(token):
    upper = token.upper()
    if (re.fullmatch(r"(?:\d+(?:\.\d+)?(?:OHM|MIL)[_-]?)?NETCLASS[_-]?\d*", upper)
            or upper in {"IMPEDANCE", "TRACE_WIDTH", "DIFFPAIR"}):
        return "annotation", "drawing/net-class rule text"
    if upper in {"COMMON", "DNI", "DNP", "NI", "NO_STUFF", "NOT_FITTED"}:
        return "assembly", "assembly annotation; population requires review"
    if REFDES.fullmatch(token):
        return "refdes", "reference designator pattern"
    if re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[pnuµmkM]?(?:[FfHh]|[Oo][Hh][Mm])|[kKmM]|[Rr])", token):
        return "component_value", "value/unit pattern; component association unknown"
    if re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z][A-Za-z0-9-]{4,}", token) and "_" not in token:
        return "part_number", "part-number pattern"
    if upper == "GND" or "_" in token or re.search(r"(?:I2C|SMB|SCL|SDA|GPIO|RESET|PWR|CLK)", token, re.I):
        return "net", "net-label pattern; connectivity unknown"
    return None


def classify_text(text):
    labels = []
    for token in TOKEN.findall(text):
        category = classify_token(token)
        if category:
            labels.append({"text": token, "kind": category[0], "reason": category[1], "candidate": True})
    for match in re.finditer(r"\b(?:NO\s+STUFF|NOT\s+FITTED)\b", text, re.I):
        labels.append({"text": match.group(), "kind": "assembly",
                       "reason": "assembly annotation; population requires review", "candidate": True})
    return labels
