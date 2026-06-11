+++
title = "One CRUD engine, two dashboards: deleting 680 duplicated lines with a variant prop"
date = "2026-06-11"
author = "Husin Hidayatul"
description = "A SonarQube duplication gate blocked my merge request. Two near-identical Postingan panels, 680 copy-pasted lines. Here is how a single variant prop turned the copy into a four-line wrapper, and which SOLID principle each step was actually paying off."
toc = true
tags = ["react", "typescript", "solid", "refactoring", "composition", "sonarqube"]
categories = ["frontend"]
+++

![One CRUD engine in the middle feeding two outputs: an Admin dashboard rendered as a card on the left, and a Guru Besar dashboard rendered inside a WidgetShell on the right, switched by a variant prop](/images/variant-prop-cover.png)

The merge request was green on every check except one. SonarQube failed the **Duplicated Lines** gate, and the culprit was a file I had written myself two hours earlier: `PostinganPanelWidget.tsx`, **681 lines**, almost character-for-character identical to a component that already existed in the admin dashboard. This is the story of how I deleted 680 of those lines, and which SOLID principle each step actually bought. Not textbook definitions, the real diff.

## How I ended up with two copies

The admin dashboard on our Guru Besar Mengajar project already had a fully working "Postingan Saya" panel: a paginated CRUD surface (list, create, edit, delete, detail) living in `DashboardPostinganModule`. When the Guru Besar dashboard redesign landed, it needed the **same** panel, but in a different visual frame. Every widget on that dashboard is wrapped in a shared `WidgetShell` (icon, title, subtitle, an action slot), while the admin version sits in a plain bordered `<section>`.

The fastest path was the obvious one: copy `DashboardPostinganModule` into a new `PostinganPanelWidget`, swap the `<section>` for `<WidgetShell>`, ship it. It worked, tests passed, coverage was 100%. And it was wrong, because now two files held the same fetch-list-paginate-mutate logic.

```tsx
// PostinganPanelWidget.tsx: the duplicate (681 lines)
export default function PostinganPanelWidget() {
  const [page, setPage] = useState(1)
  const [response, setResponse] = useState<DashboardPostinganListResponse | null>(null)
  // ...640 more lines: the same handlers, dialogs, pagination,
  //    delete confirmation, all duplicated from the admin module
  return <section className="...">{/* WidgetShell-styled body */}</section>
}
```

![Two near-identical Postingan panels pushed SonarQube duplicated lines to about 11 percent, failing the quality gate; deduplicating dropped it to zero](/images/variant-prop-sonar-gate.png)

The duplication gate is not bureaucracy here. It is a fitness function pointing at a real maintenance hazard: every future change to postingan behaviour would now have to be made twice. The gate forced a design conversation I had skipped.

## The wrong fix, and the one I picked

The tempting "fix" is to extract a `usePostingan` hook shared by two components. That removes some duplication but keeps two JSX shells in sync forever, and the markup, the dialog wiring, the `aria` attributes, is exactly where the copies had already begun to drift.

Instead I treated the two panels as **one component with two presentations**. The CRUD engine is identical; only the outer chrome differs. That is a textbook case for the **Open/Closed Principle**: I want to *extend* the module to support a new look without *modifying* the behaviour the admin dashboard already depends on. The extension point is a single prop.

```tsx
interface DashboardPostinganModuleProps {
  readonly role: Role
  readonly variant?: 'card' | 'widget'   // the new extension point
}

export default function DashboardPostinganModule({
  role,
  variant = 'card',                       // default keeps admin behaviour byte-identical
}: DashboardPostinganModuleProps) {
```

The default value matters. Existing callers pass no `variant`, get `'card'`, and render exactly what they rendered before; no admin test changed. That is the "closed for modification" half of OCP: an extension invisible to everything that came before it.

## Separating the engine from its frame

