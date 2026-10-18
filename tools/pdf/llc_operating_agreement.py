"""LLC Operating Agreement: a template operating agreement for the business as a
limited liability company.

    python3 tools/pdf/build.py llc_operating_agreement
    python3 tools/pdf/llc_operating_agreement.py

This is a template for the Members and their attorney, not a finished
agreement. The company's legal name, its state, filing date and number,
registered agent, offices, tax identification number, every Member's details,
and every threshold, period and amount the Members must agree are left as a
visible [BRACKETED BLANK] or a line to write on. Nothing in it names a state,
cites a statute or form, or states what the law of any jurisdiction requires.

What the template takes from the data when it is built:

* the business's working name, from datacenter.DATA_CENTER_NAME;
* the lines of business in the purpose clause: the service categories in the
  service_fees dataset, in rate-card order, and the number of services;
* the platform a Member may contribute as property: the number and names of
  its databases, from datacenter.config.DATABASES;
* in the formation checklist, the number of government fee lines in the
  cost-planning data, which the checklist flags as illustrative.

Clauses are numbered when the document is built and cross-references are
resolved from clause keys, so adding or moving a clause renumbers every
reference to it.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ISSUED,
    TEMPLATE_NOTICE,
    blank,
    document,
    esc,
    records,
    render,
    seeded_center,
    table,
)

from datacenter import DATA_CENTER_NAME  # noqa: E402
from datacenter.config import DATABASES  # noqa: E402

NAME = "llc-operating-agreement"
TITLE = "LLC Operating Agreement"
LEGAL_NAME = "COMPANY LEGAL NAME"
SERVICE_TABLE = "service_fees"
GOVERNMENT_TABLE = "government_fees"

#: Blank rows in the Members table, and Member signature blocks to match.
MEMBER_ROWS = 4
#: Blank rows in the Managers table, and Manager acceptance blocks to match.
MANAGER_ROWS = 2

NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven", "twelve")

#: Text that must never reach the template: unresolved values, and anything
#: shaped like an identification number or a citation to a statute or form.
FORBIDDEN = (
    (r"\bNone\b", "an unfilled value"),
    (r"\[\[", "an unresolved cross-reference"),
    (r"\b\d{2}-\d{7}\b", "something shaped like an EIN"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "something shaped like a personal taxpayer number"),
    (r"§|\bU\.S\.C\b|\bC\.F\.R\b|\bSection \d|\bForm [A-Z]*-?\d", "a citation to a statute or form"),
)

EXTRA_CSS = """
<style>
:root { --fill:#fff3cf; }
.fill { background: var(--fill); padding: 0 1.5pt; border-radius: 1.5pt;
        -webkit-box-decoration-break: clone; box-decoration-break: clone; }
.preamble, section.article { font-size: 9.5pt; line-height: 1.36; }
.preamble p { margin-bottom: 6pt; }
.drafting { border: 0.75pt solid var(--rule); border-radius: 3pt; padding: 7pt 10pt 3pt;
            font-size: 9pt; margin: 4pt 0 6pt; break-inside: avoid; }
.drafting h3 { margin: 0 0 4pt; font-size: 9.5pt; }
.drafting ul { margin-bottom: 4pt; }
section.article h2 { font-size: 11.5pt; margin: 10pt 0 4pt; padding-bottom: 2pt; }
section.article h3 { font-size: 10pt; }
.lead { break-inside: avoid; }
.cl { position: relative; padding-left: 30pt; margin: 0 0 4pt; break-inside: avoid; }
.cl > .n { position: absolute; left: 0; top: 0; font-weight: 700; }
.cl p { margin: 0 0 4pt; }
.cl p:last-child { margin-bottom: 0; }
.cl .runin { font-weight: 700; }
.keep { break-inside: avoid; }
ol.alpha { list-style: none; counter-reset: alpha; padding-left: 22pt; margin: 3pt 0 0; }
ol.alpha > li { counter-increment: alpha; position: relative; margin-bottom: 2pt; }
ol.alpha > li::before { content: "(" counter(alpha, lower-alpha) ")"; position: absolute;
                        left: -22pt; }
dl.defs { margin: 3pt 0 0; }
dl.defs dt { display: inline; font-weight: 700; }
dl.defs dd { display: inline; margin: 0; }
dl.defs div { margin: 0 0 3pt; break-inside: avoid; }
.details { display: grid; grid-template-columns: 2.05in 1fr; column-gap: 10pt; row-gap: 5pt;
           align-items: end; border: 0.75pt solid var(--rule); border-radius: 3pt;
           padding: 6pt 10pt 8pt; margin: 4pt 0 2pt; break-inside: avoid; font-size: 9pt; }
.details .label { color: var(--muted); line-height: 1.25; }
.details .value { display: flex; align-items: flex-end; gap: 6pt; min-height: 13pt; }
.details .value .blank { flex: 1 1 auto; width: auto !important; min-width: 1.4in; }
.details .value .after { flex: 0 0 auto; color: var(--muted); font-size: 8.5pt; white-space: nowrap; }
.details .text { display: block; line-height: 1.3; }
.details .text b { font-weight: 700; }
.details .text .note { color: var(--muted); }
.field { display: flex; align-items: flex-end; gap: 6pt; margin: 7pt 0 0; }
.field .label { white-space: nowrap; font-size: 9pt; color: var(--muted); }
.field .blank { flex: 1 1 auto; width: auto !important; }
.field.tall { margin-top: 16pt; }
.field .initials { flex: 0 0 0.8in; width: 0.8in !important; }
.write { break-inside: avoid; }
.write table { margin: 5pt 0 4pt; }
.write tbody td { height: 30pt; border: 0.5pt solid var(--rule); }
.write.short tbody td { height: 24pt; }
.write th { border-bottom: 1pt solid var(--ink); }
.write tfoot td { height: 20pt; border-left: 0.5pt solid var(--rule); border-right: 0.5pt solid var(--rule);
                  border-bottom: 0.5pt solid var(--rule); vertical-align: bottom; }
.choices { margin: 0 0 9pt; }
.choice { display: flex; gap: 8pt; align-items: flex-start; margin: 6pt 0 0; break-inside: avoid; }
.box { display: inline-block; flex: 0 0 auto; width: 11pt; height: 11pt; border: 1pt solid var(--ink);
       border-radius: 1pt; margin-top: 1pt; background: #fff; }
.sigs { display: grid; grid-template-columns: 1fr 1fr; column-gap: 26pt; row-gap: 4pt;
        margin: 0 0 12pt; }
.sigs > div { break-inside: avoid; }
.sigs h3 { margin: 0; font-size: 9.5pt; }
.sigs .pair { display: flex; gap: 12pt; }
.sigs .pair .field:first-child { flex: 1 1 62%; }
.sigs .pair .field:last-child { flex: 1 1 38%; }
section.checklist { font-size: 9pt; line-height: 1.36; }
section.checklist h2 { margin-top: 0; }
section.checklist .intro { margin-bottom: 8pt; }
section.checklist table { font-size: 9pt; margin-top: 2pt; }
section.checklist td { padding-top: 5pt; padding-bottom: 5pt; line-height: 1.34; }
section.checklist td.step { font-weight: 700; }
section.checklist td.done { vertical-align: bottom; }
section.checklist td.done .blank { width: 100% !important; }
section.checklist .box { margin-top: 1.5pt; }
</style>
"""


class OperatingAgreementError(RuntimeError):
    """The data cannot support a clause without guessing."""


# -- small helpers ----------------------------------------------------------------

def fill(label: str) -> str:
    """A bracketed blank the Members must complete before signing."""
    return f'<span class="fill">[{esc(label)}]</span>'


def company() -> str:
    """The Company's legal name, which stays blank until the Members choose it."""
    return f"{fill(LEGAL_NAME)}, LLC"


def words(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else f"{n:,}"


def join_and(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def p(*paragraphs: str) -> str:
    return "".join(f"<p>{text}</p>" for text in paragraphs)


def items(*entries: str) -> str:
    return '<ol class="alpha">' + "".join(f"<li>{entry}</li>" for entry in entries) + "</ol>"


def field_line(label: str = "", *, tall: bool = False) -> str:
    """A labelled line to write on. A tall line leaves room above it for a signature."""
    text = f'<span class="label">{esc(label)}</span>' if label else ""
    kind = "field tall" if tall else "field"
    return f'<div class="{kind}">{text}{blank()}</div>'


def box() -> str:
    """An empty checkbox to tick by hand."""
    return '<span class="box"></span>'


# -- data -------------------------------------------------------------------------

@dataclass
class Business:
    """What the agreement reads from the data center when it is built."""
    working_name: str
    services: int
    categories: list[str]
    databases: list[str]
    government_fee_lines: int


def read_business(center: Any) -> Business:
    services = records(center, SERVICE_TABLE)
    if not services:
        raise OperatingAgreementError(f"no rows in {SERVICE_TABLE}; the purpose clause has nothing "
                                      "to describe the business by")
    categories: list[str] = []
    for row in services:
        category = row.get("category")
        if category is None or not str(category).strip():
            raise OperatingAgreementError(f"{row.get('fee_code', '?')}: no category in {SERVICE_TABLE}")
        category = str(category).strip()
        if category not in categories:
            categories.append(category)

    databases = [str(spec.title).strip() for spec in DATABASES]
    if not databases or not all(databases):
        raise OperatingAgreementError("datacenter.config.DATABASES has no titled databases")

    name = str(DATA_CENTER_NAME).strip()
    if not name:
        raise OperatingAgreementError("DATA_CENTER_NAME is empty")

    return Business(
        working_name=name,
        services=len(services),
        categories=categories,
        databases=databases,
        government_fee_lines=len(records(center, GOVERNMENT_TABLE)),
    )


# -- clauses ----------------------------------------------------------------------

@dataclass
class Sub:
    html: str
    title: str = ""
    key: str = ""
    keep: bool = False  # keep the whole clause on one page


@dataclass
class Clause:
    key: str
    title: str
    subs: list[Sub] = field(default_factory=list)


def formation_details(biz: Business) -> str:
    def row(label: str, value: str, *, text: bool = False) -> str:
        kind = "text" if text else "value"
        return f'<span class="label">{esc(label)}</span><span class="{kind}">{value}</span>'

    return (
        '<div class="details">'
        + row("Legal name of the Company", f"<b>{company()}</b>", text=True)
        + row("Working name of the business",
              f'<b>&ldquo;{esc(biz.working_name)}&rdquo;</b> <span class="note">&ndash; working '
              "name only (clause [[form.name]])</span>", text=True)
        + row("State of organization", blank())
        + row("Date formation document filed", blank())
        + row("State filing number", blank())
        + row("Registered agent", blank())
        + row("Registered office address", blank())
        + row("Principal office address", blank())
        + row("Federal Employer Identification Number (EIN)",
              blank() + '<span class="after">&ndash; obtained from the tax authority after '
              "formation</span>")
        + "</div>"
    )


def formation(biz: Business) -> Clause:
    defs = [
        ("Capital Contribution", "cash, property or services a Member contributes under Article "
         "[[capital]]."),
        ("Fair Value", "the value of an interest in the Company set under clause [[transfer.value]]."),
        ("Majority in Interest", "Members who together hold more than half of all Percentage "
         "Interests."),
        ("Manager", "a person appointed under clause [[mgmt.manager]] while the Company is "
         "manager-managed."),
        ("Percentage Interest", "a Member&rsquo;s share of the Company as stated in clause "
         "[[members.table]], as updated."),
        ("Transfer", "any sale, gift, assignment, pledge or other disposal of all or part of an "
         "interest in the Company, whether voluntary or by operation of law."),
    ]
    entries = "".join(f'<div><dt>&ldquo;{esc(term)}&rdquo;</dt> <dd>means {text}</dd></div>'
                      for term, text in defs)
    return Clause("form", "Formation", [
        Sub(p("Complete when filing; the filed Formation Document controls if a line differs.")
            + formation_details(biz), title="Formation details", key="form.details"),
        Sub("The Company is formed as a limited liability company by filing a formation document "
            "with the state named in clause [[form.details]] (the &ldquo;<b>State of "
            "Organization</b>&rdquo;). Depending on the state, that document may be called "
            "articles of organization, a certificate of formation or something similar (the "
            "&ldquo;<b>Formation Document</b>&rdquo;). Each Member shall sign any filing needed to "
            "form the Company and keep it in existence.", title="Formation"),
        Sub(f"The Company&rsquo;s name is {company()}. &ldquo;{esc(biz.working_name)}&rdquo; is "
            "the business&rsquo;s working name, not its legal name. Before the Formation Document "
            "is filed, the Members shall check with the State of Organization that the legal name "
            "is available, and whether trading under any other name, including the working name, "
            "needs a separate registration where the Company does business. The name may be "
            "changed only under clause [[mgmt.major]].", title="Name", key="form.name"),
        Sub("The registered agent and registered office are those in clause [[form.details]]. The "
            "Company may change them, and open other offices, by a decision under Article [[mgmt]] "
            "and any filing the State of Organization requires.",
            title="Registered agent and offices"),
        Sub("The Company begins on the date its Formation Document is filed, or on any later "
            "effective date stated in it, and continues until it is dissolved and wound up under "
            "Article [[dissolve]].", title="Term"),
        Sub("The Company shall apply for its EIN after the Formation Document is filed and "
            "write it in clause [[form.details]] once the tax authority has issued it. No other "
            "number, such as a Member&rsquo;s own taxpayer number, may be used as the "
            "Company&rsquo;s.", title="Tax identification number"),
        Sub("No Member or Manager is personally liable for the debts, obligations or liabilities "
            "of the Company only because of being a Member or Manager, except as the applicable "
            "law, or that person&rsquo;s own written guarantee, provides. This protection can "
            "depend on how the Company is formed and run, including keeping Company and personal "
            "money apart under clause [[records.bank]]; the Members shall ask their attorney what "
            "it requires in the State of Organization.", title="Limited liability",
            key="form.liability"),
        Sub("If this Agreement conflicts with the Formation Document, or with a rule of law the "
            "Members cannot change by agreement, that document or rule prevails.",
            title="Formation Document and law"),
        Sub(p("In this Agreement, the &ldquo;<b>Company</b>&rdquo; is the limited liability company "
              "named in clause [[form.details]], and:") + f'<dl class="defs">{entries}</dl>',
            title="Defined terms"),
    ])


def purpose(biz: Business) -> Clause:
    lines = join_and([esc(category) for category in biz.categories])
    return Clause("purpose", "Purpose", [
        Sub(f"The purpose of the Company is to operate an AI data center services business, "
            f"including providing {lines} services to clients and owning or operating the "
            "hardware, software and facilities used to deliver them, and to engage in any other "
            "lawful activity for which a limited liability company may be formed in the State of "
            "Organization.", title="Purpose", key="purpose.purpose"),
        Sub("The Company may do anything lawful that is necessary or convenient to carry out its "
            "purpose, including buying, leasing and selling equipment; entering into client, "
            "supplier and lease agreements; employing staff and engaging contractors; borrowing "
            "money; and owning intellectual property, in each case subject to clause "
            "[[mgmt.major]].", title="Powers"),
        Sub("The Company shall obtain and keep the licenses, permits and registrations its "
            "activities require. Which ones apply depends on where the Company operates and what "
            "it does there; for a data center they may include building, electrical, fire safety "
            "and environmental approvals as well as business and tax registrations. The Members "
            "shall confirm them with their attorney or the relevant authorities.",
            title="Licenses and permits"),
    ])


def members() -> Clause:
    rows = [["", "", "", ""] for _ in range(MEMBER_ROWS)]
    member_table = table(
        ["Member name", "Address for notices", "Capital contribution", "Percentage interest"],
        rows, numeric={2, 3}, footer=["Total", "", "", "100%"],
        widths=["27%", "41%", "17%", "15%"])
    return Clause("members", "Members", [
        Sub(p("The Members, their addresses for notices, their initial Capital Contributions and "
              "their Percentage Interests are:")
            + f'<div class="write">{member_table}</div>'
            + p("Non-cash contributions are described under clause [[capital.property]]. If there "
                "are more Members than rows, attach a continuation table signed by all Members."),
            title="Members", key="members.table", keep=True),
        Sub("The Percentage Interests must add up to 100%. The Company shall update the table in "
            "clause [[members.table]] whenever a Member is admitted or leaves, an interest is "
            "Transferred, or contributions change the Percentage Interests, and all Members shall "
            "sign or initial each update.",
            title="Keeping the table current", key="members.update"),
        Sub("A Member has no authority to act for or bind the Company except as Article [[mgmt]] "
            "provides or the Members authorize in writing.", title="Authority"),
        Sub(f"A Member may carry on other business activities, except "
            f"{fill('ANY ACTIVITY A MEMBER MAY NOT CARRY ON, SUCH AS COMPETING AI DATA CENTER SERVICES, OR NONE')}. "
            "A Member shall not use Company property, client relationships or confidential "
            "information for any other business.", title="Other activities"),
        Sub("Each Member shall keep confidential, during and after membership, the Company&rsquo;s "
            "non-public information, including client data, prices, source code and security "
            "information, and use it only for the Company&rsquo;s business, except for a disclosure "
            "the law requires or the Members approve in writing.", title="Confidentiality",
            key="members.confidential"),
    ])


def capital(biz: Business) -> Clause:
    dbs = join_and([esc(name) for name in biz.databases])
    return Clause("capital", "Capital contributions", [
        Sub(f"Each Member shall make the initial Capital Contribution stated in clause "
            f"[[members.table]] by {fill('DATE')}.", title="Initial contributions"),
        Sub(f"A Member who contributes property other than cash, such as equipment, software, "
            f"domain names or other intellectual property, shall describe it in a written schedule "
            f"signed by all Members, with the value the Members agree for it. Such property may "
            f"include the data center software developed under the working name, which runs "
            f"{words(len(biz.databases))} databases ({dbs}). On contribution, the Member transfers "
            "all of their rights in the property to the Company and shall sign any document needed "
            "to confirm that. Property not listed in a schedule stays the Member&rsquo;s own, and "
            "the Company uses it only under a written license.",
            title="Contributions of property", key="capital.property"),
        Sub(f"No Member is required to make a further Capital Contribution. The Company may ask "
            f"for further contributions only with the approval of "
            f"{fill('ALL MEMBERS / A MAJORITY IN INTEREST')}, and if a Member does not contribute, "
            f"{fill('CONSEQUENCE, SUCH AS ADJUSTING PERCENTAGE INTERESTS, OR NONE')}.",
            title="Further contributions"),
        Sub("With the approval required by clause [[mgmt.major]], a Member may lend money to the "
            "Company. A loan is not a Capital Contribution, does not change Percentage Interests "
            "and shall be documented in writing with its interest rate and repayment terms.",
            title="Member loans"),
        Sub("The Company shall keep a capital account for each Member, credited with their "
            "Capital Contributions and share of profits and debited with their distributions and "
            "share of losses, as the Company&rsquo;s accountant advises.", title="Capital accounts",
            key="capital.accounts"),
        Sub("No Member earns interest on a Capital Contribution, or may withdraw or demand the "
            "return of one, except as this Agreement provides.", title="No interest or return"),
    ])


def money() -> Clause:
    return Clause("money", "Allocations and distributions", [
        Sub("Profits and losses for each fiscal year are allocated among the Members in proportion "
            "to their Percentage Interests, unless the Members agree otherwise in writing on the "
            "advice of the Company&rsquo;s tax adviser.", title="Allocations"),
        Sub(f"The Company may distribute cash it does not need for its operations, debts and "
            f"reasonable reserves, at times and in amounts decided by "
            f"{fill('THE MEMBERS BY MAJORITY IN INTEREST / THE MANAGER')}, to the Members in "
            "proportion to their Percentage Interests when declared.", title="Distributions"),
        Sub("The Company shall not make a distribution if, after it, the Company could not pay "
            "its debts as they fall due, or if it would breach a loan agreement or the law. A "
            "Member who receives a distribution made in breach of this clause shall repay it to "
            "the extent the law or the Members&rsquo; agreement requires.",
            title="Limits on distributions"),
        Sub(f"{fill('INCLUDE OR DELETE')} So far as cash allows, the Company shall distribute to "
            f"each Member, within {fill('PERIOD')} after each fiscal year, enough to pay the income "
            f"tax on that Member&rsquo;s share of Company profits at an assumed rate of "
            f"{fill('RATE')}, as an advance against the Member&rsquo;s other distributions.",
            title="Tax distributions"),
        Sub(f"The Members intend the Company to be classified for income tax purposes as "
            f"{fill('TAX CLASSIFICATION CHOSEN WITH A TAX ADVISER')}. The choices and their effects "
            "depend on the number of Members and on national and state tax rules, so the Members "
            "shall decide with a tax adviser and make any election in time.",
            title="Tax classification"),
        Sub(f"{fill('NAME OR ROLE')} shall represent the Company in dealings with tax authorities "
            "where the law requires or allows the Company to name a representative, shall keep the "
            "Members informed, and shall not settle a tax matter without the approval required by "
            "clause [[mgmt.major]].", title="Tax representative"),
    ])


def management() -> Clause:
    initials = "".join(f'<span class="blank initials"></span>' for _ in range(MEMBER_ROWS))
    choice = (
        '<div class="choices"><div class="choice">' + box() + "<div><b>Member-managed.</b> The Members manage the "
        "Company under clause [[mgmt.members]].</div></div>"
        '<div class="choice">' + box() + "<div><b>Manager-managed.</b> One or more Managers manage "
        "the Company under clause [[mgmt.manager]].</div></div>"
        f'<div class="field"><span class="label">Initials of each Member</span>{initials}</div>'
        "</div>"
    )
    manager_rows = [["", "", ""] for _ in range(MANAGER_ROWS)]
    manager_table = table(["Manager name", "Address for notices", "Date appointed"], manager_rows,
                          widths=["30%", "50%", "20%"])
    return Clause("mgmt", "Management", [
        Sub(p("Check one box only, and each Member initials the choice. If the Formation Document "
              "states how the Company is managed, the box checked must match it.") + choice,
            title="Management structure", key="mgmt.choice", keep=True),
        Sub(f"If the Company is member-managed, the Members decide its business by Majority in "
            f"Interest, except for the decisions in clause [[mgmt.major]]. Each Member may make "
            f"day-to-day decisions in the ordinary course of business within "
            f"{fill('LIMITS, SUCH AS A SPENDING LIMIT PER TRANSACTION')}.",
            title="Member-managed", key="mgmt.members"),
        Sub(p("If the Company is manager-managed, the Managers named below manage its business, "
              "except for the decisions in clause [[mgmt.major]]. A Manager need not be a Member. A "
              "Majority in Interest appoints, removes and replaces Managers, who act by majority.")
            + f'<div class="write short">{manager_table}</div>',
            title="Manager-managed", key="mgmt.manager", keep=True),
        Sub(f"Whichever structure is checked, the following require the written consent of "
            f"{fill('ALL MEMBERS / A MAJORITY IN INTEREST')}:" + items(
                "amending this Agreement or the Formation Document;",
                "admitting a new Member or issuing any new interest in the Company;",
                "selling, leasing or otherwise disposing of all or substantially all of the "
                "Company&rsquo;s assets;",
                "merging or converting the Company, or dissolving it;",
                f"borrowing money, granting security over Company assets, or guaranteeing "
                f"another person&rsquo;s debts, above {fill('AMOUNT')};",
                f"entering a client, supplier, lease or equipment contract with total commitments "
                f"above {fill('AMOUNT')} or a term longer than {fill('PERIOD')};",
                "any transaction between the Company and a Member, a Manager or a person related "
                "to either, including paying them for services;",
                "changing the Company&rsquo;s name or the nature of its business;",
                f"starting, settling or abandoning a legal proceeding or tax matter worth more "
                f"than {fill('AMOUNT')}; and",
                "filing for bankruptcy or a similar proceeding.",
            ), title="Major decisions", key="mgmt.major"),
        Sub("The Members or, if the Company is manager-managed, the Manager may appoint officers "
            "with the titles, duties and authority stated in writing, and may remove them at any "
            "time.", title="Officers"),
        Sub("Each Member who takes part in management, and each Manager, shall act in good faith "
            "and in what they reasonably believe is the Company&rsquo;s best interest, shall "
            "disclose any personal interest in a matter before the Company decides it, and shall "
            "not take a business opportunity that belongs to the Company. How far an agreement can "
            "change these duties differs between states; the Members shall settle the wording with "
            "their attorney.", title="Duties"),
        Sub("A Member or Manager is paid for services to the Company only as approved in writing "
            "under clause [[mgmt.major]]. The Company shall reimburse reasonable expenses incurred "
            "for its business when they are supported by receipts.", title="Pay and expenses"),
    ])


def voting() -> Clause:
    return Clause("vote", "Voting and meetings", [
        Sub("Each Member votes in proportion to their Percentage Interest. Unless this Agreement "
            "requires otherwise, the Members decide by Majority in Interest.", title="Votes"),
        Sub(f"Any Member or Manager may call a meeting of the Members by written notice to every "
            f"Member at least {fill('NUMBER')} days ahead, stating when and where or how to join "
            f"and the business to be decided. The Members shall meet at least "
            f"{fill('HOW OFTEN, SUCH AS ONCE A YEAR')}.", title="Meetings"),
        Sub("Members holding a Majority in Interest form a quorum. A Member may attend and vote in "
            "person, by telephone or video if all participants can hear one another, or by a proxy "
            "who is another Member appointed by written notice to the Company before the meeting.",
            title="Quorum and attendance"),
        Sub(f"The Members may act without a meeting by written consent, including by email or "
            f"electronic signature, signed by Members holding at least the votes the decision "
            f"would need at a meeting at which all Members voted. The Company shall send a copy of "
            f"each consent to every Member within {fill('NUMBER')} days.",
            title="Written consent", key="vote.consent"),
        Sub("The Company shall keep minutes of every meeting and a copy of every written consent "
            "with its records.", title="Minutes"),
        Sub(f"If the Members are deadlocked on a decision for {fill('PERIOD')}, they shall refer "
            f"it to {fill('MEDIATION, OR A NAMED ADVISER')} before any Member starts proceedings "
            "or seeks to dissolve the Company.", title="Deadlock"),
    ])


def records_clause() -> Clause:
    return Clause("records", "Records and accounts", [
        Sub("The Company shall keep at its principal office, or electronically where every Member "
            "can obtain a copy:" + items(
                "this Agreement, every amendment and every updated Members table;",
                "the Formation Document and every later filing with the State of Organization;",
                "a current list of Members and Managers, and a record of each Capital "
                "Contribution with its agreed value and date;",
                "minutes of meetings and written consents;",
                "the Company&rsquo;s tax returns and financial statements; and",
                "the Company&rsquo;s contracts, licenses and permits.",
            ), title="Company records", key="records.list"),
        Sub("Records may be kept electronically, including on the data center platform the Company "
            "operates, provided they are backed up and can be exported and printed on request.",
            title="Electronic records"),
        Sub(f"The Company&rsquo;s fiscal year ends on {fill('FISCAL YEAR END')}. The Company shall "
            f"keep complete and accurate books of account on the {fill('CASH OR ACCRUAL')} method, "
            "or another method its accountant advises.", title="Fiscal year and books"),
        Sub(f"All Company money shall be held in accounts in the Company&rsquo;s name, and no "
            f"Company money shall be mixed with a Member&rsquo;s own. A payment or withdrawal above "
            f"{fill('AMOUNT')} requires the approval of {fill('WHO MUST APPROVE')}.",
            title="Bank accounts", key="records.bank"),
        Sub(f"Within {fill('NUMBER')} days after the end of each fiscal year, the Company shall "
            "give each Member its annual financial statements and the tax information that Member "
            "needs to file their own returns.", title="Reports to Members"),
        Sub(f"Each Member may inspect and copy the Company&rsquo;s records on "
            f"{fill('NUMBER')} business days&rsquo; written notice, during normal business hours "
            "and at that Member&rsquo;s cost, for any purpose reasonably related to their interest "
            "in the Company.", title="Inspection"),
        Sub(f"The Company shall keep its records for at least {fill('PERIOD')}, or longer where "
            "the law or a contract requires.", title="Retention", key="records.retain"),
    ])


def transfers() -> Clause:
    return Clause("transfer", "Transfers of interest", [
        Sub(f"A Member may not Transfer all or any part of their interest without the prior "
            f"written consent of "
            f"{fill('ALL OTHER MEMBERS / A MAJORITY IN INTEREST OF THE OTHER MEMBERS')}, and any "
            "sale is also subject to clause [[transfer.rofr]]. A Transfer made in breach of this "
            "Article is void to the extent the law allows.", title="Consent required"),
        Sub(f"A Member who wishes to sell their interest shall first notify the Company and the "
            f"other Members in writing of the proposed buyer, price and terms. The Company, and "
            f"then the other Members in proportion to their Percentage Interests, may buy on the "
            f"same terms by notice within {fill('NUMBER')} days. If they do not, the Member may "
            f"sell to that buyer within {fill('NUMBER')} days on terms no better for the buyer, who "
            "becomes a Member only under clause [[admit.new]].", title="Right of first refusal",
            key="transfer.rofr"),
        Sub("A person who receives an interest by Transfer but is not admitted as a Member "
            "receives only the share of profits, losses and distributions attached to it, and has "
            "no right to vote, to take part in management, or to inspect records beyond what the "
            "law gives.", title="Rights of a transferee", key="transfer.assignee"),
        Sub(f"If a Member dies, becomes legally incapacitated or becomes bankrupt, the Company, "
            f"and then the other Members, may buy that Member&rsquo;s interest at Fair Value by "
            f"notice given within {fill('NUMBER')} days after they learn of the event. Until the "
            "interest is bought, the Member&rsquo;s successor holds only the rights in clause "
            "[[transfer.assignee]].", title="Death, incapacity or bankruptcy"),
        Sub(f"Fair Value is "
            f"{fill('VALUATION METHOD, SUCH AS A VALUE THE MEMBERS AGREE EACH YEAR OR ONE SET BY AN INDEPENDENT APPRAISER')}. "
            f"The price is paid {fill('PAYMENT TERMS')}.", title="Fair Value",
            key="transfer.value"),
    ])


def admission() -> Clause:
    return Clause("admit", "Admission and withdrawal", [
        Sub("A person becomes a Member only with the consent required by clause [[mgmt.major]], "
            "after making any agreed Capital Contribution and signing a written agreement to be "
            "bound by this Agreement. The table in clause [[members.table]] is then updated under "
            "clause [[members.update]].", title="New Members", key="admit.new"),
        Sub(f"A Member may withdraw by giving at least {fill('NOTICE PERIOD')} written notice to "
            f"the Company and the other Members, but not before "
            f"{fill('DATE OR EVENT, OR DELETE THESE WORDS')}. The Company, and then the other "
            f"Members, may buy the withdrawing Member&rsquo;s interest at Fair Value within "
            f"{fill('NUMBER')} days after the withdrawal takes effect.", title="Withdrawal"),
        Sub(f"{fill('INCLUDE OR DELETE')} A Member may be removed by a decision of all other "
            f"Members if that Member commits fraud or dishonesty against the Company, or materially "
            f"breaches this Agreement and does not remedy the breach within {fill('NUMBER')} days "
            "after written notice. The Company shall then buy the removed Member&rsquo;s interest "
            "at Fair Value, less any loss the breach caused the Company.",
            title="Removal for cause"),
        Sub("A Member who withdraws or is removed stops voting and taking part in management when "
            "that takes effect, remains bound by clause [[members.confidential]], shall return all "
            "Company property and information, including client data and access credentials, and "
            "remains responsible for obligations they took on before leaving.",
            title="Effect of leaving"),
    ])


def dissolution() -> Clause:
    return Clause("dissolve", "Dissolution and winding up", [
        Sub("The Company is dissolved only on the first of these to occur:" + items(
                f"a written decision of {fill('ALL MEMBERS / A MAJORITY IN INTEREST')};",
                "the sale of all or substantially all of the Company&rsquo;s assets, once the "
                "proceeds are collected, unless the Members decide to continue the Company;",
                "there being no remaining Members, unless the Company is continued in a way the "
                "law allows; or",
                "a court order, or another event that requires dissolution under the law of the "
                "State of Organization.",
            ), title="Events of dissolution", key="dissolve.events"),
        Sub("The death, incapacity, bankruptcy, withdrawal or removal of a Member does not dissolve "
            "the Company unless the remaining Members decide to dissolve it under clause "
            "[[dissolve.events]].", title="Continuation"),
        Sub(f"On dissolution, {fill('THE MEMBERS / THE MANAGER / A PERSON THE MEMBERS APPOINT')} "
            f"(the &ldquo;<b>Liquidator</b>&rdquo;) shall wind up the Company&rsquo;s business, "
            "complete or transfer client contracts, sell or distribute its assets and settle its "
            "affairs, and has the authority needed to do so.", title="Winding up"),
        Sub("Before the Company&rsquo;s systems are shut down or sold, the Liquidator shall return "
            "or delete client data as each client&rsquo;s contract requires, and shall keep a "
            "written record of what was returned, deleted or kept and why.", title="Client data"),
        Sub(f"The Liquidator shall apply the Company&rsquo;s assets, to the extent the law allows, "
            f"first to pay the Company&rsquo;s creditors, including Members who are creditors; "
            f"then to set aside reasonable reserves for contingent or unknown liabilities; and "
            f"then to the Members in proportion to their "
            f"{fill('POSITIVE CAPITAL ACCOUNT BALANCES / PERCENTAGE INTERESTS')}.",
            title="Order of payment"),
        Sub("When winding up is complete, the Liquidator shall file any notice of dissolution or "
            "cancellation the State of Organization requires, file final tax returns, close the "
            "Company&rsquo;s accounts, and arrange for its records to be kept for the period in "
            "clause [[records.retain]].", title="Final filings"),
    ])


def indemnification() -> Clause:
    return Clause("indemnity", "Indemnification", [
        Sub("In this Article, a &ldquo;<b>Covered Person</b>&rdquo; is a Member, Manager or "
            "officer, acting in that role.", title="Covered Persons"),
        Sub("To the extent the law allows, the Company shall indemnify each Covered Person against "
            "losses, judgments, settlements and reasonable legal costs arising from acting for the "
            "Company in good faith and within the authority this Agreement gives.",
            title="Indemnity", key="indemnity.indemnity"),
        Sub(f"Clause [[indemnity.indemnity]] does not cover a loss arising from the Covered "
            f"Person&rsquo;s fraud, willful misconduct or knowing breach of law, "
            f"{fill('GROSS NEGLIGENCE, OR DELETE')}, material breach of this Agreement, or a "
            "personal benefit to which that person was not entitled.", title="Exclusions"),
        Sub("The Company may pay a Covered Person&rsquo;s legal costs as they are incurred if the "
            "person agrees in writing to repay them should it be decided that they were not "
            "entitled to indemnity.", title="Advance of costs"),
        Sub("The Company may buy insurance for itself and its Covered Persons, such as general "
            "liability, professional liability and cyber insurance, in amounts the Members "
            "decide.", title="Insurance"),
        Sub("Indemnity is payable only from the Company&rsquo;s assets and insurance; no Member "
            "must contribute to it personally. This Article survives a Covered Person leaving "
            "their role.", title="Source of payment and survival"),
    ])


def general() -> Clause:
    return Clause("general", "General", [
        Sub(f"This Agreement is governed by the law of {fill('STATE OF ORGANIZATION')}.",
            title="Governing law"),
        Sub(f"A dispute about this Agreement shall first be discussed in good faith for "
            f"{fill('PERIOD')} and, if not resolved, decided by "
            f"{fill('ARBITRATION UNDER NAMED RULES / NAMED COURTS')}.",
            title="Disputes"),
        Sub("This Agreement, with the Members table and any schedules signed under it, is the "
            "entire agreement among the Members about the Company and replaces any earlier one.",
            title="Entire agreement"),
        Sub("An amendment must be in writing, signed by the Members whose consent clause "
            "[[mgmt.major]] requires.", title="Amendment"),
        Sub("Notices are given in writing to the addresses in clause [[members.table]] or to "
            "another address a Member gives by notice, including an email address given for that "
            "purpose.", title="Notices"),
        Sub("If a provision is held unenforceable, it applies to the extent it is enforceable and "
            "the rest of this Agreement continues. A delay in exercising a right does not waive "
            "it.", title="Severability and waiver"),
        Sub("This Agreement binds the Members and their permitted successors and transferees. It "
            "is for the benefit of the Members and the Company only, and no creditor or other "
            "third party may enforce it.", title="Binding effect"),
        Sub("This Agreement may be signed in counterparts, and electronically to the extent the "
            "law that applies recognizes electronic signatures. Together the counterparts form "
            "one agreement.", title="Counterparts"),
        Sub("Each Member confirms they have read this Agreement, had the chance to take "
            "independent legal and tax advice, and know that the Company&rsquo;s lawyer acts for "
            "the Company only.", title="Independent advice"),
    ])


def signatures() -> Clause:
    def block(role: str) -> str:
        return ("<div>"
                + field_line(f"{role} signature", tall=True)
                + f'<div class="pair">{field_line("Name")}{field_line("Date")}</div>'
                + "</div>")
    member_blocks = "".join(block("Member") for _ in range(MEMBER_ROWS))
    manager_blocks = "".join(block("Manager") for _ in range(MANAGER_ROWS))
    return Clause("sign", "Signatures", [
        Sub(p("Each Member signs below, effective on the Effective Date; add pages as needed.")
            + f'<div class="sigs">{member_blocks}</div>', title="Members", keep=True),
        Sub(p("If the Company is manager-managed, each Manager who is not a Member signs to accept "
              "appointment and Articles [[mgmt]], [[records]] and [[indemnity]]. Otherwise leave blank.")
            + f'<div class="sigs">{manager_blocks}</div>', title="Managers", keep=True),
    ])


# -- assembly ---------------------------------------------------------------------

def number_clauses(clauses: list[Clause]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for index, clause in enumerate(clauses, start=1):
        if clause.key in refs:
            raise OperatingAgreementError(f"duplicate clause key {clause.key}")
        refs[clause.key] = str(index)
        for sub_index, sub in enumerate(clause.subs, start=1):
            if sub.key:
                if sub.key in refs:
                    raise OperatingAgreementError(f"duplicate clause key {sub.key}")
                refs[sub.key] = f"{index}.{sub_index}"
    return refs


def resolve(html_text: str, refs: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in refs:
            raise OperatingAgreementError(f"cross-reference to unknown clause {key!r}")
        return refs[key]
    return re.sub(r"\[\[([a-z_.]+)\]\]", replace, html_text)


def render_clauses(clauses: list[Clause], refs: dict[str, str]) -> str:
    parts = []
    for index, clause in enumerate(clauses, start=1):
        subs_html = []
        for sub_index, sub in enumerate(clause.subs, start=1):
            number = f"{index}.{sub_index}"
            body = resolve(sub.html, refs)
            runin = f'<span class="runin">{esc(sub.title)}.</span> ' if sub.title else ""
            if body.startswith("<p>") and runin:
                body = "<p>" + runin + body[3:]
            elif runin:
                body = runin + body
            kind = "cl keep" if sub.keep else "cl"
            subs_html.append(f'<div class="{kind}"><span class="n">{number}</span>{body}</div>')
        head = f"<h2>Article {index}. {esc(clause.title)}</h2>"
        lead = f'<div class="lead">{head}{subs_html[0]}</div>' if subs_html else head
        parts.append(f'<section class="article">{lead}{"".join(subs_html[1:])}</section>')
    return "".join(parts)


def preamble(biz: Business) -> str:
    cats = biz.categories
    return (
        '<div class="preamble">'
        + p(f"This Operating Agreement (this &ldquo;<b>Agreement</b>&rdquo;) of {company()} (the "
            f"&ldquo;<b>Company</b>&rdquo;) is made on {fill('EFFECTIVE DATE')} (the "
            "&ldquo;<b>Effective Date</b>&rdquo;) by the persons who sign it in Article [[sign]] "
            "as members of the Company (each a &ldquo;<b>Member</b>&rdquo;).",
            f"The Members are forming, or have formed, the Company as a limited liability company "
            f"to own and operate the AI data center services business that has been working under "
            f"the name &ldquo;{esc(biz.working_name)}&rdquo;, and agree as follows.")
        + '<div class="drafting"><h3>Completing this template</h3><ul>'
        "<li>Complete or strike out every highlighted [BRACKETED BLANK] and every blank line "
        "before anyone signs; no blank has a default value. Check exactly one management box in "
        "clause [[mgmt.choice]]; both are blank on purpose.</li>"
        f"<li>&ldquo;{esc(biz.working_name)}&rdquo; is a working name. Choose the legal name, check "
        f"its availability with the state, and write it wherever [{esc(LEGAL_NAME)}] appears. "
        "Write the EIN in clause [[form.details]] only once the tax authority has issued it.</li>"
        f"<li>Clause [[purpose.purpose]] describes the business by the {words(len(cats))} service "
        f"categories in its rate card on {ISSUED:%B %-d, %Y}, which lists {biz.services} services. "
        "Update it if the business changes.</li>"
        "<li>Limited liability company law, tax rules and filing requirements differ by state, so "
        "some clauses may need changes or may not be enforceable where the Company is organized. "
        "Have a licensed attorney review the Agreement, and a tax adviser Article [[money]], "
        "before signing. The formation checklist on the last page is guidance, not part of the "
        "Agreement.</li>"
        "</ul></div></div>"
    )


def checklist(biz: Business) -> str:
    if biz.government_fee_lines:
        planning = (
            f" The business&rsquo;s cost-planning data holds {biz.government_fee_lines} government "
            "fee lines. Their jurisdictions and amounts were invented for the sample data set, so "
            "they are not a list of what applies; replace them once the state and locality are "
            "known.")
    else:
        planning = ""
    steps = [
        ("Choose and check a name",
         f"Choose the Company&rsquo;s legal name; &ldquo;{esc(biz.working_name)}&rdquo; is a "
         "working name only. Check with the state&rsquo;s business filing office that the name is "
         "available, and that it does not conflict with an existing business or trademark. Decide "
         "whether the business will also trade under another name, which may need its own "
         "registration."),
        ("File formation documents with the state",
         "File the Formation Document with the State of Organization and pay the filing fee. Write "
         "the filing date and state filing number in clause [[form.details]]."),
        ("Appoint a registered agent",
         "Appoint a registered agent to receive legal papers for the Company in the State of "
         "Organization, and keep the appointment current. Write the agent&rsquo;s name and the "
         "registered office in clause [[form.details]]."),
        ("Obtain an EIN",
         "Apply for an EIN once the Company is formed. In the United States the IRS issues these "
         "free of charge online, so there is no need to pay anyone else to get one. Write the "
         "number in clause [[form.details]] only after it has been issued to the Company."),
        ("Complete and sign this Agreement",
         "Fill in every blank, check one management box in clause [[mgmt.choice]], have the "
         "Agreement reviewed by an attorney and a tax adviser, and have every Member sign it."),
        ("Open a business bank account",
         "Open an account in the Company&rsquo;s name and keep Company money apart from personal "
         "money (clause [[records.bank]]). The bank may ask for the filed Formation Document, the "
         "EIN and this Agreement."),
        ("Record capital contributions",
         "Collect each Member&rsquo;s initial contribution, and sign a written schedule for any "
         "equipment, software or other property contributed (clause [[capital.property]])."),
        ("Check licenses, permits and annual filings",
         "Check the state and local licenses, permits and registrations the business needs, and "
         "the annual reports, fees and tax filings that keep the Company in good standing."
         + planning),
        ("Keep this Agreement with the company records",
         "Keep the signed Agreement, the Members table and every amendment with the records in "
         "clause [[records.list]], and give each Member a copy."),
    ]
    rows = "".join(
        f'<tr><td>{box()}</td><td class="step">{esc(step)}</td><td>{text}</td>'
        f'<td class="done">{blank()}</td></tr>'
        for step, text in steps
    )
    return (
        '<section class="checklist page-break">'
        "<h2>Formation checklist</h2>"
        '<p class="intro">General steps for forming the Company and putting this Agreement into '
        "effect. They are not legal advice and not a complete list. Requirements, fees, forms and "
        "the order of the steps differ by state, and sometimes by county or city, so confirm each "
        "step with the state&rsquo;s business filing office, the tax authority and the "
        "Members&rsquo; attorney. This checklist is not part of the Agreement.</p>"
        '<table><colgroup><col style="width:5%"><col style="width:23%"><col style="width:55%">'
        '<col style="width:17%"></colgroup>'
        "<thead><tr><th></th><th>Step</th><th>What to do</th><th>Date and initials</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</section>"
    )


def check(body: str) -> None:
    text = re.sub(r"<[^>]+>", " ", body)
    for pattern, what in FORBIDDEN:
        found = re.search(pattern, text)
        if found:
            raise OperatingAgreementError(f"the agreement contains {what}: {found.group(0)!r}")


def build() -> Path:
    with seeded_center() as center:
        biz = read_business(center)

    clauses = [
        formation(biz),
        purpose(biz),
        members(),
        capital(biz),
        money(),
        management(),
        voting(),
        records_clause(),
        transfers(),
        admission(),
        dissolution(),
        indemnification(),
        general(),
        signatures(),
    ]
    refs = number_clauses(clauses)
    body = (EXTRA_CSS + resolve(preamble(biz), refs) + render_clauses(clauses, refs)
            + resolve(checklist(biz), refs))
    check(body)
    html_text = document(
        TITLE, body,
        eyebrow="Agreement template",
        subtitle=f"Operating agreement of [{LEGAL_NAME}], LLC",
        notice=TEMPLATE_NOTICE,
    )
    return render(NAME, html_text)


if __name__ == "__main__":
    print(build())
