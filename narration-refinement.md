# Narration Refinement Validation Rules

When Claude refines robotic documentation-style notes into natural narration (Step 2.5), these validation rules ensure quality and faithfulness to the original content.

## Refinement Rules

### Rule 1: No New Steps

The refined narration must not introduce steps, actions, or information that were not present in the original notes. Step count must be less than or equal to the original.

**Check:** Count imperative sentences / action phrases in original vs refined. `refined_steps <= original_steps`.

### Rule 2: Per-Slide Word Cap

Each slide's refined narration must fall within the word budget:
- **Minimum:** 10 words (unless original is empty)
- **Maximum:** 55 words
- **Empty stays empty:** If the original note is empty, the refined note must also be empty

Slides exceeding 55 words tend to produce TTS audio that feels rushed or overly long for a single slide.

### Rule 3: Content Divergence Fallback

If the refined text diverges too far from the original meaning, fall back to the original text.

**Check:** Extract content words (nouns, verbs, adjectives — exclude stop words) from both original and refined. Compute overlap:

```
overlap = len(original_content_words & refined_content_words) / len(original_content_words)
```

If `overlap < 0.30` (less than 30% content word overlap), the refinement has drifted too far. Fall back to the original note for that slide.

### Rule 4: Empty Preservation

If a slide's original note text is empty (or whitespace-only), the refined version must also be empty. Never invent narration for slides that had no speaker notes.

### Rule 5: Sequence Preservation

The order of information within each slide must match the original. Do not reorder steps, even if a different order might seem more logical.

## Validation Checklist

Claude runs this checklist after generating refined notes and before writing `notes_refined.json`:

```
For each slide:
  [ ] If original is empty → refined is empty
  [ ] Word count: 10 ≤ words ≤ 55 (or 0 if empty)
  [ ] No new steps or information added
  [ ] Content word overlap ≥ 30% with original
  [ ] Information order matches original sequence
  [ ] Varied sentence openings (no more than 2 consecutive slides start the same way)
  [ ] Natural TTS pacing: commas for pauses, short sentences
```

## What Claude Fixes During Refinement

- Repetitive "Click on the X button" → varied phrasing ("Select X", "Go ahead and open X", "You'll want to click X")
- Missing transitions → adds "Next," "From here," "Now," "Once that's done,"
- No pauses → adds natural sentence breaks and commas for TTS pacing
- Flat imperative tone → conversational training-video style
- Run-on instructions → split into 1-3 sentences per slide

## Failure Handling

If a slide fails validation:
1. **Word cap exceeded:** Attempt to shorten once. If still over 55 words, use original.
2. **Content divergence:** Use original note text unchanged.
3. **Empty violation:** Set to empty string.
4. **New steps detected:** Use original note text unchanged.

Never block the pipeline on refinement failures. The original notes are always a valid fallback.
