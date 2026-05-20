+++
title = "Getting more familiar with TDD: the corner cases I would never have written"
date = "2026-04-23"
author = "Husin Hidayatul"
description = "Notes from several sprints applying TDD on the Guru Besar Mengajar project: three test categories, the +1 / -1 boundary strategy, mock placement, and what mutation testing exposed."
toc = true
tags = ["tdd", "testing", "django", "mutation-testing", "boundary-value-analysis", "qa"]
categories = ["software-testing"]
mermaid = true
+++

Honestly, I used to feel that TDD was a waste of time. Why write tests first when the feature doesn't even exist yet?

That mindset shifted over several sprints on the Guru Besar Mengajar project. None of the features are exotic on the surface, uploading certificates, managing periode, syncing kegiatan, recording attendance. But each one carries a thicket of business rules, and that's where writing tests first started paying for itself. This post collects what I learned about TDD across the project, with concrete examples drawn mostly from the Upload Sertifikat feature, because that's where the rules were densest.

## The First Moment That Clicked

When I started writing tests for PDF file validation, I peeked at the existing validator in `documents/`. It turned out the validator only checked the MIME type from the request header. Trivially spoofable: upload a shell script, rename it `.pdf`, set the content-type to `application/pdf`, done.

If I had jumped straight into the implementation without writing tests first, that bug would have surfaced during review at the earliest, or in production at the worst. Because I was forced to write the test first, I was also forced to ask, "What could break this validation?"

The answer was magic bytes. I added a check for `%PDF-` in the file's first bytes. The test went in first, the implementation followed.

## The RED-GREEN Rhythm That Felt Strange at First

My commit pattern on this feature looked like this:

```text
[RED]   test: add SertifikatService upload success and kegiatan status tests (+, -)
[GREEN] feat: add SertifikatService.upload_sertifikat core logic
[RED]   test: add SertifikatService participant membership validation tests (-, corner)
[GREEN] feat: add participant validation in SertifikatService
[RED]   test: add SertifikatService re-upload idempotency tests (corner)
[GREEN] feat: add update_or_create re-upload support in SertifikatService
```

![Placeholder: screenshot of git log showing alternating RED and GREEN commit prefixes for the Upload Sertifikat feature](/images/placeholder-tdd-commit-log.png)

At first it felt like a forced ritual. After a few cycles, I realized something: these commits are concrete evidence of the order I worked in. Anyone reading `git log` can verify that the tests came before the code, not the other way around.

## Three Test Categories That Changed How I Think

The most impactful part of this work was the discipline of writing three test categories for every feature.

{{< mermaid >}}
flowchart LR
    F[Feature: Upload Sertifikat]
    F --> P[Positive<br/>valid PDF, status SELESAI,<br/>user is participant<br/>--> 201 Created]
    F --> N[Negative<br/>kegiatan BERLANGSUNG --> 409<br/>non-participant --> 403<br/>fake PDF magic bytes --> 400]
    F --> C[Corner<br/>exactly 5MB accepted<br/>5MB + 1 byte rejected<br/>re-upload overwrites]

    classDef pos fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef neg fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef cor fill:#fef9c3,stroke:#ca8a04,color:#713f12
    class P pos
    class N neg
    class C cor
{{< /mermaid >}}

Positive is easy. All inputs valid, output as expected. That's usually what I write first.

Negative starts pushing me to think harder:

```python
def test_upload_rejected_when_kegiatan_not_selesai(self):
    self.kegiatan.status = 'BERLANGSUNG'
    self.kegiatan.save()
    response = self.client.post('/api/sertifikat/upload/', {...})
    self.assertEqual(response.status_code, 409)

def test_upload_rejected_for_non_participant(self):
    response = self.client.post('/api/sertifikat/upload/', {
        'user_id': str(self.other_user.id),  # not a participant
        ...
    })
    self.assertEqual(response.status_code, 403)
```

Corner cases are the most interesting. This is where I needed to talk things through with the team:

