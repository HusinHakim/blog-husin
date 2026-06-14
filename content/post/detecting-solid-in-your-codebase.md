+++
title = "How to tell if code already follows SOLID: a detection method (even with no legacy code)"
date = "2026-06-14"
author = "Husin Hidayatul"
description = "My project was built from zero, so it has no legacy code at all. That turned out to be the perfect lab for building a method to detect SOLID, the kind of method you can then point at any messy legacy codebase to find out whether it already holds up."
toc = true
tags = ["solid", "refactoring", "code-smells", "architecture", "ai-assisted", "clean-code"]
categories = ["software-engineering"]
+++

![Five cards, S O L I D, each showing the tell that exposes whether that principle is being followed, under the heading: is this code already SOLID, a detection method you can run on any codebase including legacy](/images/solid-cover.png)

Let me start with a confession that sounds like it disqualifies me from writing this post: **my project has no legacy code.** It is a Next.js frontend and a Django backend, both started from an empty repository this semester. There is no decade-old monolith here, no inherited mess, no `utils_final_v2.py`. So I never had the classic experience of opening someone else's code and asking "does this even follow SOLID?"

What I had instead was the opposite problem. When you write everything yourself, it is dangerously easy to *believe* you are applying SOLID without ever checking. Belief is not evidence. So as the codebase grew I needed a way to look at my own code and answer, honestly, "is this actually SOLID, or do I just feel like it is?" That question forced me to build a **detection method** rather than memorise five definitions. And here is the useful part: a detection method built on clean code is exactly what you point at legacy code. If you can spot the absence of a principle in your own fresh code, you can spot it in a ten-year-old file you have never seen. The codebase changes; the tells do not.

## Why detection beats devotion

Most SOLID explanations are devotional. They hand you five capitalised rules and tell you to follow them. The trouble is that you cannot follow what you cannot see, and SOLID is invisible until something hurts. A class is perfectly fine right up until the day a change touches it for the third unrelated reason. The principle was being violated the whole time; you just had no detector.

So I stopped asking "am I following SOLID?" and started asking "what would the *violation* look like, concretely, in a diff or a grep?" Each principle has a tell, a smell you can literally search for. Detection turns an abstract virtue into a checklist you can run in five minutes, on your code or anyone else's.

## The detection method

Here is the whole method on one page. For each principle there is a smell you can see, one question that confirms it, and the move that fixes it.

![A table mapping each SOLID letter to the grep or smell you can see, the single question to ask, and the refactor move. SRP: a function you can only describe using and. OCP: long if-elif on a type or role. LSP: an override that raises NotImplemented. ISP: props full of ignored optionals. DIP: ORM objects calls inside a view](/images/solid-detection-table.png)

**S, Single Responsibility.** The tell is linguistic: try to describe what a unit does in one sentence. If you reach for the word "and", you have found two responsibilities sharing a name. The backend taught me this the hard way with an upload handler that both created a record *and* re-uploaded a file; splitting "create-only" out of it made each path testable in isolation. The question: *does this change for more than one reason?* The move: split by reason, not by size.

**O, Open/Closed.** The loudest tells are a growing `if/elif` on a type or role, a nested ternary, and error handling that matches on string keywords. All three mean "to add a case, I edit existing code." On the backend I once mapped errors to HTTP codes by searching for substrings like "not found" in the message. The fix was to raise typed exceptions (`LaporanNotFoundError`, `LaporanForbiddenError`) and let the view catch each type. Adding a new failure mode is now a new class, not an edit to a fragile string match. On the frontend the same instinct turned one duplicated panel into a single component switched by a `variant` prop. The question: *to add a case, do I edit old code or add new?*

**L, Liskov.** The tells are an override that raises `NotImplementedError`, an `isinstance` branch that special-cases one subtype, or a variant that quietly returns less than its sibling. All three say a subtype is not a true stand-in for its base. The question: *can I swap implementations and keep every guarantee the caller relies on?* If two presentations of a component must expose the same accessibility contract and the same data, they are substitutable; if one silently drops a feature, they are not.

**I, Interface Segregation.** The tells: a props object full of optionals that most callers ignore, importing a huge module to use one function, or a permission layer that bundles unrelated checks. Each one forces a consumer to depend on things it never touches. The move is to slice the fat interface into small, purpose-built ones, and to make optional what is genuinely optional so a caller can take only the slot it needs.

**D, Dependency Inversion.** This is the highest-value tell and the easiest to grep for: `\.objects\.` inside an HTTP view, or `fetch(` inside a presentation component. When high-level code names a low-level detail, the two are welded together.

![Two diagrams. On the left, the smell: an AdminPengajuanView in the HTTP layer calling Pengajuan.objects.all and KaprodiModel.objects.filter directly, so the view knows the database. On the right, the clean version: the view depends on a PengajuanRepository abstraction, which depends on the Django ORM, with methods named by intent like list_all_ordered and get_by_pk](/images/solid-dip-before-after.png)

