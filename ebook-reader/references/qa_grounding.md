# Q&A Grounding — Answering Questions Faithfully from the Book

This reference covers how to keep Q&A answers faithful to the book's content when
using the ebook-reader skill.

## Core principle

Every answer must be traceable to the extracted text of the PDF. Treat the book as
the only source of truth for content questions. Do not answer from general
knowledge, do not guess, do not embellish.

## Citation format

After stating a fact or claim drawn from the book, cite where it comes from using
chapter title + page number:

> 书中第 3 章「人工智能与伦理」（p. 45–46）指出：……

Rules:
- Pages are zero-based in the extracted JSON; **always add 1** when presenting
  "第 N 页" to the user (p. 45 in the JSON is the 46th physical page label only if
  page labels match — when in doubt, say "PDF 第 N 页" using the zero-based index + 1).
- Prefer quoting exact wording for definitions, formulas, data, and conclusions.
  For longer claims, paraphrase accurately and keep the citation.
- When the answer uses an abbreviation, give its full name as the book states it
  on first use (or from the book's glossary); never invent an expansion. If the
  book never expands it, note "原文未给出全称".
- When several passages support the answer, cite the primary one and mention
  "另见第 X 章（p. Y）".

## Question types and how to handle them

### 1. Fact / definition questions (what does the book say about X?)
- Locate via `search_pdf.py`, read the full passage, answer with the book's
  wording, cite chapter + page.

### 2. Summary / comparison questions (compare A and B in the book)
- Search both A and B; read each passage; structure the answer as the book does;
  cite both sources. If the book compares them explicitly, quote that passage.

### 3. "Where does the book talk about X?" / "On which page?"
- Use `search_pdf.py` and answer with a list: 章节 + 页码 + 一两句内容提示, in
  match_count order (most relevant first).

### 4. Questions the book does NOT cover
- State clearly: "书中没有直接讨论这个问题。" 
- Optionally mention the nearest related passage found ("书中与此最接近的内容在第
  X 章（p. Y）：……"), but do not stretch it into an answer the book doesn't give.
- If the user explicitly asks for your own opinion/analysis, give it AFTER the
  book's content, clearly separated with a label like **我的看法（非书中内容）**.

### 5. Verifying a user's claim or quote
- Take a distinctive phrase from the claim, run it through `search_pdf.py`. If it
  matches, confirm with the full passage and cite. If it does not match, tell the
  user the book does not contain that wording (and check near-synonyms once before
  concluding).

## Anti-fabrication checklist (before replying)

- [ ] Ran `search_pdf.py` with terms derived from the question
- [ ] Read the full passage (not just the snippet) in the JSON
- [ ] Answer content matches the passage; numbers/definitions are verbatim
- [ ] Citation (chapter + page) attached
- [ ] If the book is silent on the topic, said so explicitly

## Efficiency tips

- Reuse `_pdf_extract.json` across questions; never re-run `extract_pdf.py` per
  question.
- For very large JSON, do not load it wholesale; use `search_pdf.py` hits + targeted
  Read/Grep with `--context` on the JSON.
- Batch related questions into one search run when terms overlap.
