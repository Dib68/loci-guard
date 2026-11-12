# Compiling the wiki

Read this when the session-start context says wiki pages need compiling, or the user asks you to update the project wiki.

Raw memories and chats are the sources; `.loci/wiki/` holds one compiled page per room, written by you. When a room has a page, entering it shows the page plus anything newer than the page, instead of the scattered memories it already covers.

Compile when the session's work is done, or when the user asks. Offer it first: it costs a turn per room.

1. `loci-guard wiki compile` prints a work order per room: what is new, what changed since the page was written (weigh these most: the page didn't prevent them), and what was removed.
2. Write or update the page body following `.loci/wiki/SCHEMA.md`. Cite every claim as `[#id]`. Merge duplicates, resolve contradictions in favour of the newer source, summarize chats instead of copying them, never write secrets. For chat sources, read the session with `loci-guard recall --session ID` and keep only what concerns that room.
3. `loci-guard wiki done <room>` checks the page (secrets, citations, links), stamps its sources and refreshes `index.md`. Fix any error it reports and run it again. If it lists details missing from the page (numbers, names, paths, code), put them back unless the user confirmed they changed.
4. `loci-guard wiki lint` checks the whole wiki: dangling citations, broken links, files that no longer exist, orphan and stale pages.

Write only the body. The header comment at the top of a page belongs to Loci Guard.
