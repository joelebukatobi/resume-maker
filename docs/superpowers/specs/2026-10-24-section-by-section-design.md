# Section by section: a new CV written in the chat

Date: 2026-10-24. Status: approved and built.

## Why

A new application now opens on a blank frame (`fill.blank`): headings,
education, skill labels, and each role's company, place, title and dates. The
summary, skill text, bullets and Additional Information are empty. Joel wants
the rest written with him, in the chat, one section at a time, so that every
line on the page is one he has seen and said yes to. The old way, a filled page
plus cards to check, put lines on the page he had not looked at, and that is
how the Zapier line travelled from one CV to the next.

"I cannot put stuff on my CV that I cannot defend" still holds: every claim
traces to his notes or his past CVs.

## What he sees

1. **New Application.** The CV is the blank frame.
2. **He pastes the job description.** The chat starts by itself; he types
   nothing.
3. **Professional Summary.** The chat writes one from his past CVs, his notes
   and the posting, says in a sentence why, and shows it as one card. He says
   yes, or types what to change. It rewrites and shows it again, as often as he
   asks. Yes puts it on the page and the summary is done.
4. **Skills.** All skill lines together, one card, under the labels the frame
   already has. Same yes / change loop.
5. **Experience.**
   - **Roles first.** The chat looks at every role, on and off, against the
     posting. If one that is off fits better than one that is on, it suggests
     the swap ("take X out, put Y in"). Yes swaps them; no leaves them. Several
     swaps are offered one at a time. When there is nothing worth swapping it
     says so in a line and moves on.
   - **Then bullets, one job at a time, top job first.** All of that job's
     bullets together on one card. Same yes / change loop.
6. **Additional Information.** His interests line, plus the sponsorship line
   ("US permanent resident, no sponsorship required") only when the posting
   mentions visas, sponsorship or work authorisation. Same loop.
7. When the last section is done, the chat says the CV is written.

**The rule that holds it together:** the chat never starts the next section
until he has said yes to the current one. It does not mention, preview or
draft a later section early. Education is never touched.

## Where the words come from

- **The library first.** For the section being written, code gathers every
  line he has used in that slot on past CVs, only the ones his notes back (the
  support check from the line library), ranked against the posting's frozen
  keyword list, with his most-used line first where the posting does not care.
  That short list goes to the chat with the section. The chat prefers these
  lines, as they are or lightly adjusted to the posting.
- **New wording only when nothing fits.** If no line in his history answers
  what the posting asks for, the chat may write a new one, and only from what
  his notes say. It says which note it drew on.
- **Never invented.** No tool, number, title or result that is not in his
  notes or past CVs. A line his notes do not back (held, or only backed by an
  assistant suggestion he has not confirmed) is never offered.
- Strike stays on the Review page: a struck line is never offered.

## What happens to what exists

- The automatic tailoring round after a paste (cast, wording, fit) is replaced
  by this sequence **for a blank new CV**.
- An existing application, or a Duplicate, keeps today's behaviour: the
  "Tailor To This Posting" button and its round. *(My call: those CVs already
  have text, and the section sequence assumes empty fields.)*
- The Review page is unchanged.
- The keyword list, the coverage count and the one-page measurement stay as
  they are.

## Calls I made (check these)

- **How many bullets per job:** the number that role had on the base CV,
  capped by its budget. *(The frame has no bullets left to count, so the base
  is the only record of the room each role gets.)*
- **One page:** after the last section, if the page runs over, the chat offers
  the existing fit step (shorten or drop the longest lines), still one card at
  a time. Nothing is shortened silently.
- **"Make changes"** is him typing in the chat. No separate button.
- **Leaving halfway:** if he closes the app mid-way, reopening the application
  resumes at the first section he has not said yes to.
- **A section he wants empty** (say, no Additional Information): saying "skip
  this" in the chat leaves it empty and moves on.
- **The cover letter** is out of scope.

## Not doing

- No cards panel beside the page. Everything is in the chat.
- No filling of the page before he has seen a line.
- No local model.

## Left to the builder

- How one card carries several fields (all of one job's bullets, all skill
  lines) and applies them on one yes, using the existing proposal cards.
- How the chat knows which section it is on and which are done (the chat
  history already records accepted proposals by path).
- The exact prompt wording, inside the honesty rules above.
