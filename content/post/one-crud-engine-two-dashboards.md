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

The merge request was green on every check except one. SonarQube failed the **Duplicated Lines** gate, and the culprit was a file I had written myself two hours earlier: `PostinganPanelWidget.tsx`, **681 lines**, almost character-for-character identical to a component that already existed in the admin dashboard. This is the story of how I deleted 680 of those lines, and what each step was really buying in SOLID terms. Not the textbook definitions, the actual diff.

## How I ended up with two copies

The admin dashboard on our Guru Besar Mengajar project already had a fully working "Postingan Saya" panel: a paginated CRUD surface (list, create, edit, delete, detail) living in `DashboardPostinganModule`. When the Guru Besar dashboard redesign landed, it needed the **same** panel, but in a different visual frame. Every widget on that dashboard is wrapped in a shared `WidgetShell` (icon, title, subtitle, an action slot), while the admin version sits in a plain bordered `<section>`.

The fastest path to a working screen was the obvious one: copy `DashboardPostinganModule` into a new `PostinganPanelWidget`, swap the outer `<section>` for `<WidgetShell>`, and ship it. It worked. Tests passed. Coverage was 100%. And it was wrong, because now there were two files holding the same fetch-list-paginate-mutate logic.

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

The duplication gate is not bureaucracy here. It is a fitness function pointing at a real maintenance hazard: every future change to postingan behaviour, a new field, a validation rule, a pagination tweak, would now have to be made twice, and the second edit is the one everyone forgets. The gate forced the design conversation I should have had upfront.

## The wrong fix, and the one I picked

The tempting "fix" is to extract a `usePostingan` hook and share it between two components. That removes some duplication but keeps two component shells in sync forever, and the bug surface (the JSX, the dialog wiring, the `aria` attributes) is exactly where our copies had already started to drift.

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

The default value matters. Existing callers pass no `variant`, get `'card'`, and render exactly what they rendered before. No admin test changed. That is the "closed for modification" half of OCP doing its job: an extension that is invisible to everything that came before it.

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

This is **Separation of Concerns** made physical. `body` knows about postingan data and nothing about layout. The two branches know about layout and nothing about how the data got there. The boundary is the `body` variable: one thing the data layer owns, handed to whichever frame the presentation layer chooses.

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

Those three `ReactNode` props (`icon`, `action`, `children`) are composition slots. The module does not configure the shell through a pile of boolean flags like `showIcon` or `headerStyle`; it *composes* the shell by handing it ready-made nodes. This is the **Dependency Inversion** instinct applied to UI: the shell depends on the abstraction "some renderable node", not on the concrete postingan button or the concrete `FileText` icon. I could drop that same `WidgetShell` around a calendar, a chart, or a to-do list, and it would not need a single edit. It is closed for modification precisely because it never assumed what it would contain.

The `action` slot is also where a smaller **Single Responsibility** refactor paid off. The "create" button plus the count label had been living inline inside `DashboardPostinganHeader`. The widget variant needed that same control, but in the shell's `action` slot rather than in the card's header. So I extracted it once:

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

Now `DashboardPostinganHeader` (the card path) renders `<DashboardPostinganHeaderAction />`, and the `WidgetShell` action slot renders the very same component. One button, one place to change its copy or its focus ring, two call sites. The header stopped doing two jobs (describing the panel *and* building the action) and went back to doing one.

## The payoff: a four-line component

With the engine, the frame, and the action all extracted, the original duplicate had nothing left to hold. `PostinganPanelWidget` collapsed from 681 lines into a configuration of the thing that already existed:

```tsx
import DashboardPostinganModule from '@/src/features/postingan/components/DashboardPostinganModule'

export default function PostinganPanelWidget() {
  return <DashboardPostinganModule role="GURU_BESAR" variant="widget" />
}
```

![Before and after of PostinganPanelWidget: a 681-line full copy of the CRUD logic on the left, a four-line wrapper that renders DashboardPostinganModule with variant widget on the right, for a net of 680 deletions and 99 insertions with coverage kept at 100 percent](/images/variant-prop-before-after.png)

That is the entire file. Its single responsibility is to *name a configuration*: the Guru Besar role, in the widget presentation. It has no logic to test beyond "does it render the module with these props", which is exactly the kind of component you want, the kind where there is nowhere for a bug to hide.

The commit came out at **99 insertions and 1,335 deletions** across the four touched files, a net removal of more than 1,200 lines, with the duplication gate back to green.

## Keeping it honest with tests

None of this would be safe to claim without tests proving behaviour did not move. The work was test-first throughout the dashboard rebuild: every widget landed as a `[RED]` failing test followed by a `[GREEN]` implementation. For the dedup, the discipline was slightly different, the behaviour already existed and had to *stay* identical, so the existing `PostinganPanelWidget.test.tsx` suite became the safety net. Most of its 627 lines were deleted alongside the duplicate logic, because those assertions now belonged to the module's own suite.

I added 16 lines to `DashboardPostinganModule.test.tsx` to pin the new branch: rendering with `variant="widget"` must produce the `WidgetShell` header, and rendering without it must still produce the plain `<section>`. Both files stayed at 100% coverage. The `variant` branch is the only new behaviour, and it is the one thing the new tests assert directly.

```tsx
it('renders inside a WidgetShell when variant is "widget"', () => {
  render(<DashboardPostinganModule role="GURU_BESAR" variant="widget" />)
  expect(screen.getByTestId('widget-shell-icon')).toBeInTheDocument()
})
```

## What the gate was really teaching

It is easy to read a SonarQube duplication failure as a chore: trim some lines, move on. But the gate was pointing at a design decision I had quietly deferred. "Two dashboards need the same panel" is not a copy-paste problem, it is a question about where the variation actually lives. Once I named the variation, it was a single axis (`card` versus `widget`) sitting on top of an otherwise identical engine, the rest of SOLID fell out almost mechanically:

- **OCP** gave me the `variant` prop with a safe default, so extending the module never touched the admin path.
- **SRP** pushed the action button into its own component and kept the four-line wrapper responsible for one decision.
- **SoC** drew the line between the `body` (data) and the two frames (presentation).
- **Composition / DIP** let `WidgetShell` accept slots instead of knowing anything about postingan, so it stays reusable for the next widget.

The reusable-shell-plus-thin-wrapper shape is the same one I keep reaching for elsewhere on this project, and it is worth internalising: when two screens look like a copy, the duplication is rarely the disease. It is the symptom of a variation you have not named yet. Name it, and the 680 lines tend to delete themselves.