My admin views had five direct ORM calls inlined into the request handlers. Pulling them into a `PengajuanRepository` with methods named by intent, `list_all_ordered()`, `get_by_pk()`, let the view depend on *what it wants* instead of *which tables exist*. The question: *does my business logic name a concrete dependency?* If yes, put an abstraction between them.

## Tips: how to actually apply it

Knowing the tells is half of it. The other half is restraint, because a detector with no discipline just turns into a refactoring rampage.

- **Let a signal drive the refactor, not a vibe.** My best refactors were triggered by something objective: a SonarQube duplication gate failing, a mutation-testing survivor, a smell I could grep. If nothing is pointing at the code, leave it alone.
- **Write the commit message first.** Before extracting anything, I write the refactor commit message describing *why*. If I cannot articulate which principle it serves and what pain it removes, the refactor is premature and I drop it.
- **The two-use-cases rule.** Do not abstract on the first occurrence. Wait for the second real use case before you introduce an interface or a base class. One use case is a guess; two is a pattern.
- **Keep tests as the safety net.** Behaviour must not move during a refactor. I work test-first, so the existing suite is the contract that proves a refactor preserved behaviour. When I want to know whether the tests are *actually* strong enough to catch a regression, I run mutation testing and hunt the survivors.
- **Take the smallest reversible step.** Extract one repository, run the suite, commit. Then the next. Big-bang refactors hide their own bugs.

## Where AI helped, and where it quietly lied to me

I built and ran this method with an AI assistant in the loop, and it is worth being precise about what that actually bought, because the honest answer is "a lot, and also some traps."

![Two columns. Where AI helps: spots smells fast across the whole repo, drafts the mechanical extraction and the tests, names things well, explains the pattern. Where AI misleads: over-extracts one class into twelve, abstracts before there are two use cases, is shallow on Liskov and contracts, and pattern-matches without paying the maintenance cost](/images/solid-ai-helps-hurts.png)

Where it genuinely helped: an AI is a fast, tireless smell-detector. Point it at a repo and it will grep every `.objects` in a view, every nested ternary, every fat props object, far faster than I would scroll. Once a target is chosen, it drafts the mechanical extraction and, crucially, the tests that prove behaviour did not move. It also names things well, and good names (`PengajuanRepository`, `LaporanNotFoundError`) are most of what makes a refactor readable later.

Where it misled me is more interesting, and it maps almost exactly onto the principles themselves:

- **AI over-applies SRP.** Ask it to split a class and it will happily produce twelve. But SRP taken too far is its own anti-pattern: a developer chasing one behaviour across fifteen tiny files is *more* overwhelmed, not less. The win is cohesion, not fragmentation, and AI has no built-in sense of where the sweet spot sits.

![Three states side by side. One god module is too coupled. A few cohesive units, each with one clear reason, is the sweet spot. Twenty-three micro-files with logic scattered everywhere is cognitively overwhelming. Caption: AI loves to split, ask it to stop at the sweet spot where each unit still earns its name](/images/solid-overextraction.png)

- **AI abstracts too early.** It will invent an interface on the first use case, shipping a layer of indirection for a flexibility you may never need. That is YAGNI in a nice costume. The two-use-cases rule is something *you* have to enforce, because the model will not.
- **AI is shallow on Liskov and contracts.** It can refactor syntax confidently, but it cannot *feel* which guarantee actually matters to a caller. Substitutability is a judgement about intent, and that judgement is still yours.
- **AI never pays the maintenance bill.** It suggests a pattern from training data without living with the consequences. You are the one who maintains the abstraction at 2am, so you get the final say on whether it earns its keep.

The short version: AI is an excellent *detector* and *drafter*, and a poor *editor of its own enthusiasm*. Use it to find smells and propose moves; keep human judgement on what to merge back.

## Creative ways to level up

If you want to get genuinely good at this, go beyond reading code and make the detectors automatic and adversarial:

- **Architecture fitness functions.** Write a test that fails the build if a view imports the ORM, or if a component calls `fetch` directly. A grep-based test turns DIP from a hope into a tripwire that catches the violation in CI, on every commit, forever.
- **Treat the duplication gate as a design tool.** A failing duplication check is not a chore, it is a free design review telling you a variation has not been named yet.
- **Commit-message-driven refactoring.** Make the justification a required artefact. If the message is weak, the refactor is weak.
- **Give the AI a subtraction prompt.** Everyone asks AI to split things up. Ask it the opposite: "what here is over-abstracted, what should be merged back?" Its blind spot becomes visible the moment you point it at the right question.
- **Verify refactors with mutation testing.** A refactor is only safe if the tests would catch a regression. Mutation testing measures exactly that, so you refactor on proof, not faith.

## So, is your code SOLID?

Whether you are staring at a greenfield repo like mine or a legacy monolith you inherited last week, the loop is the same: **grep for the smell, ask the one question, make the smallest reversible move, and prove it with a test.** SOLID stops being five rules you swear to follow and becomes five things you can detect. The method is the deliverable, not the dogma, and the nice thing about a method is that it works on code you have never seen, including, one day, the code you wrote and forgot.
