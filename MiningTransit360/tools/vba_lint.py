# -*- coding: utf-8 -*-
"""
Analyseur statique du projet VBA MINING TRANSIT 360 (sans Excel).

Controles :
  1. Option Explicit present dans chaque composant
  2. Equilibre des blocs (Sub/Function/Property, If, For, Do, While, Select, With)
  3. Identifiants non declares (simulation d'Option Explicit)
  4. Noms de procedures publiques ambigus entre modules standard
  5. Membres appeles sur des variables typees (classes / formulaires du projet) inexistants
  6. Macros referencees par chaine (OnAction, OnTime, Application.Run) inexistantes
  7. Caracteres non representables en Windows-1252 (encodage d'import VBE)
  8. Nombre d'arguments des appels a des procedures du projet (positionnels, hors ParamArray)

Ce n'est PAS un compilateur : la compilation reelle est faite par le script
build/Build-MT360.ps1 dans Excel (Debogage > Compiler). Il reduit fortement le
risque d'erreur de compilation.
Usage : python3 tools/vba_lint.py  (code retour 1 si erreur)
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src", "vba")

KEYWORDS = set("""
and as attribute binary boolean byref byte byval call case close compare const currency date debug declare
dim do double each else elseif empty end enum erase error event exit explicit false for friend function get global
goto if implements in input integer is let lib like long loop lset me mod new next not nothing null object on open
option optional or output paramarray preserve print private property public put raiseevent redim rem resume
return rset select set single static step stop string sub then to true type typeof until variant wend while with
withevents write xor append access lock read shared spc tab longlong longptr
""".split())

BUILTINS = set("""
abs array asc ascw atn cbool cbyte ccur cdate cdbl cdec chdir chdrive choose chr chrw cint clng clnglng clngptr
command cos createobject csng cstr curdir cvar cverr date dateadd datediff datepart dateserial datevalue day ddb
dir doevents environ eof err error exp fileattr filecopy filedatetime filelen filter fix format formatcurrency
formatdatetime formatnumber formatpercent freefile fv getattr getobject hex hour iif imestatus input inputbox instr
instrrev int ipmt irr isarray isdate isempty iserror ismissing isnull isnumeric isobject join kill lbound lcase left
len loc lof log ltrim mid minute mirr mkdir month monthname msgbox now nper npv oct partition pmt ppmt pv qbcolor
randomize rate replace rgb right rmdir rnd round rtrim second seek sgn shell sin sln space split sqr str strcomp
strconv string strreverse switch syd tan time timer timeserial timevalue trim typename ubound ucase val vartype
weekday weekdayname year collection application thisworkbook activesheet activewindow activeworkbook workbooks
worksheets worksheet workbook range shape shapes listobject listrow listcolumn chartobject chart window selection
cells columns rows unload load msforms returnboolean returninteger commandbutton label listbox textbox combobox
checkbox userform control controls debug err dictionary vba excel office textframe2 font interior borders
""".split())

UF_MEMBERS = set("""show hide controls caption width height insidewidth insideheight backcolor scrollbars scrollheight
scrolltop left top startupposition repaint setfocus""".split())


def strip_line(line):
    """Supprime chaines et commentaires ; retourne (code, chaines)."""
    out, strings, i, n = [], [], 0, len(line)
    while i < n:
        c = line[i]
        if c == '"':
            j = i + 1
            buf = []
            while j < n:
                if line[j] == '"':
                    if j + 1 < n and line[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(line[j])
                j += 1
            strings.append("".join(buf))
            out.append('""')
            i = j + 1
            continue
        if c == "'":
            break
        if line[i:i + 4].lower() == "rem " and (i == 0 or not line[i - 1].isalnum()) and "".join(out).strip() == "":
            break
        out.append(c)
        i += 1
    return "".join(out), strings


class Component:
    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.kind = {".bas": "module", ".cls": "class", ".frm": "form"}[os.path.splitext(path)[1]]
        if self.name == "ThisWorkbook":
            self.kind = "document"
        raw = open(path, encoding="utf-8").read()
        self.raw = raw
        lines = raw.split("\n")
        # saute l'en-tete VERSION/BEGIN/END et les Attribute
        code = []
        in_hdr = False
        for ln in lines:
            s = ln.strip()
            if s.startswith("VERSION ") or s == "BEGIN" or s.startswith("Begin {"):
                in_hdr = True
                code.append("")
                continue
            if in_hdr:
                if s in ("END", "End"):
                    in_hdr = False
                code.append("")
                continue
            if s.startswith("Attribute "):
                code.append("")
                continue
            code.append(ln.rstrip("\r"))
        # jointure des lignes continues
        self.lines = []      # (lineno, code_sans_chaines, chaines)
        buf, start, strs = "", None, []
        for no, ln in enumerate(code, 1):
            c, s = strip_line(ln)
            if start is None:
                start = no
            if c.rstrip().endswith(" _"):
                buf += c.rstrip()[:-1] + " "
                strs += s
                continue
            buf += c
            strs += s
            self.lines.append((start, buf, strs))
            buf, start, strs = "", None, []
        self.procs = {}       # nom -> dict(vis, kind, params, start, end, body)
        self.mod_vars = {}    # nom -> (vis, type)
        self.parse()

    def parse(self):
        rx_proc = re.compile(r"^\s*(public|private|friend)?\s*(static\s+)?(sub|function|property\s+get|property\s+let|property\s+set)\s+(\w+)\s*\((.*)\)\s*(as\s+(\w+))?\s*$", re.I)
        cur = None
        for no, c, s in self.lines:
            for stmt in split_colon(c):
                m = rx_proc.match(stmt)
                if m and cur is None:
                    vis = (m.group(1) or "public").lower()
                    kind = m.group(3).lower()
                    cur = dict(name=m.group(4), vis=vis, kind=kind, params=parse_params(m.group(5)),
                               ret=(m.group(7) or "").lower(), start=no, body=[], vars={})
                    for p in cur["params"]:
                        cur["vars"][p["name"].lower()] = p["type"]
                    continue
                if cur is not None and re.match(r"^\s*end\s+(sub|function|property)\b", stmt, re.I):
                    cur["end"] = no
                    key = cur["name"].lower()
                    if cur["kind"].startswith("property"):
                        key = key  # get/let partagent le nom
                    self.procs.setdefault(key, cur)
                    cur = None
                    continue
                if cur is not None:
                    cur["body"].append((no, stmt))
                else:
                    mv = re.match(r"^\s*(public|private|dim|global)\s+(const\s+)?(withevents\s+)?(.*)$", stmt, re.I)
                    if mv and not re.match(r"^\s*(public|private)\s+(sub|function|property|declare|enum|type)\b", stmt, re.I):
                        vis = mv.group(1).lower()
                        for d in split_decl(mv.group(4)):
                            self.mod_vars[d[0].lower()] = (vis, d[1])


def split_colon(c):
    # separe les instructions sur ":" (hors ":=" et etiquettes simples)
    parts, buf, i = [], "", 0
    while i < len(c):
        if c[i] == ":" and not (i + 1 < len(c) and c[i + 1] == "="):
            parts.append(buf)
            buf = ""
        else:
            buf += c[i]
        i += 1
    parts.append(buf)
    # "Label:" en debut de ligne -> la premiere partie est une etiquette
    return [p for p in parts if p.strip()]


def parse_params(s):
    out = []
    if not s.strip():
        return out
    for p in split_top(s, ","):
        p = p.strip()
        pa = bool(re.match(r"^paramarray\b", p, re.I))
        opt = bool(re.match(r"^(optional\b)", p, re.I))
        p2 = re.sub(r"^(optional|byval|byref|paramarray)\s+", "", p, flags=re.I)
        p2 = re.sub(r"^(optional|byval|byref|paramarray)\s+", "", p2, flags=re.I)
        m = re.match(r"(\w+)(\(\))?\s*(as\s+([\w.]+))?", p2, re.I)
        if m:
            out.append(dict(name=m.group(1), type=(m.group(4) or "variant").lower(), optional=opt or "=" in p2, paramarray=pa,
                            byval=bool(re.search(r"\bbyval\b", p, re.I)), isarray=bool(m.group(2))))
    return out


def split_top(s, sep):
    parts, depth, buf = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return parts


def split_decl(s):
    out = []
    for part in split_top(s, ","):
        part = part.strip()
        m = re.match(r"(withevents\s+)?(\w+)\s*(\([^)]*\))?\s*(as\s+(new\s+)?([\w.]+))?", part, re.I)
        if m:
            out.append((m.group(2), (m.group(6) or "variant").lower()))
    return out


def _has_top_eq(st):
    depth = 0
    for i, ch in enumerate(st):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "=" and depth == 0:
            return True
    return False


def sigs_for(c, all_proc_sig):
    d = dict(all_proc_sig)
    for k, p in c.procs.items():
        d[k] = p
    return d


def _calls(stmt, sigs):
    out = []
    s2 = stmt.strip()
    mc = re.match(r"^(call\s+)?([A-Za-z_]\w*)(\s+(?!=)(.*))?$", s2, re.I)
    if mc and mc.group(2).lower() in sigs and not re.match(r"^\w+\s*=", s2):
        args = mc.group(4) or ""
        if mc.group(1):
            args = re.sub(r"^\((.*)\)$", r"\1", args.strip())
        out.append((mc.group(2), args))
    for fm in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)\(", stmt):
        nm = fm.group(1)
        if nm.lower() in sigs and sigs[nm.lower()]["kind"] == "function":
            depth, j, start = 1, fm.end(), fm.end()
            while j < len(stmt) and depth:
                if stmt[j] == "(":
                    depth += 1
                elif stmt[j] == ")":
                    depth -= 1
                j += 1
            out.append((nm, stmt[start:j - 1]))
    return out


def load():
    comps = []
    for sub in ("modules", "classes", "forms", "document"):
        d = os.path.join(SRC, sub)
        for f in sorted(os.listdir(d)):
            if f.endswith((".bas", ".cls", ".frm")):
                comps.append(Component(os.path.join(d, f)))
    return comps


def main():
    comps = load()
    errors, warnings = [], []
    byname = {c.name.lower(): c for c in comps}
    std = [c for c in comps if c.kind == "module"]
    classes = {c.name.lower(): c for c in comps if c.kind in ("class", "form")}

    # 7. encodage
    for c in comps:
        for i, ln in enumerate(c.raw.split("\n"), 1):
            try:
                ln.encode("cp1252")
            except UnicodeEncodeError as e:
                errors.append("%s:%d caractère non Windows-1252 : %r" % (c.name, i, ln[e.start:e.end]))

    # 1. Option Explicit
    for c in comps:
        if not re.search(r"^\s*Option Explicit", c.raw, re.M):
            errors.append("%s : Option Explicit absent" % c.name)

    # 4. ambiguite
    seen = {}
    for c in std:
        for k, p in c.procs.items():
            if p["vis"] != "private":
                if k in seen and seen[k] != c.name:
                    errors.append("Nom ambigu : %s dans %s et %s" % (p["name"], seen[k], c.name))
                seen[k] = c.name
        for k, (vis, t) in c.mod_vars.items():
            if vis in ("public", "global"):
                if k in seen and seen[k] != c.name:
                    errors.append("Nom ambigu (variable/constante) : %s dans %s et %s" % (k, seen[k], c.name))
                seen[k] = c.name
    public_names = set(seen.keys())
    all_proc_sig = {}
    for c in std:
        for k, p in c.procs.items():
            if p["vis"] != "private":
                all_proc_sig[k] = p

    # 2. blocs
    for c in comps:
        for pk, p in c.procs.items():
            stack = []
            for no, stmt in p["body"]:
                s = stmt.strip()
                low = s.lower()
                if not low:
                    continue
                if re.match(r"^if\b.*\bthen\s*$", low):
                    stack.append(("if", no))
                elif re.match(r"^(elseif|else)\b", low):
                    if not stack or stack[-1][0] != "if":
                        errors.append("%s.%s:%d Else/ElseIf hors bloc If" % (c.name, p["name"], no))
                elif re.match(r"^end\s+if\b", low):
                    if not stack or stack[-1][0] != "if":
                        errors.append("%s.%s:%d End If sans If" % (c.name, p["name"], no))
                    else:
                        stack.pop()
                elif re.match(r"^for\b", low):
                    stack.append(("for", no))
                elif re.match(r"^next\b", low):
                    if not stack or stack[-1][0] != "for":
                        errors.append("%s.%s:%d Next sans For" % (c.name, p["name"], no))
                    else:
                        stack.pop()
                elif re.match(r"^do\b", low):
                    stack.append(("do", no))
                elif re.match(r"^loop\b", low):
                    if not stack or stack[-1][0] != "do":
                        errors.append("%s.%s:%d Loop sans Do" % (c.name, p["name"], no))
                    else:
                        stack.pop()
                elif re.match(r"^while\b", low):
                    stack.append(("while", no))
                elif re.match(r"^wend\b", low):
                    if not stack or stack[-1][0] != "while":
                        errors.append("%s.%s:%d Wend sans While" % (c.name, p["name"], no))
                    else:
                        stack.pop()
                elif re.match(r"^select\s+case\b", low):
                    stack.append(("select", no))
                elif re.match(r"^end\s+select\b", low):
                    if not stack or stack[-1][0] != "select":
                        errors.append("%s.%s:%d End Select sans Select" % (c.name, p["name"], no))
                    else:
                        stack.pop()
                elif re.match(r"^with\b", low):
                    stack.append(("with", no))
                elif re.match(r"^end\s+with\b", low):
                    if not stack or stack[-1][0] != "with":
                        errors.append("%s.%s:%d End With sans With" % (c.name, p["name"], no))
                    else:
                        stack.pop()
                elif re.match(r"^(sub|function|property)\b", low) or re.match(r"^(public|private)\s+(sub|function)", low):
                    errors.append("%s.%s:%d procédure imbriquée (End Sub manquant ?)" % (c.name, p["name"], no))
            for k, no in stack:
                errors.append("%s.%s:%d bloc %s non fermé" % (c.name, p["name"], no, k))
            if "end" not in p:
                errors.append("%s.%s : fin de procédure absente" % (c.name, p["name"]))

    # 3. identifiants non declares + 5. membres + 8. arguments
    known_global = set(KEYWORDS) | set(BUILTINS) | public_names | set(byname.keys())
    rx_tok = re.compile(r"(?<![\w.#&])([A-Za-z_]\w*)\$?(?!\w)")
    for c in comps:
        mod_known = set(k for k in c.mod_vars) | set(c.procs.keys())
        for pk, p in c.procs.items():
            local = dict(p["vars"])
            labels = set()
            for no, stmt in p["body"]:
                st = stmt.strip()
                ml = re.match(r"^(\w+):\s*$", st + (":" if False else ""))
                if re.match(r"^\w+$", st) and False:
                    pass
                mlab = re.match(r"^([A-Za-z_]\w*)$", st)
                for d in re.finditer(r"\b(dim|static|const|redim(?:\s+preserve)?)\s+(.*)$", st, re.I):
                    decl = d.group(2)
                    if d.group(1).lower() == "const":
                        decl = re.sub(r"=.*$", "", decl)
                    for name, t in split_decl(re.sub(r"\s*=\s*[^,]+", "", decl)):
                        if d.group(1).lower().startswith("redim") and name.lower() not in local and name.lower() not in c.mod_vars:
                            errors.append("%s.%s:%d ReDim d'une variable non déclarée : %s" % (c.name, p["name"], no, name))
                        local.setdefault(name.lower(), t)
            # etiquettes
            for no, stmt in p["body"]:
                pass
            for no, c_line, _s in c.lines:
                if p["start"] <= no <= p.get("end", 10 ** 9):
                    m = re.match(r"^\s*([A-Za-z_]\w*):(?!=)", c_line)
                    if m and m.group(1).lower() not in KEYWORDS:
                        labels.add(m.group(1).lower())
            for no, stmt in p["body"]:
                st = stmt
                if re.match(r"^\s*([A-Za-z_]\w*)\s*$", st) and st.strip().lower() in labels:
                    continue
                st = re.sub(r"\b(goto|resume)\s+\w+", " ", st, flags=re.I)
                st = re.sub(r"\b\w+\s*:=", " ", st)
                st = re.sub(r"&H[0-9A-Fa-f]+&?", " ", st)
                st = re.sub(r"\b\d+(\.\d+)?[#!&@]?", " ", st)
                st = re.sub(r"#\w+", " ", st)
                for m in rx_tok.finditer(st):
                    tok = m.group(1)
                    low = tok.lower()
                    if low in local or low in mod_known or low in known_global or low in labels:
                        continue
                    if re.match(r"^(vb|xl|mso|fm)[A-Z]", tok):
                        continue
                    errors.append("%s.%s:%d identifiant non déclaré : %s" % (c.name, p["name"], no, tok))
                # membres de classes du projet
                for mm in re.finditer(r"(?<![\w.])(\w+)\.(\w+)", stmt):
                    var, mem = mm.group(1).lower(), mm.group(2).lower()
                    t = local.get(var) or (c.mod_vars.get(var, (None, None))[1])
                    if var == "me":
                        t = c.name.lower()
                    if t in classes:
                        cls = classes[t]
                        members = set(cls.procs.keys()) | set(k for k, v in cls.mod_vars.items() if v[0] == "public")
                        if cls.kind == "form":
                            members |= UF_MEMBERS
                        if mem not in members:
                            errors.append("%s.%s:%d membre inexistant %s.%s (classe %s)" % (c.name, p["name"], no, mm.group(1), mm.group(2), cls.name))
                # nombre d'arguments des appels "Proc a, b" et "x = Proc(a, b)"
                s2 = stmt.strip()
                sigs = dict(all_proc_sig)
                for kk, pp in c.procs.items():
                    sigs[kk] = pp
                mc = re.match(r"^(call\s+)?([A-Za-z_]\w*)(\s+(?!=)(.*))?$", s2, re.I)
                calls = []
                if mc and mc.group(2).lower() in sigs and not re.match(r"^\w+\s*=", s2):
                    args = mc.group(4) or ""
                    if mc.group(1):
                        args = re.sub(r"^\((.*)\)$", r"\1", args.strip())
                    calls.append((mc.group(2), args))
                for fm in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)\(", stmt):
                    nm = fm.group(1)
                    if nm.lower() in sigs and sigs[nm.lower()]["kind"] == "function" and nm.lower() not in local:
                        depth, j, start = 1, fm.end(), fm.end()
                        while j < len(stmt) and depth:
                            if stmt[j] == "(":
                                depth += 1
                            elif stmt[j] == ")":
                                depth -= 1
                            j += 1
                        calls.append((nm, stmt[start:j - 1]))
                # appels de methodes sur variables typees (classes / formulaires du projet)
                mm2 = re.match(r"^\s*(set\s+\w+\s*=\s*|\w+\s*=\s*)?(\w+)\.(\w+)(\s+(?!=)(.*)|\((.*)\))?\s*$", stmt, re.I)
                if mm2:
                    var, mem = mm2.group(2).lower(), mm2.group(3).lower()
                    t = local.get(var) or (c.mod_vars.get(var, (None, None))[1])
                    if var == "me":
                        t = c.name.lower()
                    if t in classes and mem in classes[t].procs:
                        sig = classes[t].procs[mem]["params"]
                        args = mm2.group(5) if mm2.group(5) is not None else (mm2.group(6) or "")
                        if not any(x["paramarray"] for x in sig) and not classes[t].procs[mem]["kind"].startswith("property"):
                            n = 0 if not args.strip() else len(split_top(args, ","))
                            req = sum(1 for x in sig if not x["optional"])
                            if n < req or n > len(sig):
                                errors.append("%s.%s:%d appel %s.%s avec %d argument(s) (attendu %d à %d)" % (c.name, p["name"], no, mm2.group(2), mm2.group(3), n, req, len(sig)))
                # procedures privees du module
                for nm2, pp in c.procs.items():
                    pass
                for nm, args in calls:
                    sig = sigs[nm.lower()]["params"]
                    if any(x["paramarray"] for x in sig):
                        continue
                    n = 0 if not args.strip() else len(split_top(args, ","))
                    req = sum(1 for x in sig if not x["optional"])
                    if n < req or n > len(sig):
                        errors.append("%s.%s:%d appel %s avec %d argument(s) (attendu %d à %d)" % (c.name, p["name"], no, nm, n, req, len(sig)))

    # 9. controles complementaires (erreurs de compilation frequentes)
    OBJ_TYPES = set("object collection worksheet workbook range shape listobject listrow chartobject window dictionary".split()) | set(classes.keys())
    for c in comps:
        for i, ln in enumerate(c.raw.split("\n"), 1):
            if len(ln) > 1023:
                errors.append("%s:%d ligne de plus de 1023 caractères" % (c.name, i))
        cont = 0
        for i, ln in enumerate(c.raw.split("\n"), 1):
            if ln.rstrip().endswith(" _"):
                cont += 1
                if cont > 24:
                    errors.append("%s:%d plus de 24 continuations de ligne" % (c.name, i))
            else:
                cont = 0
        for pk, p in c.procs.items():
            declared = {}
            for prm in p["params"]:
                declared[prm["name"].lower()] = "param"
            local_types = dict(p["vars"])
            for no, stmt in p["body"]:
                st = stmt.strip()
                low = st.lower()
                # declarations en double
                for d in re.finditer(r"^(dim|static)\s+(.*)$", st, re.I):
                    for name, t in split_decl(d.group(2)):
                        if name.lower() in declared:
                            errors.append("%s.%s:%d déclaration en double : %s" % (c.name, p["name"], no, name))
                        declared[name.lower()] = "dim"
                        local_types[name.lower()] = t
                        if name.lower() in public_names and name.lower() not in c.procs:
                            warnings.append("%s.%s:%d variable locale masquant une procédure publique : %s" % (c.name, p["name"], no, name))
                        if name.lower() in c.procs:
                            errors.append("%s.%s:%d variable locale portant le nom d'une procédure du module : %s" % (c.name, p["name"], no, name))
                # Exit coherent
                me = re.match(r"^exit\s+(sub|function|property)\b", low)
                if me and not p["kind"].startswith(me.group(1)):
                    errors.append("%s.%s:%d Exit %s dans une procédure %s" % (c.name, p["name"], no, me.group(1), p["kind"]))
                # For Each sur variable typee non objet
                mf = re.match(r"^for\s+each\s+(\w+)\s+in\b", low)
                if mf:
                    t = local_types.get(mf.group(1)) or c.mod_vars.get(mf.group(1), (None, "variant"))[1]
                    if t in ("string", "long", "integer", "double", "date", "boolean", "single", "byte", "currency"):
                        errors.append("%s.%s:%d variable de For Each de type %s (Variant ou Object requis)" % (c.name, p["name"], no, t))
                # appel parenthese a plusieurs arguments en instruction
                mp = re.match(r"^([\w.]+)\((.*)\)\s*$", st)
                if mp and not low.startswith("call ") and not _has_top_eq(st):
                    head = mp.group(1).split(".")[-1].lower()
                    if head not in ("redim", "if") and len(split_top(mp.group(2), ",")) > 1 and not re.match(r"^\w+\(.*\)\.\w+", st):
                        errors.append("%s.%s:%d appel entre parenthèses avec plusieurs arguments (utiliser Call ou retirer les parenthèses) : %s" % (c.name, p["name"], no, st[:60]))
                # Set manquant / Set abusif
                ma = re.match(r"^(set\s+)?([A-Za-z_]\w*)\s*=\s*(.+)$", st, re.I)
                if ma and not re.match(r"^(if|for|elseif|do|loop|while|case|const)\b", low):
                    var = ma.group(2).lower()
                    t = local_types.get(var) or c.mod_vars.get(var, (None, None))[1]
                    if var == p["name"].lower() and p["kind"] == "function":
                        t = p["ret"] or "variant"
                    if t in OBJ_TYPES and not ma.group(1) and not re.match(r"^nothing$", ma.group(3).strip(), re.I):
                        errors.append("%s.%s:%d affectation d'objet sans Set : %s" % (c.name, p["name"], no, st[:60]))
                    if ma.group(1) and t in ("string", "long", "integer", "double", "date", "boolean", "single"):
                        errors.append("%s.%s:%d Set sur une variable non objet : %s" % (c.name, p["name"], no, st[:60]))
                # ByRef : type de l'argument
                for nm, args in _calls(st, sigs_for(c, all_proc_sig)):
                    sig = sigs_for(c, all_proc_sig)[nm.lower()]["params"]
                    arglist = [a.strip() for a in split_top(args, ",")] if args.strip() else []
                    for idx, a in enumerate(arglist):
                        if idx >= len(sig) or sig[idx]["paramarray"]:
                            break
                        prm = sig[idx]
                        if prm.get("byval") or prm["type"] == "variant":
                            continue
                        if re.match(r"^[A-Za-z_]\w*$", a):
                            at = local_types.get(a.lower()) or c.mod_vars.get(a.lower(), (None, None))[1]
                            if at and at != prm["type"] and not (at in OBJ_TYPES and prm["type"] in ("object",)):
                                errors.append("%s.%s:%d ByRef : argument %s (%s) passé au paramètre %s (%s) de %s" % (c.name, p["name"], no, a, at, prm["name"], prm["type"], nm))

    # 6. macros referencees par chaine
    allprocs = set()
    for c in std:
        allprocs |= set(c.procs.keys())
    pats = [r'OnAction\s*=\s*"(\w+)"', r'OnTime\s+[^,]+,\s*"(\w+)"', r'Application\.Run\s+"(\w+)"', r'UI_SetAction\s+\w+,\s*"(\w+)"']
    for c in comps:
        for i, ln in enumerate(c.raw.split("\n"), 1):
            for pat in pats:
                for m in re.finditer(pat, ln):
                    if m.group(1).lower() not in allprocs:
                        errors.append("%s:%d macro inexistante référencée : %s" % (c.name, i, m.group(1)))

    for e in errors:
        print("ERREUR  " + e)
    for w in warnings:
        print("ALERTE  " + w)
    nproc = sum(len(c.procs) for c in comps)
    nlines = sum(len(c.raw.split("\n")) for c in comps)
    print("\n%d composants, %d procédures, %d lignes analysées : %d erreur(s), %d alerte(s)" % (len(comps), nproc, nlines, len(errors), len(warnings)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