```python
def test_file_exactly_5mb_is_accepted(self):
    five_mb = b'%PDF-' + b'A' * (5 * 1024 * 1024 - 5)
    file = SimpleUploadedFile("cert.pdf", five_mb, content_type="application/pdf")
    response = self.client.post('/api/sertifikat/upload/', {'content': file, ...})
    self.assertEqual(response.status_code, 201)  # exactly 5MB must be accepted

def test_reupload_overwrites_existing_certificate(self):
    SertifikatModel.objects.create(user=self.guru_besar, kegiatan=self.kegiatan,
                                   content="https://old-url.com")
    response = self.client.post('/api/sertifikat/upload/', {...})
    self.assertEqual(response.status_code, 201)
    self.assertEqual(
        SertifikatModel.objects.filter(user=self.guru_besar, kegiatan=self.kegiatan).count(), 1
    )
```

The re-upload corner case came from a simple question: what happens if the Admin uploads the wrong certificate? Should it error out? Or overwrite? That business decision belongs in a test, not as a hidden assumption inside the code.

## Finding Corner Cases: From Hunch to Method

Early on, my corner cases came from gut feel: write the happy and sad paths, then guess what might break. That misses cases I haven't seen before and piles up redundant tests of cases I have. The Advanced Programming module on TDD (Ichlasul Affan, *Module 04: TDD & Refactoring*, Fasilkom UI, 2024) gives names and methods to what used to be intuition.

### The +1 / -1 Strategy: Boundary Value Analysis

Split the input into valid and invalid partitions, then for each partition test five values: `min`, `min + 1`, `middle`, `max - 1`, `max`. Bugs cluster at edges because that's where `<` vs `<=` mistakes live.

{{< mermaid >}}
flowchart LR
    I1["< min<br/>(invalid)"]:::inv --> B1["min"]:::bnd --> B2["min + 1"]:::bnd --> M["middle"]:::mid --> B4["max - 1"]:::bnd --> B5["max"]:::bnd --> I2["> max<br/>(invalid)"]:::inv

    classDef inv fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef bnd fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef mid fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
{{< /mermaid >}}

The 5 MB file-size check is textbook BVA. My early test suite accepted a 1 MB file and rejected a 10 MB file, but never asserted "exactly 5 MB passes, 5 MB + 1 byte fails." mutmut found the gap before I did. For multi-parameter functions the module adds **Single Fault Assumption**: fix all but one parameter at the middle, vary only one. That's `4n + 1` tests, not exponential.

### Beyond Numeric Edges

BVA only knows inputs, not behavior. Four other lenses I picked up while building reupload (SCRUM-251):

- **Output enumeration.** The module's triangle example: BVA holds two sides equal, so Scalene `(3, 4, 5)` never fires. Ask: does every reachable result have a test driving it?
- **Invariants across actions.** Reupload must update `content` and `updated_at` but leave `created_at` alone. That's not an input edge, it's a property that holds before and after. One assertion per invariant.
- **Side-effect absence.** When the duplicate-sertifikat guard triggers, Supabase upload must **not** be called. Easy to forget, cheap to test: `mock_upload.assert_not_called()`.
- **Garbage input outside the type.** Sending `"not-a-uuid"` as `kegiatan_id` should return 400, not 500. BVA tests valid UUIDs at the size boundary, this tests inputs that aren't UUIDs at all.
- **Regression guards.** Adding PUT for reupload meant POST started returning 409 "Gunakan PUT" instead of silently overwriting. I locked that new contract in a test so the next person can't quietly roll it back.

Myers, Badgett, and Sandler call this grab-bag **Error Guessing** (*The Art of Software Testing*, 3rd ed., 2012): the cases you only find by knowing the domain, not the schema. The module's rule and mine end up the same: don't lean on a single method. BVA closed the numeric-edge survivors in mutmut; the other four lenses closed the rest.

## Is mock/stub mandatory?

Short answer: no. They solve one specific problem, not a default.

