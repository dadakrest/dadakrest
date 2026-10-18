"""Employment Application: a fillable PDF form for people applying to work at
the data center.

    python3 tools/pdf/build.py employment_application
    python3 tools/pdf/employment_application.py

Unlike the other documents this one is not printed from HTML: it needs real
form fields, so it is drawn with reportlab's platypus and AcroForm. It follows
the same look as the rest of the family (teal accent, the cover header, the
template notice and a footer on every page).

What the form takes from the data when it is built:

* the positions in the role list (section 2), and the employment types offered
  as check boxes, from the employment_fees dataset;
* the shift pattern and on-call rules quoted in section 7, from the knowledge
  base article on working hours, shifts and on-call. The build fails if the
  article no longer states them;
* the policies listed for acknowledgement in section 8: every document whose
  source is kb/employment;
* the equal opportunity statement in section 9, which restates commitments in
  the equal opportunity policy; the build fails if the policy no longer makes
  them.

The form never asks for a government identification number, a social security
or national insurance number, a date of birth or a copy of an ID. Identity and
right-to-work checks happen in person after an offer and only the fact of the
check is recorded, in the People Operations section.

Section numbers are assigned from SECTIONS when the form is built, and every
cross-reference in the text is resolved from those keys.
"""

from __future__ import annotations

import calendar
import contextlib
import io
import math
import os
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reportlab.lib.colors import HexColor, white  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.rl_accel import fp_str  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from common import ISSUED, OUT_DIR, TEMPLATE_NOTICE, esc, records, seeded_center  # noqa: E402

from datacenter import DATA_CENTER_NAME, VERSION  # noqa: E402

NAME = "employment-application"
TITLE = "Employment Application"
EMPLOYMENT_SOURCE = "kb/employment"

ID_NOTICE = (
    "Do not write government identification numbers on this application. Identity and "
    "right-to-work documents are checked in person after an offer is made, and the numbers "
    "are not recorded on this form."
)
PEOPLE_OPS_HEADING = "For People Operations use only — after an offer"
BUSINESS_NOTE_LEAD = "For the business issuing this form:"
BUSINESS_NOTE = (
    "the notice above is addressed to you, not to applicants. Once every [BRACKETED BLANK] is "
    "completed and the form has been reviewed, remove this box before you give the form to "
    "anyone applying."
)

#: Section order. A section's number is its position here, so every reference
#: to a section in the text is written as SECTION[key].
SECTIONS = (
    "applicant",
    "position",
    "eligibility",
    "experience",
    "education",
    "references",
    "availability",
    "acknowledgements",
    "equal_opportunity",
    "declaration",
    "people_ops",
)
SECTION = {key: index for index, key in enumerate(SECTIONS, start=1)}

#: Display order for employment types; a type in the data that is not listed
#: here still gets a check box, after these.
TYPE_ORDER = ("full-time", "part-time", "contract")

#: Sentences quoted from the on-call article in section 7. Each must match the
#: article exactly once, or the build fails rather than quote a rule that has
#: changed.
ON_CALL_QUOTES = (
    r"Shift technicians work [^.]*\.",
    r"The rotation is published [^.]*\.",
    r"Nobody is on call [^.]*\.",
)

#: Commitments the section 9 statement restates. The equal opportunity policy
#: must still contain each phrase.
EQUAL_OPPORTUNITY_PHRASES = (
    "written criteria agreed before any application is read",
    "same core questions of every candidate",
    "Reasonable adjustments are made at every stage",
    "never counts against anyone",
)

# -- look ------------------------------------------------------------------------

ACCENT = HexColor("#0f5f68")
INK = HexColor("#17201f")
MUTED = HexColor("#56635f")
RULE = HexColor("#cfd6d4")
TINT = HexColor("#eaf2f2")
PANEL = HexColor("#f3f6f5")
FIELD_BORDER = HexColor("#8a9894")
FIELD_FILL = HexColor("#fbfdfd")

PAGE_W, PAGE_H = letter
MARGIN_X = 0.75 * inch
MARGIN_TOP = 0.75 * inch
MARGIN_BOTTOM = 0.95 * inch
CONTENT_W = PAGE_W - 2 * MARGIN_X
FOOTER_RULE_Y = 0.62 * inch

FONT = "Helvetica"
BOLD = "Helvetica-Bold"
CONTROL_H = 18          # height of a one-line text field
TABLE_ROW_H = 18        # height of a text field in a table row
TABLE_ROW_GAP = 4
NUM_W = 26              # width of the clause-number column
CHECK = 10              # check box and radio button size
FIELD_FONT_SIZE = 9.5
#: Whole points: viewers that redraw a field once it is filled in (pdfium, and
#: others) round a fractional border width down, and a 0.75pt border vanishes.
FIELD_BORDER_W = 1
#: Line spacing PDF viewers use for FIELD_FONT_SIZE text in a multi-line field,
#: and the room taken by the border and the viewer's inner padding.
FIELD_LEADING = 11.5
FIELD_INSET = 3
#: Lines in the free-text adjustment field in the equal opportunity section:
#: room for a short paragraph while the applicant's sections still end on the
#: page before People Operations.
ADJUSTMENT_LINES = 4


