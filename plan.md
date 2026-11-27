# PMbot: a project manager that lives in Discord

Our work already happens in Discord: decisions in channels, questions in threads, and a dozen things everyone quietly assumes someone else is doing. The problem isn’t a missing tool, it’s that the plan lives in my head and in pinned messages nobody reads. I want a helper that keeps the plan inside the conversation, without asking anyone to leave for a new app or learn a new habit.

## What it should do for us

- **Start a project in one step.** Answer a few questions and there is a place for the work: the tasks, the deadlines, and who owns what.
- **Show the state at a glance.** One message that always says what is open, what is late and who is on it, kept current without anyone maintaining a list by hand.
- **Never let a deadline arrive unnoticed.** A quiet nudge to the person responsible while there is still time, and a word to whoever is in charge once something slips.
- **A standup that runs itself.** It asks the team each day what moved and collects the answers in one place; on Sunday evening it closes the week with a short summary of what finished, what slipped and what looks risky.

## How it should feel

- Nothing to install and no new interface: it behaves like a sensible member of the server. It stays quiet unless it has something worth saying, and the plan is always one **!todo** away and readable in the channel itself.

## What I do not want

- Another dashboard to log into, a second place where the truth is supposed to live, or time tracking and billable hours. This should make the team’s week lighter, not add ceremony.

**Questions to settle first:** should this be its own helper or part of the one already in the server? Who is allowed to start a project? When in the day is the check-in?

---

# Features: starting from the smallest thing that works

Every need below gets the least effort that actually removes the annoyance. If a feature needs explaining in the channel, it is too big for the first version.

## Worth having now

- **One todo list per project.** A single message, summoned with **!todo**, that always shows the tasks, owners and dates. People look at it; nobody edits a list by hand.
- **A task is one line with one owner and one date.** If it has neither, it is still an idea, not a task, and that rule alone removes most clutter.
- **Replying with a tap.** “Done · On it · Blocked · Not started” buttons instead of typing a status update, so a check-in costs one second.
- **The daily question.** The bot asks once, in the channel, and collects the answers itself: no forms, no second conversation.
- **Two reminders, ever.** One before the deadline, one when it is first late. After that it appears in the end of week note and stays quiet.
- **The end of week note.** Posted Sunday evening: what finished, what slipped, what looks at risk. One screen, the same shape every week.
- **“Who is on this?”** One question answers ownership without asking the room.

## Later, if it earns its place

- **My week.** A short direct message on Sunday with only my tasks and dates.
- **Templates.** Reuse the shape of a project that went well instead of starting from nothing.
- **A gentle capacity hint.** Mention that someone already has six open tasks before handing over a seventh.
- **Looking back.** “What did we decide about …” that finds the closed task and its outcome.
- **Something to paste elsewhere.** A plain summary that can be screenshotted for a client without exporting a report.

## Deliberately skipped

- Drag-and-drop boards, Gantt charts, custom fields and burndown lines, ceremony for the sake of looking organised. Notifications for every change. A thread per task. Time tracking, ticket-system sync, and a web dashboard. Settings for everything: good defaults beat a control panel. AI drafting: turning a sentence into tasks needs a model with project context, too much to build and too much to trust for the first version.

**How we will judge an idea:** it must save more time than it costs to use, a newcomer must get it right after reading one example message, and it must not create a second place where the plan lives.
