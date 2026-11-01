"""pyawk's regex matching against the recorded majority of three awks.

awk_tests/regex_differential.json holds, for every pattern of the corpus in
awk_tests/regex_corpus.py, what mawk, onetrue-awk and gawk --posix answer by
majority vote (see scripts/regex_fuzz.py, which regenerates it).  Each
pattern is one test: pyawk runs the same probe program in-process and every
recorded line must match.  Lines where the references split three ways are
recorded as ``null`` and skipped.  The references are not needed here.

KNOWN_FAILURES lists pattern keys pyawk is known to get wrong (expected
failures, ``strict`` so a fix forces the list to shrink).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "awk_tests"))
import regex_corpus as rc  # noqa: E402

RECORDS = json.loads((ROOT / "awk_tests" / "regex_differential.json").read_text())["records"]

KNOWN_FAILURES: set[str] = {
    'alternation:a|ab',
    'alternation:a|ab|abc',
    'alternation:x(a|ab)',
    'alternation:(a|ab)b',
    'alternation:b|ab|aba',
    'alternation:(a|aa)a',
    'alternation:(b|ba|bab)',
    'alternation:c|ca|cab|cabb',
    'alternation:(foo|foobar)',
    'nested:((a|ab)(c|b))',
    'nested:(a(b|bc))*',
    'nested:(a|(a(b|c)))+',
    'quantifiers:x*(xy)?',
    'quantifiers:a)',
    'quantifiers:)',
    'intervals:a{2}+',
    'intervals:a{1}?',
    'intervals:a{2}*',
    'intervals:a{,2}',
    'intervals:a{1',
    'intervals:a{1,2',
    'intervals:a{1}{2}',
    'brackets:[[=a=]]',
    'brackets:[[=a=]b]',
    'brackets:[[=ab=]]',
    'brackets:[[.ab.]]',
    'brackets:[[.space.]]',
    'escapes:\\w',
    'escapes:\\s',
    'empty:(a|)b',
    'empty:(|a)b',
    'empty:a||b',
    'empty:a|',
    'empty:|a',
    'empty:(|)',
    'empty:(a|b|)',
    'empty:(|a|b)+',
    'random:b+([ab]?|ac|c+b{0,1})|.?|(b*(b+)^)',
    'random:(c[^a]|.|(c+|a?b.).)?',
    'random:b(a|c*|(.|c{0,1}b){2,}c)[^bc]|b([^bc](a{2}bc|abc+|aa?)|(.+b+.?)*[ab]{2,})*.',
    'random:(b?)c.|c|cb+(c(bb+[^a])[^a]|.+(b+b*|b|c*)|c(b*a+|.{1,2}|b)c?)',
    'random:c.*|.(c(a)a|.{0,1}c*(b*|ab?|b))',
    'random:a|b|(^|(.*c*)?(b?a)*)b',
    'random:b*[ab](.)|^[^a]|a(cb*|(b*|ab|[bc].b).*a|a)[bc]',
    'random:c?([^bc]?a|c)a|[bc]|((b?b*a*)|^)*',
    'random:c*a|($|c)*((b?bc*))+|a?c?',
    'random:b(a?a)(b?)?|aa|[^bc]aa*',
    'random:(c[^bc]?)*((a|.*ac*))?a',
    'random:.c+((aa|b+b|c*)a)|(b[ab]?)*|(([ab]|bbb|[^bc]*)?)',
    'random:(c[ab]*|b?c*a|c.a){1,2}|a((a|b[ab]*|b)*[bc]+)*(ab?c*)|(ac).+',
    'random:(b(a{2}c))?b?|a?|(bcc|b{2,}(a[^a]|ac)*$)+bc',
    'random:((aa*c*|b.b)?){2,}|b((b|bb{0,1}b){0,1}c*)([^a]+([^a]?.*)+b)',
    'random:[^bc]|[^a].?(ab(b{1,2}c*|b.[^a]|b?aa))|(c*b?|.c|(b)a[^a]*)*',
    'random:(bc?|b+..|b[bc]c)?',
    'random:([^bc]+(ca|[^a]+b))?b*|((b?b)+c+(.ba+))c*|.c*(a)',
    'random:b*[^bc]*|b.*([^bc]+(a*[bc]*a*)c|b|bc*(b))',
    'random:(cbb)(c.+b+)b*|(ab(b|.{1,2}b?|c+a+))|b.',
    'random:.(a?a[bc]*|a?(.a*|a))b+|([^bc]).?|[ab]*(c.|b)?',
    'random:c?|b*|a+',
    'random:a?|..(^|(c{2}cb?)*.(caa*)|[bc]+(.a)c?)',
    'random:([bc](c{2,}b*.)?)?((bb?|c+|bb*)a|c*[bc]?|(a*ac*|a*c*|b?)a)+|b.|aa',
    'random:a(b+|.([ab]|b+b?)a|..{1,2}.)[^a]|c*b',
    'random:^b|b?a+|(..(ac|aba*|aba)+|(.)b)',
    'random:(c*bb+|aa|[^bc]*)|b',
    'random:((.bb?)a^)c*|((.a){2}|(b|[^bc]{2,}b*b|a+a)|(aa*|b|a*.))?((b?a*)|ca|a?(b+)*)',
    'random:([bc]*(c?a)|(ab?))?.?[^a]?',
    'random:((b))?([ab](.ca|a).+)*|..',
    'random:b+|(.*|c*(a+|[ab].).)(a|a)|b',
    'random:((a)*a+)(a*(a+|.*aa?)*|b(ba[ab]*)|(b{1,2}.)c)|c+',
    'random:(b[^bc]+)*.|cca|(a)[bc]+b?',
    'random:b?b|c{0,1}c[bc]*|b?a',
    'random:[^a]((bab)?|(acb?|a)){2,}',
    'random:((a..*|a?a|c)c*)c*a|(b*)|(b+|([bc]*b|a*)?b)([^a]+$|b{0,1}|[ab]+a.*)',
    'random:c?(.*|(ac)|(bab?)(b[^a].?)*)b|(ab+b+|.|a[^bc])',
    'random:(c+)*(a|a)*|a*b|ba',
    'random:b|(b?)*c?',
    'random:((..+b))(b[^a].){0,1}([bc]|b)|([bc]?|[^a]*(baa|ab*)b*)',
    'random:(b*a?(a?|aa*c?|a)|.b[^bc])|(a+a|(c+aa|ba)?.)b.?|c*cc',
    'random:[^a]|c((a|b[bc]b|.*[ab]a))|.',
    'random:([bc](a?a*)*a?|[^bc]*[^bc]+|ac*(a*a|a..|[^a]?b+))',
    'random:([^a][^bc]*)|a?a|..*',
    'random:.a?|[^a]b*|((c*|ab.|c)b)((a+|c)(c*ab+|[bc]{2,}bc+)*^|b+)',
    'random:[ab]*|^.+(c[bc]c)',
    'random:.?|[ab]?b*|.cb+',
    'random:[bc]+(a*b)a|a^((ba*a*|b*|b+))+|((cb*|b?b?))*b(acb?|.(aa)?)',
    'random:a+$([^a]+a*$|bcc)|(ac(a|a{2}ac|.b?b)|c{2,}|a*)^',
    'random:(c|(cc*a+)|(c|a+c?b)b+^)|a',
    'random:[^bc]*|(([ab]ac|a.a+)|^|b+(a.+a|aa)a?)((a+ca?)([^a]+c?b+|bab?|.a.*)?^|a*)[ab]',
    'random:(ab?|cb?)(ba(bba+)+|(.*ab?|a[ab]|a*.?a*)[ab]*|b(.+|[bc]b?c|a+a))?|a[bc]+|(abb)',
    'random:((a+c|b{0,1}b[bc]|abb)b*)*.',
    'random:((a+|[bc]c|c)?(ba|cab{1,2}|b.?b+))',
    'random:(a(b*a*b)(ac|bb+b|b+b*)|c+)ac|(b(b[bc]+|b+ba)(cc){1,2}|[^a]|.)((b)+c*[^a])|(c|([bc]*b|b*c|c)*ac*|(a))',
    'random:(cc)(a)|.b|(.+c|a(.*b)b*|a+(ab|b.{2,}){0,1})',
    'random:(c^|a+(a)b*|c+c)?|$(c+b(aa|[bc]bb?)|[ab][bc].|(bb?|ab?a?)+b)',
    'random:(a(.a|[^a]b|.c{0,1}b).*|(bc|b?a[^a])?(a+b*c+)*)+|(ab*)ac*|[^bc][ab]',
    'random:(c)?b|c+((b*b|a)|[ab]*a?)',
    'random:(a+)|[ab].c+|[^bc]b+',
    'random:a{0,1}(c|a*|c)|.ac+|a',
    'random:c+a{0,1}a|(.c)?([^bc]a+(a)*|a+(b+a+b)[^a]|c(a+a*|ab))(b|[ab]aa*)*|a*b',
    'random:cc?|.(a*)',
    'random:(.|a{2}b?|c(ac)?)',
    'random:(c?c{0,1}|b*)|.*',
    'random:[bc]((bb+|[^bc]+b.?)(a|ba.|bb)?|c(bb)[ab]?|ba)',
    'random:a*[ab]*b|b*([^a])+.+|b.+c',
    'random:(b*a|ac)',
}


def _param(rec: dict):
    key = f"{rec['category']}:{rec['pattern']}"
    marks = [pytest.mark.xfail(reason="regex KNOWN_FAILURES", strict=True)] if key in KNOWN_FAILURES else []
    return pytest.param(rec, id=key, marks=marks)


@pytest.mark.parametrize("rec", [_param(r) for r in RECORDS])
def test_regex_differential(rec: dict) -> None:
    entry = rc.Entry(rec["category"], rec["pattern"], tuple(rec["inputs"]))
    out = rc.run_pyawk(entry)
    got = [rc.ERROR] * len(entry.inputs) if out == rc.ERROR else out.split("\n")
    bad = [
        f"  {inp!r}: expected {exp!r}, got {g!r}"
        for inp, exp, g in zip(entry.inputs, rec["expected"], got)
        if exp is not None and g != exp
    ]
    assert not bad, f"/{rec['pattern']}/:\n" + "\n".join(bad)


def test_corpus_and_record_agree() -> None:
    """The record covers exactly the current corpus (regenerate if not)."""
    assert [(e.category, e.pattern, list(e.inputs)) for e in rc.corpus()] == [
        (r["category"], r["pattern"], r["inputs"]) for r in RECORDS
    ]