Reach for one when the code under test crosses a boundary you don't control or don't want hit in a test: external service (Supabase storage, email/SMTP, payment gateway), database when you want unit-test speed, time / randomness / filesystem, or a side effect you need to verify happened (or didn't).

Skip them when the collaborator is a pure function, a value object, a model with just data, or anything already cheap and deterministic. Wrapping those in a mock just couples the test to the implementation.

{{< mermaid >}}
flowchart TD
    Q[Code calls a collaborator]
    Q --> E{External, slow,<br/>or non-deterministic?}
    E -->|No| D[Use the real thing]
    E -->|Yes| R{Need to verify<br/>the call happened?}
    R -->|No, just need a return value| S[Stub]
    R -->|Yes, assert args / call count| M[Mock]

    classDef ok fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef neu fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    class D ok
    class S,M neu
{{< /mermaid >}}

In `unittest.mock` and Mockito the same object plays both roles. The intent in the test should still be clear: a stub drives the test (the upload returns this URL so the rest of the flow can run), a mock asserts a contract (the storage upload was never called when the guard tripped).

## Mocks Are Not a Cheat, They're a Design Choice

This project uses Supabase as storage. I didn't want tests to be slow because of real Supabase calls, and I didn't want the bucket to fill up with test garbage.

The solution: mock at the right layer.

```python
# Service layer: mock the Supabase client
@patch("sertifikat.services.DocumentSupabaseClient.upload_sertifikat")
def test_upload_success_saves_url_to_model(self, mock_upload):
    mock_upload.return_value = "https://storage.example.com/cert.pdf"
    result = SertifikatService.upload_sertifikat(...)
    self.assertEqual(result.content, "https://storage.example.com/cert.pdf")
    mock_upload.assert_called_once()
```

```python
# View layer: mock the entire service
@patch("sertifikat.views.views_admin.SertifikatService.upload_sertifikat")
def test_admin_upload_returns_201(self, mock_service):
    mock_service.return_value = self.mock_sertifikat_instance
    self.client.force_authenticate(user=self.admin)
    response = self.client.post('/api/sertifikat/upload/', self.valid_payload, format='multipart')
    self.assertEqual(response.status_code, 201)
```

Because I had to decide what needed mocking, I ended up with a much clearer picture of each layer's responsibility. The view test only checks: was the request forwarded to the service correctly? Business logic is none of the view test's concern.

{{< mermaid >}}
flowchart TB
    subgraph ViewTest[View layer test]
        V[test_admin_upload_returns_201]
    end
    subgraph ServiceTest[Service layer test]
        S[test_upload_success_saves_url_to_model]
    end
    subgraph Real[Real dependencies]
        SVC[SertifikatService]
        SB[(Supabase storage)]
    end

    V -. mocks .-> SVC
    S -. mocks .-> SB
    V --> |asserts HTTP 201| Done1[Pass]
    S --> |asserts model.content set| Done2[Pass]

    classDef mock fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef real fill:#f3f4f6,stroke:#6b7280,color:#111827
    class SVC,SB real
{{< /mermaid >}}

## Coverage and Mutation Testing

After all tests turned green, I ran the coverage report:

```bash
pytest --cov=sertifikat --cov-report=term-missing
```

```text
Name                          Stmts   Miss  Cover
-------------------------------------------------
sertifikat/models.py             18      0   100%
sertifikat/services.py           52      0   100%
sertifikat/views/views_admin.py  28      1    96%
TOTAL                           151      3    98%
```

But 98% coverage doesn't always mean the tests are good. Enter mutmut:

```bash
mutmut run --paths-to-mutate sertifikat/services.py
```

Mutmut mutates my code automatically (for example, `>` to `>=`, `or` to `and`) and reruns the tests. If the tests stay green after a mutation, the tests aren't actually detecting that change.

```text
Mutation score: 94.3% (66/70 mutants killed)
Survived:
- services.py:47 - changed `>` to `>=` (file size check)
```

From this I learned: my file-size tests weren't strict enough because no test distinguished "exactly 5MB accepted" from "5MB + 1 byte rejected". That's what pushed me to write the specific corner case above.

## What's Still Hard

One thing I'm still not fully comfortable with: setting up fixtures and mocks at the start takes longer than writing the implementation itself. There were moments where I spent 30 minutes just getting `setUp()` right before I could write a single test.

But I'm starting to see a pattern: the more complex the setup, the more complex the dependencies in my code usually are. That's a signal to refactor, not an excuse to skip the test.

## Takeaway

TDD on a real project isn't about rigid discipline. It's about having a conversation with yourself before writing code: "What happens if this condition isn't met?"

If a single feature ends up with 30+ tests covering positive, negative, and corner cases, that isn't a burden. It's an asset. The next developer who touches the code has a safety net, and I get the confidence to refactor without fearing silent breakage.

*Written as part of the documentation for the Upload Sertifikat feature in the Guru Besar Mengajar project. Original version (Indonesian): [Medium post](https://medium.com/@husinhidayatul/penerapan-tdd-di-proyek-perangkat-lunak-bukan-teori-tapi-pengalaman-dc57d44d295b).*
