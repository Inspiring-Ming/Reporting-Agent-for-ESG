# Presenter script — AMP AI Expo stall

**Total walk-through time:** ~3 minutes per attendee.
**Three activities, one screen, three short stories.**

---

## 30-second hook (use when someone walks up)

> "Most people have asked ChatGPT something it got confidently wrong.
> I've built an AI agent that *can't* do that — because instead of guessing,
> it has to look up an answer in a knowledge graph and show you where the
> number came from. Want to see the difference?"

Click **Tab 1**.

---

## Tab 1 — Grounded vs Ungrounded (~75 sec)

**What it shows:** the same ESG question answered two ways. Now with a free-text
box (so attendees can ask anything they want) and on-the-fly visualisations.

1. **Open with a chip.** Click the *TSMC water 2023* chip — fastest path to
   the headline moment.
2. Read the **left pane** out loud — sounds plausible, sounds confident, no
   source. Point at the red issues list.
3. Now the **right pane** — the agent identifies the SASB metric, framework,
   unit, the years of data available, and **withholds the number** because
   the engine's unit doesn't match the SASB definition (off by 1000×).
4. **Scroll down.** The coverage grid shows which (metric × year) cells have
   data — most are sparse, with one or two `!` cells flagging the same
   unit-mismatch issue across years. The trend chart shows water withdrawal
   2016 → 2023 with a clear caveat banner: "shape is informative, absolute
   numbers not yet reconciled".
5. **Now invite them to type.** *"Try changing the year, or the company. Type
   'TSMC water 2050' — something that doesn't exist."* The agent will say so.
   Type 'Apple emissions' — the parser strip will say `missing: company` and
   the agent refuses to invent.

**Punchline to deliver:**
> "Look at the difference. The chatbot invents a number. The agent says
> exactly what the framework asks for, and either gives you a value with
> lineage or tells you it can't confirm it. The second behaviour is worth
> more than the first — that's a system you can put in front of an auditor."

**Why year matters (riff for finance / risk attendees):**
> "Year-on-year is the entire game in ESG analysis — that's how you decide
> whether a company is actually improving or just gaming the disclosure
> period. Watch what happens when I ask for a year we don't have data
> for…" *[type 'TSMC water 2030']* "…the agent tells you. It doesn't
> silently substitute the nearest year. That's the same discipline you'd
> want from any analyst."

**If the value is withheld for the question you picked, lean in:**
> "Right — see how it refuses to make one up? An ungrounded LLM never does
> that. This is the entire point of grounding."

**Best demo: pick the TSMC water question — it triggers a real unit mismatch.**
The calculation engine returns 113.6 million m³, but the SASB metric (TC-SC-140a.1)
is defined in *thousand* cubic metres — a 1000× difference. The agent catches it
and refuses to publish:
> "Watch this. The engine produced a number — 113 million. Looks reasonable
> for a chip fab. But the SASB code is defined in *thousand* cubic metres, not
> cubic metres. So the agent withholds it. An ungrounded LLM would have just
> said 'yep, 113 million m³' and you'd have a number off by a factor of a
> thousand in your disclosure. *That* is what grounding buys you."

**Themes hit:** Responsible AI, Agent Building.

---

## Tab 2 — Myth-busting (~45 sec)

**What it shows:** six fact/fiction cards on common AI misconceptions.

Use this when someone is hesitant or sceptical, *or* when someone is
overconfident about what AI can do. Pick the one most relevant to them:

- Sceptic? Flip *"If an AI sounds confident, the answer is probably right."*
- Overconfident? Flip *"Grounding eliminates hallucinations."*
- Privacy-minded? Flip *"Anything you type into a chatbot may be used for training."*

**Punchline:**
> "AI literacy isn't memorising prompts — it's knowing which of these you'd
> bet your job on."

**Theme hit:** AI myth-busting.

---

## Tab 3 — Sign-off Workbench (~75 sec)

**What it shows:** an AI-drafted climate disclosure. Attendee tags each
paragraph as *Publish as-is* or *Needs human review*, then reveals the key.

1. Read the first paragraph — **specific number with a source.** Tag *publish*.
2. Read the second — **comparative claim ("top quartile").** Tag *needs review*.
3. Let them try the rest themselves. Click *Reveal answer key*.

**Punchline:**
> "This is the boundary. AI handles retrieval and arithmetic. Humans handle
> judgment, comparison, and forward-looking statements. That boundary is
> the same whether you're writing climate disclosures or financial advice."

**Theme hit:** Human-in-the-loop decisioning.

---

## Closing line (when handing them the takeaway card)

> "Three rules: ground it, log the lineage, sign it off. If your team's
> AI workflow does those three things, you can defend it in front of an auditor."

---

## If a technical attendee dives deeper

- Show `/api/health` — the demo reuses the existing ESG KG project; nothing
  is duplicated.
- Open `app/server.py` — the grounded answer comes from a real
  `KnowledgeGraphService` + `CalculationService` querying RDF + CSV.
- Mention the 7 competency questions (CQ1–CQ7) in the sibling project.

## If asked "why isn't the ungrounded answer from a live LLM?"

Honest answer: a live LLM call at a booth is unreliable (network, key,
latency) and on a good day might *not* hallucinate, which would muddy the
demo. The canned ungrounded answer is illustrative of what these models
*do* return when there's no grounding — and we're upfront about it.
