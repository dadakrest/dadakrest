"""Software and Services License Agreement: a template between the business, as
Licensor, and a client, as Licensee.

    python3 tools/pdf/build.py license_agreement
    python3 tools/pdf/license_agreement.py

This is a template for an attorney to review, not a finished contract. Every
party detail, date, jurisdiction and commercial term the parties have to agree
is left as a visible [BRACKETED BLANK] or a line to write on.

What the template takes from the data when it is built:

* the services and their fee codes, for Schedule 1 and every clause that
  names a fee code, from the service_fees dataset;
* the support services that set the service levels in the service levels
  clause: the service_fees rows in the support category;
* the client-data periods (log retention, deletion, backup age-out, export,
  incident notice, audit-log delivery), from the governance knowledge base.
  Each period is read from every article that states it; the build fails if
  an article no longer states it or two articles disagree;
* the number and names of the platform's databases, from datacenter.config.

Clauses are numbered when the document is built and cross-references are
resolved from clause keys, so adding or moving a clause renumbers every
reference to it. Schedule 1 shows, for each fee code, the clauses that name it.
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
    document,
    esc,
    records,
    render,
    seeded_center,
    table,
)

from datacenter import DATA_CENTER_NAME  # noqa: E402
from datacenter.config import DATABASES  # noqa: E402

NAME = "license-agreement"
TITLE = "Software and Services License Agreement"
SERVICE_TABLE = "service_fees"
SUPPORT_CATEGORY = "support"
COLOCATION_CATEGORY = "colocation"

NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven", "twelve")

#: Where the knowledge base states each client-data period. Every source must
#: state the period, and all sources for one period must agree. A source is
#: (document external_id, pattern whose first group is the figure).
POLICY_SOURCES: dict[str, list[tuple[str, str]]] = {
    "log_days": [
        ("kb-gov-02", r"prompt and completion logs from a client deployment are kept for (\w+) days"),
        ("kb-gov-17", r"logs of prompts and responses are kept for (\w+) days"),
        ("kb-gov-13", r"prompt and completion logs .{0,80}? kept for (\w+) days"),
    ],
    "delete_days": [
        ("kb-gov-02", r"when a contract ends, working data is deleted within (\w+) days"),
        ("kb-gov-17", r"when the contract ends, working data is deleted within (\w+) days"),
    ],
    "backup_days": [
        ("kb-gov-02", r"backups containing the data age out within (\w+) days"),
        ("kb-gov-17", r"backups age out within (\w+) days"),
    ],
    "export_business_days": [
        ("kb-gov-17", r"exports are delivered within (\w+) business days"),
    ],
    "notice_hours": [
        ("kb-gov-17", r"we tell you within (\w+) hours of confirming"),
        ("kb-gov-09", r"reported to the affected client within (\w+) hours of confirmation"),
    ],
    "summary_business_days": [
        ("kb-gov-17", r"written summary from your account manager within (\w+) business days"),
        ("kb-gov-09", r"written summary within (\w+) business days"),
    ],
    "audit_business_days": [
        ("kb-gov-13", r"receive it within (\w+) business days"),
    ],
}

EXTRA_CSS = """
<style>
:root { --fill:#fff3cf; }
.fill { background: var(--fill); padding: 0 1.5pt; border-radius: 1.5pt;
        -webkit-box-decoration-break: clone; box-decoration-break: clone; }
.preamble p { margin-bottom: 6pt; }
.drafting { border: 0.75pt solid var(--rule); border-radius: 3pt; padding: 7pt 10pt 3pt;
            font-size: 9pt; margin: 4pt 0 6pt; break-inside: avoid; }
.drafting h3 { margin: 0 0 4pt; font-size: 9.5pt; }
.drafting ul { margin-bottom: 4pt; }
.preamble, section.article { font-size: 9.5pt; line-height: 1.38; }
section.article h2 { font-size: 11.5pt; margin: 11pt 0 5pt; padding-bottom: 2pt; }
section.article h3 { font-size: 10pt; }
.lead { break-inside: avoid; }
.cl { position: relative; padding-left: 30pt; margin: 0 0 5pt; orphans: 3; widows: 3; }
.cl > .n { position: absolute; left: 0; top: 0; font-weight: 700; }
.cl p { margin: 0 0 5pt; }
.cl p:last-child { margin-bottom: 0; }
.cl .runin { font-weight: 700; }
ol.alpha { list-style: none; counter-reset: alpha; padding-left: 22pt; margin: 3pt 0 0; }
ol.alpha > li { counter-increment: alpha; position: relative; margin-bottom: 2pt; }
ol.alpha > li::before { content: "(" counter(alpha, lower-alpha) ")"; position: absolute;
                        left: -22pt; }
dl.defs { margin: 3pt 0 0; }
dl.defs dt { display: inline; font-weight: 700; }
dl.defs dd { display: inline; margin: 0; }
dl.defs div { margin: 0 0 4pt; break-inside: avoid; }
.cl table { margin: 5pt 0 4pt; }
.code { white-space: nowrap; }
.parties { display: flex; gap: 22pt; margin: 6pt 0 2pt; break-inside: avoid; }
.parties > div { flex: 1 1 0; min-width: 0; }
.parties h3 { margin: 0 0 2pt; }
.field { display: flex; align-items: flex-end; gap: 6pt; margin: 7pt 0 0; }
.field .label { white-space: nowrap; font-size: 9pt; color: var(--muted); }
.field .blank { flex: 1 1 auto; width: auto !important; }
.sign { margin-top: 8pt; }
.sign h3 { margin: 0 0 2pt; }
.field.tall { margin-top: 16pt; }
.signatures { break-inside: avoid; }
.schedule .intro { margin-bottom: 6pt; font-size: 9.5pt; line-height: 1.42; }
.schedule td { padding-top: 2.5pt; padding-bottom: 2.5pt; line-height: 1.3; }
td.refs { white-space: nowrap; }
</style>
"""


class LicenseAgreementError(RuntimeError):
    """The data cannot support a clause without guessing."""


# -- small helpers ----------------------------------------------------------------

def fill(label: str) -> str:
    """A bracketed blank the parties must complete before signing."""
    return f'<span class="fill">[{esc(label)}]</span>'


def code(value: Any) -> str:
    return f'<span class="code">{esc(value)}</span>'


def words(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else f"{n:,}"


def join_and(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def need(row: dict[str, Any], key: str) -> Any:
    """A field that must be present and, for text, not empty. Zero is a value."""
    value = row.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise LicenseAgreementError(f"{row.get('fee_code', '?')}: no {key}")
    return value


def figure(token: str, label: str) -> int:
    """'30' or 'five' as an int."""
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    if token in NUMBER_WORDS:
        return NUMBER_WORDS.index(token)
    raise LicenseAgreementError(f"{label}: {token!r} is not a number")


# -- data -------------------------------------------------------------------------

@dataclass
class Policy:
    """Client-data periods, each read from the knowledge base."""
    figures: dict[str, int]
    articles: dict[str, str]  # external_id -> title, for every article read

    def __getattr__(self, name: str) -> int:
        try:
            return self.__dict__["figures"][name]
        except KeyError:
            raise AttributeError(name) from None


def read_policy(center: Any) -> Policy:
    by_external = {doc["external_id"]: doc for doc in center.list_documents() if doc.get("external_id")}
    bodies: dict[str, str] = {}
    titles: dict[str, str] = {}
    for sources in POLICY_SOURCES.values():
        for external_id, _ in sources:
            if external_id in bodies:
                continue
            listed = by_external.get(external_id)
            if listed is None:
                raise LicenseAgreementError(f"knowledge-base article {external_id} is missing")
            full = center.get_document(listed["id"])
            if full is None or not full.get("body"):
                raise LicenseAgreementError(f"knowledge-base article {external_id} has no text")
            bodies[external_id] = " ".join(str(full["body"]).split())
            titles[external_id] = str(need(listed, "title"))

    figures: dict[str, int] = {}
    for name, sources in POLICY_SOURCES.items():
        found: dict[str, int] = {}
        for external_id, pattern in sources:
            match = re.search(pattern, bodies[external_id], flags=re.IGNORECASE)
            if match is None:
                raise LicenseAgreementError(
                    f"{external_id} ({titles[external_id]}) no longer states {name}; "
                    "update POLICY_SOURCES or the clause that uses it")
            found[external_id] = figure(match.group(1), f"{external_id} {name}")
        if len(set(found.values())) != 1:
            raise LicenseAgreementError(f"knowledge-base articles disagree on {name}: {found}")
        figures[name] = next(iter(found.values()))
    return Policy(figures, titles)


def read_services(center: Any) -> list[dict[str, Any]]:
    services = records(center, SERVICE_TABLE)
    if not services:
        raise LicenseAgreementError("no service fees in the data; the agreement has nothing to license")
    codes = [str(need(row, "fee_code")) for row in services]
    duplicates = sorted({c for c in codes if codes.count(c) > 1})
    if duplicates:
        raise LicenseAgreementError(f"duplicate service fee codes: {', '.join(duplicates)}")
    for row in services:
        for key in ("service", "category", "unit"):
            need(row, key)
        need(row, "minimum_units")
    return sorted(services, key=lambda row: str(row["fee_code"]))


def categories(services: list[dict[str, Any]]) -> list[str]:
    """Categories in the order they first appear in the service table."""
    return list(dict.fromkeys(str(row["category"]) for row in services))


def in_category(services: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [row for row in services if str(row["category"]).strip().lower() == category]


# -- clauses ----------------------------------------------------------------------

@dataclass
class Sub:
    html: str
    title: str = ""
    key: str = ""


@dataclass
class Clause:
    key: str
    title: str
    subs: list[Sub] = field(default_factory=list)


def p(*paragraphs: str) -> str:
    return "".join(f"<p>{text}</p>" for text in paragraphs)


def items(*entries: str) -> str:
    return '<ol class="alpha">' + "".join(f"<li>{entry}</li>" for entry in entries) + "</ol>"


def field_line(label: str = "", *, tall: bool = False) -> str:
    """A labelled line to write on; without a label, a continuation line.
    A tall line leaves room above it for a handwritten signature."""
    text = f'<span class="label">{esc(label)}</span>' if label else ""
    kind = "field tall" if tall else "field"
    return f'<div class="{kind}">{text}<span class="blank"></span></div>'


def party_box(role: str, organized: str) -> str:
    return (
        f"<div><h3>{esc(role)}</h3>"
        + field_line("Legal name")
        + field_line("Entity type")
        + field_line(organized)
        + field_line("Principal address")
        + field_line()
        + field_line("Contact for notices")
        + field_line("Notice email")
        + "</div>"
    )


def parties() -> Clause:
    return Clause("parties", "Parties", [
        Sub(p("This Agreement is between the following parties.")
            + '<div class="parties">'
            + party_box("Licensor", "State of organization")
            + party_box("Licensee", "Jurisdiction of organization")
            + "</div>", key="parties.names"),
        Sub(p("Each is a &ldquo;<b>Party</b>&rdquo; and together they are the &ldquo;<b>Parties</b>&rdquo;. A Party may "
              "change its notice details by giving notice to the other under clause [[general.notices]]."),
            key="parties.terms"),
    ])


def definitions(services: list[dict[str, Any]]) -> Clause:
    names = join_and([esc(spec.title) for spec in DATABASES])
    cats = join_and([esc(c) for c in categories(services)])
    defs = [
        ("AI Output", "any text, code, embedding, classification, transcription or other content "
         "that a model produces through the Services in response to Client Data or to "
         "instructions from Licensee or its Authorized Users."),
        ("Authorized Users", "Licensee's employees and contractors whom Licensee permits to use "
         "the Services under its account."),
        ("Client Data", "all data, documents, prompts, files and datasets that Licensee or its "
         "Authorized Users submit to the Services, and everything the Services derive from them "
         "for Licensee, including prompt and completion logs, embeddings, search indexes, "
         "evaluation sets, fine-tuned model checkpoints and AI Output."),
        ("Confidential Information", "information one Party discloses to the other that is marked "
         "confidential or that a reasonable person would understand to be confidential. Client "
         "Data is Licensee's Confidential Information. The Software and any Documentation Licensor "
         "has not made public are Licensor's Confidential Information."),
        ("Documentation", "the user guides, knowledge-base articles and technical descriptions "
         "of the Services that Licensor makes available to Licensee."),
        ("Fee Code", "the code that identifies a service in the Fee Schedule and in Schedule 1."),
        ("Fee Schedule", "Licensor's schedule of fees, which states a unit price, billing unit and "
         "minimum units for each Fee Code, in the version identified in the applicable Order Form."),
        ("Order Form", "an ordering document signed by both Parties that lists the Services "
         "ordered by Fee Code, with quantities, start dates and terms."),
        ("Platform", f"the AI data center that Licensor operates under the name "
         f"&ldquo;{esc(DATA_CENTER_NAME)}&rdquo;, including the hardware, network and facilities "
         "used to provide the Services."),
        ("Services", f"the services listed in Schedule 1 that Licensee orders in an Order Form. "
         f"They fall into {words(len(categories(services)))} categories: {cats}."),
        ("Software", f"Licensor's software that runs the Platform, including its "
         f"{words(len(DATABASES))} databases ({names}), its command-line interface and web portal, "
         "and any updates Licensor makes generally available to its clients."),
        ("Third-Party Model", "a model, dataset or software component owned by someone other than "
         "Licensor and made available through the Services, whether hosted on the Platform or "
         "reached through another provider's service."),
    ]
    entries = "".join(f'<div><dt>&ldquo;{esc(term)}&rdquo;</dt> <dd>means {text}</dd></div>'
                      for term, text in defs)
    return Clause("defs", "Definitions", [
        Sub(p("In this Agreement:") + f'<dl class="defs">{entries}</dl>'),
        Sub(p("&ldquo;Including&rdquo; means including without limitation. Headings are for convenience only. "
              "The singular includes the plural. References to clauses and Schedules are to those "
              "of this Agreement. A requirement for something in writing includes email, but not "
              "for a notice of breach or termination."), title="Interpretation"),
    ])


def licence_grant(services: list[dict[str, Any]]) -> Clause:
    subs = [
        Sub("Subject to this Agreement and to payment of the fees, Licensor grants Licensee, "
            "for the Term, a non-exclusive, non-transferable license, without the right to "
            "sublicense except under clause [[grant.contractors]], for its Authorized Users to "
            "access and use the Software and the Services ordered in each Order Form, for "
            "Licensee's internal business purposes and within the quantities stated in that Order "
            "Form.", title="Grant", key="grant.grant"),
        Sub("The Software is provided as a hosted service. Where Licensor supplies client-side "
            "tools, such as command-line tools or configuration files, Licensee may install and "
            "use them only to access the Services.", title="Hosted delivery"),
        Sub("Licensee may let its contractors use the Services on its behalf as Authorized Users. "
            "Licensee is responsible for every act and omission of its Authorized Users as if they "
            "were its own.", title="Contractors", key="grant.contractors"),
        Sub("Some Services make Third-Party Models available. Each is subject to its own license "
            "terms, which Licensor shall identify in the Documentation or the Order Form before "
            "Licensee first uses it. Licensee shall comply with those terms, including any limits "
            "on field of use or on distributing outputs.", title="Third-Party Models",
            key="grant.third_party"),
    ]
    colocation = in_category(services, COLOCATION_CATEGORY)
    if colocation:
        codes = join_and([code(row["fee_code"]) for row in colocation])
        subs.append(Sub(
            f"Where an Order Form includes colocation services (Fee Codes {codes}), equipment "
            "Licensee installs at the Platform remains Licensee's property, and access to the data "
            "hall is subject to Licensor's site rules as given to Licensee in writing.",
            title="Colocation"))
    subs.append(Sub("All rights not expressly granted in this Agreement are reserved by "
                    "Licensor and its licensors.", title="Reservation of rights"))
    return Clause("grant", "License grant and scope", subs)


def restrictions() -> Clause:
    return Clause("restrict", "Restrictions", [
        Sub("Licensee shall not, and shall not permit anyone else to:" + items(
            "copy, modify or create derivative works of the Software, except as the "
            "Documentation permits;",
            "reverse engineer, decompile or disassemble the Software, except to the extent "
            "applicable law permits this despite this restriction;",
            "sell, resell, rent, lease or sublicense the Services, or provide them to third "
            "parties as a service bureau, unless an Order Form expressly permits it;",
            "use the Services to build a competing product, or publish benchmarks of the "
            "Services, without Licensor's prior written consent;",
            "circumvent usage limits, access controls, logging or content filtering;",
            "scan, probe or test the security of the Platform, except under a written test scope "
            "agreed with Licensor in advance;",
            "introduce malicious code into the Platform, or interfere with its operation or with "
            "other clients' use of it; or",
            "use the Services or AI Output in breach of clause [[ai]].",
        ), key="restrict.list"),
        Sub("If Licensee's use threatens the security or stability of the Platform, Licensor may "
            "suspend the affected Service as far as necessary to deal with the threat, giving "
            "notice beforehand where it can and otherwise promptly afterwards, and shall restore "
            "the Service once the threat is removed.",
            title="Suspension", key="restrict.suspend"),
    ])


def client_data(policy: Policy) -> Clause:
    return Clause("cd", "Client Data", [
        Sub("As between the Parties, Licensee owns all right, title and interest in Client Data. "
            "Licensor acquires no rights in Client Data other than the limited license in clause "
            "[[cd.license]].", title="Ownership", key="cd.own"),
        Sub("Licensee grants Licensor a non-exclusive, royalty-free license, for the Term and any "
            "deletion period under clause [[cd.delete]], to host, copy, process and transmit Client "
            "Data only as needed to provide the Services to Licensee, to resolve Licensee's support "
            "requests and security incidents, and as Licensee otherwise instructs in writing.",
            title="Licensor's use", key="cd.license"),
        Sub("Licensor shall use Client Data only for the purpose described in the applicable Order "
            "Form or statement of work. Any other use, including a new use within Licensee's own "
            "project, requires Licensee's prior written consent.", title="Purpose limitation",
            key="cd.purpose"),
        Sub("Licensor shall not use Client Data to train, fine-tune, evaluate or otherwise improve "
            "any model, or to build embedding indexes, evaluation sets or synthetic data, unless "
            "Licensee has given prior written consent to that specific use. Consent to one use does "
            "not extend to another. A model, checkpoint or index built from Client Data with that "
            "consent is used only for Licensee and is itself Client Data. Licensor shall never use "
            "Client Data, or anything built from it, for another client.",
            title="No training without consent", key="cd.training"),
        Sub("Where a Service sends Client Data to a Third-Party Model hosted by another provider, "
            "Licensor shall use only providers whose terms allow retention of prompts and training "
            "on customer data to be switched off, shall switch both off, and shall tell Licensee on "
            "request which providers each Service uses.", title="Hosted model providers",
            key="cd.providers"),
        Sub("Licensor shall store Client Data in a project namespace dedicated to Licensee, "
            "encrypted at rest, and shall limit access to the Licensor personnel assigned to "
            "Licensee's project. Licensor shall not copy Client Data to laptops or personal "
            "devices. Licensor shall review that access at least quarterly and give Licensee the "
            "current list of people with access on request.", title="Storage and access",
            key="cd.storage"),
        Sub(f"Prompt and completion logs from Licensee's deployments are kept for "
            f"{policy.log_days}&nbsp;days for troubleshooting and then deleted automatically.",
            title="Logs", key="cd.logs"),
        Sub("Licensee is responsible for having the rights, notices and consents needed to submit "
            "personal data to the Services and to have it processed as this Agreement describes, "
            "and shall identify in writing any dataset that contains sensitive personal data before "
            "submitting it. If Licensee tells Licensor that an individual's data must be removed, "
            "Licensor shall remove it from the source dataset, rebuild any index that included it, "
            "and tell Licensee which model versions were trained before the removal. Data "
            "protection law differs between jurisdictions and may call for further terms, such as "
            "a data processing agreement; any such terms are to be agreed in a separate document "
            "reviewed by each Party's attorney.", title="Personal data", key="cd.personal"),
        Sub(f"Licensee may request an export of its Client Data at any time during the Term or the "
            f"deletion period in clause [[cd.delete]]. Licensor shall deliver the export within "
            f"{policy.export_business_days}&nbsp;business days in {fill('EXPORT FORMAT')}.",
            title="Export", key="cd.export"),
        Sub(f"Licensee may ask for any Client Data to be deleted at any time. When this Agreement "
            f"or an Order Form ends, Licensor shall delete the affected Client Data from its working "
            f"systems within {policy.delete_days}&nbsp;days, and backups containing it shall age out "
            f"within {policy.backup_days}&nbsp;days after that. Licensor does not edit backups "
            "selectively; Client Data held in a backup until it ages out remains subject to clauses "
            "[[cd]] and [[sec]]. Licensor shall confirm each deletion in writing, listing the "
            "datasets, indexes and model checkpoints removed.", title="Deletion", key="cd.delete"),
        Sub("Licensor shall keep Client Data beyond the periods in this clause only where an Order "
            "Form says so expressly, such as an evaluation set Licensee asks Licensor to keep as a "
            "baseline for future work.", title="Agreed retention", key="cd.retain"),
        Sub("The periods in clauses [[cd.logs]], [[cd.export]] and [[cd.delete]] are Licensor's "
            "standard practice. The Parties may agree different periods in an Order Form.",
            title="Standard periods", key="cd.periods"),
    ])


def ai_outputs() -> Clause:
    return Clause("ai", "AI outputs and acceptable use", [
        Sub("AI Output is produced by statistical models. It can be inaccurate, incomplete or "
            "inappropriate even when it reads as confident, the same input can produce different "
            "outputs, and other clients may receive similar outputs.", title="Nature of AI Output",
            key="ai.nature"),
        Sub("Licensee is responsible for reviewing AI Output before relying on it, publishing it or "
            "giving it to others, and for every decision it makes using AI Output. A person at "
            "Licensee shall review AI Output, and own the decision, before it is used in a decision "
            "that significantly affects an individual, such as a decision about employment, credit, "
            "housing, insurance, education, health care or legal rights.",
            title="Licensee's responsibility", key="ai.review"),
        Sub("Licensee shall not use the Services or AI Output:" + items(
            "for any unlawful purpose, or in a way that infringes the intellectual property, "
            "privacy or other rights of others;",
            "to create or distribute malicious code, or to gain unauthorized access to systems "
            "or data;",
            "to defraud, deceive, harass or harm people, including by impersonating a person or by "
            "presenting AI Output as reviewed by a person when it was not;",
            "to create content that sexually exploits minors, or to help develop weapons capable "
            "of mass harm;",
            "to make decisions with legal or similarly significant effects on individuals without "
            "the human review required by clause [[ai.review]]; or",
            "in breach of the license terms of a Third-Party Model under clause [[grant.third_party]].",
        ), title="Prohibited uses", key="ai.prohibited"),
        Sub("If Licensor reasonably believes that a use breaches this clause, it may suspend the "
            "affected Service under clause [[restrict.suspend]]. Licensor does not review Client "
            "Data or AI Output for compliance unless an Order Form says it will.",
            title="Enforcement"),
    ])


def fees(services: list[dict[str, Any]]) -> Clause:
    example = ""
    above_one = [row for row in services if float(row["minimum_units"]) > 1]
    if above_one:
        row = max(above_one, key=lambda r: (float(r["minimum_units"]), str(r["fee_code"])))
        example = (f" For example, Fee Code {code(row['fee_code'])} ({esc(row['service'])}) is "
                   f"billed per {esc(row['unit'])} with a minimum of "
                   f"{float(row['minimum_units']):g}&nbsp;units.")
    return Clause("fees", "Fees and payment", [
        Sub("Licensee shall pay, for each Service, the unit price shown against its Fee Code in "
            "the Fee Schedule identified in the applicable Order Form, for the quantities used or "
            "ordered. Schedule 1 lists the Fee Codes.", title="Fees", key="fees.fees"),
        Sub("Each Service is billed in the unit shown for it in Schedule 1, and never for fewer "
            f"than the minimum units stated in the Fee Schedule.{example}", title="Billing units"),
        Sub(f"Licensor shall invoice {fill('INVOICING FREQUENCY AND TIMING')}. Licensee shall pay "
            f"each invoice within {fill('PAYMENT TERMS')} of the invoice date by "
            f"{fill('PAYMENT METHOD')}. Fees are stated in {fill('CURRENCY')}.",
            title="Invoicing and payment", key="fees.invoice"),
        Sub(f"Licensee may dispute an invoice in good faith by notice given within "
            f"{fill('DISPUTE PERIOD')} of receiving it, stating the reasons, and shall pay the "
            "undisputed part when due. The Parties shall work to resolve the dispute promptly.",
            title="Disputed invoices"),
        Sub(f"Overdue amounts bear interest at {fill('LATE PAYMENT RATE')}, but never more than "
            f"the highest rate applicable law allows. If an undisputed amount is overdue for "
            f"{fill('PERIOD')} after written notice, Licensor may suspend the Services until it is "
            "paid.", title="Late payment"),
        Sub("Fees exclude taxes. Licensee shall pay any sales, use, value-added or similar taxes "
            "on the Services, other than taxes on Licensor's income. Which taxes apply depends on "
            "where the Parties and the Services are located; each Party should take its own tax "
            "advice.", title="Taxes"),
        Sub(f"Licensor may change the Fee Schedule by giving at least {fill('NOTICE PERIOD')} "
            "written notice. A change applies to an Order Form only from its next renewal, and "
            "never to a committed term already running.", title="Changes to fees"),
    ])


def service_levels(services: list[dict[str, Any]]) -> Clause:
    support = in_category(services, SUPPORT_CATEGORY)
    if not support:
        raise LicenseAgreementError(
            f"no {SUPPORT_CATEGORY} services in {SERVICE_TABLE}; the service levels clause "
            "has no support tiers to reference")
    rows = [[code(row["fee_code"]), esc(row["service"]), esc(row["unit"]), esc(row.get("notes"))]
            for row in support]
    tiers = table(["Fee Code", "Support service", "Billed per", "Service description"], rows,
                  widths=["13%", "33%", "14%", "40%"])
    return Clause("sla", "Service levels", [
        Sub(p("Licensee's support level is set by the support services in its Order Form. The "
              "support services available, each listed in Schedule 1, are:") + tiers
            + p("For each support service Licensee has ordered whose description states a "
                "response time, Licensor shall meet that response time."),
            title="Support tiers", key="sla.tiers"),
        Sub(f"A response time runs from Licensee's report through {fill('SUPPORT CHANNEL')} to the "
            f"first response from a qualified Licensor engineer. It is not a time to resolve the "
            f"issue. &ldquo;Business hours&rdquo; means {fill('BUSINESS HOURS, DAYS AND TIME ZONE')}.",
            title="Response times"),
        Sub("If an Order Form includes no support service, Licensor shall provide support through "
            "the support channel on a reasonable-efforts basis, with no committed response time.",
            title="Without a support tier"),
        Sub(f"Licensor shall make each Service available at least {fill('AVAILABILITY TARGET')} of "
            f"the time in each calendar month, excluding maintenance announced at least "
            f"{fill('NOTICE PERIOD')} in advance and events under clause [[general.force]]. If "
            f"Licensor misses the target, Licensee is entitled to {fill('SERVICE CREDITS')}.",
            title="Availability", key="sla.availability"),
        Sub("The recovery point objective and recovery time objective for each of Licensee's "
            "deployments shall be stated in the Order Form. If an event exceeds either, Licensor's "
            "incident report shall say so and explain why.", title="Recovery objectives"),
    ])


def security(policy: Policy) -> Clause:
    return Clause("sec", "Security and confidentiality", [
        Sub("Licensor shall maintain administrative, technical and physical safeguards designed to "
            "protect Client Data, including encryption at rest, access granted per project on a "
            "least-privilege basis and reviewed regularly, an audit log of access changes and data "
            "events, and encrypted backups stored separately from the primary systems.",
            title="Security measures", key="sec.measures"),
        Sub(f"Licensee may request the audit log for its project at any time, and Licensor shall "
            f"deliver it within {policy.audit_business_days}&nbsp;business days. Audit entries record "
            "who did what and when, not the content of Client Data.",
            title="Audit log"),
        Sub(f"Licensor shall notify Licensee within {policy.notice_hours}&nbsp;hours of confirming that "
            f"Client Data has been accessed by, or disclosed to, anyone not authorized under this "
            f"Agreement, and shall give Licensee a written summary within "
            f"{policy.summary_business_days}&nbsp;business days of confirmation, describing what "
            "happened, the data affected and the steps taken. Whether either Party must also "
            "notify regulators or individuals depends on the law that applies; the Parties shall "
            "cooperate on any such notice.", title="Security incidents", key="sec.incident"),
        Sub("Licensee is responsible for keeping its credentials and keys secret, for the devices "
            "and networks its Authorized Users use, and for telling Licensor promptly if it "
            "suspects a credential has been compromised.", title="Licensee's security"),
        Sub("Each Party that receives Confidential Information shall keep it confidential, use it "
            "only to perform this Agreement or exercise its rights under it, and disclose it only "
            "to its personnel, contractors and professional advisers who need to know it and are "
            "bound by confidentiality duties at least as protective as this clause.",
            title="Confidentiality", key="sec.conf"),
        Sub("Clause [[sec.conf]] does not apply to information that is or becomes public through "
            "no fault of the receiving Party, that the receiving Party already lawfully knew, that "
            "it develops independently, or that it receives from a third party free to disclose "
            "it. This exception never applies to Client Data.", title="Exceptions"),
        Sub("A Party may disclose Confidential Information when required by law or by a court or "
            "other authority, after giving the other Party notice where the law allows, and only "
            "to the extent required.", title="Required disclosure"),
        Sub(f"The duties in this clause continue for {fill('PERIOD')} after this Agreement ends, "
            "and for Client Data and trade secrets for as long as they remain confidential.",
            title="Duration"),
    ])


def intellectual_property(services: list[dict[str, Any]]) -> Clause:
    tuning = [row for row in services if "fine-tun" in str(row["service"]).lower()]
    tuning_ref = ""
    if tuning:
        tuning_ref = " (Fee Code " + join_and([code(row["fee_code"]) for row in tuning]) + ")"
    return Clause("ip", "Intellectual property", [
        Sub("Licensor and its licensors own the Software, the Platform, the Documentation and all "
            "improvements to them, including anything Licensor develops while providing the "
            "Services, but excluding Client Data.", title="Licensor's property"),
        Sub("As between the Parties, Licensee owns Client Data, including AI Output, and Licensor "
            "assigns to Licensee any rights it may have in AI Output. Whether AI Output is protected "
            "by intellectual property rights depends on the law that applies, and Licensor does "
            "not promise that it is.", title="Licensee's property"),
        Sub(f"Model weights that a fine-tuning service{tuning_ref} produces from Client Data are "
            "Client Data, subject to the license terms of the Third-Party Model they were built "
            "from. Licensor shall export them to Licensee on request where those terms allow.",
            title="Fine-tuned models"),
        Sub("Licensor may freely use suggestions and feedback about the Services, provided that "
            "doing so does not disclose Client Data or Licensee's other Confidential Information.",
            title="Feedback"),
        Sub("Licensor may record usage measurements, such as compute hours, tokens processed and "
            "storage used, to bill, plan capacity and operate the Platform. Usage measurements "
            "do not include the content of Client Data, and Licensor shall not disclose them in a "
            "form that identifies Licensee.", title="Usage measurements"),
    ])


def warranties() -> Clause:
    return Clause("warr", "Warranties and disclaimers", [
        Sub("Each Party warrants that it has the authority to enter into and perform this "
            "Agreement.", title="Authority"),
        Sub("Licensor warrants that the Services will perform materially as described in the "
            "Documentation, and that it will provide professional services with reasonable skill "
            "and care. If Licensee notifies a breach of this warranty within "
            f"{fill('WARRANTY CLAIM PERIOD')}, Licensor shall correct the Service or re-perform the "
            "work; if it cannot do so within a reasonable time, either Party may end the affected "
            "Service and Licensor shall refund any fees prepaid for the period after it ends.",
            title="Services"),
        Sub("Licensee warrants that it has the rights needed to submit Client Data to the Services "
            "and to have it processed under this Agreement, and that its use of the Services "
            "complies with clause [[ai]].", title="Client Data"),
        Sub("<b>Except as expressly stated in this Agreement, the Software, the Services and AI "
            "Output are provided &ldquo;as is&rdquo;. Licensor does not warrant that the Services "
            "will be uninterrupted or error-free, or that AI Output will be accurate, complete or "
            "fit for any purpose, and disclaims all implied warranties, including merchantability, "
            "fitness for a particular purpose and non-infringement.</b> Some jurisdictions limit "
            "how far implied warranties can be excluded and how such an exclusion must be "
            "presented; this clause applies only to the extent the law that applies allows.",
            title="Disclaimer", key="warr.disclaimer"),
    ])


def liability() -> Clause:
    return Clause("liab", "Limitation of liability", [
        Sub(f"Each Party's total liability arising out of or relating to this Agreement, whether "
            f"in contract, tort or otherwise, shall not exceed {fill('LIABILITY CAP')}.",
            title="Cap", key="liab.cap"),
        Sub("Neither Party is liable for loss of profits, revenue or goodwill, or for indirect, "
            "special, incidental, consequential or punitive damages, even if it was told they were "
            "possible.", title="Excluded losses", key="liab.excluded"),
        Sub(f"Clauses [[liab.cap]] and [[liab.excluded]] do not apply to {fill('EXCLUSIONS FROM THE CAP')}, "
            "or to any liability that cannot be limited or excluded under the law that applies. "
            "How far liability can be limited differs between jurisdictions and types of claim, "
            "and this clause applies only to the extent that law allows.",
            title="Exceptions"),
    ])


def term_and_termination() -> Clause:
    return Clause("term", "Term and termination", [
        Sub(f'This Agreement starts on the Effective Date and continues for {fill("INITIAL TERM")} '
            f"(with each renewal, the &ldquo;<b>Term</b>&rdquo;). It then renews for successive "
            f"periods of {fill('RENEWAL PERIOD')} unless either Party gives at least "
            f"{fill('NOTICE PERIOD')} written notice of non-renewal before the end of the "
            "current period. Each Order Form runs for the term stated in it, and this Agreement "
            "continues to govern an Order Form until that Order Form ends.", title="Term"),
        Sub(f"Either Party may terminate this Agreement or an affected Order Form by written notice "
            f"if the other Party materially breaches it and does not cure the breach within "
            f"{fill('CURE PERIOD')} after receiving written notice describing it.",
            title="Termination for breach"),
        Sub("Either Party may terminate this Agreement by written notice if the other Party becomes "
            "insolvent, makes a general assignment for the benefit of its creditors, or becomes "
            "subject to bankruptcy, receivership or similar proceedings, to the extent the law that "
            "applies allows termination on those grounds.", title="Insolvency"),
        Sub("When this Agreement or an Order Form ends: Licensee's right to use the affected "
            "Services ends; Licensee shall pay all fees accrued up to the end date; Client Data is "
            "exported and deleted under clauses [[cd.export]] and [[cd.delete]]; and each Party "
            "shall return or destroy the other's Confidential Information, subject to backups "
            "aging out as clause [[cd.delete]] describes.", title="Effect of termination",
            key="term.effect"),
        Sub("Clauses [[cd]], [[fees]] (for amounts owed), [[sec]], [[ip]], [[warr.disclaimer]], "
            "[[liab]], [[term.effect]], [[law]] and [[general]] survive the end of this Agreement.",
            title="Survival"),
    ])


def governing_law() -> Clause:
    return Clause("law", "Governing law and disputes", [
        Sub(f"This Agreement is governed by the laws of {fill('GOVERNING LAW JURISDICTION')}, "
            "without regard to its conflict-of-laws rules.", title="Governing law"),
        Sub(f"A Party with a dispute shall give the other written notice of it. Senior "
            f"representatives of both Parties shall try in good faith to resolve it for "
            f"{fill('PERIOD')} after the notice before either Party starts proceedings.",
            title="Escalation"),
        Sub(f"A dispute not resolved under the escalation process shall be decided by "
            f"{fill('COURTS OF A NAMED VENUE, OR ARBITRATION UNDER NAMED RULES AND SEAT')}.",
            title="Forum"),
        Sub("Either Party may seek urgent injunctive or similar relief from a court of competent "
            "jurisdiction to protect its Confidential Information or intellectual property.",
            title="Urgent relief"),
    ])


def general() -> Clause:
    return Clause("general", "General", [
        Sub("This Agreement, its Schedules, each Order Form and the Fee Schedule an Order Form "
            "identifies are the entire agreement between the Parties on their subject. If they "
            "conflict, this Agreement prevails, then Schedule 1, then the Fee Schedule, unless an "
            "Order Form expressly overrides a named clause for the Services in that Order Form.",
            title="Entire agreement"),
        Sub("A change to this Agreement is valid only in writing signed by both Parties.",
            title="Amendment"),
        Sub("Neither Party may assign this Agreement without the other's written consent, which "
            "shall not be unreasonably withheld, except to a successor in a merger or a sale of "
            "substantially all of its business, on written notice to the other Party.",
            title="Assignment"),
        Sub(f"Notices are to be sent to the contact and address in clause [[parties]]. Routine "
            f"notices may be sent by email. A notice of breach, termination or dispute must also be "
            f"sent by {fill('NOTICE METHOD')}.", title="Notices", key="general.notices"),
        Sub("Neither Party is liable for a failure or delay caused by events beyond its reasonable "
            "control, such as natural disasters, utility or network failures outside the Platform, "
            "or acts of government, provided it notifies the other Party promptly and works to "
            "limit the effect. This clause does not excuse a payment obligation.",
            title="Force majeure", key="general.force"),
        Sub("Licensor may use subcontractors to provide the Services and remains responsible for "
            "them. On request, Licensor shall identify each subcontractor that processes Client "
            "Data.", title="Subcontractors"),
        Sub("The Parties are independent contractors. Nothing in this Agreement creates a "
            "partnership, joint venture, agency or employment relationship.",
            title="Relationship"),
        Sub("Each Party shall comply with the export control and sanctions laws that apply to it "
            "in connection with the Services.", title="Export and sanctions"),
        Sub("If a provision is held unenforceable, it applies to the extent it is enforceable and "
            "the rest of this Agreement continues. Delay in exercising a right does not waive it.",
            title="Severability and waiver"),
        Sub("This Agreement may be signed in counterparts, and electronically to the extent the "
            "law that applies recognizes electronic signatures. Together the counterparts form "
            "one agreement.", title="Counterparts"),
    ])


def signatures() -> Clause:
    def block(role: str) -> str:
        return (f"<div><h3>{esc(role)}</h3>"
                + field_line("Legal name of party")
                + field_line("Signature", tall=True)
                + field_line("Name")
                + field_line("Title")
                + field_line("Date")
                + "</div>")
    return Clause("sign", "Signatures", [
        Sub(p("Signed by the authorized representative of each Party, effective on the Effective "
              "Date.")
            + f'<div class="sign">{block("Licensor")}{block("Licensee")}</div>'),
    ])


# -- assembly ---------------------------------------------------------------------

def number_clauses(clauses: list[Clause]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for index, clause in enumerate(clauses, start=1):
        if clause.key in refs:
            raise LicenseAgreementError(f"duplicate clause key {clause.key}")
        refs[clause.key] = str(index)
        for sub_index, sub in enumerate(clause.subs, start=1):
            if sub.key:
                if sub.key in refs:
                    raise LicenseAgreementError(f"duplicate clause key {sub.key}")
                refs[sub.key] = f"{index}.{sub_index}"
    return refs


def resolve(html_text: str, refs: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in refs:
            raise LicenseAgreementError(f"cross-reference to unknown clause {key!r}")
        return refs[key]
    return re.sub(r"\[\[([a-z_.]+)\]\]", replace, html_text)


def render_clauses(clauses: list[Clause], refs: dict[str, str], codes: set[str]) -> tuple[str, dict[str, list[str]]]:
    """The clauses as HTML, and for each fee code the clause numbers that name it."""
    code_pattern = re.compile(r"\b[A-Z]{2,5}-\d{2,4}\b")
    cited: dict[str, list[str]] = {}
    parts = []
    for index, clause in enumerate(clauses, start=1):
        subs_html = []
        for sub_index, sub in enumerate(clause.subs, start=1):
            number = f"{index}.{sub_index}"
            body = resolve(sub.html, refs)
            for found in code_pattern.findall(body):
                if found not in codes:
                    raise LicenseAgreementError(f"clause {number} names fee code {found}, "
                                                f"which is not in {SERVICE_TABLE}")
                if number not in cited.setdefault(found, []):
                    cited[found].append(number)
            runin = f'<span class="runin">{esc(sub.title)}.</span> ' if sub.title else ""
            if body.startswith("<p>") and runin:
                body = "<p>" + runin + body[3:]
            elif runin:
                body = runin + body
            subs_html.append(f'<div class="cl"><span class="n">{number}</span>{body}</div>')
        head = f"<h2>{index}. {esc(clause.title)}</h2>"
        lead = f'<div class="lead">{head}{subs_html[0]}</div>' if subs_html else head
        parts.append(f'<section class="article">{lead}{"".join(subs_html[1:])}</section>')
    return "".join(parts), cited


def preamble(services: list[dict[str, Any]], policy: Policy) -> str:
    articles = join_and([f"&ldquo;{esc(title)}&rdquo;" for title in policy.articles.values()])
    return (
        '<div class="preamble">'
        + p(f"This {esc(TITLE)} (this &ldquo;<b>Agreement</b>&rdquo;) is made on "
            f"{fill('EFFECTIVE DATE')} (the &ldquo;<b>Effective Date</b>&rdquo;) between the "
            "Licensor and the Licensee named in clause [[parties]].",
            "Licensor operates an AI data center and the software that runs it. Licensee wishes to "
            "use that software and the services provided with it, and Licensor is willing to "
            "provide them on the terms of this Agreement.")
        + '<div class="drafting"><h3>Completing this template</h3><ul>'
        "<li>Complete or strike out every highlighted [BRACKETED BLANK] and every blank line "
        "before signature. No blank has a default value.</li>"
        f"<li>Schedule 1 lists the {len(services)} services Licensor offered on "
        f"{ISSUED:%B %-d, %Y}; check it against the Fee Schedule named in the Order Form.</li>"
        "<li>The periods for logs, export, deletion, incident notice and audit-log delivery follow "
        f"Licensor's knowledge-base articles {articles}. Change them there or in an Order Form, "
        "not by editing one copy of this template.</li>"
        "<li>Contract, data protection, consumer and tax law differ between jurisdictions. Some "
        "clauses may need changes, or may not be enforceable, where the Parties are, and whether "
        "the choice of law and forum in clause [[law]] will be enforced depends on the Parties and "
        "the claim. A licensed attorney should review the whole Agreement before it is signed.</li>"
        "</ul></div></div>"
    )


def schedule(services: list[dict[str, Any]], cited: dict[str, list[str]]) -> str:
    cats = categories(services)
    rows = []
    for row in services:
        fee_code = str(row["fee_code"])
        where = cited.get(fee_code)
        rows.append([
            code(fee_code),
            esc(row["service"]),
            esc(row["category"]),
            esc(row["unit"]),
            ", ".join(where) if where else '<span class="muted">&ndash;</span>',
        ])
    referenced = sum(1 for row in services if cited.get(str(row["fee_code"])))
    return (
        '<section class="schedule page-break">'
        "<h2>Schedule 1. Services and Fee Codes</h2>"
        + p(f"The {len(services)} services in {words(len(cats))} categories that an Order Form may "
            f"order under this Agreement, as Licensor offered them on {ISSUED:%B %-d, %Y}. Unit "
            f"prices and minimum units are in the Fee Schedule. The last "
            f"column gives the clauses of this Agreement that name a Fee Code; {referenced} of the "
            f"{len(services)} are named in a clause.").replace("<p>", '<p class="intro">', 1)
        + table(["Fee Code", "Service", "Category", "Billing unit", "Clause"], rows,
                widths=["13%", "41%", "16%", "18%", "12%"])
        + "</section>"
    )


def build() -> Path:
    with seeded_center() as center:
        services = read_services(center)
        policy = read_policy(center)

    clauses = [
        parties(),
        definitions(services),
        licence_grant(services),
        restrictions(),
        client_data(policy),
        ai_outputs(),
        fees(services),
        service_levels(services),
        security(policy),
        intellectual_property(services),
        warranties(),
        liability(),
        term_and_termination(),
        governing_law(),
        general(),
        signatures(),
    ]
    refs = number_clauses(clauses)
    codes = {str(row["fee_code"]) for row in services}
    clauses_html, cited = render_clauses(clauses, refs, codes)
    body = (EXTRA_CSS + resolve(preamble(services, policy), refs) + clauses_html
            + schedule(services, cited))
    if re.search(r"\bNone\b|\[\[", body):
        raise LicenseAgreementError("unfilled value or unresolved cross-reference in the agreement")
    # A client cannot see the data center's own tables; the agreement points to
    # the documents it defines, Schedule 1 and the Fee Schedule, instead.
    visible = re.sub(r"<[^>]+>", " ", body)
    if re.search(rf"\b{SERVICE_TABLE}\b|\bservice[ _]fees?\s+table\b", visible, flags=re.IGNORECASE):
        raise LicenseAgreementError(f"the agreement names the internal {SERVICE_TABLE} table; "
                                    "refer to Schedule 1 or the Fee Schedule")
    html_text = document(
        TITLE, body,
        eyebrow="Agreement template",
        subtitle="Between the business, as Licensor, and a client, as Licensee, for the AI data "
                 "center software and services",
        notice=TEMPLATE_NOTICE,
    )
    return render(NAME, html_text)


if __name__ == "__main__":
    print(build())
