+++
title = "Applying TDD in a Real Project: Not Theory, but Experience"
date = "2026-04-23"
author = "Husin Hidayatul"
description = "Notes from shipping an Upload Sertifikat feature with the RED-GREEN-REFACTOR discipline. Three test categories, mock placement, coverage, and mutation testing."
toc = true
tags = ["tdd", "testing", "django", "mutation-testing", "qa"]
categories = ["software-testing"]
mermaid = true
+++

Honestly, I used to feel that TDD was a waste of time. Why write tests first when the feature doesn't even exist yet?

That mindset changed when I started working on the Upload Sertifikat feature in the Guru Besar Mengajar project. On the surface, the feature isn't complex: an Admin uploads a PDF, it's stored in Supabase, then Kaprodi and Guru Besar can download it. But the business rules pile up quickly, and that's where TDD started paying off.

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