To branch on `variant` without duplicating the body, I first had to give the body a name. The list and pagination were the parts both presentations share, so I lifted them into a single `body` element and let only the chrome differ:

```tsx
const body = (
  <>
    <DashboardPostinganList
      loading={loading} loadError={loadError} postingans={postingans}
      onViewClick={setViewingPostingan} onEditClick={openEditDialog}
      onDeleteClick={setDeletingPostingan}
    />
    <DashboardPostinganPagination
      page={page} response={response} loading={loading}
      onPrevious={() => setPage((p) => Math.max(1, p - 1))}
      onNext={() => setPage((p) => p + 1)}
    />
  </>
)

return variant === 'widget' ? (
  <WidgetShell
    title="Postingan Saya"
    subtitle="Kelola artikel yang Anda tulis dari dashboard."
    aria-label="Postingan Saya"
    icon={<FileText className="h-4 w-4" />}
    action={<DashboardPostinganHeaderAction count={response?.count} onCreateClick={openCreateDialog} />}
  >
    {body}
  </WidgetShell>
) : (
  <section className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm" aria-label="Postingan Saya">
    <DashboardPostinganHeader count={response?.count} onCreateClick={openCreateDialog} />
    <div className="mt-5">{body}</div>
  </section>
)
```

This is **Separation of Concerns** made physical. `body` knows about postingan data and nothing about layout; the two branches know about layout and nothing about how the data got there. The `body` variable is the boundary: data on one side, frame choice on the other.

## Composition, not configuration

The detail that makes this clean rather than clever is `WidgetShell`. It is not a postingan component, it knows nothing about postingan at all. It is a generic shell that accepts pieces and arranges them:

```tsx
export interface WidgetShellProps {
  readonly title: string
  readonly subtitle?: string
  readonly icon?: ReactNode      // slot
  readonly action?: ReactNode    // slot
  readonly children: ReactNode   // slot
  readonly 'aria-label': string
}
```

![WidgetShell as a frame with three named slots: an icon slot, a title/subtitle block with an action slot beside it, and a children slot below holding the shared CRUD body](/images/variant-prop-widgetshell.png)

Those three `ReactNode` props (`icon`, `action`, `children`) are composition slots, and two SOLID letters fall out of them at once. First, **Dependency Inversion**: the shell depends on the abstraction "some renderable node", not on the concrete postingan button or the concrete `FileText` icon, so the high-level frame and the low-level content both lean on the `ReactNode` boundary instead of on each other. The module does not configure the shell through a pile of boolean flags like `showIcon` or `headerStyle`; it *composes* the shell by handing it ready-made nodes. Second, **Interface Segregation**: every slot except `children` is optional (`icon?`, `subtitle?`, `action?`), so a widget that only needs a title and a body is never forced to depend on parts of the interface it will not use. I could drop that same `WidgetShell` around a calendar, a chart, or a to-do list, and it would not need a single edit, because it never assumed what it would contain.

The `action` slot is also where a smaller **Single Responsibility** win landed. The "create" button and count label had lived inline inside `DashboardPostinganHeader`; the widget variant needed the same control in the shell's `action` slot, so I extracted it once:

```tsx
function DashboardPostinganHeaderAction({ count, onCreateClick }: DashboardPostinganHeaderActionProps) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
      {count !== undefined && <p className="...">{count} postingan</p>}
      <button type="button" onClick={onCreateClick} className="...">
        <Plus className="h-4 w-4" /> {CREATE_POSTINGAN_COPY.action}
      </button>
    </div>
  )
}
```

Now the card header and the `WidgetShell` action slot render the very same `<DashboardPostinganHeaderAction />`. One button, one place to change its copy or focus ring, two call sites. The header stopped doing two jobs and went back to one.

## The payoff: a four-line component

With the engine, the frame, and the action all extracted, the original duplicate had nothing left to hold. `PostinganPanelWidget` collapsed from 681 lines into a configuration of the thing that already existed:

