# Helve brand assets

> *helve* /helv/ — the handle of a hammer or axe: the part the human holds.

The name is the product's thesis. Helve's distinguishing feature is not that it
runs an agent — it is the non-bypassable tool guard, the approval gates and the
fact that everything stays on your machine. The agent is the head; you hold the
helve.

## Files

| File | Use |
| --- | --- |
| `helve-lockup.svg` | Mark + wordmark. README headers, docs, slides. 184×64. |
| `helve-mark.svg` | Mark alone, brand amber. Favicons, avatars, app icons. 64×64. |
| `helve-mark-mono.svg` | Mark alone, `currentColor`. Inline in text that already has a colour. |

## The mark

A hammer reduced to two parts:

- **The head** carries block-cursor proportions — a hammer to anyone, a caret to
  anyone who lives in a terminal. Its face is flat and its peen tapers, because a
  symmetrical head reads as the letter T next to the wordmark.
- **The haft** sits off-centre under the face and flares toward the butt, the way
  a real handle does so it cannot slip out of the hand. Two grip bands are cut
  out of it — not painted — so the mark sits on any background without a light
  halo where the binding should be.

Cutouts use `fill-rule="evenodd"` counter-subpaths rather than a `<mask>`, so
they survive scaling and reuse inside a transformed group.

## Colour

| Token | Hex | Note |
| --- | --- | --- |
| Helve amber | `#C8791B` | Forge amber. Chosen to hold contrast on both a white and a near-black ground, so one file serves both themes. |

Do not add a second brand colour. Semantic colours (approval, risk, blocked)
belong to the terminal theme in `shell/src/theme.ts`, not to the mark.

## Wordmark

The wordmark is drawn as geometric paths, not type — it depends on no installed
font, and its blocky construction matches the ANSI block letters the shell
prints on startup (`HELVE_LOGO` in `shell/src/presentation.ts`). Keep the two in
sync: they are the same wordmark in two media.

## Don'ts

- Don't recolour the mark outside the amber or `currentColor` variants.
- Don't set the wordmark in a system font — use `helve-lockup.svg`.
- Don't fill the grip bands. They are the point of the name.