def multiline_size(lines: int, width: float) -> tuple[float, int]:
    """Height and character limit for a multi-line text field of `lines` lines.

    The limit is what fits when every character is as wide as a lower-case
    'e', which is wider than the average of English text with its spaces, so
    the whole answer stays visible on screen and on a printed copy."""
    height = lines * FIELD_LEADING + 2 * FIELD_INSET
    per_line = int((width - 2 * FIELD_INSET) // stringWidth("e", FONT, FIELD_FONT_SIZE))
    return height, lines * per_line

BODY = ParagraphStyle("body", fontName=FONT, fontSize=9.5, leading=13, textColor=INK,
                      spaceAfter=5)
NUMBERED = ParagraphStyle("numbered", parent=BODY, leftIndent=NUM_W, bulletIndent=0,
                          bulletFontName=BOLD, bulletFontSize=9.5, bulletColor=ACCENT)
LABEL = ParagraphStyle("label", fontName=FONT, fontSize=8.5, leading=10.5, textColor=INK)
HINT = ParagraphStyle("hint", fontName=FONT, fontSize=8.5, leading=11.5, textColor=MUTED,
                      spaceAfter=4)
NOTICE = ParagraphStyle("notice", fontName=FONT, fontSize=9, leading=12.5, textColor=INK)
ID_STYLE = ParagraphStyle("id", fontName=FONT, fontSize=9.5, leading=13, textColor=INK)
CHECK_LABEL = ParagraphStyle("check", fontName=FONT, fontSize=9, leading=11.5, textColor=INK)


def issued_text() -> str:
    return f"{ISSUED:%B} {ISSUED.day}, {ISSUED.year}"


def number(label: str) -> str:
    """A clause number in the accent color, for use inside paragraph markup."""
    return f'<font name="{BOLD}" color="#0f5f68">{esc(label)}</font>'


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def type_label(employment_type: str) -> str:
    return employment_type[:1].upper() + employment_type[1:]


# -- data -----------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    external_id: str
    title: str


@dataclass(frozen=True)
class FormData:
    roles: list[dict[str, str]]
    employment_types: list[str]
    policies: list[Policy]
    on_call_title: str
    on_call_quotes: list[str]
    equal_opportunity_title: str


def _only(items: list[dict[str, Any]], test: Callable[[dict[str, Any]], bool], what: str) -> dict[str, Any]:
    found = [item for item in items if test(item)]
    if len(found) != 1:
        raise RuntimeError(f"expected exactly one {what} in {EMPLOYMENT_SOURCE}, found {len(found)}")
    return found[0]


def _plain(body: str) -> str:
    """Article text without markdown emphasis or code marks, on one line."""
    return " ".join(re.sub(r"[*`]", "", body).split())


def load() -> FormData:
    with seeded_center() as center:
        fees = records(center, "employment_fees")
        documents = [d for d in center.list_documents() if d["source"] == EMPLOYMENT_SOURCE]
        on_call = _only(documents, lambda d: "on-call" in d["title"].lower(), "on-call article")
        equal = _only(documents, lambda d: "equal opportunity" in d["title"].lower(),
                      "equal opportunity policy")
        on_call_doc = center.get_document(on_call["id"])
        equal_doc = center.get_document(equal["id"])
    if on_call_doc is None or equal_doc is None:
        raise RuntimeError("an employment policy listed in the documents database has no body")

    roles = []
    for fee in sorted(fees, key=lambda f: str(f["fee_code"])):
        role = str(fee.get("role") or "").strip()
        employment_type = str(fee.get("employment_type") or "").strip()
        if not role or not employment_type:
            raise RuntimeError(f"employment_fees row {fee.get('fee_code')} has no role or type")
        roles.append({"role": role, "employment_type": employment_type,
                      "fee_code": str(fee["fee_code"])})
    names = [r["role"] for r in roles]
    if len(set(names)) != len(names):
        raise RuntimeError("employment_fees lists the same role twice")
    if not roles:
        raise RuntimeError("employment_fees has no roles")

    present = {r["employment_type"] for r in roles}
    types = [t for t in TYPE_ORDER if t in present] + sorted(present - set(TYPE_ORDER))

    on_call_text = _plain(on_call_doc["body"])
    quotes = []
    for pattern in ON_CALL_QUOTES:
        matches = re.findall(pattern, on_call_text)
        if len(matches) != 1:
            raise RuntimeError(f"{on_call['title']!r} no longer states /{pattern}/ exactly once")
        quotes.append(matches[0])

    equal_text = _plain(equal_doc["body"])
    for phrase in EQUAL_OPPORTUNITY_PHRASES:
        if phrase not in equal_text:
            raise RuntimeError(f"{equal['title']!r} no longer says {phrase!r}")

    policies = [Policy(d["external_id"], d["title"])
                for d in sorted(documents, key=lambda d: d["external_id"])]
    return FormData(roles=roles, employment_types=types, policies=policies,
                    on_call_title=on_call["title"], on_call_quotes=quotes,
                    equal_opportunity_title=equal["title"])


# -- form fields ----------------------------------------------------------------

class Registry:
    """Hands out field names and refuses a name used twice."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def claim(self, name: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"field name {name!r} is not a plain snake_case name")
        if name in self.names:
            raise ValueError(f"field name {name!r} is used twice")
        self.names.append(name)
        return name


def text_field(canv: Any, registry: Registry, name: str, tooltip: str, x: float, y: float,
               width: float, height: float = CONTROL_H, *, multiline: bool = False,
               maxlen: int = 200) -> None:
    canv.acroForm.textfield(
        name=registry.claim(name), tooltip=tooltip, value="",
        x=x, y=y, width=width, height=height, relative=True,
        borderStyle="solid", borderWidth=FIELD_BORDER_W, borderColor=FIELD_BORDER,
        fillColor=FIELD_FILL, textColor=INK, fontName=FONT, fontSize=FIELD_FONT_SIZE,
        fieldFlags="multiline" if multiline else "", maxlen=maxlen, forceBorder=True,
    )


def check_box(canv: Any, registry: Registry, name: str, tooltip: str, x: float, y: float) -> None:
    canv.acroForm.checkbox(
        name=registry.claim(name), tooltip=tooltip, checked=False,
        x=x, y=y, size=CHECK, relative=True, buttonStyle="check",
        borderStyle="solid", borderWidth=FIELD_BORDER_W, borderColor=FIELD_BORDER,
        fillColor=white, textColor=INK, fieldFlags="", forceBorder=True,
    )


def circle_path(size: float, r: float, arcs: tuple[int, ...] = (0, 1, 2, 3),
                rotated: bool = False) -> str:
    """A circle of radius r centered in a size-by-size box, as PDF path operators.

    Stands in for reportlab's AcroForm.circleArcStream, which scales the circle
    by size/20 a second time: below 20pt a radio button's ring and dot come out
    a fraction of their size in one corner of the button and are clipped. Same
    arguments and output, without the extra scaling."""
    k = 0.55231 * r  # control-point distance for a quarter circle
    quarters = {
        0: ((r, 0), (r, k, k, r, 0, r)),
        1: ((0, r), (-k, r, -r, k, -r, 0)),
        2: ((-r, 0), (-r, -k, -k, -r, 0, -r)),
        3: ((0, -r), (k, -r, r, -k, r, 0)),
    }
    matrix = "0.7071 0.7071 -0.7071 0.7071" if rotated else "1 0 0 1"
    ops = [f"{matrix} {fp_str(size / 2, size / 2)} cm"]
    for arc in arcs:
        start, curve = quarters[arc]
        if len(ops) == 1:
            ops.append(f"{fp_str(*start)} m")
        ops.append(f"{fp_str(*curve)} c")
    return "\n".join(ops)


def radio_button(canv: Any, group: str, value: str, tooltip: str, x: float, y: float) -> None:
    """One button of a radio group. Every button of a group shares the group's
    name, so a viewer lets exactly one of them be on. The caller claims the
    group name from the Registry once, before drawing the first button."""
    canv.acroForm.circleArcStream = circle_path
    canv.acroForm.radio(
        name=group, value=value, selected=False, tooltip=tooltip,
        x=x, y=y, size=CHECK, relative=True, buttonStyle="circle", shape="circle",
        borderStyle="solid", borderWidth=FIELD_BORDER_W, borderColor=FIELD_BORDER,
        fillColor=white, textColor=INK, fieldFlags="noToggleToOff radio", forceBorder=True,
    )


#: The "nothing chosen yet" entry at the top of a choice list. It is a space
#: rather than an empty string because reportlab cannot draw a choice field
#: whose value is empty, and a space leaves the box blank on a printed copy.
NO_CHOICE = " "


def choice_field(canv: Any, registry: Registry, name: str, tooltip: str, options: list[str],
                 x: float, y: float, width: float, height: float = CONTROL_H) -> None:
    canv.acroForm.choice(
        name=registry.claim(name), tooltip=tooltip, value=NO_CHOICE,
        options=[NO_CHOICE, *options],
        x=x, y=y, width=width, height=height, relative=True,
        borderStyle="solid", borderWidth=FIELD_BORDER_W, borderColor=FIELD_BORDER,
        fillColor=FIELD_FILL, textColor=INK, fontName=FONT, fontSize=FIELD_FONT_SIZE,
        fieldFlags="combo", forceBorder=True,
    )


# -- flowables ------------------------------------------------------------------

class Cover(Flowable):
    """Eyebrow, title, subtitle and the business name over an accent rule."""

    def __init__(self, eyebrow: str, title: str, subtitle: str, meta: str) -> None:
        super().__init__()
        self.eyebrow, self.title, self.subtitle, self.meta = eyebrow, title, subtitle, meta
        self.height_ = 84

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        return avail_width, self.height_

    def draw(self) -> None:
        canv = self.canv
        top = self.height_
        canv.saveState()            # the eyebrow's letter spacing must not carry on
        text = canv.beginText(0, top - 8.5)
        text.setFont(BOLD, 8.5)
        text.setFillColor(ACCENT)
        text.setCharSpace(1.0)
        text.textOut(self.eyebrow.upper())
        canv.drawText(text)
        canv.restoreState()
        canv.setFillColor(INK)
        canv.setFont(BOLD, 22)
        canv.drawString(0, top - 35, self.title)
        canv.setFillColor(MUTED)
        canv.setFont(FONT, 11)
        canv.drawString(0, top - 52, self.subtitle)
        canv.setFont(FONT, 9)
        canv.drawString(0, top - 66, self.meta)
        canv.setStrokeColor(ACCENT)
        canv.setLineWidth(2)
        canv.line(0, 1, self.width, 1)


class SectionHeading(Flowable):
    """'4  Experience' in the accent color with an accent rule beneath."""

    def __init__(self, key: str, title: str) -> None:
        super().__init__()
        self.label = f"{SECTION[key]}"
        self.title = title
        self.spaceBefore = 13
        self.spaceAfter = 6

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        return avail_width, 20

    def draw(self) -> None:
        canv = self.canv
        canv.setFillColor(ACCENT)
        canv.setFont(BOLD, 12.5)
        canv.drawString(0, 5.5, self.label)
        canv.drawString(22, 5.5, self.title)
        canv.setStrokeColor(ACCENT)
        canv.setLineWidth(1.2)
        canv.line(0, 0.6, self.width, 0.6)


@dataclass
class Cell:
    """One labelled control in a FieldRow."""

    kind: str                      # "text", "choice" or "checks"
    clause: str
    label: str
    share: float
    name: str = ""
    options: list[str] = field(default_factory=list)
    checks: list[tuple[str, str]] = field(default_factory=list)
    height: float = CONTROL_H
    multiline: bool = False
    maxlen: int = 200


class FieldRow(Flowable):
    """Labelled controls side by side; each label sits above its control."""

    GAP = 12

    def __init__(self, registry: Registry, cells: list[Cell]) -> None:
        super().__init__()
        total = sum(c.share for c in cells)
        if abs(total - 1) > 1e-6:
            raise ValueError(f"cell shares add up to {total}, not 1")
        self.registry, self.cells = registry, cells
        self.spaceAfter = 6

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        usable = avail_width - self.GAP * (len(self.cells) - 1)
        self.widths = [usable * c.share for c in self.cells]
        self.labels = [Paragraph(f"{number(c.clause)}&nbsp;&nbsp;{esc(c.label)}", LABEL)
                       for c in self.cells]
        self.label_h = max(p.wrap(w, avail_height)[1] for p, w in zip(self.labels, self.widths))
        self.control_h = max(c.height for c in self.cells)
        self.height = self.label_h + 3 + self.control_h
        return avail_width, self.height

    def draw(self) -> None:
        canv = self.canv
        x = 0.0
        for cell, width, label in zip(self.cells, self.widths, self.labels):
            label.drawOn(canv, x, self.control_h + 3 + (self.label_h - label.height))
            y = self.control_h - cell.height
            tip = f"{cell.clause} {cell.label}"
            if cell.kind == "text":
                text_field(canv, self.registry, cell.name, tip, x, y, width, cell.height,
                           multiline=cell.multiline, maxlen=cell.maxlen)
            elif cell.kind == "choice":
                choice_field(canv, self.registry, cell.name, tip, cell.options, x, y, width,
                             cell.height)
            elif cell.kind == "checks":
                cx = x
                for name, text in cell.checks:
                    check_box(canv, self.registry, name, f"{tip}: {text}", cx,
                              y + (cell.height - CHECK) / 2)
                    canv.setFillColor(INK)
                    canv.setFont(FONT, 9)
                    canv.drawString(cx + CHECK + 4, y + (cell.height - CHECK) / 2 + 2, text)
                    cx += CHECK + 4 + stringWidth(text, FONT, 9) + 16
                if cx - 16 > x + width:
                    raise ValueError(f"check boxes for {cell.clause} do not fit their cell")
            else:
                raise ValueError(f"unknown cell kind {cell.kind!r}")
            x += width + self.GAP


class FieldTable(Flowable):
    """Numbered rows of text fields under column headings (employers, references)."""

    GAP = 6
    HEAD_H = 13

    def __init__(self, registry: Registry, key: str, prefix: str,
                 columns: list[tuple[str, str, float]], rows: int) -> None:
        super().__init__()
        total = sum(share for _, _, share in columns)
        if abs(total - 1) > 1e-6:
            raise ValueError(f"column shares add up to {total}, not 1")
        self.registry, self.section = registry, SECTION[key]
        self.prefix, self.columns, self.rows = prefix, columns, rows
        self.spaceAfter = 6

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        usable = avail_width - NUM_W - self.GAP * (len(self.columns) - 1)
        self.widths = [usable * share for _, _, share in self.columns]
        self.height = self.HEAD_H + 4 + self.rows * (TABLE_ROW_H + TABLE_ROW_GAP)
        return avail_width, self.height

    def draw(self) -> None:
        canv = self.canv
        canv.setFont(BOLD, 8)
        canv.setFillColor(MUTED)
        x = NUM_W
        for (heading, _, _), width in zip(self.columns, self.widths):
            canv.drawString(x, self.height - 9, heading)
            x += width + self.GAP
        canv.setStrokeColor(INK)
        canv.setLineWidth(0.75)
        canv.line(0, self.height - self.HEAD_H, self.width, self.height - self.HEAD_H)
        for row in range(1, self.rows + 1):
            top = self.height - self.HEAD_H - 4 - (row - 1) * (TABLE_ROW_H + TABLE_ROW_GAP)
            y = top - TABLE_ROW_H
            canv.setFont(BOLD, 9)
            canv.setFillColor(ACCENT)
            canv.drawString(0, y + 5.5, f"{self.section}.{row}")
            x = NUM_W
            for (heading, key, _), width in zip(self.columns, self.widths):
                text_field(canv, self.registry, f"{self.prefix}_{row}_{key}",
                           f"{self.section}.{row} {heading}", x, y, width, TABLE_ROW_H)
                x += width + self.GAP


class CheckList(Flowable):
    """Check boxes, each with a numbered label, in one or more columns.

    Items run down the first column before the second, so numbers read in order.
    """

    GAP = 18
    ROW_GAP = 4

    def __init__(self, registry: Registry, items: list[tuple[str, str, str]], columns: int = 1) -> None:
        super().__init__()
        self.registry, self.items, self.columns = registry, items, columns
        self.spaceAfter = 6

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        self.col_w = (avail_width - self.GAP * (self.columns - 1)) / self.columns
        self.per_col = math.ceil(len(self.items) / self.columns)
        self.paras = []
        for _, _, markup in self.items:
            p = Paragraph(markup, CHECK_LABEL)
            p.wrap(self.col_w - NUM_W - CHECK - 6, avail_height)
            self.paras.append(p)
        self.row_h = []
        for row in range(self.per_col):
            cells = [self.paras[c * self.per_col + row] for c in range(self.columns)
                     if c * self.per_col + row < len(self.paras)]
            self.row_h.append(max(max(p.height for p in cells), CHECK + 1))
        self.height = sum(self.row_h) + self.ROW_GAP * (self.per_col - 1)
        return avail_width, self.height

    def draw(self) -> None:
        canv = self.canv
        for index, ((label, name, _), para) in enumerate(zip(self.items, self.paras)):
            col, row = divmod(index, self.per_col)
            top = self.height - sum(self.row_h[:row]) - self.ROW_GAP * row
            x = col * (self.col_w + self.GAP)
            canv.setFont(BOLD, 9)
            canv.setFillColor(ACCENT)
            canv.drawString(x, top - 8.6, label)
            check_box(canv, self.registry, name, f"{label} {para.getPlainText()}", x + NUM_W,
                      top - CHECK - 0.5)
            para.drawOn(canv, x + NUM_W + CHECK + 6, top - para.height)


class YesNo(Flowable):
    """A numbered statement with a Yes / No choice on the right.

    The two answers are one radio group, so an applicant can choose one of them
    and never both. The group, not each answer, is the form field."""

    BOX_W = 104
    ANSWERS = ("Yes", "No")

    def __init__(self, registry: Registry, clause: str, statement: str, name: str) -> None:
        super().__init__()
        self.registry, self.clause, self.statement, self.name = registry, clause, statement, name
        self.spaceAfter = 6

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        self.para = Paragraph(esc(self.statement), BODY)
        self.para.wrap(avail_width - NUM_W - self.BOX_W, avail_height)
        self.height = max(self.para.height, CHECK + 2)
        return avail_width, self.height

    def draw(self) -> None:
        canv = self.canv
        top = self.height
        canv.setFont(BOLD, 9.5)
        canv.setFillColor(ACCENT)
        canv.drawString(0, top - 9.5, self.clause)
        self.para.drawOn(canv, NUM_W, top - self.para.height)
        x = self.width - self.BOX_W + 14
        group = self.registry.claim(self.name)
        tooltip = f"{self.clause} {self.statement} Choose Yes or No."
        for answer in self.ANSWERS:
            radio_button(canv, group, answer.lower(), tooltip, x, top - CHECK - 1)
            canv.setFillColor(INK)
            canv.setFont(FONT, 9.5)
            canv.drawString(x + CHECK + 4, top - 9.5, answer)
            x += 46


def boxed(content: list[Flowable], commands: list[tuple], *, pad: float = 9) -> Table:
    table = Table([[content]], colWidths=[CONTENT_W])
    table.setStyle(TableStyle(commands + [
        ("LEFTPADDING", (0, 0), (-1, -1), pad + 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad + 1),
        ("TOPPADDING", (0, 0), (-1, -1), pad - 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad - 2),
    ]))
    return table


def template_notice() -> Table:
    """The shared template notice, and a note that it is for the business.

    The notice tells whoever signs to take legal advice first. On this form the
    person who signs is the applicant, so the business is told to remove the
    box once the form has been reviewed, rather than issue it to applicants."""
    return boxed([
        Paragraph(esc(TEMPLATE_NOTICE), NOTICE),
        Spacer(1, 4),
        Paragraph(f"<b>{esc(BUSINESS_NOTE_LEAD)}</b> {esc(BUSINESS_NOTE)}", NOTICE),
    ], [("BACKGROUND", (0, 0), (-1, -1), TINT),
        ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT)])


def id_notice() -> Table:
    heading = ParagraphStyle("id-head", parent=ID_STYLE, fontName=BOLD, textColor=ACCENT,
                             spaceAfter=3)
    return boxed([Paragraph("Identity documents", heading), Paragraph(esc(ID_NOTICE), ID_STYLE)],
                 [("BACKGROUND", (0, 0), (-1, -1), TINT),
                  ("BOX", (0, 0), (-1, -1), 1.5, ACCENT)], pad=10)


def clause(label: str, markup: str, style: ParagraphStyle = NUMBERED) -> Paragraph:
    return Paragraph(markup, style, bulletText=label)


# -- the form -------------------------------------------------------------------

def story(data: FormData, registry: Registry) -> list[Flowable]:
    s = SECTION
    flow: list[Flowable] = [
        Cover("Form", TITLE,
              "Fillable form. Complete it in a PDF reader, or print it and write in the boxes.",
              DATA_CENTER_NAME),
        Spacer(1, 14),
        template_notice(),
        Spacer(1, 8),
        Paragraph(
            f"Return the completed form to [PEOPLE OPERATIONS CONTACT]. Sections {s['applicant']} "
            f"to {s['declaration']} are for the applicant. Section {s['people_ops']} is completed by "
            "People Operations after an offer has been made. Leave blank anything that does not "
            "apply to you.", BODY),
    ]

    # 1 Applicant details
    a = s["applicant"]
    flow.append(KeepTogether([
        SectionHeading("applicant", "Applicant details"),
        FieldRow(registry, [
            Cell("text", f"{a}.1", "Full name", 0.6, "applicant_full_name"),
            Cell("text", f"{a}.2", "Preferred name, if different", 0.4, "applicant_preferred_name"),
        ]),
        FieldRow(registry, [
            Cell("text", f"{a}.3", "Address", 0.62, "applicant_address"),
            Cell("text", f"{a}.4", "City", 0.38, "applicant_city"),
        ]),
        FieldRow(registry, [
            Cell("text", f"{a}.5", "Postal code", 0.22, "applicant_postal_code"),
            Cell("text", f"{a}.6", "Phone", 0.3, "applicant_phone"),
            Cell("text", f"{a}.7", "Email", 0.48, "applicant_email"),
        ]),
    ]))

    # 2 Position
    p = s["position"]
    role_names = [r["role"] for r in data.roles]
    groups = []
    for employment_type in data.employment_types:
        in_type = [r["role"] for r in data.roles if r["employment_type"] == employment_type]
        groups.append(f"<b>{esc(type_label(employment_type))} ({len(in_type)}):</b> "
                      + esc(", ".join(in_type)) + ".")
    flow.append(KeepTogether([
        SectionHeading("position", "Position"),
        FieldRow(registry, [
            Cell("choice", f"{p}.1", "Position applied for (choose from the list)", 0.62,
                 "position_applied_for",
                 options=role_names),
            Cell("text", f"{p}.2", "Desired start date", 0.38, "position_desired_start_date"),
        ]),
        Paragraph(f"The list in {p}.1 holds {len(role_names)} positions. On a printed copy, write "
                  "one of them in the box. " + " ".join(groups), HINT),
        Spacer(1, 3),
        FieldRow(registry, [
            Cell("checks", f"{p}.3", "Employment type (check each you would accept)", 0.55,
                 checks=[(f"employment_type_{slug(t)}", type_label(t))
                         for t in data.employment_types]),
            Cell("text", f"{p}.4", "Salary expectation (state the currency and period)", 0.45,
                 "position_salary_expectation"),
        ]),
    ]))

    # 3 Work eligibility
    e = s["eligibility"]
    flow.append(KeepTogether([
        SectionHeading("eligibility", "Work eligibility"),
        id_notice(),
        Spacer(1, 9),
        YesNo(registry, f"{e}.1",
              "I am legally authorized to work in the country where this role is based.",
              "eligibility_authorized_to_work"),
        YesNo(registry, f"{e}.2", "I will require sponsorship.",
              "eligibility_requires_sponsorship"),
    ]))

    # 4 Experience
    flow.append(KeepTogether([
        SectionHeading("experience", "Experience"),
        Paragraph("Start with your most recent employer. Include paid, unpaid and contract work.",
                  HINT),
        FieldTable(registry, "experience", "experience", [
            ("Employer", "employer", 0.27),
            ("Job title", "job_title", 0.23),
            ("Dates (from – to)", "dates", 0.18),
            ("Reason for leaving", "reason_for_leaving", 0.32),
        ], rows=3),
    ]))

    # 5 Education and certifications
    flow.append(KeepTogether([
        SectionHeading("education", "Education and certifications"),
        Paragraph("Include degrees, vocational qualifications and industry certifications. For a "
                  "certification that expires, give the date it is valid until.", HINT),
        FieldTable(registry, "education", "education", [
            ("Institution or awarding body", "institution", 0.38),
            ("Qualification or certification", "qualification", 0.38),
            ("Status or valid until", "status", 0.24),
        ], rows=3),
    ]))

    # 6 References
    flow.append(KeepTogether([
        SectionHeading("references", "References"),
        Paragraph("Give two people who have supervised or worked closely with you, and let them "
                  "know you have listed them.", HINT),
        FieldTable(registry, "references", "reference", [
            ("Name", "name", 0.30),
            ("Relationship to you", "relationship", 0.30),
            ("Phone or email", "phone_or_email", 0.40),
        ], rows=2),
    ]))

    # 7 Availability for shift and on-call work
    v = s["availability"]
    quote = " ".join(esc(q) for q in data.on_call_quotes)
    flow.append(KeepTogether([
        SectionHeading("availability", "Availability for shift and on-call work"),
        boxed([Paragraph(f"From the policy “{esc(data.on_call_title)}”: “{quote}”", HINT)],
              [("LINEBEFORE", (0, 0), (0, -1), 1.5, RULE)], pad=6),
        Spacer(1, 7),
        CheckList(registry, [
            (f"{v}.1", "availability_shift_work",
             "I can work the shift pattern described above, including nights."),
            (f"{v}.2", "availability_on_call",
             "I can join the on-call rotation described above."),
            (f"{v}.3", "availability_core_hours_only",
             "I am looking for a role without shift or on-call work."),
        ]),
        Spacer(1, 2),
        FieldRow(registry, [
            Cell("text", f"{v}.4", "Times you cannot work, if any (optional)", 1.0,
                 "availability_times_unavailable"),
        ]),
    ]))

    # 8 Acknowledgements
    k = s["acknowledgements"]
    flow.append(KeepTogether([
        SectionHeading("acknowledgements", "Acknowledgements"),
        Paragraph(f"Check each of the {len(data.policies)} employment policies you have read. "
                  "Copies are at [WHERE THE POLICIES ARE PUBLISHED].", HINT),
        Spacer(1, 2),
        CheckList(registry, [
            (f"{k}.{i}", f"acknowledged_{slug(policy.title)}", esc(policy.title))
            for i, policy in enumerate(data.policies, start=1)
        ], columns=2),
    ]))

    # 9 Equal opportunity
    q = s["equal_opportunity"]
    adjustment_h, adjustment_max = multiline_size(ADJUSTMENT_LINES, CONTENT_W)
    flow.append(KeepTogether([
        SectionHeading("equal_opportunity", "Equal opportunity"),
        clause(f"{q}.1",
               f"{esc(DATA_CENTER_NAME)} decides who to hire on the work the role requires and "
               "nothing else. Applications are shortlisted against written criteria agreed before "
               "any application is read, and every candidate interviewed is asked the same core "
               "questions. Reasonable adjustments are made at every stage of recruitment, and "
               "asking for one never counts against you. The full policy is "
               f"“{esc(data.equal_opportunity_title)}”."),
        Spacer(1, 2),
        FieldRow(registry, [
            Cell("text", f"{q}.2",
                 "If you need an adjustment to take part in the recruitment process, describe it "
                 "here (optional)", 1.0, "recruitment_adjustments", height=adjustment_h,
                 multiline=True, maxlen=adjustment_max),
        ]),
    ]))

    # 10 Applicant declaration
    d = s["declaration"]
    statements = [
        "The information I have given on this form is true and complete to the best of my "
        "knowledge.",
        "I understand that a statement on this form that I knew to be false may lead to an "
        "offer being withdrawn.",
        "I understand that the information on this form is used to assess my application, and "
        "is kept and deleted as described in [PRIVACY NOTICE OR RECORDS RETENTION POLICY].",
    ]
    declaration: list[Flowable] = [SectionHeading("declaration", "Applicant declaration")]
    declaration += [clause(f"{d}.{i}", esc(text)) for i, text in enumerate(statements, start=1)]
    n = len(statements)
    declaration += [
        Spacer(1, 2),
        CheckList(registry, [(f"{d}.{n + 1}", "declaration_confirmed",
                              f"I confirm the statements in {d}.1 to {d}.{n}.")]),
        Spacer(1, 4),
        FieldRow(registry, [
            Cell("text", f"{d}.{n + 2}", "Signature", 0.65, "applicant_signature", height=24),
            Cell("text", f"{d}.{n + 3}", "Date", 0.35, "applicant_signature_date", height=24),
        ]),
    ]
    flow.append(KeepTogether(declaration))

    # 11 For People Operations use only
    o = s["people_ops"]
    panel = boxed([
        Paragraph("Complete this section only after an offer has been made. Record that each "
                  "check was done in person, not what was seen: do not write document numbers on "
                  "this form or attach copies of documents to it (see section "
                  f"{s['eligibility']}).", BODY),
        Spacer(1, 3),
        CheckList(registry, [
            (f"{o}.1", "people_ops_identity_verified_in_person", "Identity verified in person"),
            (f"{o}.2", "people_ops_right_to_work_verified_in_person",
             "Right-to-work verified in person"),
        ], columns=2),
        Spacer(1, 4),
        FieldRow(registry, [
            Cell("text", f"{o}.3", "Verified by", 0.62, "people_ops_verified_by"),
            Cell("text", f"{o}.4", "Date", 0.38, "people_ops_verification_date"),
        ]),
        Paragraph("Note for the business: which documents may be accepted, how they must be "
                  "checked, and whether a separate record of the check must be kept differ "
                  "between jurisdictions. Have this procedure reviewed by your attorney before "
                  "relying on it.", HINT),
    ], [("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.75, MUTED)], pad=10)
    flow.append(KeepTogether([
        SectionHeading("people_ops", PEOPLE_OPS_HEADING),
        Spacer(1, 2),
        panel,
    ]))
    return flow


# -- pages ----------------------------------------------------------------------

def footer(total: int | None) -> Callable[[Any, Any], None]:
    def draw(canv: Any, doc: Any) -> None:
        canv.saveState()
        canv.setStrokeColor(RULE)
        canv.setLineWidth(0.5)
        canv.line(MARGIN_X, FOOTER_RULE_Y, PAGE_W - MARGIN_X, FOOTER_RULE_Y)
        canv.setFillColor(MUTED)
        canv.setFont(FONT, 7.5)
        baseline = FOOTER_RULE_Y - 11
        canv.drawString(MARGIN_X, baseline, f"{DATA_CENTER_NAME} — {TITLE}")
        page = f"Page {doc.page} of {total}" if total else f"Page {doc.page}"
        canv.drawRightString(PAGE_W - MARGIN_X, baseline,
                             f"Issued {issued_text()} · v{VERSION} · {page}")
        canv.restoreState()
    return draw


@contextlib.contextmanager
def issue_date_stamp() -> Iterator[None]:
    """Give the PDF's creation and modification dates as the issue date.

    The form is built in reportlab's invariant mode so that an unchanged form
    rebuilds to the same file, and that mode dates every PDF 1 January 2000
    unless SOURCE_DATE_EPOCH (the reproducible-builds convention, which
    reportlab reads) names another moment. Set it to the issue date while the
    form is built, then put back whatever was there before."""
    previous = os.environ.get("SOURCE_DATE_EPOCH")
    os.environ["SOURCE_DATE_EPOCH"] = str(calendar.timegm(ISSUED.timetuple()))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("SOURCE_DATE_EPOCH", None)
        else:
            os.environ["SOURCE_DATE_EPOCH"] = previous


def render_form(data: FormData, total: int | None) -> tuple[bytes, int, list[str]]:
    buffer = io.BytesIO()
    registry = Registry()
    doc = BaseDocTemplate(
        buffer, pagesize=letter, title=TITLE, author=DATA_CENTER_NAME,
        subject=f"{TITLE} — fillable form", creator=DATA_CENTER_NAME,
        leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM, invariant=1,
    )
    frame = Frame(MARGIN_X, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="body")
    doc.addPageTemplates([PageTemplate(id="form", frames=[frame], onPage=footer(total))])
    with issue_date_stamp():
        doc.build(story(data, registry))
    return buffer.getvalue(), doc.page, registry.names


def build() -> Path:
    data = load()
    # The footer says "Page n of N", so lay the form out once to count the pages.
    _, pages, _ = render_form(data, None)
    pdf, final_pages, names = render_form(data, pages)
    if final_pages != pages:
        raise RuntimeError(f"page count changed between passes ({pages} then {final_pages})")
    forbidden = re.compile(r"birth|social_security|national_insurance|passport|licen[cs]e|"
                           r"id_number|document_number|ssn", re.I)
    if any(forbidden.search(name) for name in names):
        raise RuntimeError("the form must not ask for identity numbers or a date of birth")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"{NAME}.pdf"
    target.write_bytes(pdf)
    return target


if __name__ == "__main__":
    print(build())
