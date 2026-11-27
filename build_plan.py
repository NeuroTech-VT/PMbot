#!/usr/bin/env python3
"""Render the conceptual planning doc for PMbot (Discord project-manager bot).

Page 1: what it is and why (written from the user's side).
Page 2: feature brainstorm, with the smallest thing that would solve each need.

Rules followed (see AGENTS.md): body text Times New Roman 12 pt; high-level,
user's point of view, no tools/stacks/schemas/libraries.

Single source of truth for the content: writes both
  pmbot/plan.md  (editable notes)
  pmbot/plan.pdf (two-page one-pager)

Run: ../.venv-pdf/bin/python pmbot/build_plan.py
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

BASE = Path(__file__).resolve().parent
PDF_PATH = BASE / "plan.pdf"
MD_PATH = BASE / "plan.md"

NAVY = colors.HexColor("#1f3a5f")

TITLE = "PMbot: a project manager that lives in Discord"

INTRO = (
    "Our work already happens in Discord: decisions in channels, questions in threads, and a dozen "
    "things everyone quietly assumes someone else is doing. The problem isn’t a missing tool, it’s that "
    "the plan lives in my head and in pinned messages nobody reads. I want a helper that keeps the plan "
    "inside the conversation, without asking anyone to leave for a new app or learn a new habit."
)

PAGE1 = [
    (
        "What it should do for us",
        [
            "<b>Start a project in one step.</b> Answer a few questions and there is a place for the work: "
            "the tasks, the deadlines, and who owns what.",
            "<b>Show the state at a glance.</b> One message that always says what is open, what is late and "
            "who is on it, kept current without anyone maintaining a list by hand.",
            "<b>Never let a deadline arrive unnoticed.</b> A quiet nudge to the person responsible while there "
            "is still time, and a word to whoever is in charge once something slips.",
            "<b>A standup that runs itself.</b> It asks the team each day what moved and collects the answers "
            "in one place; on Sunday evening it closes the week with a short summary of what finished, what slipped "
            "and what looks risky.",
        ],
    ),
    (
        "How it should feel",
        [
            "Nothing to install and no new interface: it behaves like a sensible member of the server. "
            "It stays quiet unless it has something worth saying, and the plan is always one <b>!todo</b> away "
            "and readable in the channel itself.",
        ],
    ),
    (
        "What I do not want",
        [
            "Another dashboard to log into, a second place where the truth is supposed to live, or "
            "time tracking and billable hours. This should make the team’s week lighter, not add ceremony.",
        ],
    ),
]

OPEN1 = (
    "<b>Questions to settle first:</b> should this be its own helper or part of the one already in the "
    "server? Who is allowed to start a project? When in the day is the check-in?"
)

PAGE2_TITLE = "Features: starting from the smallest thing that works"

PAGE2_INTRO = (
    "Every need below gets the least effort that actually removes the annoyance. If a feature needs "
    "explaining in the channel, it is too big for the first version."
)

PAGE2 = [
    (
        "Worth having now",
        [
            "<b>One todo list per project.</b> A single message, summoned with <b>!todo</b>, that always shows the "
            "tasks, owners and dates. People look at it; nobody edits a list by hand.",
            "<b>A task is one line with one owner and one date.</b> If it has neither, it is still an idea, "
            "not a task, and that rule alone removes most clutter.",
            "<b>Replying with a tap.</b> “Done · On it · Blocked · Not started” buttons instead of typing a "
            "status update, so a check-in costs one second.",
            "<b>The daily question.</b> The bot asks once, in the channel, and collects the answers itself: "
            "no forms, no second conversation.",
            "<b>Two reminders, ever.</b> One before the deadline, one when it is first late. After that it "
            "appears in the end of week note and stays quiet.",
            "<b>The end of week note.</b> Posted Sunday evening: what finished, what slipped, what looks at risk. "
            "One screen, the same shape every week.",
            "<b>“Who is on this?”</b> One question answers ownership without asking the room.",
        ],
    ),
    (
        "Later, if it earns its place",
        [
            "<b>My week.</b> A short direct message on Sunday with only my tasks and dates.",
            "<b>Templates.</b> Reuse the shape of a project that went well instead of starting from nothing.",
            "<b>A gentle capacity hint.</b> Mention that someone already has six open tasks before handing "
            "over a seventh.",
            "<b>Looking back.</b> “What did we decide about …” that finds the closed task and its outcome.",
            "<b>Something to paste elsewhere.</b> A plain summary that can be screenshotted for a client "
            "without exporting a report.",
        ],
    ),
    (
        "Deliberately skipped",
        [
            "Drag-and-drop boards, Gantt charts, custom fields and burndown lines, ceremony for the sake of "
            "looking organised. Notifications for every change. A thread per task. Time tracking, ticket-system "
            "sync, and a web dashboard. Settings for everything: good defaults beat a control panel. AI drafting: "
            "turning a sentence into tasks needs a model with project context, too much to build and too much "
            "to trust for the first version.",
        ],
    ),
]

OPEN2 = (
    "<b>How we will judge an idea:</b> it must save more time than it costs to use, a newcomer must get it "
    "right after reading one example message, and it must not create a second place where the plan lives."
)


def styles():
    body = dict(fontName="Times-Roman", fontSize=12, leading=14.2,
                textColor=colors.HexColor("#1b1f23"))
    return {
        "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=17, leading=19, textColor=NAVY),
        "title2": ParagraphStyle("title2", fontName="Times-Bold", fontSize=15, leading=17, textColor=NAVY),
        "h": ParagraphStyle("h", fontName="Times-Bold", fontSize=12, leading=14, textColor=NAVY,
                            spaceBefore=7, spaceAfter=3),
        "body": ParagraphStyle("body", **body),
        "bullet": ParagraphStyle("bullet", leftIndent=14, bulletIndent=2, bulletFontName="Times-Roman",
                                 bulletFontSize=12, **body),
    }


def story(s):
    out = [Paragraph(TITLE, s["title"]), Spacer(1, 4),
           HRFlowable(width="100%", thickness=1.0, color=NAVY, spaceBefore=1, spaceAfter=5),
           Paragraph(INTRO, s["body"])]
    for name, items in PAGE1:
        out.append(Paragraph(name.upper(), s["h"]))
        for it in items:
            out.append(Paragraph(it, s["bullet"], bulletText="–"))
    out.append(Paragraph(OPEN1, s["body"]))

    out += [PageBreak(), Paragraph(PAGE2_TITLE, s["title2"]), Spacer(1, 4),
            HRFlowable(width="100%", thickness=1.0, color=NAVY, spaceBefore=1, spaceAfter=5),
            Paragraph(PAGE2_INTRO, s["body"])]
    for name, items in PAGE2:
        out.append(Paragraph(name.upper(), s["h"]))
        for it in items:
            out.append(Paragraph(it, s["bullet"], bulletText="–"))
    out.append(Paragraph(OPEN2, s["body"]))
    return out


def build_pdf():
    s = styles()
    doc = SimpleDocTemplate(
        str(PDF_PATH), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=16 * mm,
        title="PMbot: planning doc", author="Owen Harlow",
        subject="Planning doc: PMbot, a project-manager Discord bot",
    )
    flow = story(s)
    frame_w = A4[0] - 40 * mm
    heights, current = [], 0.0
    for f in flow:
        if isinstance(f, PageBreak):
            heights.append(current)
            current = 0.0
            continue
        current += (f.wrap(frame_w, A4[1])[1] + getattr(f, "getSpaceBefore", lambda: 0)()
                    + getattr(f, "getSpaceAfter", lambda: 0)())
    heights.append(current)
    doc.build(flow)
    return heights


def write_md():
    def clean(t):
        return t.replace("<b>", "**").replace("</b>", "**")

    lines = [f"# {TITLE}", "", INTRO, ""]
    for name, items in PAGE1:
        lines += [f"## {name}", ""] + [f"- {clean(it)}" for it in items] + [""]
    lines += [clean(OPEN1), "", "---", "", f"# {PAGE2_TITLE}", "", PAGE2_INTRO, ""]
    for name, items in PAGE2:
        lines += [f"## {name}", ""] + [f"- {clean(it)}" for it in items] + [""]
    lines += [clean(OPEN2), ""]
    MD_PATH.write_text("\n".join(lines))


if __name__ == "__main__":
    heights = build_pdf()
    write_md()
    usable = A4[1] - 34 * mm
    for i, h in enumerate(heights, 1):
        print(f"page {i}: {h / mm:.0f} mm of {usable / mm:.0f} mm usable "
              f"(~{100 * h / usable:.0f}% of the text area)")
    print(f"wrote {PDF_PATH} and {MD_PATH}")