```tsx
import DashboardPostinganModule from '@/src/features/postingan/components/DashboardPostinganModule'

export default function PostinganPanelWidget() {
  return <DashboardPostinganModule role="GURU_BESAR" variant="widget" />
}
```

![Before and after of PostinganPanelWidget: a 681-line full copy of the CRUD logic on the left, a four-line wrapper that renders DashboardPostinganModule with variant widget on the right, for a net of 680 deletions and 99 insertions with coverage kept at 100 percent](/images/variant-prop-before-after.png)

That is the entire file. Its single responsibility is to *name a configuration*: the Guru Besar role, in the widget presentation. There is no logic to test beyond "does it render the module with these props", and nowhere for a bug to hide.

The commit came out at **99 insertions and 1,335 deletions** across four files, a net removal over 1,200 lines, with the duplication gate back to green.

## Keeping it honest with tests

None of this is safe to claim without tests proving behaviour did not move. The dashboard rebuild was test-first throughout, every widget landing as a `[RED]` test then a `[GREEN]` implementation. For the dedup the behaviour already existed and had to *stay* identical, so the existing `PostinganPanelWidget.test.tsx` suite was the safety net; most of its 627 lines were deleted with the duplicate logic, since those assertions now belonged to the module.

I added 16 lines to `DashboardPostinganModule.test.tsx` to pin the new branch: rendering with `variant="widget"` must produce the `WidgetShell` header, and rendering without it must still produce the plain `<section>`. Both files stayed at 100% coverage. The `variant` branch is the only new behaviour, and it is the one thing the new tests assert directly.

```tsx
it('renders inside a WidgetShell when variant is "widget"', () => {
  render(<DashboardPostinganModule role="GURU_BESAR" variant="widget" />)
  expect(screen.getByTestId('widget-shell-icon')).toBeInTheDocument()
})
```

There is a quiet **Liskov** guarantee underneath those tests. Both variants expose the identical `aria-label="Postingan Saya"` contract and render the exact same `body`, so swapping `'card'` for `'widget'` never weakens what a caller can rely on: the panel still lists, paginates, and mutates postingan the same way. One presentation is a behaviour-preserving substitute for the other, and the suite asserts that contract on both branches rather than trusting it.

## What the gate was really teaching

It is easy to read a SonarQube duplication failure as a chore: trim some lines, move on. But the gate was pointing at a design decision I had quietly deferred. "Two dashboards need the same panel" is not a copy-paste problem, it is a question about where the variation actually lives. Once I named the variation, it was a single axis (`card` versus `widget`) sitting on top of an otherwise identical engine, and the full SOLID set fell out almost mechanically. Each letter maps to a concrete line of the diff, not a slogan:

- **S, Single Responsibility:** the action button became its own `DashboardPostinganHeaderAction`, the header went back to one job, and the wrapper does nothing but name a configuration.
- **O, Open/Closed:** the `variant` prop with its `'card'` default extends the module for a new look without modifying the admin path that every existing caller depends on.
- **L, Liskov:** both variants honour the same `aria-label` and the same `body`, so either presentation substitutes for the other without weakening the panel's contract.
- **I, Interface Segregation:** `WidgetShell`'s optional `icon?`, `subtitle?`, and `action?` slots let a consumer depend only on the parts it actually renders.
- **D, Dependency Inversion:** `WidgetShell` depends on the `ReactNode` abstraction, never on anything postingan-specific, so it stays reusable for the next widget.

And the maintenance payoff is the point the gate was really making: a new postingan field, a copy change, or a validation rule is now a one-file edit that both dashboards inherit for free, instead of two edits where the second is the one you forget.

The reusable-shell-plus-thin-wrapper shape is the same one I keep reaching for elsewhere on this project, and it is worth internalising: when two screens look like a copy, the duplication is rarely the disease. It is the symptom of a variation you have not named yet. Name it, and the 680 lines tend to delete themselves.
